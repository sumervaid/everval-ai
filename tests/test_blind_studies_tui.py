"""Terminal study coordination preserves concealed identities until locked reveal."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from textual.widgets import Button, RichLog, Static, TextArea

from cvals.config import Settings
from cvals.tui import WorkbenchApp
from cvals.tui.blind_studies import BlindStudyApp, BlindStudyScreen, study_text
from cvals.tui.dialogs import EvidenceDialog, FormDialog


class Studies:
    def __init__(self, missing: int = 0) -> None:
        self.row: dict[str, Any] = {
            "id": "study-1",
            "name": "Blinded study",
            "queue_id": "queue-1",
            "status": "collecting",
            "assigned_reviews": 2,
            "completed_reviews": 2 - missing,
            "missing_reviews": missing,
            "evidence_hash": "a" * 64,
        }
        self.calls: list[tuple[Any, ...]] = []

    def list(self) -> list[dict[str, Any]]:
        return [deepcopy(self.row)]

    def get(self, identifier: str) -> dict[str, Any]:
        assert identifier == self.row["id"]
        return deepcopy(self.row)

    def lock(self, identifier: str, *, actor: str, expected_hash: str) -> dict[str, Any]:
        if expected_hash != self.row["evidence_hash"]:
            raise ValueError("Study evidence changed; refresh before locking.")
        assert not self.row["missing_reviews"]
        self.calls.append(("lock", identifier, actor, expected_hash))
        self.row.update(status="locked", evidence_hash="b" * 64)
        return deepcopy(self.row)

    def reveal(
        self, identifier: str, *, actor: str, expected_hash: str, rationale: str
    ) -> dict[str, Any]:
        assert self.row["status"] == "locked"
        assert expected_hash == self.row["evidence_hash"]
        assert rationale
        self.calls.append(("reveal", identifier, actor, expected_hash, rationale))
        self.row.update(status="revealed", name="Support comparison", evidence_hash="c" * 64)
        return deepcopy(self.row)

    def report(self, identifier: str) -> dict[str, Any]:
        assert self.row["status"] == "revealed"
        self.calls.append(("report", identifier))
        return {"status": "revealed", "systems": [{"name": "A released model"}]}


async def settle(app: Any, pilot: Any) -> None:
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()


def test_study_flow_requires_separate_lock_and_reveal_actions(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Studies()
        app = BlindStudyApp(Settings(tmp_path), service=service)
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            assert isinstance(screen, BlindStudyScreen)
            assert screen.query_one("#blind-reveal", Button).disabled
            assert screen.query_one("#blind-report", Button).disabled
            assert not screen.query_one("#blind-lock", Button).disabled
            assert "A released model" not in study_text(screen.study)
            await pilot.click("#blind-lock")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            assert "permanently closes" in app.screen.description
            assert not service.calls
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert screen.study["status"] == "locked"
            assert screen.query_one("#blind-lock", Button).disabled
            assert not screen.query_one("#blind-reveal", Button).disabled
            assert screen.query_one("#blind-report", Button).disabled
            await pilot.click("#blind-reveal")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-rationale", TextArea).load_text(
                "Independent reviews are complete."
            )
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert screen.study["status"] == "revealed"
            assert not screen.query_one("#blind-report", Button).disabled
            await pilot.click("#blind-report")
            await settle(app, pilot)
            assert isinstance(app.screen, EvidenceDialog)
            assert "A released model" in app.screen.text
            assert [entry[0] for entry in service.calls] == ["lock", "reveal", "report"]

    asyncio.run(exercise())


def test_incomplete_reviews_cannot_be_locked_at_small_terminal_size(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Studies(missing=1)
        app = BlindStudyApp(Settings(tmp_path), service=service)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            assert screen.query_one("#blind-lock", Button).disabled
            assert screen.query_one("#blind-reveal", Button).disabled
            assert "1 remaining" in study_text(screen.study)
            assert screen.query_one("#blind-evidence", RichLog).size.height >= 5
            service.row.update(missing_reviews=0, completed_reviews=2)
            await pilot.press("ctrl+r")
            await settle(app, pilot)
            assert not screen.query_one("#blind-lock", Button).disabled
            assert not service.calls

    asyncio.run(exercise())


def test_stale_lock_is_reported_without_revealing_or_erasing_study(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Studies()
        app = BlindStudyApp(Settings(tmp_path), service=service)
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(app, pilot)
            screen = app.screen
            await pilot.click("#blind-lock")
            await pilot.pause()
            service.row["evidence_hash"] = "d" * 64
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert screen.study["status"] == "collecting"
            assert "evidence changed" in str(screen.query_one("#blind-notice", Static).render())
            assert not service.calls
            assert screen.query_one("#blind-reveal", Button).disabled

    asyncio.run(exercise())


def test_new_study_explains_missing_independent_reviewers(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = BlindStudyApp(Settings(tmp_path), service=Studies())
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            await pilot.click("#blind-new")
            await settle(app, pilot)
            assert "reviewer-only identities" in str(
                app.screen.query_one("#blind-notice", Static).render()
            )

    asyncio.run(exercise())


def test_workbench_shortcut_opens_and_returns_without_a_worker(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+y")
            await settle(app, pilot)
            assert isinstance(app.screen, BlindStudyScreen)
            assert app.screen.study is None
            await pilot.press("ctrl+y")
            await settle(app, pilot)
            assert len(app.screen_stack) == 1
            assert app.page == "overview"

    asyncio.run(exercise())
