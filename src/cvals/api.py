"""Local HTTP interface for the evaluation workspace."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from cvals import __version__
from cvals.config import Settings
from cvals.engine import Engine
from cvals.exporting import export_experiment
from cvals.identity import (
    IdentityStore,
    Principal,
    _request_actor,
    register_capability,
    route_capability,
)
from cvals.models import Collection, ExperimentCreate, ReplaySubmission, System, content_hash
from cvals.reviews import ReviewStore
from cvals.schedules import ScheduleStore
from cvals.store import Store

log = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}


class RepositoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class SubmissionImport(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=200)
    collection_id: str = Field(min_length=1, max_length=128)
    collection_hash: str = Field(min_length=64, max_length=64)
    submissions: dict[str, ReplaySubmission] = Field(min_length=1, max_length=1000)
    provenance: dict[str, Any] = Field(default_factory=dict)
    system_name: str | None = Field(default=None, max_length=200)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    trial_id: str = Field(min_length=1, max_length=128)
    reviewer: str = Field(min_length=1, max_length=200)
    dimension: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0, le=1, strict=True)
    rationale: str = Field(min_length=1, max_length=10000)


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=200)
    experiment_id: str = Field(min_length=1, max_length=128)
    interval_minutes: int = Field(ge=5, le=525600)
    enabled: bool = True
    lifetime_budget_usd: float | None = Field(default=None, ge=0, le=1000000)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class Worker:
    """A single recoverable consumer; durable work remains in SQLite."""

    def __init__(self, store: Store, settings: Settings) -> None:
        self.engine = Engine(store, settings)
        self.settings = settings
        self.schedules = ScheduleStore(settings, store)
        from cvals.autopilot import Autopilot
        from cvals.jobs import Jobs
        from cvals.optimization import Optimizer

        self.autopilot = Autopilot(settings, store)
        self.optimizer = Optimizer(settings)
        from cvals.posttraining import PostTraining

        self.training = PostTraining(settings, store)
        self.jobs = Jobs(settings)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        from cvals.sandbox import cleanup_sandboxes

        self.engine.recover()
        cleanup_sandboxes(self.settings)
        self.thread = threading.Thread(target=self._run, name="cvals-worker", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        next_schedule_check = 0.0
        while not self.stop_event.is_set():
            try:
                if time.monotonic() >= next_schedule_check:
                    self.schedules.tick()
                    self.autopilot.tick()
                    self.optimizer.tick()
                    self.autopilot.investigate_pending()
                    self.training.tick()
                    next_schedule_check = time.monotonic() + 5
                self.jobs.run_pending_once()
                if self.engine.run_pending_once():
                    continue
            except Exception:
                log.exception("Worker iteration failed; pending work is retained.")
            self.wake_event.wait(0.5)
            self.wake_event.clear()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=5)


def docker_available(settings: Settings) -> bool:
    if shutil.which("docker") is None:
        return False
    env = os.environ.copy()
    if settings.docker_host:
        env["DOCKER_HOST"] = settings.docker_host
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            env=env,
            capture_output=True,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    store = Store(settings)
    store.init()
    worker = Worker(store, settings)
    reviews = ReviewStore(settings)
    identities = IdentityStore(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(
        title="Everval AI",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.worker = worker
    app.state.settings = settings
    app.state.identities = identities
    app.state.capabilities = {}
    from cvals.workbench_api import register_workbench_routes

    register_workbench_routes(app, settings)
    from cvals.team_api import register_team_routes

    register_team_routes(app, settings)
    from cvals.experience_api import register_experience_routes

    register_experience_routes(app, settings)
    from cvals.evolution_api import register_evolution_routes

    register_evolution_routes(app, settings)
    from cvals.posttraining_api import register_training_routes

    register_training_routes(app, settings)
    hosts = ["localhost", "127.0.0.1", "::1", "[::1]", "testserver"]
    hosts.extend(
        h.strip()
        for h in os.getenv("EVERVAL_ALLOWED_HOSTS", os.getenv("CVALS_ALLOWED_HOSTS", "")).split(",")
        if h.strip()
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    from cvals.http_limits import BodyLimitMiddleware

    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)

    @app.middleware("http")
    async def boundaries(request: Request, call_next: Any) -> Response:
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > settings.max_request_bytes or int(length) < 0:
                    return JSONResponse(
                        {"detail": "Request exceeds the upload limit."}, status_code=413
                    )
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
        who = Principal("local-console", "Local operator", "admin", "local")
        is_api = request.url.path == "/api" or request.url.path.startswith("/api/")
        public_health = request.url.path == "/api/health" and request.method == "GET"
        if is_api and not public_health:
            if await run_in_threadpool(identities.enabled):
                authorization = request.headers.get("authorization", "")
                token = authorization[7:] if authorization.startswith("Bearer ") else ""
                authenticated = await run_in_threadpool(identities.authenticate, token)
                if authenticated is None:
                    return JSONResponse(
                        {"detail": "A valid personal access token is required."},
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                    )
                who = authenticated
                capability = route_capability(app, request)
                if capability is None or not who.permits(capability):
                    return JSONResponse(
                        {"detail": "This identity cannot access this endpoint."},
                        status_code=403,
                        headers={"Cache-Control": "no-store"},
                    )
            elif settings.token:
                provided = request.headers.get("authorization", "")
                if not hmac.compare_digest(
                    provided.encode("utf-8"), ("Bearer " + settings.token).encode("utf-8")
                ):
                    return JSONResponse(
                        {"detail": "A workspace access token is required."},
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                    )
                who = Principal("legacy-token", "Workspace operator", "admin", "legacy")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                host = request.headers.get("host", "")
                if origin:
                    try:
                        parsed_origin = urlsplit(origin)
                        matches_origin = (
                            parsed_origin.scheme == request.url.scheme
                            and parsed_origin.netloc == host
                            and parsed_origin.scheme in {"http", "https"}
                            and not parsed_origin.path
                            and not parsed_origin.query
                            and not parsed_origin.fragment
                        )
                    except ValueError:
                        matches_origin = False
                    if not matches_origin:
                        return JSONResponse(
                            {"detail": "Cross-origin changes are not allowed."}, status_code=403
                        )
                if request.headers.get("sec-fetch-site") == "cross-site":
                    return JSONResponse(
                        {"detail": "Cross-site changes are not allowed."}, status_code=403
                    )
                content_type = request.headers.get("content-type", "").split(";")[0]
                if request.method in {"POST", "PUT", "PATCH"} and content_type not in {
                    "application/json",
                    "multipart/form-data",
                    "",
                }:
                    return JSONResponse(
                        {"detail": "Use JSON or a multipart upload."}, status_code=415
                    )
        request.state.principal = who
        context = _request_actor.set(who.id if who.mode == "team" else None)
        try:
            response: Response = await call_next(request)
        finally:
            _request_actor.reset(context)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": "The requested record was not found."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def execution_status() -> dict[str, Any]:
        from cvals.runtime import worker_running

        dedicated = worker_running(settings)
        embedded = worker.running
        mode = (
            "embedded+dedicated"
            if embedded and dedicated
            else "embedded"
            if embedded
            else "dedicated"
            if dedicated
            else "stopped"
        )
        return {"worker_running": embedded or dedicated, "worker_mode": mode}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, **execution_status()}

    @app.get("/api/schema")
    def schema() -> dict[str, Any]:
        return app.openapi()

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        return store.overview()

    @app.get("/api/collections")
    def collections() -> list[dict[str, Any]]:
        return store.list_collections()

    @app.get("/api/collections/{collection_id}")
    def collection(collection_id: str) -> dict[str, Any]:
        return store.get_collection(collection_id)

    @app.get("/api/collections/{collection_id}/agent-view")
    def agent_view(collection_id: str) -> Response:
        item = Collection.model_validate(store.get_collection(collection_id))
        value = {
            "schema_version": 1,
            "collection_id": item.id,
            "name": item.name,
            "collection_hash": content_hash(item.model_dump(mode="json")),
            "scenarios": [scenario.agent_view() for scenario in item.scenarios],
        }
        return Response(
            json.dumps(value, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{item.id}-tasks.json"'},
        )

    @app.post("/api/collections", status_code=201)
    def save_collection(body: Collection) -> dict[str, Any]:
        return store.save_collection(body)

    @app.delete("/api/collections/{collection_id}", status_code=204)
    def delete_collection(collection_id: str) -> Response:
        store.delete_collection(collection_id)
        return Response(status_code=204)

    @app.post("/api/collections/{collection_id}/validate")
    def validate(collection_id: str) -> dict[str, Any]:
        from cvals.domains import validate_collection

        return validate_collection(
            Collection.model_validate(store.get_collection(collection_id)), settings
        )

    @app.post("/api/starters")
    def starters() -> dict[str, Any]:
        from cvals.packs import starter_collections

        existing = {c["id"] for c in store.list_collections()}
        installed = []
        for item in starter_collections():
            if item.id not in existing:
                installed.append(store.save_collection(item))
        store.ensure_fixture_systems()
        return {"installed": len(installed), "collections": store.list_collections()}

    async def bounded_upload(file: StarletteUploadFile) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(64 * 1024):
            total += len(chunk)
            if total > settings.max_request_bytes:
                raise HTTPException(413, "File exceeds the upload limit.")
            chunks.append(chunk)
        await file.close()
        return b"".join(chunks)

    @app.post("/api/import", status_code=201)
    async def import_collection(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            async with request.form(
                max_files=1, max_fields=4, max_part_size=settings.max_request_bytes
            ) as form:
                file = form.get("file")
                if not isinstance(file, StarletteUploadFile):
                    raise HTTPException(422, "Choose a JSON or YAML collection file.")
                raw = await bounded_upload(file)
                name = getattr(file, "filename", "") or ""
                filename = name
        else:
            raw = await request.body()
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "File exceeds the upload limit.")
            filename = "collection.json"

        def save_import() -> dict[str, Any]:
            body = _load_collection(raw, filename)
            return store.save_collection(Collection.model_validate(body))

        return await run_in_threadpool(save_import)

    @app.post("/api/sources")
    async def sources(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        from cvals.ingestion import ingest_source

        name = file.filename or "source.txt"
        raw = await bounded_upload(file)
        return await run_in_threadpool(ingest_source, name, raw)

    @app.post("/api/repositories/inspect")
    def inspect_repo(body: RepositoryRequest) -> dict[str, Any]:
        from cvals.ingestion import inspect_repository

        return inspect_repository(body.path, body.limit)

    @app.post("/api/repositories/mine")
    def mine_repo(body: RepositoryRequest) -> dict[str, Any]:
        from cvals.domains.mining import mine_repository

        result = mine_repository(body.path, body.limit)
        if result.get("collection") is not None:
            result["collection"] = result["collection"].model_dump(mode="json")
        return result

    @app.get("/api/schedules")
    def schedules() -> list[dict[str, Any]]:
        return worker.schedules.list()

    @app.post("/api/schedules", status_code=201)
    def create_schedule(body: ScheduleRequest) -> dict[str, Any]:
        return worker.schedules.create(
            body.name,
            body.experiment_id,
            body.interval_minutes,
            body.enabled,
            lifetime_budget_usd=body.lifetime_budget_usd,
        )

    @app.patch("/api/schedules/{schedule_id}")
    def update_schedule(schedule_id: str, body: ScheduleUpdate) -> dict[str, Any]:
        return worker.schedules.set_enabled(schedule_id, body.enabled)

    @app.get("/api/systems")
    def systems() -> list[dict[str, Any]]:
        values = store.list_systems()
        for item in values:
            key_env = item.get("api_key_env")
            item["configured"] = (
                item.get("kind") != "http" or not key_env or bool(os.getenv(key_env))
            )
        return values

    @app.post("/api/systems", status_code=201)
    def save_system(body: System) -> dict[str, Any]:
        return store.save_system(body)

    @app.delete("/api/systems/{system_id}", status_code=204)
    def delete_system(system_id: str) -> Response:
        store.delete_system(system_id)
        return Response(status_code=204)

    @app.post("/api/submissions", status_code=201)
    def import_submissions(body: SubmissionImport) -> dict[str, Any]:
        result = store.import_submissions(
            collection_id=body.collection_id,
            name=body.name,
            collection_hash=body.collection_hash,
            submissions={
                key: value.model_dump(mode="json") for key, value in body.submissions.items()
            },
            provenance=body.provenance,
            system_name=body.system_name,
        )
        worker.wake_event.set()
        return result

    @app.get("/api/experiments")
    def experiments() -> list[dict[str, Any]]:
        return store.list_experiments()

    @app.post("/api/experiments", status_code=201)
    def create_experiment(body: ExperimentCreate) -> dict[str, Any]:
        result = store.create_experiment(body)
        worker.wake_event.set()
        return result

    @app.get("/api/experiments/{experiment_id}")
    def experiment(experiment_id: str) -> dict[str, Any]:
        item = store.get_experiment(experiment_id)
        item["human_reviews"] = reviews.list(experiment_id)
        return item

    @app.get("/api/experiments/{experiment_id}/reviews")
    def list_reviews(experiment_id: str) -> list[dict[str, Any]]:
        return reviews.list(experiment_id)

    @app.post("/api/experiments/{experiment_id}/reviews", status_code=201)
    def add_review(experiment_id: str, body: ReviewRequest) -> dict[str, Any]:
        return reviews.add(
            experiment_id, body.trial_id, body.reviewer, body.dimension, body.score, body.rationale
        )

    @app.post("/api/experiments/{experiment_id}/cancel")
    def cancel(experiment_id: str) -> dict[str, Any]:
        return store.cancel_experiment(experiment_id)

    @app.post("/api/experiments/{experiment_id}/rerun", status_code=201)
    def rerun(experiment_id: str) -> dict[str, Any]:
        result = store.rerun_experiment(experiment_id)
        worker.wake_event.set()
        return result

    @app.get("/api/experiments/{experiment_id}/export")
    def export(experiment_id: str, format: Literal["json", "csv", "html"] = "json") -> Response:
        item = store.get_experiment(experiment_id)
        item["human_reviews"] = reviews.list(experiment_id)
        content, media_type = export_experiment(item, format)
        return Response(
            content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="experiment-{item["id"]}.{format}"'
            },
        )

    @app.get("/api/experiments/{experiment_id}/events")
    async def events(
        experiment_id: str,
        request: Request,
        after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    ) -> StreamingResponse:
        await run_in_threadpool(store.execution_state, experiment_id)
        if after == 0 and request.headers.get("last-event-id"):
            try:
                after = int(request.headers["last-event-id"])
                if not 0 <= after <= 2**63 - 1:
                    raise ValueError("Cursor out of range")
            except ValueError as exc:
                raise HTTPException(
                    400, "Last-Event-ID must be a nonnegative 64-bit integer."
                ) from exc

        async def stream() -> AsyncIterator[str]:
            cursor = after
            while not await request.is_disconnected():
                if await run_in_threadpool(identities.enabled) and not await run_in_threadpool(
                    identities.active, request.state.principal
                ):
                    return
                batch = await run_in_threadpool(store.events, experiment_id, cursor)
                if batch:
                    for event in batch:
                        cursor = event["id"]
                        yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                    # Drain every bounded page before inspecting terminal status.
                    continue
                item = await run_in_threadpool(store.execution_state, experiment_id)
                if item["status"] in TERMINAL:
                    # Completion may have committed between the empty read and
                    # the state read. Include its final event before closing.
                    if await run_in_threadpool(store.events, experiment_id, cursor):
                        continue
                    yield "event: complete\ndata: {}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/settings")
    def workspace_settings() -> dict[str, Any]:
        return {
            "data_dir": str(settings.data_dir),
            "version": __version__,
            "python_version": platform.python_version(),
            "storage_free_bytes": shutil.disk_usage(settings.data_dir).free,
            "docker_available": docker_available(settings),
            "remote_providers_enabled": settings.allow_remote_providers,
            "internal_provider_origins": list(settings.internal_provider_origins),
            "provider_ca_configured": settings.provider_ca_bundle is not None,
            **execution_status(),
            "auth_enabled": bool(settings.token) or identities.enabled(),
        }

    for path in (
        "/api/schema",
        "/api/overview",
        "/api/collections",
        "/api/collections/{collection_id}",
        "/api/collections/{collection_id}/agent-view",
        "/api/schedules",
        "/api/systems",
        "/api/experiments",
        "/api/experiments/{experiment_id}",
        "/api/experiments/{experiment_id}/reviews",
        "/api/experiments/{experiment_id}/export",
        "/api/experiments/{experiment_id}/events",
        "/api/settings",
        "/api/projects",
        "/api/projects/{project_id}/candidates",
        "/api/projects/{project_id}/profile",
        "/api/releases",
        "/api/jobs",
        "/api/jobs/{job_id}",
        "/api/audit",
        "/api/audit/verify",
        "/api/experiments/{experiment_id}/gates",
        "/api/compare",
    ):
        register_capability(app, "GET", path, "workspace.read")
    for path in (
        "/api/collections",
        "/api/collections/{collection_id}/validate",
        "/api/starters",
        "/api/import",
        "/api/sources",
        "/api/repositories/inspect",
        "/api/repositories/mine",
        "/api/schedules",
        "/api/systems",
        "/api/submissions",
        "/api/experiments",
        "/api/experiments/{experiment_id}/reviews",
        "/api/experiments/{experiment_id}/cancel",
        "/api/experiments/{experiment_id}/rerun",
        "/api/projects",
        "/api/projects/{project_id}/discover",
        "/api/projects/{project_id}/candidates/{candidate_id}/review",
        "/api/projects/{project_id}/candidates/{candidate_id}/validate",
        "/api/projects/{project_id}/profile",
        "/api/projects/{project_id}/environment/plan",
        "/api/projects/{project_id}/environment/prepare",
        "/api/projects/{project_id}/releases",
        "/api/experiments/{experiment_id}/gates",
    ):
        register_capability(app, "POST", path, "workspace.write")
    register_capability(app, "PATCH", "/api/schedules/{schedule_id}", "workspace.write")
    register_capability(app, "DELETE", "/api/collections/{collection_id}", "workspace.write")
    register_capability(app, "DELETE", "/api/systems/{system_id}", "workspace.write")

    static = Path(__file__).parent / "static"
    if static.is_dir():
        assets = static / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> Response:
            if path.startswith("api/"):
                raise HTTPException(404, "Endpoint not found.")
            if path in {"favicon.svg", "favicon.ico"} and (static / path).is_file():
                return FileResponse(static / path)
            return FileResponse(static / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def frontend_help() -> dict[str, str]:
            return {
                "message": "The API is ready. Build the web interface with make web.",
                "schema": "/api/schema",
            }

    return app


def _load_collection(raw: bytes, filename: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        if filename.lower().endswith((".yaml", ".yml")):
            # Alias expansion and deeply nested structures are unnecessary for manifests.
            depth = 0
            for index, token in enumerate(yaml.scan(text)):
                if index >= 100000:
                    raise ValueError("YAML contains too many tokens.")
                if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
                    raise ValueError("YAML aliases and anchors are not supported.")
                if isinstance(
                    token,
                    (
                        yaml.tokens.FlowMappingStartToken,
                        yaml.tokens.FlowSequenceStartToken,
                        yaml.tokens.BlockMappingStartToken,
                        yaml.tokens.BlockSequenceStartToken,
                    ),
                ):
                    depth += 1
                    if depth > 64:
                        raise ValueError("YAML exceeds the nesting limit.")
                elif isinstance(
                    token,
                    (
                        yaml.tokens.FlowMappingEndToken,
                        yaml.tokens.FlowSequenceEndToken,
                        yaml.tokens.BlockEndToken,
                    ),
                ):
                    depth -= 1
            value = yaml.safe_load(text)
        else:
            value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError, RecursionError) as exc:
        raise ValueError("The collection must be valid UTF-8 JSON or YAML.") from exc
    if not isinstance(value, dict):
        raise ValueError("A collection must be an object.")
    return value
