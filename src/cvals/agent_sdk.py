"""A public-task protocol for adapting an existing agent or service."""

from __future__ import annotations

import hmac
import inspect
import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from cvals.models import Manifest, canonical_json


class PublicTask(Manifest):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    domain: Literal["coding", "documents", "data", "workflow"]
    instruction: str = Field(min_length=1, max_length=100000)
    inputs: dict[str, Any] = Field(default_factory=dict)


class AgentLimits(Manifest):
    input_tokens: int = Field(ge=1, le=100000000)
    output_tokens: int = Field(ge=1, le=100000000)
    timeout_seconds: float = Field(gt=0, le=120)


class AgentRequest(Manifest):
    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    task: PublicTask
    limits: AgentLimits


class AgentUsage(Manifest):
    prompt_tokens: int = Field(strict=True, ge=0, le=1000000000)
    completion_tokens: int = Field(strict=True, ge=0, le=1000000000)


class AgentResult(Manifest):
    protocol_version: Literal[1] = 1
    revision: str = Field(min_length=1, max_length=200)
    output: dict[str, Any] | None = None
    usage: AgentUsage | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    error: str | None = Field(default=None, max_length=2000)


Handler = Callable[[AgentRequest], AgentResult | Awaitable[AgentResult]]


def create_agent_app(handler: Handler, *, revision: str, token_env: str | None = None) -> FastAPI:
    """Wrap trusted adapter code; repository execution remains the adapter's responsibility.

    The handler must honor the supplied ceilings and isolate any task code it executes.
    Everval never sends checks, reference outputs, or private provenance to this service.
    """
    if not revision.strip() or len(revision) > 200:
        raise ValueError("A bounded adapter revision is required")
    token = os.environ.get(token_env, "") if token_env else None
    if token_env and not token:
        raise ValueError("The configured adapter token environment variable is unset")
    app = FastAPI(title="Everval agent adapter", docs_url=None, redoc_url=None)

    @app.get("/everval/v1/health")
    def health() -> dict[str, Any]:
        return {"protocol_version": 1, "revision": revision, "ready": True}

    @app.post("/everval/v1/tasks")
    async def execute(request: Request) -> dict[str, Any]:
        if token is not None and not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            raise HTTPException(401, "Adapter authentication required")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 128 * 1024:
                raise HTTPException(413, "Public task exceeds 128 KiB")
        try:
            task = AgentRequest.model_validate_json(raw)
        except ValueError as exc:
            raise HTTPException(422, "Invalid public task contract") from exc
        if task.revision != revision:
            raise HTTPException(409, "Adapter revision differs from the frozen system")
        # Starlette offloads synchronous handlers; async adapters retain their event loop.
        try:
            response = await run_in_threadpool(handler, task)
            if inspect.isawaitable(response):
                response = await response
            result = AgentResult.model_validate(response)
            if result.revision != revision:
                raise ValueError("Adapter result revision mismatch")
            value = result.model_dump(mode="json")
            if len(canonical_json(value).encode()) > 2 * 1024 * 1024:
                raise ValueError("Adapter result exceeds 2 MiB")
            return value
        except Exception as exc:
            raise HTTPException(502, "Adapter failed; inspect its private local logs") from exc

    return app
