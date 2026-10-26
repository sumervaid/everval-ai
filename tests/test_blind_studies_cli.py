"""CLI admission and reveal use real frozen evidence and reviewer identities."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.widgets import Select, SelectionList
from typer.testing import CliRunner

from cvals.cli import app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore, Principal
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.review_queues import Dimension, Judgment, ReviewQueues, Rubric
from cvals.store import Store
from cvals.tui.blind_studies import BlindStudyApp, BlindStudyScreen
from cvals.tui.dialogs import FormDialog

runner = CliRunner()


def setup(tmp_path: Path, monkeypatch):
    settings = Settings(tmp_path / "workspace")
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("CVALS_DATA_DIR", str(settings.data_dir))
    identities = IdentityStore(settings)
    identities.bootstrap("Coordinator")
    people = [
        identities.create_user(name, "reviewer", "local-console")
        for name in ("Reviewer One", "Reviewer Two")
    ]
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="support",
            name="Support tasks",
            scenarios=[
                Scenario(
                    name="Route",
                    domain="data",
                    instruction="Return the support category.",
                    reference_output={"category": "card"},
                    checks=[
                        Check(name="Category", kind="equals", path="category", expected="card")
                    ],
                )
            ],
        )
    )
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Comparison",
            collection_id="support",
            system_ids=["fixture-reference", "fixture-incomplete"],
        )
    )
    Engine(store, settings).run_experiment(experiment["id"])
    queues = ReviewQueues(settings)
    rubric = queues.create_rubric(
        Rubric(
            name="Correctness",
            version="1",
            instructions="Judge the actual answer.",
            dimensions=[
                Dimension(
                    id="correct",
                    name="Correctness",
                    guidance="Zero for incorrect; one for correct.",
                )
            ],
        ),
        actor="local-console",
    )
    return settings, store, identities, queues, people, experiment, rubric


def test_blind_help_is_a_distinct_capability() -> None:
    result = runner.invoke(app, ["blind", "--help"])
    assert result.exit_code == 0, result.output
    assert "double-blind studies" in result.output
    for command in ("create", "list", "show", "lock", "reveal", "report", "tui"):
        assert command in result.output


def test_cli_study_lock_reveal_and_report(tmp_path: Path, monkeypatch) -> None:
    _, store, _, queues, people, experiment, rubric = setup(tmp_path, monkeypatch)
    command = [
        "blind",
        "create",
        "--name",
        "Private vendor comparison",
        "--experiment",
        experiment["id"],
        "--rubric",
        rubric["id"],
    ]
    for value in ("fixture-reference", "fixture-incomplete"):
        command += ["--system", value]
    for person in people:
        command += ["--reviewer", person["id"]]
    created = runner.invoke(app, command)
    assert created.exit_code == 0, created.output
    study = json.loads(created.output)
    assert study["status"] == "collecting"
    assert "fixture-reference" not in created.output
    report = runner.invoke(app, ["blind", "report", study["id"]])
    assert report.exit_code == 1
    lock = runner.invoke(
        app, ["blind", "lock", study["id"], "--expected-hash", study["evidence_hash"]]
    )
    assert lock.exit_code == 1
    for person in people:
        who = Principal(person["id"], person["name"], "reviewer", "team")
        assigned = queues.assigned(who)
        assert len(assigned) == 1
        item = queues.item(assigned[0]["id"], who)
        queues.judge(
            item["id"],
            who,
            Judgment(
                scores={
                    label: {"correct": 1.0 if item["responses"][label]["output"] else 0.0}
                    for label in item["responses"]
                },
                preference="tie",
                disposition="needs_discussion",
                rationale="Installation control review.",
            ),
        )
    shown = runner.invoke(app, ["blind", "show", study["id"]])
    assert shown.exit_code == 0, shown.output
    current = json.loads(shown.output)
    assert current["missing_reviews"] == 0
    locked = runner.invoke(
        app, ["blind", "lock", study["id"], "--expected-hash", current["evidence_hash"]]
    )
    assert locked.exit_code == 0, locked.output
    current = json.loads(locked.output)
    assert current["status"] == "locked"
    premature = runner.invoke(app, ["blind", "report", study["id"]])
    assert premature.exit_code == 1
    revealed = runner.invoke(
        app,
        [
            "blind",
            "reveal",
            study["id"],
            "--expected-hash",
            current["evidence_hash"],
            "--reason",
            "All independent reviews are frozen.",
        ],
    )
    assert revealed.exit_code == 0, revealed.output
    assert json.loads(revealed.output)["status"] == "revealed"
    report = runner.invoke(app, ["blind", "report", study["id"]])
    assert report.exit_code == 0, report.output
    assert "Reference fixture" in report.output
    assert len(store.list_experiments()) == 1
    listing = runner.invoke(app, ["blind", "list"])
    assert listing.exit_code == 0
    assert json.loads(listing.output)[0]["id"] == study["id"]


def test_terminal_creation_uses_completed_outputs_without_another_run(
    tmp_path: Path, monkeypatch
) -> None:
    settings, store, _, _, people, experiment, rubric = setup(tmp_path, monkeypatch)

    async def exercise() -> None:
        app = BlindStudyApp(settings)
        async with app.run_test(size=(100, 42)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.click("#blind-new")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-experiment_id", Select).value = experiment["id"]
            app.screen.query_one("#field-rubric_id", Select).value = rubric["id"]
            selected = app.screen.query_one("#field-reviewer_ids", SelectionList)
            for person in people:
                selected.select(person["id"])
            await pilot.click("#form-submit")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            assert app.screen.query_one("#field-system_ids", SelectionList).selected == [
                "fixture-reference",
                "fixture-incomplete",
            ]
            await pilot.click("#form-submit")
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, BlindStudyScreen)
            assert screen.study["status"] == "collecting"
            assert screen.study["assigned_reviews"] == 2
            assert screen.study["missing_reviews"] == 2
            assert "fixture-reference" not in json.dumps(screen.study)
            assert len(store.list_experiments()) == 1

    asyncio.run(exercise())
