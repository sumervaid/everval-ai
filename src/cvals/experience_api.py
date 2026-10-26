"""Evidence investigation and cumulative spending controls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, FastAPI, Query
from pydantic import Field

from cvals.config import Settings
from cvals.identity import register_capability
from cvals.models import Manifest
from cvals.spend_limits import SpendLimits
from cvals.workbench import Workbench

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


class LimitRequest(Manifest):
    scope: str = Field(default="workspace", min_length=1, max_length=200)
    daily_usd: float | None = Field(default=None, ge=0)
    monthly_usd: float | None = Field(default=None, ge=0)
    lifetime_usd: float | None = Field(default=None, ge=0)


def register_experience_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(prefix="/api")
    workbench = Workbench(settings)
    registry: list[tuple[str, str, str]] = []

    def endpoint(
        method: str, path: str, capability: str, **kwargs: Any
    ) -> Callable[[Endpoint], Endpoint]:
        registry.append((method, "/api" + path, capability))
        return router.api_route(path, methods=[method], **kwargs)

    @endpoint("GET", "/rankings", "workspace.read")
    def rankings(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict[str, Any]:
        return workbench.rankings(limit=limit)

    @endpoint("GET", "/experiments/{experiment_id}/triage", "workspace.read")
    def triage(experiment_id: str) -> dict[str, Any]:
        return workbench.triage(experiment_id)

    @endpoint("GET", "/experiments/{experiment_id}/trials/{trial_id}/diagnostics", "workspace.read")
    def diagnostics(experiment_id: str, trial_id: str) -> dict[str, Any]:
        return workbench.diagnose(experiment_id, trial_id)

    @endpoint(
        "POST",
        "/experiments/{experiment_id}/trials/{trial_id}/regrade",
        "workspace.write",
        status_code=201,
    )
    def regrade(experiment_id: str, trial_id: str) -> dict[str, Any]:
        return workbench.regrade(experiment_id, trial_id)

    @endpoint("GET", "/experiments/{experiment_id}/economics", "workspace.read")
    def economics(experiment_id: str) -> dict[str, Any]:
        return workbench.economics(experiment_id)

    @endpoint("GET", "/experiments/{experiment_id}/brief", "summary.read")
    def brief(experiment_id: str) -> dict[str, Any]:
        from cvals.blind_studies import BlindStudies

        BlindStudies(settings).require_revealed_experiment(experiment_id)
        return workbench.decision_brief(experiment_id)

    @endpoint("GET", "/spending-limits", "workspace.read")
    def limits() -> list[dict[str, Any]]:
        return SpendLimits(settings).list()

    @endpoint("PUT", "/spending-limits", "team.admin")
    def configure_limit(body: LimitRequest) -> dict[str, Any]:
        return SpendLimits(settings).configure(**body.model_dump())

    app.include_router(router)
    for method, path, capability in registry:
        register_capability(app, method, path, capability)
