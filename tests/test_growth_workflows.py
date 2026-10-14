"""Guided setup, lineage health, recurring budgets and change admission contracts."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from cvals.benchmark_health import BenchmarkHealth
from cvals.change_runs import ChangeRuns
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System
from cvals.onboarding import Onboarding
from cvals.projects import Projects
from cvals.schedules import ScheduleStore
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Settings, Store, dict[str, Any], dict[str, Any]]:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "solution.py").write_text("def total(values):\n    return sum(values) + 1\n")
    commands = [
        ["init", "--quiet"],
        ["add", "."],
        ["commit", "--quiet", "-m", "Initial"],
        ["-c", "core.hooksPath=/dev/null", "status", "--porcelain"],
    ]
    for command in commands:
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                *command,
            ],
            check=True,
            capture_output=True,
        )
    (repository / "solution.py").write_text("def total(values):\n    return sum(values)\n")
    (repository / "test_total.py").write_text(
        "from solution import total\ndef test_total():\n    assert total([2,3]) == 5\n"
    )
    for command in [["add", "."], ["commit", "--quiet", "-m", "Correct total"]]:
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                *command,
            ],
            check=True,
            capture_output=True,
        )
    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="tasks",
            name="Tasks",
            domain="data",
            scenarios=[
                Scenario(
                    id="sum",
                    name="Sum",
                    domain="data",
                    instruction="Return the sum",
                    reference_output={"value": 5},
                    checks=[
                        Check(id="sum", name="Total", kind="numeric", path="value", expected=5)
                    ],
                )
            ],
        )
    )
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Baseline",
            collection_id="tasks",
            system_ids=["fixture-reference"],
            budget_usd=1,
        )
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    project = Projects(settings).connect(str(repository))
    return settings, store, project, result


def test_guide_resumes_and_never_marks_choices_as_validation(
    workspace: tuple[Settings, Store, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, project, _ = workspace
    monkeypatch.setattr("cvals.onboarding.sandbox_status", lambda settings: {"available": True})
    guides = Onboarding(settings)
    guide = guides.start("Repository evaluation", project["source"])
    guides.select(guide["id"], project_id=project["id"])
    Projects(settings).discover(project["id"])
    recommendation = guides.recommendations(project["id"])[0]
    guides.select(guide["id"], candidate_id=recommendation["candidate_id"])
    resumed = Onboarding(settings).status(guide["id"])
    states = {step["id"]: step["status"] for step in resumed["steps"]}
    assert states["connect"] == states["discover"] == "complete"
    assert states["review"] == states["validate"] == states["run"] == "pending"
    assert resumed["next_step"] == "review"
    assert not recommendation["automatic_approval"]
    assert Onboarding(settings).list()[0]["id"] == guide["id"]
    assert guides.audit.verify()["valid"]


def test_guide_rejects_unrelated_evidence_controls_and_secrets(workspace: Any) -> None:
    settings, _, project, experiment = workspace
    guides = Onboarding(settings)
    guide = guides.start()
    with pytest.raises(ValueError, match="candidate belonging"):
        guides.select(guide["id"], candidate_id="missing")
    with pytest.raises(ValueError, match="live model"):
        guides.select(guide["id"], system_id="fixture-reference")
    with pytest.raises(ValueError, match="release"):
        guides.select(guide["id"], collection_id="tasks")
    with pytest.raises(ValueError, match="credential-free"):
        guides.start(source="https://user:secret@github.com/owner/repo")
    assert guides.get(guide["id"])["experiment_id"] is None


def test_health_keeps_unvalidated_candidates_and_denominators(workspace: Any) -> None:
    settings, _, project, _ = workspace
    Projects(settings).discover(project["id"])
    health = BenchmarkHealth(settings).project(project["id"])
    assert health["candidate_count"] >= 1
    assert health["admitted_count"] == 0
    assert health["excluded_count"] == health["candidate_count"]
    assert health["admission_rate"] == 0
    assert health["coverage"]["discovery_fraction"] is None
    assert health["observed_flaky_count"] == 0
    assert any("representative" in item for item in health["limitations"])


def test_change_trigger_is_atomic_and_deduplicates_concurrent_calls(workspace: Any) -> None:
    settings, store, project, source = workspace
    service = ChangeRuns(settings)
    baseline = service.register(
        "Agent changes", project["id"], source["id"], {"prompt_revision": "v1"}, enabled=True
    )

    def trigger(_: int) -> dict[str, Any]:
        return ChangeRuns(settings).trigger(baseline["id"], project["head_sha"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(trigger, [0, 1]))
    assert first["experiment_id"] == second["experiment_id"]
    assert {first["deduplicated"], second["deduplicated"]} == {True, False}
    assert len(service.evaluations(baseline["id"])) == 1
    frozen = store.get_experiment(first["experiment_id"])
    assert frozen["collection_hash"] == source["collection_hash"]
    assert frozen["parent_experiment_id"] == source["id"]
    assert service.audit.verify()["valid"]


def test_change_trigger_freezes_current_system_configuration(workspace: Any) -> None:
    settings, store, project, source = workspace
    service = ChangeRuns(settings)
    baseline = service.register("Agent changes", project["id"], source["id"], enabled=True)
    first = service.trigger(baseline["id"], project["head_sha"])
    Engine(store, settings).run_experiment(first["experiment_id"])
    store.save_system(System(id="fixture-reference", name="Changed fixture", kind="incomplete"))
    revised = service.revise(baseline["id"], config={"prompt_revision": "v2"})
    second = service.trigger(baseline["id"], project["head_sha"])
    assert revised["version"] == 2
    assert first["fingerprint"] != second["fingerprint"]
    assert store.get_experiment(second["experiment_id"])["systems"][0]["kind"] == "incomplete"
    assert (
        store.get_experiment(second["experiment_id"])["collection_hash"]
        == source["collection_hash"]
    )


def test_change_trigger_rolls_back_on_interrupted_insert(
    workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, store, project, source = workspace
    service = ChangeRuns(settings)
    baseline = service.register("Crash test", project["id"], source["id"], enabled=True)
    original = service.store._insert_experiment

    def crash(*args: Any, **kwargs: Any) -> str:
        original(*args, **kwargs)
        raise RuntimeError("Process loss")

    monkeypatch.setattr(service.store, "_insert_experiment", crash)
    with pytest.raises(RuntimeError):
        service.trigger(baseline["id"], project["head_sha"])
    assert len(store.list_experiments()) == 1
    assert not service.evaluations(baseline["id"])


def test_change_admission_preserves_pause_and_credentials(workspace: Any) -> None:
    settings, _, project, source = workspace
    service = ChangeRuns(settings)
    baseline = service.register("Paused", project["id"], source["id"])
    with pytest.raises(ValueError, match="Enable"):
        service.trigger(baseline["id"], project["head_sha"])
    with pytest.raises(ValueError, match="never secret"):
        service.revise(baseline["id"], config={"api_key": "sensitive"})
    with pytest.raises(ValueError, match="finite"):
        service.revise(baseline["id"], config={"weight": float("nan")})
    assert service.revise(baseline["id"], config={"api_key_env": "AGENT_KEY"})["config"] == {
        "api_key_env": "AGENT_KEY"
    }
    assert not service.get(baseline["id"])["enabled"]


def due(settings: Settings, identifier: str) -> None:
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE evaluation_schedules SET next_run_epoch=? WHERE id=?",
            (time.time() - 1, identifier),
        )


def test_schedule_reserves_maximum_then_accounts_for_settled_spend(workspace: Any) -> None:
    settings, store, _, source = workspace
    service = ScheduleStore(settings, store)
    schedule = service.create("Bounded", source["id"], 5, lifetime_budget_usd=1.5)
    due(settings, schedule["id"])
    run = service.tick()[0]
    assert service.budget(schedule["id"])["committed_usd"] == 1
    Engine(store, settings).run_experiment(run)
    with store._connect(write=True) as connection:
        _, state = store._read_experiment(connection, run)
        state["budget_committed_usd"] = 0.75
        store._save_state(connection, state)
    assert service.budget(schedule["id"])["committed_usd"] == 0.75
    due(settings, schedule["id"])
    assert service.tick() == []
    assert "Lifetime" in service.list()[0]["last_error"]
    service.set_budget(schedule["id"], 2)
    service.set_enabled(schedule["id"], True)
    due(settings, schedule["id"])
    assert len(service.tick()) == 1
    assert service.budget(schedule["id"])["committed_usd"] == 1.75


def test_schedule_uncertain_billing_retains_full_reservation(workspace: Any) -> None:
    settings, store, _, source = workspace
    service = ScheduleStore(settings, store)
    schedule = service.create("Uncertain", source["id"], 5, lifetime_budget_usd=3)
    due(settings, schedule["id"])
    run = service.tick()[0]
    Engine(store, settings).run_experiment(run)
    with store._connect(write=True) as connection:
        _, state = store._read_experiment(connection, run)
        state.update(budget_uncertain=True, budget_committed_usd=0.1)
        store._save_state(connection, state)
    budget = service.budget(schedule["id"])
    assert budget["committed_usd"] == 1 and budget["reservations"][0]["status"] == "uncertain"
    with pytest.raises(ValueError, match="less than"):
        service.set_budget(schedule["id"], 0.5)
    due(settings, schedule["id"])
    assert not service.tick()
    assert "uncertain" in service.list()[0]["last_error"]


def test_weekly_digest_is_frozen_and_retains_unknown_cost(workspace: Any) -> None:
    settings, store, _, source = workspace
    service = ScheduleStore(settings, store)
    schedule = service.create("Weekly", source["id"], 10080, lifetime_budget_usd=10)
    due(settings, schedule["id"])
    run = service.tick()[0]
    pending = service.digest(schedule["id"])
    assert pending["occurrences"][0]["incomplete"] == ["fixture-reference/sum"]
    Engine(store, settings).run_experiment(run)
    with store._connect(write=True) as connection:
        row = connection.execute(
            "SELECT id,data FROM trials WHERE experiment_id=?", (run,)
        ).fetchone()
        trial = json.loads(row["data"])
        trial.update(passed=False, cost_usd=None, provider_metadata={"known_cost_usd": 0.02})
        store._save_trial(connection, trial)
    final = service.digest(schedule["id"])
    assert final["occurrences"][0]["newly_failing"] == ["fixture-reference/sum"]
    assert final["known_cost_usd"] == 0.02
    assert not final["cost_complete"]
    saved = {value["id"]: value for value in service.digests(schedule["id"])}
    assert saved[pending["id"]] == pending
    assert saved[final["id"]] == final
