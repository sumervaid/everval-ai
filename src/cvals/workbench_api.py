"""Bounded repository and benchmark endpoints behind workspace authentication."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Query
from pydantic import Field

from cvals.config import Settings
from cvals.models import Manifest
from cvals.workbench import Workbench


class ConnectRequest(Manifest):
    source: str = Field(min_length=1, max_length=2048)


class DiscoveryRequest(Manifest):
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2000)


class StatementRequest(Manifest):
    statement: str = Field(min_length=1, max_length=50000)
    reviewer: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=10000)


class ReleaseRequest(Manifest):
    name: str = Field(min_length=1, max_length=200)


class EnvironmentRequest(Manifest):
    packages: list[str] | None = Field(default=None, max_length=100)
    revision: str | None = Field(default=None, max_length=100)
    base_image: str | None = Field(default=None, max_length=300)
    language: Literal["python", "javascript", "typescript"] = "python"
    runner: Literal["node-test", "jest", "vitest"] = "node-test"
    artifacts: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] | None = Field(
        default=None, max_length=200
    )


class GateRequest(Manifest):
    policy: dict[str, Any] | None = None
    baseline_id: str | None = Field(default=None, max_length=100)
    policy_id: str | None = Field(default=None, max_length=100)


class ProfileRequest(Manifest):
    image: str = Field(min_length=1, max_length=300)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    plugins: list[str] | None = Field(default=None, max_length=16)
    runtime: Literal["python-pytest", "node-test", "jest", "vitest"] = "python-pytest"
    language: Literal["python", "javascript", "typescript"] = "python"


def register_workbench_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(prefix="/api")

    def workbench() -> Workbench:
        return Workbench(settings)

    def enqueue(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        from cvals.jobs import Jobs
        from cvals.runtime import ensure_worker

        ensure_worker(settings)
        return Jobs(settings).enqueue(kind, payload)

    @router.get("/projects")
    def projects() -> list[dict[str, Any]]:
        return workbench().projects()

    @router.post("/projects", status_code=201)
    def connect(body: ConnectRequest) -> dict[str, Any]:
        return workbench().connect(body.source)

    @router.post("/projects/{project_id}/discover", status_code=202)
    def discover(project_id: str, body: DiscoveryRequest) -> dict[str, Any]:
        from cvals.projects import Projects

        Projects(settings).get(project_id)
        return enqueue("discover", {"project_id": project_id, **body.model_dump()})

    @router.get("/projects/{project_id}/candidates")
    def candidates(project_id: str) -> list[dict[str, Any]]:
        return workbench().candidates(project_id)

    @router.post("/projects/{project_id}/candidates/{candidate_id}/review")
    def review(project_id: str, candidate_id: str, body: StatementRequest) -> dict[str, Any]:
        return workbench().review_candidate(project_id, candidate_id, **body.model_dump())

    @router.post("/projects/{project_id}/candidates/{candidate_id}/validate", status_code=202)
    def validate(project_id: str, candidate_id: str) -> dict[str, Any]:
        from cvals.projects import Projects

        Projects(settings).candidate(project_id, candidate_id)
        return enqueue("validate", {"project_id": project_id, "candidate_id": candidate_id})

    @router.get("/projects/{project_id}/profile")
    def profile(project_id: str) -> dict[str, Any] | None:
        return workbench().project_profile(project_id)

    @router.post("/projects/{project_id}/profile")
    def configure(project_id: str, body: ProfileRequest) -> dict[str, Any]:
        return workbench().add_project_profile(project_id, **body.model_dump())

    @router.post("/projects/{project_id}/environment/plan")
    def plan(project_id: str, body: EnvironmentRequest) -> dict[str, Any]:
        return workbench().environment_plan(project_id, **body.model_dump())

    @router.post("/projects/{project_id}/environment/prepare", status_code=202)
    def prepare(project_id: str, body: EnvironmentRequest) -> dict[str, Any]:
        # Admission parses dependencies before queuing any network operation.
        plan = workbench().environment_plan(project_id, **body.model_dump())
        payload: dict[str, Any] = {
            "project_id": project_id,
            "packages": body.packages,
            "revision": plan["source_commit"],
            "base_image": plan["base_image_digest"],
        }
        if body.language != "python":
            payload.update(language=body.language, runner=body.runner)
        if body.artifacts:
            payload["artifacts"] = body.artifacts
        return enqueue("prepare", payload)

    @router.get("/releases")
    def releases(project_id: str | None = None) -> list[dict[str, Any]]:
        return workbench().releases(project_id)

    @router.post("/projects/{project_id}/releases", status_code=201)
    def release(project_id: str, body: ReleaseRequest) -> dict[str, Any]:
        return workbench().release(project_id, body.name)

    @router.get("/jobs")
    def jobs() -> list[dict[str, Any]]:
        from cvals.jobs import Jobs

        return Jobs(settings).list()

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        from cvals.jobs import Jobs

        return Jobs(settings).get(job_id)

    @router.get("/audit")
    def audit(limit: int = Query(default=100, ge=1, le=10000)) -> list[dict[str, Any]]:
        return workbench().audit(limit)

    @router.get("/audit/verify")
    def verify() -> dict[str, Any]:
        from cvals.audit import AuditLog

        return AuditLog(settings).verify()

    @router.get("/experiments/{experiment_id}/gates")
    def gate_decisions(experiment_id: str) -> list[dict[str, Any]]:
        from cvals.policies import PolicyStore

        return PolicyStore(settings).decisions(experiment_id)

    @router.post("/experiments/{experiment_id}/gates", status_code=201)
    def gates(experiment_id: str, body: GateRequest) -> dict[str, Any]:
        return workbench().gates(experiment_id, **body.model_dump())

    @router.get("/compare")
    def compare(before: str, after: str) -> dict[str, Any]:
        return workbench().diff(before, after)

    app.include_router(router)
