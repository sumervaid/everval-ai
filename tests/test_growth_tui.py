"""Headless checks for the guided and recurring terminal workflows."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.widgets import DataTable, Input, Select, TextArea

from cvals.config import Settings
from cvals.onboarding import Onboarding
from cvals.tui.app import WorkbenchApp
from cvals.tui.dialogs import EvidenceDialog, FormDialog
from cvals.tui.presentation import human_text
from cvals.workbench import Workbench


async def settle(app: WorkbenchApp, pilot: Any) -> None:
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()


def test_guide_can_be_started_and_resumed_at_small_terminal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("cvals.onboarding.sandbox_status", lambda settings: {"available": True})

    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        app = WorkbenchApp(settings, workbench=Workbench(settings))
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(app, pilot)
            app.command("guide")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-name", Input).value = "Customer repository"
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert app.page == "journey"
            assert app.query_one("#ledger", DataTable).row_count == 9
            guide_id = app.guide_id
            assert guide_id
            assert app.selected and app.selected["id"] == "connect"
            app.command("continue")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            assert app.screen.query_one("#field-source", Input).value == ""
            await pilot.press("escape", "f9")
            await settle(app, pilot)
            assert app.page == "guides"
            assert app.query_one("#ledger", DataTable).row_count == 1
            app.command("resume")
            await settle(app, pilot)
            assert app.guide_id == guide_id and app.page == "journey"
        resumed = Onboarding(settings).get(guide_id)
        assert resumed["name"] == "Customer repository"
        assert resumed["project_id"] is None

    asyncio.run(exercise())


def test_schedule_form_collects_cumulative_cap_without_json(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        service = Workbench(settings)
        calls = []

        def add_schedule(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append((args, kwargs))
            return {"id": "schedule"}

        service.add_schedule = add_schedule
        app = WorkbenchApp(settings, workbench=service)
        async with app.run_test(size=(120, 45)) as pilot:
            await settle(app, pilot)
            app.selected = {"id": "experiment", "budget_usd": 2}
            app.command_schedule()
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-name", Input).value = "Weekly quality"
            app.screen.query_one("#field-lifetime_budget_usd", Input).value = "8"
            app.screen.query_one("#field-interval_minutes", Input).value = "10080"
            app.screen.query_one("#field-enabled", Select).value = "paused"
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert calls == [
                (
                    ("experiment", "Weekly quality", 10080),
                    {"enabled": False, "lifetime_budget_usd": 8.0},
                )
            ]

    asyncio.run(exercise())


def test_candidate_review_guide_collects_independent_text(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        service = Workbench(settings)
        app = WorkbenchApp(settings, workbench=service)
        async with app.run_test(size=(120, 45)) as pilot:
            await settle(app, pilot)
            app.page = "journey"
            app.payload = {"project_id": "project", "candidate_id": "candidate"}
            app.selected = {"id": "review"}
            app.command_continue()
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            assert app.screen.query_one("#field-statement", TextArea).text == ""
            assert app.screen.query_one("#field-rationale", TextArea).text == ""
            await pilot.press("escape")

    asyncio.run(exercise())


def test_human_evidence_keeps_unknown_cost_and_escapes_control_text(tmp_path: Path) -> None:
    rendered = human_text({"cost_usd": None, "observations": ["Unfinished", "\x1b[2J"]})
    assert "Cost usd: unknown" in rendered
    assert "\x1b" not in rendered
    assert "{" not in rendered

    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        app = WorkbenchApp(settings, workbench=Workbench(settings))
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(app, pilot)
            app.human_evidence("Cost evidence", {"cost_usd": None, "status": "incomplete"})
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert not app.screen.is_json
            assert "unknown" in app.screen.text

    asyncio.run(exercise())


def test_typescript_plan_requires_review_and_preserves_artifact_choice(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from textual.widgets import SelectionList

    from cvals.tui.dialogs import PlanDialog

    artifact_id = "c" * 64
    monkeypatch.setattr(
        "cvals.private_artifacts.PrivateArtifacts.list",
        lambda self: [
            {
                "id": artifact_id,
                "kind": "npm",
                "name": "@fixture/private",
                "version": "1.0.0",
                "registry": "https://registry.example",
            }
        ],
    )

    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        service = Workbench(settings)
        calls = []

        def plan(project_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(("plan", project_id, kwargs))
            return {
                "source_commit": "a" * 40,
                "base_image_digest": "sha256:" + "b" * 64,
                "language": "typescript",
                "runner": "jest",
                "requirements": [],
                "network_policy": "offline",
            }

        def prepare(project_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(("prepare", project_id, kwargs))
            return {"image_digest": "sha256:" + "d" * 64}

        service.environment_plan = plan
        service.prepare_environment = prepare
        app = WorkbenchApp(settings, workbench=service)
        async with app.run_test(size=(120, 48)) as pilot:
            await settle(app, pilot)
            app.page = "projects"
            app.selected = {"id": "project"}
            app.command_prepare()
            await settle(app, pilot)
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-language", Select).value = "typescript"
            app.screen.query_one("#field-runner", Select).value = "jest"
            app.screen.query_one("#field-base_image", Input).value = "node:24-slim"
            app.screen.query_one("#field-artifacts", SelectionList).select(artifact_id)
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert isinstance(app.screen, PlanDialog)
            assert len(calls) == 1
            await pilot.click("#plan-prepare")
            await settle(app, pilot)
            assert calls[-1] == (
                "prepare",
                "project",
                {
                    "packages": None,
                    "revision": "a" * 40,
                    "base_image": "sha256:" + "b" * 64,
                    "language": "typescript",
                    "runner": "jest",
                    "artifacts": [artifact_id],
                },
            )

    asyncio.run(exercise())


def test_external_agent_form_pins_adapter_revision(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = Settings(tmp_path / "workspace")
        service = Workbench(settings)
        app = WorkbenchApp(settings, workbench=service)
        async with app.run_test(size=(120, 48)) as pilot:
            await settle(app, pilot)
            app.new_system()
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-name", Input).value = "Local tested adapter"
            app.screen.query_one("#field-base_url", Input).value = "http://127.0.0.1:8888"
            app.screen.query_one("#field-model", Input).value = "agent"
            app.screen.query_one("#field-harness", Select).value = "external-agent"
            app.screen.query_one("#field-adapter_revision", Input).value = "commit-123"
            await pilot.click("#form-submit")
            await settle(app, pilot)
            saved = [item for item in service.systems() if item["kind"] == "http"]
            assert len(saved) == 1 and saved[0]["harness"] == "external-agent"
            assert saved[0]["adapter_revision"] == "commit-123"

    asyncio.run(exercise())
