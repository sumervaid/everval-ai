"""End-to-end chat execution checks use original, provider-free controls."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cvals.config import Settings
from cvals.conversation_execution import ExecutionService
from cvals.engine import Engine
from cvals.models import Check, Collection, Scenario
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="support",
            name="Support checks",
            scenarios=[
                Scenario(
                    id="route",
                    name="Route request",
                    domain="data",
                    instruction="Return the support category",
                    checks=[
                        Check(
                            name="Expected category",
                            kind="equals",
                            path="category",
                            expected="card",
                        )
                    ],
                    reference_output={"category": "card"},
                )
            ],
        )
    )
    return settings, store, ExecutionService(settings, store)


def plan(**overrides):
    return {
        "name": "Fortnightly support comparison",
        "collection_id": "support",
        "system_ids": ["fixture-reference", "fixture-incomplete"],
        "interval_minutes": 20160,
        "report_formats": ["html", "markdown", "csv", "json"],
        "budget_usd": 0.0,
        "lifetime_budget_usd": 0.0,
        "repetitions": 1,
        **overrides,
    }


def due(store, schedule_id):
    with store._connect(write=True) as connection:
        connection.execute(
            "UPDATE evaluation_schedules SET next_run_epoch=? WHERE id=?",
            (time.time() - 1, schedule_id),
        )


def test_atomic_activation_is_restart_safe_and_idempotent(workspace):
    settings, store, execution = workspace
    first = execution.activate("session-1", plan())
    assert first["status"] == "active"
    assert first["schedule"]["interval_minutes"] == 20160
    restarted = ExecutionService(settings, store)
    second = restarted.activate("session-1", plan())
    assert second["experiment_id"] == first["experiment_id"]
    assert second["schedule_id"] == first["schedule_id"]
    assert len(store.list_experiments()) == 1
    assert len(execution.schedules.list()) == 1
    with pytest.raises(ValueError, match="frozen benchmark"):
        restarted.activate("session-1", plan(repetitions=2))


def test_parallel_activation_admits_only_one_run(workspace):
    settings, store, execution = workspace
    other = ExecutionService(settings, store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(
            pool.map(lambda service: service.activate("same", plan()), [execution, other])
        )
    assert result[0]["experiment_id"] == result[1]["experiment_id"]
    assert len(store.list_experiments()) == 1


def test_schedule_failure_rolls_back_initial_experiment(workspace, monkeypatch):
    _, store, execution = workspace
    original = execution.schedules._create

    def fail_after_schedule(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Simulated crash after schedule creation")

    monkeypatch.setattr(execution.schedules, "_create", fail_after_schedule)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        execution.activate("failed", plan())
    assert store.list_experiments() == []
    assert execution.schedules.list() == []
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM conversation_executions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "override",
    [
        {"budget_usd": 2.0, "lifetime_budget_usd": 1.0},
        {"budget_usd": True},
        {"budget_usd": float("nan")},
        {"interval_minutes": 1},
        {"report_formats": ["email"]},
        {"report_formats": []},
        {"destination": "../../public"},
        {"system_ids": ["fixture-reference", "fixture-reference"]},
    ],
)
def test_invalid_request_never_enqueues(workspace, override):
    _, store, execution = workspace
    with pytest.raises(ValueError):
        execution.activate("invalid", plan(**override))
    assert not store.list_experiments()
    assert not execution.schedules.list()


def test_missing_data_never_enqueues(workspace):
    _, store, execution = workspace
    with pytest.raises(KeyError):
        execution.activate("missing", plan(collection_id="unknown"))
    assert not store.list_experiments()
    assert not execution.schedules.list()


def test_source_is_inside_total_budget_and_exhaustion_pauses_schedule(workspace):
    settings, store, execution = workspace
    receipt = execution.activate("bounded", plan(budget_usd=1.0, lifetime_budget_usd=1.0))
    budget = receipt["budget"]
    assert budget["committed_usd"] == 1.0
    assert budget["remaining_usd"] == 0.0
    assert "Includes the initial source run" in budget["scope"]
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    # Exercise settled accounting, without making any provider call.
    with store._connect(write=True) as connection:
        _, state = store._read_experiment(connection, receipt["experiment_id"])
        state["budget_committed_usd"] = 0.25
        store._save_state(connection, state)
    due(store, receipt["schedule_id"])
    assert execution.schedules.tick() == []
    current = execution.get("bounded")
    assert current["status"] == "paused"
    assert "Lifetime schedule budget" in current["schedule"]["last_error"]
    assert len(store.list_experiments()) == 1


def test_pause_resume_retains_source_and_frozen_recurring_snapshot(workspace):
    settings, store, execution = workspace
    receipt = execution.activate("recurring", plan())
    assert execution.pause("recurring")["status"] == "paused"
    due(store, receipt["schedule_id"])
    assert execution.schedules.tick() == []
    assert execution.resume("recurring")["status"] == "active"
    source = Engine(store, settings).run_experiment(receipt["experiment_id"])
    collection = store.get_collection("support")
    collection.update(name="Later data", version="2.0")
    store.save_collection(Collection.model_validate(collection))
    due(store, receipt["schedule_id"])
    next_id = execution.schedules.tick()[0]
    next_run = store.get_experiment(next_id)
    assert next_run["collection_hash"] == source["collection_hash"]
    Engine(store, settings).run_experiment(next_id)
    assert len(execution.tick()) == 8
    assert {row["experiment_id"] for row in execution.reports("recurring")} == {
        source["id"],
        next_id,
    }


def test_completed_results_create_all_requested_private_reports(workspace):
    settings, store, execution = workspace
    receipt = execution.activate("reports", plan(name="<script>alert(1)</script>"))
    assert execution.tick() == []
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    reports = execution.tick()
    assert {row["format"] for row in reports} == {"html", "markdown", "csv", "json"}
    assert len(reports) == 4
    for row in reports:
        path = execution.resolve_report("reports", row["id"])
        assert path.is_relative_to(settings.artifacts_dir)
        assert os.stat(path).st_mode & 0o777 == 0o600
    html = next(row for row in reports if row["format"] == "html")
    assert "<script>" not in execution.resolve_report("reports", html["id"]).read_text()
    data = next(row for row in reports if row["format"] == "json")
    result = json.loads(execution.resolve_report("reports", data["id"]).read_text())
    assert result["report"]["systems"][0]["pass_rate"] == 1.0
    assert result["report"]["systems"][1]["pass_rate"] == 0.0
    assert ExecutionService(settings, store).tick() == []
    assert len(execution.reports("reports")) == 4


def test_cancelled_run_still_gets_report(workspace):
    settings, store, execution = workspace
    receipt = execution.activate("cancelled", plan(report_formats=["markdown"]))
    store.cancel_experiment(receipt["experiment_id"])
    assert store.get_experiment(receipt["experiment_id"])["status"] == "cancelled"
    report = execution.tick()[0]
    text = execution.resolve_report("cancelled", report["id"]).read_text()
    assert "cancelled" in text
    assert "no valid graded result" in text


def test_report_failure_is_visible_and_retried_after_restart(workspace, monkeypatch):
    settings, store, execution = workspace
    receipt = execution.activate("retry", plan())
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    original = execution._export
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Report disk temporarily unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, "_export", fail_second)
    assert len(execution.tick()) == 1
    assert "temporarily unavailable" in execution.get("retry")["report_errors"][0]["error"]
    restarted = ExecutionService(settings, store)
    assert len(restarted.tick()) == 3
    assert restarted.get("retry")["report_errors"] == []
    assert len(restarted.reports("retry")) == 4


def test_concurrent_report_workers_do_not_duplicate_receipts_or_events(workspace):
    settings, store, execution = workspace
    receipt = execution.activate("parallel", plan())
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    other = ExecutionService(settings, store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        produced = list(pool.map(lambda service: service.tick(), [execution, other]))
    assert sum(map(len, produced)) == 4
    assert len(execution.reports("parallel")) == 4
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE kind='conversation.report_saved'"
            ).fetchone()[0]
            == 4
        )


def test_report_download_checks_ownership_and_integrity(workspace):
    settings, store, execution = workspace
    receipt = execution.activate("owner", plan(report_formats=["json"]))
    execution.activate("other", plan(report_formats=["json"]))
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    report = execution.tick()[0]
    with pytest.raises(KeyError):
        execution.resolve_report("other", report["id"])
    path = execution.resolve_report("owner", report["id"])
    path.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        execution.resolve_report("owner", report["id"])


def test_report_symlink_never_writes_outside_workspace(workspace, tmp_path):
    settings, store, execution = workspace
    receipt = execution.activate("symlink", plan())
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    external = tmp_path / "external"
    external.mkdir()
    (settings.artifacts_dir / "conversations").symlink_to(external, target_is_directory=True)
    assert execution.tick() == []
    assert not list(external.iterdir())
    assert "symbolic links" in execution.get("symlink")["report_errors"][0]["error"]


def test_interrupted_report_commit_is_recovered_without_duplicate_files(workspace, monkeypatch):
    settings, store, execution = workspace
    receipt = execution.activate("interrupted-export", plan(report_formats=["json"]))
    Engine(store, settings).run_experiment(receipt["experiment_id"])
    original = store._event

    def crash(connection, experiment_id, kind, data):
        if kind == "conversation.report_saved":
            raise RuntimeError("Process stopped after writing report")
        return original(connection, experiment_id, kind, data)

    monkeypatch.setattr(store, "_event", crash)
    with pytest.raises(RuntimeError, match="Process stopped"):
        execution.tick()
    assert execution.reports("interrupted-export") == []
    directory = execution._directory("interrupted-export")
    assert len(list(directory.iterdir())) == 1
    monkeypatch.setattr(store, "_event", original)
    recovered = ExecutionService(settings, store)
    report = recovered.tick()[0]
    assert recovered.resolve_report("interrupted-export", report["id"]).is_file()
    assert len(list(directory.iterdir())) == 1
    assert recovered.tick() == []
