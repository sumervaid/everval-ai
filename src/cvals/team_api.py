"""Browser endpoints for local team access, assigned reviews, and approved cases."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from cvals.case_imports import Approval, CaseImports
from cvals.config import Settings
from cvals.identity import IdentityStore, principal, register_capability
from cvals.models import Manifest
from cvals.review_queues import Judgment, QueueRequest, ReviewQueues, Rubric
from cvals.store import Store

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


class UserRequest(Manifest):
    name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "operator", "reviewer", "viewer"]


class TokenRequest(Manifest):
    label: str = Field(min_length=1, max_length=200)
    days: int = Field(default=30, ge=1, le=365)


class UserState(Manifest):
    active: bool


class Assignments(Manifest):
    reviewer_ids: list[str] = Field(min_length=1, max_length=50)


class DecisionRequest(Manifest):
    evidence_hash: str = Field(min_length=64, max_length=64)
    outcome: Literal["adopt", "reject", "more_evidence"]
    rationale: str = Field(min_length=1, max_length=10000)


class PublishCases(Manifest):
    name: str = Field(min_length=1, max_length=200)
    case_ids: list[str] = Field(min_length=1, max_length=500)


def register_team_routes(app: FastAPI, settings: Settings) -> None:
    router = APIRouter(prefix="/api")
    identities = IdentityStore(settings)
    queues = ReviewQueues(settings)
    cases = CaseImports(settings)
    registry: list[tuple[str, str, str]] = []

    def endpoint(
        method: str, path: str, capability: str, **kwargs: Any
    ) -> Callable[[Endpoint], Endpoint]:
        registry.append((method.upper(), "/api" + path, capability))
        return router.api_route(path, methods=[method.upper()], **kwargs)

    @endpoint("GET", "/session", "identity.self")
    def session(request: Request) -> dict[str, Any]:
        who = principal(request)
        return {
            **who.public(),
            "team_enabled": identities.enabled(),
            "capabilities": [
                key
                for key in (
                    "workspace.read",
                    "workspace.write",
                    "summary.read",
                    "reviews.assigned",
                    "team.admin",
                    "identity.self",
                )
                if who.permits(key)
            ],
        }

    @endpoint("GET", "/team/users", "team.admin")
    def users() -> list[dict[str, Any]]:
        return identities.users()

    @endpoint("GET", "/team/reviewers", "workspace.read")
    def reviewers() -> list[dict[str, Any]]:
        return [
            user
            for user in identities.users()
            if user["active"] and user["role"] in {"reviewer", "operator", "admin"}
        ]

    @endpoint("POST", "/team/users", "team.admin", status_code=201)
    def create_user(body: UserRequest, request: Request) -> dict[str, Any]:
        return identities.create_user(body.name, body.role, principal(request).id)

    @endpoint("PATCH", "/team/users/{user_id}", "team.admin")
    def update_user(user_id: str, body: UserState, request: Request) -> dict[str, Any]:
        return identities.set_active(user_id, body.active, principal(request).id)

    @endpoint("GET", "/team/tokens", "team.admin")
    def tokens() -> list[dict[str, Any]]:
        return identities.tokens()

    @endpoint("POST", "/team/users/{user_id}/tokens", "team.admin", status_code=201)
    def issue_token(user_id: str, body: TokenRequest, request: Request) -> dict[str, Any]:
        return identities.issue_token(user_id, body.label, body.days, actor=principal(request).id)

    @endpoint("POST", "/team/tokens/{token_id}/revoke", "team.admin", status_code=204)
    def revoke(token_id: str, request: Request) -> Response:
        identities.revoke(token_id, principal(request).id)
        return Response(status_code=204)

    @endpoint("GET", "/team/summary", "summary.read")
    def summary() -> dict[str, Any]:
        store = Store(settings)
        overview = store.overview()
        return {
            "overview": {
                key: overview.get(key)
                for key in (
                    "collections",
                    "scenarios",
                    "experiments",
                    "completed_experiments",
                    "pass_rate",
                    "total_cost_usd",
                )
            },
            "review_queues": queues.queues(),
        }

    @endpoint("GET", "/review-rubrics", "workspace.read")
    def rubrics() -> list[dict[str, Any]]:
        return queues.rubrics()

    @endpoint("POST", "/review-rubrics", "workspace.write", status_code=201)
    def create_rubric(body: Rubric, request: Request) -> dict[str, Any]:
        return queues.create_rubric(body, principal(request).id)

    @endpoint("GET", "/review-queues", "summary.read")
    def list_queues() -> list[dict[str, Any]]:
        return queues.queues()

    @endpoint("POST", "/review-queues", "workspace.write", status_code=201)
    def create_queue(body: QueueRequest, request: Request) -> dict[str, Any]:
        return queues.create(body, principal(request).id)

    @endpoint("POST", "/review-queues/{queue_id}/assignments", "workspace.write", status_code=204)
    def assign(queue_id: str, body: Assignments, request: Request) -> Response:
        queues.assign(queue_id, body.reviewer_ids, principal(request).id)
        return Response(status_code=204)

    @endpoint("GET", "/review-inbox", "reviews.assigned")
    def inbox(request: Request) -> list[dict[str, Any]]:
        return queues.assigned(principal(request))

    @endpoint("GET", "/review-items/{item_id}", "reviews.assigned")
    def item(item_id: str, request: Request) -> dict[str, Any]:
        return queues.item(item_id, principal(request))

    @endpoint("POST", "/review-items/{item_id}/judgments", "reviews.assigned", status_code=201)
    def judge(item_id: str, body: Judgment, request: Request) -> dict[str, Any]:
        return queues.judge(item_id, principal(request), body)

    @endpoint("GET", "/review-queues/{queue_id}/brief", "summary.read")
    def brief(queue_id: str) -> dict[str, Any]:
        return queues.brief(queue_id)

    @endpoint("POST", "/review-queues/{queue_id}/decisions", "workspace.write", status_code=201)
    def decide(queue_id: str, body: DecisionRequest, request: Request) -> dict[str, Any]:
        return queues.decide(
            queue_id, body.outcome, body.rationale, principal(request).id, body.evidence_hash
        )

    @endpoint("GET", "/case-imports", "workspace.read")
    def case_imports() -> list[dict[str, Any]]:
        return cases.imports()

    @endpoint("GET", "/case-imports/{import_id}", "workspace.read")
    def case_import(import_id: str) -> dict[str, Any]:
        return cases.get(import_id)

    @endpoint("POST", "/case-imports", "workspace.write", status_code=201)
    async def stage(request: Request) -> dict[str, Any]:
        async with request.form(
            max_files=1, max_fields=2, max_part_size=settings.max_request_bytes
        ) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(422, "Choose a CSV, JSONL, or JSON file")
            raw = await file.read(settings.max_request_bytes + 1)
            if len(raw) > settings.max_request_bytes:
                raise HTTPException(413, "File exceeds the upload limit")
            try:
                fields = (
                    json.loads(str(form["redacted_fields"]))
                    if form.get("redacted_fields")
                    else None
                )
                literals = (
                    json.loads(str(form["redacted_literals"]))
                    if form.get("redacted_literals")
                    else None
                )
            except json.JSONDecodeError as exc:
                raise ValueError("Redaction settings must be JSON arrays") from exc
            if (fields is not None and not isinstance(fields, list)) or (
                literals is not None and not isinstance(literals, list)
            ):
                raise ValueError("Redaction settings must be arrays")
            return await run_in_threadpool(
                cases.stage,
                raw,
                file.filename or "",
                principal(request).id,
                redacted_fields=fields,
                redacted_literals=literals,
            )

    @endpoint("POST", "/production-cases/{case_id}/approve", "workspace.write", status_code=201)
    def approve(case_id: str, body: Approval, request: Request) -> dict[str, Any]:
        return cases.approve(case_id, body, principal(request).id)

    @endpoint("POST", "/case-imports/{import_id}/publish", "workspace.write", status_code=201)
    def publish(import_id: str, body: PublishCases, request: Request) -> dict[str, Any]:
        return cases.publish(import_id, body.case_ids, body.name, principal(request).id)

    app.include_router(router)
    for method, path, capability in registry:
        register_capability(app, method, path, capability)
