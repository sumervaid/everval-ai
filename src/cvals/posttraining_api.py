"""Capability-registered checkpoint monitoring and research endpoints."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from fastapi import APIRouter, FastAPI, HTTPException, Query

from cvals.config import Settings
from cvals.identity import register_capability
from cvals.posttraining import PostTraining
from cvals.posttraining_models import (
    CheckpointCreate,
    InvestigationCreate,
    MixCreate,
    MixValidation,
    QualificationCreate,
    TrainingRunCreate,
)

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


def register_training_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(prefix="/api/training")
    registry: list[tuple[str, str, str]] = []

    def endpoint(
        method: str, path: str, capability: str, **kwargs: Any
    ) -> Callable[[Endpoint], Endpoint]:
        registry.append((method, "/api/training" + path, capability))

        def register(function: Endpoint) -> Endpoint:
            @wraps(function)
            def bounded(*args: Any, **values: Any) -> Any:
                try:
                    return function(*args, **values)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)[:2000]) from exc

            return cast(Endpoint, router.api_route(path, methods=[method], **kwargs)(bounded))

        return register

    @endpoint("GET", "/runs", "workspace.read")
    def runs(
        limit: int = Query(default=100, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> list[dict[str, Any]]:
        return PostTraining(settings).list_runs(limit, offset)

    @endpoint("POST", "/runs", "workspace.write", status_code=201)
    def create(body: TrainingRunCreate) -> dict[str, Any]:
        return PostTraining(settings).create_run(body)

    @endpoint("GET", "/runs/{run_id}", "workspace.read")
    def show(run_id: str) -> dict[str, Any]:
        return PostTraining(settings).dashboard(run_id)

    @endpoint("POST", "/runs/{run_id}/checkpoints", "workspace.write", status_code=201)
    def checkpoint(run_id: str, body: CheckpointCreate) -> dict[str, Any]:
        return PostTraining(settings).register_checkpoint(run_id, body)

    @endpoint("POST", "/runs/{run_id}/checkpoints/{checkpoint_id}/queue", "workspace.write")
    def queue(run_id: str, checkpoint_id: str) -> dict[str, Any]:
        result = PostTraining(settings).queue_checkpoint(run_id, checkpoint_id)
        app.state.worker.wake_event.set()
        return result

    @endpoint("POST", "/runs/{run_id}/checkpoints/{checkpoint_id}/analyze", "workspace.write")
    def analyze(run_id: str, checkpoint_id: str) -> dict[str, Any]:
        return PostTraining(settings).analyze(run_id, checkpoint_id)

    @endpoint("POST", "/runs/{run_id}/investigations", "workspace.write", status_code=201)
    def investigate(run_id: str, body: InvestigationCreate) -> dict[str, Any]:
        return PostTraining(settings).investigate(run_id, body)

    @endpoint("POST", "/runs/{run_id}/qualifications", "workspace.write", status_code=201)
    def qualify(run_id: str, body: QualificationCreate) -> dict[str, Any]:
        return PostTraining(settings).qualify(run_id, body)

    @endpoint("POST", "/runs/{run_id}/mix", "workspace.write", status_code=201)
    def mix(run_id: str, body: MixCreate) -> dict[str, Any]:
        return PostTraining(settings).select_mix(run_id, body)

    @endpoint(
        "POST", "/runs/{run_id}/mix/{selection_id}/validate", "workspace.write", status_code=201
    )
    def validate_mix(run_id: str, selection_id: str, body: MixValidation) -> dict[str, Any]:
        return PostTraining(settings).validate_mix(run_id, selection_id, body)

    app.include_router(router)
    for method, path, capability in registry:
        register_capability(app, method, path, capability)
