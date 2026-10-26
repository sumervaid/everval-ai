"""Keyboard and recovery checks for the conversational benchmark interface."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from textual.widgets import Button, RichLog, Select, Static

from cvals.config import Settings
from cvals.tui.chat import ChatApp, ChatInput, ChatScreen, cadence, plan_text, safe_text
from cvals.tui.dialogs import EvidenceDialog


class Conversations:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.error = False

    def catalog(self) -> dict[str, Any]:
        return {
            "collections": [{"id": "tickets", "name": "Support tickets"}],
            "systems": [{"id": "a", "name": "Model A"}, {"id": "b", "name": "Model B"}],
        }

    def create(self) -> dict[str, Any]:
        identity = f"conversation-{len(self.saved) + 1}"
        self.saved[identity] = {
            "id": identity,
            "status": "draft",
            "messages": [],
            "ready": False,
            "draft": {"name": "Support quality", "system_ids": [], "report_formats": []},
            "reports": [],
        }
        return self.get(identity)

    def get(self, identity: str) -> dict[str, Any]:
        return deepcopy(self.saved[identity])

    def list(self) -> list[dict[str, Any]]:
        return [self.get(key) for key in reversed(self.saved)]

    def send(self, identity: str, message: str) -> dict[str, Any]:
        if self.error:
            raise ValueError("The task collection is no longer available.")
        self.calls.append(("send", message))
        record = self.saved[identity]
        record["messages"].extend(
            [
                {"role": "user", "content": message},
                {
                    "role": "assistant",
                    "content": "Ready. Two models, every 2 weeks, with HTML and CSV.",
                },
            ]
        )
        record["ready"] = True
        record["draft"].update(
            collection_id="tickets",
            system_ids=["a", "b"],
            interval_minutes=20160,
            report_formats=["html", "csv"],
            budget_usd=2,
            lifetime_budget_usd=20,
        )
        return self.get(identity)

    def activate(self, identity: str) -> dict[str, Any]:
        self.calls.append(("activate", identity))
        self.saved[identity].update(status="active", schedule_id="schedule-1")
        return self.get(identity)

    def pause(self, identity: str) -> dict[str, Any]:
        self.calls.append(("pause", identity))
        self.saved[identity]["status"] = "paused"
        return self.get(identity)

    def resume(self, identity: str) -> dict[str, Any]:
        self.calls.append(("resume", identity))
        self.saved[identity]["status"] = "active"
        return self.get(identity)


async def settle(app: Any, pilot: Any) -> None:
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()


def test_chat_completes_plan_by_keyboard_and_can_pause_without_losing_it(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Conversations()
        app = ChatApp(Settings(tmp_path / "workspace"), service=service)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            assert isinstance(screen, ChatScreen)
            assert screen.query_one("#chat-start", Button).disabled
            assert not screen.query_one("#chat-plan-pane").display
            editor = screen.query_one("#chat-input", ChatInput)
            assert app.focused is editor
            editor.load_text("Compare Model A and Model B every 2 weeks.")
            await pilot.press("enter")
            await settle(app, pilot)
            assert service.calls == [("send", "Compare Model A and Model B every 2 weeks.")]
            assert editor.text == ""
            assert not screen.query_one("#chat-start", Button).disabled
            assert "Every 2 weeks" in plan_text(screen.session, service.catalog())
            await pilot.click("#chat-start")
            await settle(app, pilot)
            assert screen.session["status"] == "active"
            assert screen.query_one("#chat-start", Button).disabled
            assert "SCHEDULED" in str(screen.query_one("#chat-state", Static).render())
            await pilot.click("#chat-toggle")
            await settle(app, pilot)
            assert screen.session["status"] == "paused"
            assert str(screen.query_one("#chat-toggle", Button).label) == "Resume"
            await pilot.click("#chat-toggle")
            await settle(app, pilot)
            assert screen.session["status"] == "active"
            assert [name for name, _ in service.calls] == ["send", "activate", "pause", "resume"]
            await pilot.click("#chat-details")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "Support tickets" in app.screen.text
            assert app.screen.query_one("#evidence-log", RichLog).scroll_y == 0
            assert "$2 per run · $20 total" in app.screen.text

    asyncio.run(exercise())


def test_failed_message_preserves_input_and_refresh_does_not_resend(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Conversations()
        app = ChatApp(Settings(tmp_path / "workspace"), service=service)
        async with app.run_test(size=(110, 35)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            editor = screen.query_one("#chat-input", ChatInput)
            editor.load_text("My original request")
            service.error = True
            await pilot.press("enter")
            await settle(app, pilot)
            assert editor.text == "My original request"
            assert screen.query_one("#chat-error").display
            assert "no longer available" in str(screen.query_one("#chat-error", Static).render())
            assert not service.calls
            service.error = False
            await pilot.press("ctrl+r")
            await settle(app, pilot)
            assert editor.text == "My original request"
            assert not service.calls
            editor.load_text("x" * 8001)
            await pilot.press("enter")
            await settle(app, pilot)
            assert not service.calls
            assert "8,000" in str(screen.query_one("#chat-error", Static).render())

    asyncio.run(exercise())


def test_saved_conversation_switching_and_reports_remain_local(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Conversations()
        first = service.create()
        service.send(first["id"], "First benchmark")
        service.activate(first["id"])
        receipt = {
            "format": "html",
            "path": str(tmp_path / "report.html"),
            "created_at": "2026-10-26",
        }
        service.saved[first["id"]]["reports"] = [receipt]
        app = ChatApp(Settings(tmp_path / "workspace"), service=service, session_id=first["id"])
        async with app.run_test(size=(130, 42)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            assert screen.session_id == first["id"]
            assert screen.query_one("#chat-plan-pane").display
            await pilot.click("#chat-results")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert receipt["path"] in app.screen.text
            await pilot.press("escape")
            await pilot.click("#chat-new")
            await settle(app, pilot)
            assert screen.session_id == "conversation-2"
            screen.query_one("#chat-sessions", Select).value = first["id"]
            await settle(app, pilot)
            assert screen.session_id == first["id"]
            assert screen.session["messages"][0]["content"] == "First benchmark"
            assert [name for name, _ in service.calls] == ["send", "activate"]

    asyncio.run(exercise())


def test_shift_enter_inserts_newline_without_sending(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Conversations()
        app = ChatApp(Settings(tmp_path / "workspace"), service=service)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            editor = app.screen.query_one("#chat-input", ChatInput)
            await pilot.press("a", "shift+enter", "b")
            assert editor.text == "a\nb"
            assert not service.calls

    asyncio.run(exercise())


def test_text_keeps_paragraphs_without_terminal_control_or_markup_interpretation() -> None:
    assert safe_text("first\n[red]second\x1b[2J") == "first\n[red]second[2J"
    assert cadence(None) == "Not set yet"
    assert cadence(20160) == "Every 2 weeks"
    assert cadence(1440) == "Every 1 day"
    assert cadence(45) == "Every 45 minutes"


def test_workbench_chat_opens_and_closes_at_100_columns(tmp_path: Path) -> None:
    from cvals.tui.app import WorkbenchApp
    from cvals.workbench import Workbench

    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        app = WorkbenchApp(settings, workbench=Workbench(settings))
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+j")
            await settle(app, pilot)
            assert isinstance(app.screen, ChatScreen)
            assert app.screen.session_id
            await pilot.click("#chat-catalog")
            await settle(app, pilot)
            assert "Systems:" in app.screen.session["messages"][-1]["content"]
            assert "Collections:" in app.screen.session["messages"][-1]["content"]
            assert app.screen.query_one("#chat-input", ChatInput).region.bottom <= 35
            assert any(
                command.title == "Benchmark chat" for command in app.get_system_commands(app.screen)
            )
            await pilot.press("escape")
            await settle(app, pilot)
            assert not isinstance(app.screen, ChatScreen)
            assert app.page == "overview"

    asyncio.run(exercise())
