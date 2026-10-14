"""Keyboard workflows and adapter boundaries for the terminal workbench."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import DataTable, Input, SelectionList, Static, TextArea

from cvals.config import Settings
from cvals.tui import WorkbenchApp
from cvals.tui.dialogs import EvidenceDialog, FormDialog
from cvals.tui.presentation import money, percent, plain, record_text, system_label


class WorkspaceDouble:
    """Explicit test double; the application never loads fixture UI data."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.project_rows: list[dict[str, Any]] = []
        self.connect_started = threading.Event()
        self.connect_allowed = threading.Event()
        self.connect_allowed.set()
        self.raise_projects = False

    def overview(self) -> dict[str, Any]:
        return {
            "projects": len(self.project_rows),
            "candidates": 0,
            "releases": 0,
            "experiments": 0,
            "recent_projects": self.project_rows,
        }

    def projects(self) -> list[dict[str, Any]]:
        if self.raise_projects:
            raise ValueError("Repository is unavailable")
        return self.project_rows

    def connect(self, source: str) -> dict[str, Any]:
        self.calls.append(("connect", source))
        self.connect_started.set()
        assert self.connect_allowed.wait(timeout=5)
        record = {"id": "project-1", "name": "Reviewed repository", "source": source}
        self.project_rows.append(record)
        return record

    def candidates(self, project_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "candidate-1",
                "title": "A historical repair",
                "status": "draft",
                "validation_status": "not run",
                "reason": "Review required",
            }
        ]

    def review_candidate(self, project_id: str, candidate_id: str, **values: Any) -> dict[str, Any]:
        self.calls.append(("review_candidate", project_id, candidate_id, values))
        return values

    def releases(self) -> list[dict[str, Any]]:
        return []

    def systems(self) -> list[dict[str, Any]]:
        return [
            {"id": "fixture-reference", "name": "Reference fixture · control", "kind": "reference"},
            {"id": "imported", "name": "Recorded", "kind": "replay"},
        ]

    def collections(self) -> list[dict[str, Any]]:
        return [{"id": "collection-1", "name": "Explicit test collection", "scenarios": []}]

    def experiments(self) -> list[dict[str, Any]]:
        return []

    def run(self, **values: Any) -> dict[str, Any]:
        self.calls.append(("run", values))
        return {"id": "experiment-1", "status": "queued"}

    def schedules(self) -> list[dict[str, Any]]:
        return []

    def audit(self) -> list[dict[str, Any]]:
        return []

    def diagnostics(self) -> dict[str, Any]:
        return {"storage": "available", "provider_keys": "environment references only"}


async def settle(app: WorkbenchApp, pilot: Any) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_terminal_text_and_missing_values_preserve_semantics() -> None:
    assert money(None) == "unknown"
    assert money(0) == "$0.0000"
    assert percent(None) == "—"
    assert percent(0) == "0.0%"
    assert "\x1b" not in plain("repo\x1b[2J\u202etitle")
    assert "\u202e" not in plain("repo\u202etitle")
    assert "\x1b" not in record_text({"output": "\x1b]52;clipboard"})
    assert "fixture" not in system_label({"name": "Recorded", "kind": "replay"})
    assert "fixture control" in system_label({"name": "Reference", "kind": "reference"})


def test_empty_workspace_keyboard_navigation_at_small_terminal(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path), workbench=WorkspaceDouble())
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            assert app.page == "overview"
            assert not app.query_one("#rail").display
            assert app.query_one("#ledger", DataTable).row_count == 0
            await pilot.press("f2")
            await settle(app, pilot)
            assert app.page == "projects"
            assert "No repositories connected" in str(app.query_one("#empty", Static).render())
            await pilot.press("f5")
            await settle(app, pilot)
            assert app.query_one("#ledger", DataTable).row_count == 2
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await pilot.press("escape", "f8")
            await settle(app, pilot)
            assert app.page == "diagnostics"
            assert app.query_one("#ledger", DataTable).row_count == 2

    asyncio.run(exercise())


def test_connection_runs_in_background_and_keeps_navigation_available(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = WorkspaceDouble()
        service.connect_allowed.clear()
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-source", Input).value = "/authorized/repository"
            await pilot.click("#form-submit")
            await pilot.pause()
            assert service.connect_started.is_set()
            await pilot.press("f5")
            await pilot.pause()
            assert app.page == "systems"
            assert app._mutating
            service.connect_allowed.set()
            await settle(app, pilot)
            assert service.calls == [("connect", "/authorized/repository")]
            assert app.page == "projects"
            assert app.query_one("#ledger", DataTable).row_count == 1

    asyncio.run(exercise())


def test_candidate_review_keeps_statement_and_rationale_separate(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = WorkspaceDouble()
        service.project_rows = [{"id": "project-1", "name": "Repository", "source": "/repo"}]
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(120, 42)) as pilot:
            await settle(app, pilot)
            await pilot.press("f2")
            await settle(app, pilot)
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await settle(app, pilot)
            assert app.page == "candidates"
            app.command("review_candidate")
            await pilot.pause()
            app.screen.query_one(
                "#field-statement", TextArea
            ).text = "Handle empty input without losing valid items."
            app.screen.query_one("#field-reviewer", Input).value = "Maintainer"
            app.screen.query_one(
                "#field-rationale", TextArea
            ).text = "The pre-repair issue documents this requirement."
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert service.calls[0] == (
                "review_candidate",
                "project-1",
                "candidate-1",
                {
                    "statement": "Handle empty input without losing valid items.",
                    "reviewer": "Maintainer",
                    "rationale": "The pre-repair issue documents this requirement.",
                },
            )

    asyncio.run(exercise())


def test_new_experiment_requires_system_selection_and_preserves_zero_budget(tmp_path: Path) -> None:
    async def exercise() -> None:
        from textual.widgets import Select

        service = WorkspaceDouble()
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(120, 48)) as pilot:
            await settle(app, pilot)
            app.new_run()
            await settle(app, pilot)
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-name", Input).value = "Control verification"
            app.screen.query_one("#field-collection_id", Select).value = "collection-1"
            await pilot.click("#form-submit")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            assert not service.calls
            selection = app.screen.query_one("#field-system_ids", SelectionList)
            assert selection.option_count == 1  # Replays cannot be new independent executions.
            selection.select("fixture-reference")
            await pilot.pause(0.3)
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert service.calls == [
                (
                    "run",
                    {
                        "name": "Control verification",
                        "collection_id": "collection-1",
                        "system_ids": ["fixture-reference"],
                        "repetitions": 1,
                        "budget": 0.0,
                    },
                )
            ]

    asyncio.run(exercise())


def test_backend_failure_is_visible_and_navigation_recovers(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = WorkspaceDouble()
        service.raise_projects = True
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(app, pilot)
            await pilot.press("f2")
            await settle(app, pilot)
            assert app.query_one("#notice").display
            assert "Repository is unavailable" in str(app.query_one("#notice", Static).render())
            await pilot.press("f5")
            await settle(app, pilot)
            assert app.query_one("#ledger", DataTable).row_count == 2
            assert not app.query_one("#notice").display

    asyncio.run(exercise())


def test_real_workspace_trial_evidence_is_the_persisted_record(tmp_path: Path) -> None:
    from cvals.engine import Engine
    from cvals.models import Check, Collection, ExperimentCreate, Scenario
    from cvals.store import Store
    from cvals.workbench import Workbench

    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="ledger",
            name="Ledger controls",
            domain="data",
            scenarios=[
                Scenario(
                    id="sum",
                    name="Recorded sum",
                    domain="data",
                    instruction="Sum the two values.",
                    inputs={"values": [2, 3]},
                    reference_output={"total": 5},
                    checks=[
                        Check(
                            id="total",
                            name="Correct total",
                            kind="numeric",
                            path="total",
                            expected=5,
                        )
                    ],
                )
            ],
        )
    )
    created = store.create_experiment(
        ExperimentCreate(
            name="Terminal evidence check",
            collection_id="ledger",
            system_ids=["fixture-reference", "fixture-incomplete"],
        )
    )
    Engine(store, settings).run_experiment(created["id"])

    async def exercise() -> None:
        app = WorkbenchApp(settings, workbench=Workbench(settings))
        async with app.run_test(size=(160, 50)) as pilot:
            await settle(app, pilot)
            await pilot.press("f4")
            await settle(app, pilot)
            assert app.rows[0]["id"] == created["id"]
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await settle(app, pilot)
            assert app.page == "trials"
            assert len(app.rows) == 2
            assert {trial["passed"] for trial in app.rows} == {True, False}
            app.command("record")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert '"checks"' in app.screen.text
            assert '"cost_usd": 0' in app.screen.text

    asyncio.run(exercise())


def test_environment_download_waits_for_review_and_pins_the_plan(tmp_path: Path) -> None:
    from cvals.tui.dialogs import PlanDialog

    class PreparationWorkspace(WorkspaceDouble):
        def environment_plan(self, project_id: str, packages: Any = None) -> dict[str, Any]:
            self.calls.append(("plan", project_id, packages))
            return {
                "source_commit": "a" * 40,
                "base_image_digest": "sha256:" + "b" * 64,
                "requirements": ["pytest==8.0"],
                "notices": [],
            }

        def prepare_environment(self, project_id: str, **values: Any) -> dict[str, Any]:
            self.calls.append(("prepare", project_id, values))
            return {"status": "prepared"}

    async def exercise() -> None:
        service = PreparationWorkspace()
        service.project_rows = [{"id": "project-1", "name": "Repository", "source": "/repo"}]
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(120, 45)) as pilot:
            await settle(app, pilot)
            await pilot.press("f2")
            await settle(app, pilot)
            app.command("prepare")
            await settle(app, pilot)
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert isinstance(app.screen, PlanDialog)
            assert service.calls == [("plan", "project-1", None)]
            await pilot.click("#plan-prepare")
            await settle(app, pilot)
            assert service.calls[-1] == (
                "prepare",
                "project-1",
                {"packages": None, "revision": "a" * 40, "base_image": "sha256:" + "b" * 64},
            )

    asyncio.run(exercise())


def test_pagination_resumes_saved_cursor_and_disables_after_last_page(tmp_path: Path) -> None:
    class PagedWorkspace(WorkspaceDouble):
        def discover(
            self, project_id: str, limit: int = 20, cursor: str | None = None
        ) -> dict[str, Any]:
            self.calls.append(("discover", project_id, limit, cursor))
            self.project_rows[0]["last_discovery"]["next_cursor"] = None
            return {"coverage": {"next_cursor": None}}

    async def exercise() -> None:
        service = PagedWorkspace()
        service.project_rows = [
            {
                "id": "project-1",
                "name": "Repository",
                "source": "/repo",
                "last_discovery": {"next_cursor": "saved-cursor"},
            }
        ]
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(120, 45)) as pilot:
            await settle(app, pilot)
            await pilot.press("f2")
            await settle(app, pilot)
            await pilot.press("enter")
            await settle(app, pilot)
            assert app.page == "candidates"
            assert not app.query_one("#tool-older").disabled
            app.command("older")
            await settle(app, pilot)
            assert service.calls == [("discover", "project-1", 20, "saved-cursor")]
            assert app.query_one("#tool-older").disabled

    asyncio.run(exercise())


def test_long_forms_keep_primary_actions_visible_at_80_by_24(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path), workbench=WorkspaceDouble())
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            app.new_system()
            await pilot.pause()
            submit = app.screen.query_one("#form-submit")
            assert app.screen.region.contains_region(submit.region)
            assert app.screen.query_one("#form-fields").scrollable_content_region.height > 0
            await pilot.press("escape")
            app.page = "experiments"
            app.rows = [{"id": "record", "name": "Test"}]
            app.selected = app.rows[0]
            app.command_gates()
            await pilot.pause()
            submit = app.screen.query_one("#form-submit")
            assert app.screen.region.contains_region(submit.region)
            # The last threshold remains reachable by keyboard scrolling.
            app.screen.query_one("#field-max_pass_rate_regression").focus()
            await pilot.pause(0.4)
            field = app.screen.query_one("#field-max_pass_rate_regression")
            assert app.screen.region.contains_region(field.region)

    asyncio.run(exercise())


@pytest.mark.parametrize("backend_fails", [False, True])
def test_shutdown_detaches_pending_operations_and_periodic_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_fails: bool
) -> None:
    async def exercise() -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        errors: list[BaseException] = []
        delivered: list[Any] = []
        app = WorkbenchApp(Settings(tmp_path), workbench=WorkspaceDouble())

        def operation() -> str:
            started.set()
            assert release.wait(timeout=5)
            if backend_fails:
                raise ValueError("Backend finished after the terminal closed")
            return "Persisted by backend"

        try:
            async with app.run_test(size=(80, 24)) as pilot:
                await settle(app, pilot)
                original_run_worker = app.run_worker

                def observe_worker(work: Any, **kwargs: Any) -> Any:
                    # Observe the actual thread through its final UI dispatch,
                    # even after Textual cancels its asyncio worker wrapper.
                    def observed() -> None:
                        try:
                            work()
                        except BaseException as error:
                            errors.append(error)
                            raise
                        finally:
                            finished.set()

                    return original_run_worker(observed, **kwargs)

                monkeypatch.setattr(app, "run_worker", observe_worker)
                app.request("Pending operation", operation, delivered.append, mutation=True)
                assert await asyncio.to_thread(started.wait, 2)
                app.new_system()
                await pilot.pause()
                assert isinstance(app.screen, FormDialog)
                assert app._pending
                assert len(app._refresh_timers) == 2
                # Exiting while the modal and operation are active reproduces
                # screen teardown without cancelling the durable backend work.
            assert not app.is_running
            assert not app._refresh_timers
            assert not app._pending
            # Timer messages already queued before stop must be harmless too.
            app._status()
            app._poll()
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        assert not errors
        assert not delivered
        assert not app._mutating

    asyncio.run(exercise())
