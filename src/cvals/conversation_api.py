"""Role-scoped conversation setup and private local report downloads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import Field

from cvals.config import Settings
from cvals.identity import register_capability
from cvals.models import Manifest


class ConversationMessage(Manifest):
    message: str = Field(min_length=1, max_length=8000)


def register_conversation_routes(app: FastAPI, settings: Settings) -> None:
    from cvals.conversation import ConversationService
    from cvals.conversation_execution import ExecutionService

    service = ConversationService(settings, app.state.store)
    execution = ExecutionService(settings, app.state.store)
    router = APIRouter(prefix="/api/conversations")

    @router.get("/catalog")
    def catalog() -> dict[str, Any]:
        return service.catalog()

    @router.get("")
    def conversations() -> list[dict[str, Any]]:
        return service.list()

    @router.post("", status_code=201)
    def create() -> dict[str, Any]:
        return service.create()

    @router.get("/{conversation_id}")
    def conversation(conversation_id: str) -> dict[str, Any]:
        return service.get(conversation_id)

    @router.post("/{conversation_id}/messages")
    def message(conversation_id: str, body: ConversationMessage) -> dict[str, Any]:
        result = service.send(conversation_id, body.message)
        app.state.worker.wake_event.set()
        return result

    @router.get("/{conversation_id}/reports")
    def reports(conversation_id: str) -> list[dict[str, Any]]:
        current = service.get(conversation_id)
        if not current.get("experiment_id"):
            return []
        return execution.reports(conversation_id)

    @router.get("/{conversation_id}/reports/{report_id}")
    def report(conversation_id: str, report_id: str) -> FileResponse:
        service.get(conversation_id)
        path = execution.resolve_report(conversation_id, report_id)
        return FileResponse(path, filename=path.name, content_disposition_type="attachment")

    app.include_router(router)
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            register_capability(
                app,
                method,
                route.path,
                "workspace.write" if method == "POST" else "workspace.read",
            )
