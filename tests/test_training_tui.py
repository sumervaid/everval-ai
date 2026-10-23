"""Keyboard and missing-evidence behavior of the read-only checkpoint monitor."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Select, Static

from cvals.config import Settings
from cvals.tui import WorkbenchApp
from cvals.tui.dialogs import EvidenceDialog
from cvals.tui.training import checkpoint_details, evaluation_lag


def dashboard(identity: str = "run-a") -> dict[str, Any]:
    run = {
        "id": identity,
        "name": "Synthetic navigation control",
        "training_run_id": identity,
        "provenance_kind": "synthetic_control",
        "created_at": "2026-10-22T12:00:00Z",
        "checkpoint_count": 2,
        "status": "registered",
        "protocol": {"domains": [{"name": "Reasoning"}, {"name": "Safety"}]},
    }
    checkpoints = []
    for step in [100, 0]:
        checkpoints.append(
            {
                "id": f"{identity}-{step}",
                "step": step,
                "weight_sha256": "a" * 64,
                "system_id": "fixture-control",
                "system_hash": "b" * 64,
                "created_at": run["created_at"],
                "status": "partial" if step else "completed",
                "known_cost_usd": 0,
                "unknown_cost_count": 4 if step else 0,
                "lag_seconds": 75 if step else 0,
                "metrics": [
                    {
                        "domain": "Reasoning",
                        "experiment_id": "experiment-a",
                        "status": "running" if step else "completed",
                        "value": None if step else 0.0,
                        "lower": None if step else 0,
                        "upper": None if step else 0.5,
                        "interval_method": "task-family Hoeffding (descriptive)",
                        "n": 2 if step else 4,
                        "expected_n": 4,
                        "known_cost_usd": 0,
                        "cost_complete": not step,
                        "unknown_cost_count": 2 if step else 0,
                        "errors": 0,
                        "missing": 2 if step else 0,
                    }
                ],
            }
        )
    return {
        "schema_version": 1,
        "run": run,
        "checkpoints": checkpoints,
        "comparisons": [{"id": "decision-a", "stale": True, "decision": "inconclusive"}],
        "alerts": [],
        "investigations": [],
        "qualifications": [],
        "mix_studies": [],
        "budget": {"known_cost_usd": 0, "unknown_cost_count": 4},
    }


class Workspace:
    def overview(self) -> dict[str, Any]:
        return {"recent_projects": []}


class Training:
    def __init__(self, empty: bool = False):
        self.reports = {} if empty else {key: dashboard(key) for key in ["run-a", "run-b"]}
        self.calls: list[str] = []
        self.error = False

    def list_runs(self) -> list[dict[str, Any]]:
        self.calls.append("list")
        if self.error:
            raise ValueError("Checkpoint store is unavailable")
        return [copy.deepcopy(report["run"]) for report in self.reports.values()]

    def dashboard(self, identity: str) -> dict[str, Any]:
        self.calls.append(identity)
        return copy.deepcopy(self.reports[identity])


async def settle(app: WorkbenchApp, pilot: Any) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def make_app(tmp_path: Path, service: Training) -> WorkbenchApp:
    app = WorkbenchApp(Settings(tmp_path), workbench=Workspace())
    app._training_service = service
    return app


def test_training_keyboard_chronology_missingness_and_small_terminal_details(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        app = make_app(tmp_path, Training())
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+t")
            await settle(app, pilot)
            assert app.page == "training"
            assert any(
                command.title == "Training" for command in app.get_system_commands(app.screen)
            )
            table = app.query_one("#ledger", DataTable)
            assert table.row_count == 2 and len(table.columns) == 4
            assert "Step 0" in str(table.get_cell_at(Coordinate(0, 0)))
            assert "0.0%" in str(table.get_cell_at(Coordinate(0, 1)))
            assert "2/4 graded" in str(table.get_cell_at(Coordinate(1, 1)))
            assert "not scheduled" in str(table.get_cell_at(Coordinate(1, 2)))
            assert "≥ $0.0000" in str(table.get_cell_at(Coordinate(1, 3)))
            assert "SYNTHETIC CONTROL" in str(app.query_one("#training-summary", Static).render())
            assert not app.query_one("#detail-pane").display
            await pilot.press("up", "enter")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "step 0" in app.screen.text
            assert "not model performance" in app.screen.text
            assert "Reported interval: 0.0%–50.0%" in app.screen.text
            assert "STALE" in app.screen.text
            await pilot.press("escape", "down", "enter")
            await pilot.pause()
            assert "Graded: 2/4" in app.screen.text
            assert "Known spend: at least $0.0000" in app.screen.text
            await pilot.press("escape", "f1")
            await settle(app, pilot)
            assert app.page == "overview"
            assert not app.query_one("#training-run").display

    asyncio.run(exercise())


def test_run_selector_keyboard_refresh_keeps_checkpoint_and_errors_visible(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = Training()
        app = make_app(tmp_path, service)
        async with app.run_test(size=(140, 42)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+t")
            await settle(app, pilot)
            selector = app.query_one("#training-run", Select)
            await pilot.press("shift+tab")
            assert app.focused is selector
            await pilot.press("enter", "end", "enter")
            await settle(app, pilot)
            assert app.training_run_id == "run-b"
            await pilot.press("up")
            await pilot.press("ctrl+r")
            await settle(app, pilot)
            assert app.training_checkpoint_id == "run-b-0"
            assert app.training_run_id == "run-b"
            service.reports["run-b"]["budget"].update(
                admission_blocked=True, blocking_reasons=["Unsettled provider accounting."]
            )
            await pilot.press("ctrl+r")
            await settle(app, pilot)
            assert "Unsettled provider accounting" in str(app.query_one("#notice", Static).render())
            service.error = True
            await pilot.press("ctrl+r")
            await settle(app, pilot)
            assert "Checkpoint store is unavailable" in str(
                app.query_one("#notice", Static).render()
            )
            assert app.training_checkpoint_id == "run-b-0"
            assert app.query_one("#ledger", DataTable).row_count == 2

    asyncio.run(exercise())


def test_training_empty_workspace_has_no_invented_scores_or_enabled_evidence(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        app = make_app(tmp_path, Training(empty=True))
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+t")
            await settle(app, pilot)
            assert app.query_one("#ledger", DataTable).row_count == 0
            assert app.query_one("#training-run", Select).disabled
            assert app.query_one("#tool-training_details", Button).disabled
            assert app.query_one("#tool-training_protocol", Button).disabled
            assert "never creates demonstration scores" in str(
                app.query_one("#empty", Static).render()
            )

    asyncio.run(exercise())


def test_lag_unknown_zero_and_recorded_durations_are_distinct() -> None:
    assert evaluation_lag(None) == "unknown"
    assert evaluation_lag(0) == "0 s"
    assert evaluation_lag(75) == "1.2 min"
    assert evaluation_lag(7200) == "2.0 h"
    report = dashboard()
    text = checkpoint_details(report, report["checkpoints"][0])
    assert "It is not queue wait time." in text
    assert "Absence of an alert does not establish equivalence." in text
