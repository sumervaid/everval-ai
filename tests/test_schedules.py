from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System, content_hash
from cvals.schedules import ScheduleStore
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    scenario = Scenario(
        id="sum",
        name="Sum",
        domain="data",
        instruction="Return the sum",
        checks=[Check(name="Sum", kind="numeric", path="value", expected=5)],
        reference_output={"value": 5},
    )
    store.save_collection(Collection(id="tasks", name="Tasks", scenarios=[scenario]))
    experiment = store.create_experiment(
        ExperimentCreate(name="Original", collection_id="tasks", system_ids=["fixture-reference"])
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    return settings, store, result


def make_due(settings, id, *, ago=1):
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE evaluation_schedules SET next_run_epoch=? WHERE id=?", (time.time() - ago, id)
        )


def test_due_schedule_preserves_source_snapshot_and_skips_catchup(workspace):
    settings, store, source = workspace
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Every hour", source["id"], 60)
    collection = store.get_collection("tasks")
    collection.update(version="2.0", name="Edited after source")
    store.save_collection(Collection.model_validate(collection))
    make_due(settings, schedule["id"], ago=10 * 86400)
    created = schedules.tick()
    assert len(created) == 1
    fresh = store.get_experiment(created[0])
    assert fresh["collection_hash"] == source["collection_hash"]
    assert fresh["collection_version"] == source["collection_version"]
    assert fresh["parent_experiment_id"] == source["id"]
    assert schedules.tick() == []
    assert schedules.list()[0]["last_experiment_id"] == fresh["id"]


def test_concurrent_ticks_create_one_occurrence(workspace):
    settings, store, source = workspace
    first = ScheduleStore(settings, store)
    second = ScheduleStore(settings, store)
    schedule = first.create("Concurrent", source["id"], 5)
    make_due(settings, schedule["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda schedules: schedules.tick(), [first, second]))
    assert sum(len(result) for result in results) == 1
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schedule_occurrences").fetchone()[0] == 1


def test_schedule_never_overlaps_its_outstanding_run(workspace):
    settings, store, source = workspace
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Single outstanding", source["id"], 5)
    make_due(settings, schedule["id"])
    first = schedules.tick()
    make_due(settings, schedule["id"])
    assert schedules.tick() == []
    Engine(store, settings).run_experiment(first[0])
    make_due(settings, schedule["id"])
    assert len(schedules.tick()) == 1


def test_schedule_waits_for_source_and_disabled_never_runs(workspace):
    settings, store, source = workspace
    schedules = ScheduleStore(settings, store)
    queued = store.rerun_experiment(source["id"])
    schedule = schedules.create("Wait", queued["id"], 5)
    make_due(settings, schedule["id"])
    assert schedules.tick() == []
    assert not schedules.set_enabled(schedule["id"], False)["enabled"]
    Engine(store, settings).run_experiment(queued["id"])
    make_due(settings, schedule["id"])
    assert schedules.tick() == []
    enabled = schedules.set_enabled(schedule["id"], True)
    assert enabled["enabled"] and enabled["last_error"] is None
    assert schedules.tick() == []


def test_crash_before_commit_rolls_back_occurrence_and_job(workspace, monkeypatch):
    settings, store, source = workspace
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Crash safety", source["id"], 5)
    make_due(settings, schedule["id"])
    original_insert = store._insert_experiment

    def crash(*args, **kwargs):
        original_insert(*args, **kwargs)
        raise RuntimeError("Simulated process loss before commit")

    monkeypatch.setattr(store, "_insert_experiment", crash)
    with pytest.raises(RuntimeError):
        schedules.tick()
    assert len(store.list_experiments()) == 1
    monkeypatch.setattr(store, "_insert_experiment", original_insert)
    assert len(schedules.tick()) == 1
    assert len(store.list_experiments()) == 2


def test_admission_failure_disables_schedule_without_orphan_job(workspace, monkeypatch):
    settings, store, source = workspace
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Admission failure", source["id"], 5)
    make_due(settings, schedule["id"])
    original_insert = store._insert_experiment

    def fail(*args, **kwargs):
        original_insert(*args, **kwargs)
        raise ValueError("Configuration changed")

    monkeypatch.setattr(store, "_insert_experiment", fail)
    assert schedules.tick() == []
    assert len(store.list_experiments()) == 1
    current = schedules.list()[0]
    assert not current["enabled"] and current["last_error"] == "Configuration changed"


@pytest.mark.parametrize("interval", [0, 4, 5.5, True, 525601])
def test_schedule_interval_bounds(workspace, interval):
    settings, store, source = workspace
    with pytest.raises(ValidationError):
        ScheduleStore(settings, store).create("Invalid interval", source["id"], interval)


def test_replay_schedule_is_rejected(workspace):
    settings, store, _ = workspace
    replay = store.import_submissions(
        "tasks",
        "Recorded output",
        content_hash(store.get_collection("tasks")),
        {"sum": {"output": {"value": 5}}},
    )
    with pytest.raises(ValueError, match="new evidence"):
        ScheduleStore(settings, store).create("Cannot repeat evidence", replay["id"], 5)


def test_remote_schedule_requires_current_optin(workspace, monkeypatch):
    settings, store, _ = workspace
    opted_in = Settings(data_dir=settings.data_dir, allow_remote_providers=True)
    authorized = Store(opted_in)
    authorized.save_system(
        System(
            id="remote",
            name="Remote",
            kind="http",
            model="example",
            base_url="https://provider.example/v1",
            input_cost_per_million=1,
            output_cost_per_million=1,
        )
    )
    source = authorized.create_experiment(
        ExperimentCreate(
            name="Configured only", collection_id="tasks", system_ids=["remote"], budget_usd=1
        )
    )
    with pytest.raises(ValueError, match="disabled"):
        ScheduleStore(settings, store).create("No opt-in", source["id"], 5)
    # No provider request is made by creating either a source or a schedule.
    assert ScheduleStore(opted_in, authorized).create("Opted in", source["id"], 5)["enabled"]


def test_prior_uncertain_billing_disables_future_occurrences(workspace):
    import json

    settings, store, source = workspace
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Budget safety", source["id"], 5)
    make_due(settings, schedule["id"])
    generated = schedules.tick()[0]
    Engine(store, settings).run_experiment(generated)
    with sqlite3.connect(settings.database_path) as connection:
        state = json.loads(
            connection.execute("SELECT state FROM experiments WHERE id=?", (generated,)).fetchone()[
                0
            ]
        )
        state["budget_uncertain"] = True
        connection.execute(
            "UPDATE experiments SET state=? WHERE id=?", (json.dumps(state), generated)
        )
    make_due(settings, schedule["id"])
    assert schedules.tick() == []
    assert not schedules.list()[0]["enabled"]
    assert "uncertain billing" in schedules.list()[0]["last_error"]
