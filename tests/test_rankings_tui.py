"""Navigate recorded model evidence without changing runs or calling providers."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Static

from cvals.config import Settings
from cvals.tui import WorkbenchApp
from cvals.tui.dialogs import EvidenceDialog
from cvals.tui.rankings import duration


def ranking_report() -> dict[str, Any]:
    systems = [
        {"id": "config-a", "name": "Atlas", "model": "same-model", "kind": "http"},
        {"id": "config-b", "name": "Atlas", "model": "same-model", "kind": "http"},
        {"id": "control", "name": "Original control", "kind": "reference", "fixture_control": True},
        {"id": "partial", "name": "Partial", "model": "another", "kind": "http"},
        {"id": "replay", "name": "Recorded", "kind": "replay", "submission_replay": True},
    ]
    benchmarks = [
        {
            "id": identity,
            "name": "Invoice tasks",
            "version": "1.0",
            "collection_id": "invoices",
            "collection_hash": identity,
            "contract_hash": "contract-" + identity,
            "benchmark_partition": "development",
            "repetitions": 1,
            "scenario_count": 4,
        }
        for identity in ("cohort-old", "cohort-new")
    ]

    def cell(system: str, benchmark: str = "cohort-old", **changes: Any) -> dict[str, Any]:
        return {
            "benchmark_id": benchmark,
            "system_id": system,
            "source_system_id": "source-" + system,
            "experiment_id": "run-" + system,
            "experiment_name": "Saved " + system,
            "created_at": "2026-10-16T10:00:00Z",
            "status": "completed",
            "pass_rate": 0.5,
            "passed": 2,
            "scheduled": 4,
            "completed": 4,
            "completion_rate": 1,
            "ci_low": 0.15,
            "ci_high": 0.85,
            "interval_method": "Wilson 95%",
            "cost_usd": 0.4,
            "known_cost_usd": 0.4,
            "cost_per_success_usd": 0.2,
            "cost_complete": True,
            "median_duration_ms": 900,
            "p95_duration_ms": 1200,
            "duration_complete": True,
            "duration_recorded": 4,
            "ranks": {"quality": 1, "cost": 1, "latency": 2},
            "eligibility": {"quality": True, "cost": True, "latency": True},
            "issues": [],
            **changes,
        }

    unranked = {"quality": None, "cost": None, "latency": None}
    cells = [
        cell("config-a"),
        cell(
            "config-b",
            ranks={"quality": 1, "cost": 2, "latency": 1},
            cost_usd=0.8,
            known_cost_usd=0.8,
            cost_per_success_usd=0.4,
            p95_duration_ms=800,
        ),
        cell("config-b", "cohort-new", ranks={"quality": 1, "cost": 1, "latency": 1}),
        cell(
            "control",
            passed=4,
            pass_rate=1,
            ranks=unranked,
            issues=["Fixture controls do not measure model performance."],
        ),
        cell(
            "partial",
            completed=3,
            completion_rate=0.75,
            cost_usd=None,
            cost_per_success_usd=None,
            known_cost_usd=0.02,
            cost_complete=False,
            duration_recorded=3,
            duration_complete=False,
            status="interrupted",
            ranks=unranked,
            issues=["One scheduled attempt has no graded result; cost remains incomplete."],
        ),
        cell("replay", ranks=unranked, issues=["Replayed evidence is not a new execution."]),
    ]
    return {
        "schema_version": 1,
        "benchmarks": benchmarks,
        "systems": systems,
        "cells": cells,
        "window": {"limit": 100, "scanned_runs": 100, "listed_runs": 103, "truncated": True},
        "counts": {"benchmarks": 2, "systems": 5, "cells": len(cells)},
        "warnings": ["Only the latest 100 experiments are scanned."],
    }


class RankingsWorkspace:
    def __init__(self, report: dict[str, Any] | None = None):
        self.report = ranking_report() if report is None else report
        self.calls: list[tuple[str, ...]] = []

    def overview(self) -> dict[str, Any]:
        return {"projects": 0, "recent_projects": []}

    def rankings(self) -> dict[str, Any]:
        self.calls.append(("rankings",))
        return copy.deepcopy(self.report)

    def experiments(self) -> list[dict[str, Any]]:
        return []

    def systems(self) -> list[dict[str, Any]]:
        return []

    def experiment(self, identifier: str) -> dict[str, Any]:
        self.calls.append(("experiment", identifier))
        return {
            "id": identifier,
            "name": identifier,
            "trials": [
                {"id": "other", "system_id": "other", "status": "completed", "passed": True},
                {
                    "id": "selected",
                    "system_id": "source-config-a",
                    "status": "completed",
                    "passed": True,
                    "scenario_name": "Saved task",
                    "cost_usd": 0.1,
                },
            ],
        }


async def settle(app: WorkbenchApp, pilot: Any) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_rankings_discoverability_matrix_ties_and_metric_switching(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = RankingsWorkspace()
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(160, 48)) as pilot:
            await settle(app, pilot)
            assert app.query_one("#tool-rankings", Button).display
            assert any(
                command.title == "Rankings" for command in app.get_system_commands(app.screen)
            )
            await pilot.click("#tool-rankings")
            await settle(app, pilot)
            assert app.page == "rankings"
            assert app.query_one("#nav-rankings", Button).has_class("active")
            table = app.query_one("#ledger", DataTable)
            assert table.fixed_columns == 1 and table.cursor_type == "cell"
            assert table.row_count == 5 and len(table.columns) == 3
            assert "#1 tie" in str(table.get_cell_at(Coordinate(0, 1)))
            assert "#1 tie" in str(table.get_cell_at(Coordinate(1, 1)))
            assert "unranked" in str(table.get_cell_at(Coordinate(2, 1)))
            assert "unranked" in str(table.get_cell_at(Coordinate(4, 1)))
            assert "not run" in str(table.get_cell_at(Coordinate(0, 2)))
            assert "older runs omitted" in str(app.query_one("#ranking-summary", Static).render())
            await pilot.press("2")
            await pilot.pause()
            assert app.ranking_metric == "cost"
            assert "$0.2000" in str(table.get_cell_at(Coordinate(0, 1)))
            assert "#1" in str(table.get_cell_at(Coordinate(0, 1)))
            assert "tie" not in str(table.get_cell_at(Coordinate(0, 1)))
            assert "≥$0.0200 known" in str(table.get_cell_at(Coordinate(3, 1)))
            await pilot.press("3")
            await pilot.pause()
            assert "1.20 s" in str(table.get_cell_at(Coordinate(0, 1)))
            assert "#2" in str(table.get_cell_at(Coordinate(0, 1)))
            assert "4/4 timed" in str(table.get_cell_at(Coordinate(0, 1)))
            assert service.calls == [("rankings",)]  # Presentation changes do not rescan runs.

    asyncio.run(exercise())


def test_small_terminal_details_missing_cells_and_exact_run_drilldown(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = RankingsWorkspace()
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+l")
            await settle(app, pilot)
            assert app.page == "rankings"
            assert not app.query_one("#detail-pane").display
            assert app.query_one("#tool-ranking_quality", Button).region.height > 0
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "Configuration: config-a" in app.screen.text
            assert "Grading coverage: 4/4" in app.screen.text
            assert "Run identity: run-config-a" in app.screen.text
            await pilot.press("escape", "right", "enter")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "Not run for this configuration" in app.screen.text
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#tool-ranking_run", Button).disabled
            await pilot.press("left", "o")
            await settle(app, pilot)
            assert app.page == "trials"
            assert app.experiment_id == "run-config-a"
            assert app.selected and app.selected["system_id"] == "source-config-a"
            assert len(app.rows) == 2  # Opening a run does not hide other scheduled trials.
            assert service.calls[-1] == ("experiment", "run-config-a")
            await pilot.press("f4")
            await settle(app, pilot)
            assert app.page == "experiments"
            assert app.query_one("#ledger", DataTable).cursor_type == "row"
            assert app.query_one("#tool-rankings", Button).display
            await pilot.press("f5")
            await settle(app, pilot)
            assert app.page == "systems"

    asyncio.run(exercise())


def test_rankings_refresh_preserves_selected_configuration_and_partial_evidence(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        service = RankingsWorkspace()
        app = WorkbenchApp(Settings(tmp_path), workbench=service)
        async with app.run_test(size=(140, 42)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+l")
            await settle(app, pilot)
            await pilot.press("down", "down", "down", "2", "enter")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "Cost coverage: incomplete" in app.screen.text
            assert "Known spend: at least $0.0200" in app.screen.text
            assert "3/4 scheduled attempts" in app.screen.text
            await pilot.press("escape")
            service.report["systems"].reverse()
            await pilot.press("ctrl+r")
            await settle(app, pilot)
            assert app.ranking_selection == ("partial", "cohort-old")
            assert app.selected and app.selected["system_id"] == "partial"
            assert app.ranking_metric == "cost"

    asyncio.run(exercise())


def test_empty_rankings_do_not_invent_evidence_or_allow_drilldown(tmp_path: Path) -> None:
    async def exercise() -> None:
        report = ranking_report()
        report.update(systems=[], benchmarks=[], cells=[])
        app = WorkbenchApp(Settings(tmp_path), workbench=RankingsWorkspace(report))
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+l")
            await settle(app, pilot)
            assert app.query_one("#ledger", DataTable).row_count == 0
            assert "No recorded benchmark runs" in str(app.query_one("#empty", Static).render())
            assert app.query_one("#tool-ranking_details", Button).disabled
            assert app.query_one("#tool-ranking_run", Button).disabled
            await pilot.press("o")
            assert app.page == "rankings" and len(app.screen_stack) == 1
            await pilot.press("f1")
            await settle(app, pilot)
            assert app.page == "overview"

    asyncio.run(exercise())


def test_duration_keeps_unknown_and_zero_distinct() -> None:
    assert duration(None) == "unknown"
    assert duration(0) == "0.00 s"
    assert duration(120_000) == "2.0 min"


def test_real_saved_workspace_populates_controls_without_model_rank(tmp_path: Path) -> None:
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
            id="rankings-controls",
            name="Recorded arithmetic controls",
            domain="data",
            scenarios=[
                Scenario(
                    id="sum",
                    name="Sum two values",
                    domain="data",
                    instruction="Return the total.",
                    inputs={"values": [2, 3]},
                    reference_output={"total": 5},
                    checks=[
                        Check(id="total", name="Total", kind="numeric", path="total", expected=5)
                    ],
                )
            ],
        )
    )
    run = store.create_experiment(
        ExperimentCreate(
            name="Saved control evidence",
            collection_id="rankings-controls",
            system_ids=["fixture-reference", "fixture-incomplete"],
        )
    )
    Engine(store, settings).run_experiment(run["id"])

    async def exercise() -> None:
        app = WorkbenchApp(settings, workbench=Workbench(settings))
        async with app.run_test(size=(140, 42)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+l")
            await settle(app, pilot)
            assert len(app.rows) == 2
            assert len(app.payload["benchmarks"]) == 1
            assert all(cell["ranks"]["quality"] is None for cell in app.payload["cells"])
            table = app.query_one("#ledger", DataTable)
            assert "CONTROL" in str(table.get_cell_at(Coordinate(0, 0)))
            assert "unranked" in str(table.get_cell_at(Coordinate(0, 1)))
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "Saved control evidence" in app.screen.text
            assert "Fixture control" in app.screen.text
            assert run["id"] in app.screen.text

    asyncio.run(exercise())


def test_rankings_shortcuts_do_not_consume_form_input(tmp_path: Path) -> None:
    from textual.widgets import Input

    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path), workbench=RankingsWorkspace())
        async with app.run_test(size=(100, 36)) as pilot:
            await settle(app, pilot)
            await pilot.press("ctrl+n")
            await pilot.pause()
            field = app.screen.query_one("#field-source", Input)
            field.focus()
            await pilot.press("1", "2", "3", "o")
            assert field.value == "123o"
            assert app.page == "overview"

    asyncio.run(exercise())
