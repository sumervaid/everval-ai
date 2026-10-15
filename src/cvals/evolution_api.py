"""Role-scoped continuous evaluation, forecasts and private research evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, FastAPI, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from cvals.autopilot import Autopilot
from cvals.budgeting import BudgetPlanner
from cvals.community import CommunityPacks
from cvals.config import Settings
from cvals.identity import principal, register_capability
from cvals.models import Manifest
from cvals.optimization import Optimizer
from cvals.research import ResearchLab

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


class AutopilotRequest(Manifest):
    name: str = Field(min_length=1, max_length=160)
    experiment_id: str = Field(min_length=1, max_length=128)
    screening_experiment_id: str = Field(min_length=1, max_length=128)
    interval_minutes: int = Field(default=60, ge=5, le=525600, strict=True)
    trigger: Literal["periodic", "change", "both"] = "periodic"
    project_id: str | None = Field(default=None, max_length=128)
    lifetime_budget_usd: float = Field(ge=0, le=1000000)
    enabled: bool = False
    discover: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    escalate: Literal["always", "regression", "change"] = "regression"


class AutopilotUpdate(Manifest):
    enabled: bool | None = None
    lifetime_budget_usd: float | None = Field(default=None, ge=0, le=1000000)
    config: dict[str, Any] | None = None


class ProjectionRequest(Manifest):
    experiment_id: str = Field(min_length=1, max_length=128)
    runs: int = Field(default=30, ge=1, le=100000, strict=True)
    screening_experiment_id: str | None = Field(default=None, max_length=128)
    escalation_rate: float = Field(default=1, ge=0, le=1)
    cap_usd: float | None = Field(default=None, ge=0, le=1000000)


class ResearchRequest(Manifest):
    repetitions: int = Field(default=3, ge=2, le=10, strict=True)


class ReductionRequest(Manifest):
    max_evaluations: int = Field(default=40, ge=2, le=100, strict=True)


class OptimizationRequest(Manifest):
    name: str = Field(min_length=1, max_length=160)
    development_collection_id: str = Field(min_length=1, max_length=128)
    holdout_collection_id: str = Field(min_length=1, max_length=128)
    system_ids: list[str] = Field(min_length=1, max_length=10)
    budget_usd: float = Field(ge=0, le=100000)
    min_pass_rate: float = Field(default=0.95, ge=0, le=1)
    max_p95_ms: float = Field(default=60000, ge=1, le=604800000)
    repetitions: int = Field(default=1, ge=1, le=10, strict=True)


class PackApprovalRequest(Manifest):
    rationale: str = Field(min_length=1, max_length=10000)
    expected_behaviors: dict[str, str] = Field(min_length=1, max_length=1000)


def register_evolution_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(prefix="/api")
    registry: list[tuple[str, str, str]] = []

    def endpoint(
        method: str, path: str, capability: str, **kwargs: Any
    ) -> Callable[[Endpoint], Endpoint]:
        registry.append((method, "/api" + path, capability))
        return router.api_route(path, methods=[method], **kwargs)

    @endpoint("GET", "/budget/status", "workspace.read")
    def budget_status() -> dict[str, Any]:
        return BudgetPlanner(settings).status()

    @endpoint("POST", "/budget/projection", "workspace.read")
    def budget_projection(body: ProjectionRequest) -> dict[str, Any]:
        return BudgetPlanner(settings).project(**body.model_dump())

    @endpoint("GET", "/autopilot", "workspace.read")
    def autopilots() -> list[dict[str, Any]]:
        return Autopilot(settings).list()

    @endpoint("POST", "/autopilot", "workspace.write", status_code=201)
    def create_autopilot(body: AutopilotRequest) -> dict[str, Any]:
        return Autopilot(settings).create(**body.model_dump())

    @endpoint("GET", "/autopilot/{policy_id}", "workspace.read")
    def autopilot(policy_id: str) -> dict[str, Any]:
        service = Autopilot(settings)
        return {
            "policy": service.get(policy_id),
            "budget": service.budget(policy_id),
            "history": service.history(policy_id),
            "investigations": service.investigations(policy_id),
        }

    @endpoint("PATCH", "/autopilot/{policy_id}", "workspace.write")
    def update_autopilot(policy_id: str, body: AutopilotUpdate) -> dict[str, Any]:
        return Autopilot(settings).configure(policy_id, **body.model_dump(exclude_none=True))

    @endpoint("GET", "/research", "workspace.read")
    def research_records() -> list[dict[str, Any]]:
        return ResearchLab(settings).list_records()

    @endpoint("GET", "/research/{record_id}", "workspace.read")
    def research_record(record_id: str) -> dict[str, Any]:
        return ResearchLab(settings).get_record(record_id)

    @endpoint(
        "POST",
        "/experiments/{experiment_id}/trials/{trial_id}/investigate",
        "workspace.write",
        status_code=201,
    )
    def investigate(experiment_id: str, trial_id: str, body: ResearchRequest) -> dict[str, Any]:
        return ResearchLab(settings).repeat_regrade(experiment_id, trial_id, **body.model_dump())

    @endpoint(
        "POST",
        "/experiments/{experiment_id}/trials/{trial_id}/reduce",
        "workspace.write",
        status_code=201,
    )
    def reduce_output(experiment_id: str, trial_id: str, body: ReductionRequest) -> dict[str, Any]:
        return ResearchLab(settings).minimize_output(experiment_id, trial_id, **body.model_dump())

    @endpoint("POST", "/collections/{collection_id}/quality", "workspace.write", status_code=201)
    def quality(collection_id: str) -> dict[str, Any]:
        return ResearchLab(settings).quality(collection_id)

    @endpoint("GET", "/optimization", "workspace.read")
    def studies() -> list[dict[str, Any]]:
        return Optimizer(settings).list()

    @endpoint("POST", "/optimization", "workspace.write", status_code=201)
    def create_study(body: OptimizationRequest) -> dict[str, Any]:
        return Optimizer(settings).create(**body.model_dump())

    @endpoint("GET", "/optimization/{study_id}", "workspace.read")
    def study(study_id: str) -> dict[str, Any]:
        return Optimizer(settings).get(study_id)

    @endpoint("POST", "/optimization/{study_id}/advance", "workspace.write")
    def advance_study(study_id: str) -> dict[str, Any]:
        return Optimizer(settings).advance(study_id)

    @endpoint("GET", "/community/catalog", "summary.read")
    def pack_catalog() -> dict[str, Any]:
        return CommunityPacks(settings).catalog()

    @endpoint("GET", "/community/packs", "workspace.read")
    def packs() -> list[dict[str, Any]]:
        return CommunityPacks(settings).list()

    @endpoint("POST", "/community/packs", "workspace.write", status_code=201)
    async def stage_pack(request: Request) -> dict[str, Any]:
        raw = await request.body()
        return await run_in_threadpool(
            CommunityPacks(settings).stage, raw, actor=principal(request).id
        )

    @endpoint("GET", "/community/packs/{pack_id}", "workspace.read")
    def pack(pack_id: str) -> dict[str, Any]:
        return CommunityPacks(settings).get(pack_id)

    @endpoint("POST", "/community/packs/{pack_id}/approve", "workspace.write", status_code=201)
    def approve_pack(pack_id: str, body: PackApprovalRequest, request: Request) -> dict[str, Any]:
        return CommunityPacks(settings).approve(
            pack_id, actor=principal(request).id, **body.model_dump()
        )

    app.include_router(router)
    for method, path, capability in registry:
        register_capability(app, method, path, capability)
