"""No business mutation may commit when its global audit append fails."""

from __future__ import annotations

import time

import pytest

from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System, content_hash
from cvals.reviews import ReviewStore
from cvals.schedules import ScheduleStore
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="collection",
            name="Arithmetic",
            domain="data",
            scenarios=[
                Scenario(
                    id="task",
                    name="Total",
                    domain="data",
                    instruction="Add rows.",
                    checks=[Check(name="Total", kind="numeric", path="total", expected=3)],
                    reference_output={"total": 3},
                )
            ],
        )
    )
    return settings, store


def request():
    return ExperimentCreate(
        name="Trial", collection_id="collection", system_ids=["fixture-reference"]
    )


def fail_append(monkeypatch):
    def failed(*args, **kwargs):
        raise ValueError("Audit write failed")

    monkeypatch.setattr("cvals.audit.append_audit", failed)


def table_rows(store, table):
    with store._connect() as connection:
        return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]


@pytest.mark.parametrize(
    "operation",
    [
        "collection_create",
        "collection_update",
        "collection_delete",
        "system_create",
        "system_delete",
        "experiment_create",
    ],
)
def test_basic_mutations_roll_back_with_audit_failure(workspace, monkeypatch, operation):
    _, store = workspace
    before = {
        name: table_rows(store, name)
        for name in ("collections", "systems", "experiments", "trials", "events", "workspace_audit")
    }
    collection = Collection.model_validate(store.get_collection("collection"))
    fail_append(monkeypatch)
    with pytest.raises(ValueError, match="Audit write failed"):
        if operation == "collection_create":
            store.save_collection(collection.model_copy(update={"id": "new"}))
        elif operation == "collection_update":
            store.save_collection(collection.model_copy(update={"version": "2.0"}))
        elif operation == "collection_delete":
            store.delete_collection("collection")
        elif operation == "system_create":
            store.save_system(System(name="New fixture", kind="reference"))
        elif operation == "system_delete":
            store.delete_system("fixture-reference")
        else:
            store.create_experiment(request())
    for name, rows in before.items():
        assert table_rows(store, name) == rows


@pytest.mark.parametrize("operation", ["cancel", "rerun", "replay"])
def test_experiment_actions_are_atomic_with_the_global_chain(workspace, monkeypatch, operation):
    _, store = workspace
    original = store.create_experiment(request())
    before = {
        name: table_rows(store, name)
        for name in ("systems", "experiments", "trials", "events", "workspace_audit")
    }
    fail_append(monkeypatch)
    with pytest.raises(ValueError, match="Audit write failed"):
        if operation == "cancel":
            store.cancel_experiment(original["id"])
        elif operation == "rerun":
            store.rerun_experiment(original["id"])
        else:
            collection = store.get_collection("collection")
            store.import_submissions("collection", "Replay", content_hash(collection), {})
    for name, rows in before.items():
        assert table_rows(store, name) == rows


def test_review_and_schedule_mutations_cannot_outlive_failed_audit(workspace, monkeypatch):
    settings, store = workspace
    original = store.create_experiment(request())
    store.cancel_experiment(original["id"])
    reviews = ReviewStore(settings)
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Paused", original["id"], 5, enabled=False)
    before = {
        name: table_rows(store, name)
        for name in ("human_reviews", "evaluation_schedules", "events", "workspace_audit")
    }
    fail_append(monkeypatch)
    with pytest.raises(ValueError, match="Audit write failed"):
        reviews.add(
            original["id"], original["trials"][0]["id"], "Reviewer", "clarity", 0.5, "A reason."
        )
    with pytest.raises(ValueError, match="Audit write failed"):
        schedules.create("Another", original["id"], 5)
    with pytest.raises(ValueError, match="Audit write failed"):
        schedules.set_enabled(schedule["id"], True)
    for name, rows in before.items():
        assert table_rows(store, name) == rows


def test_schedule_occurrence_failure_rolls_back_admission_and_due_time(workspace, monkeypatch):
    settings, store = workspace
    original = store.create_experiment(request())
    store.cancel_experiment(original["id"])
    schedules = ScheduleStore(settings, store)
    schedule = schedules.create("Recurring", original["id"], 5)
    with store._connect(write=True) as connection:
        connection.execute(
            "UPDATE evaluation_schedules SET next_run_epoch=? WHERE id=?",
            (time.time() - 1, schedule["id"]),
        )
    before = {
        name: table_rows(store, name)
        for name in (
            "evaluation_schedules",
            "schedule_occurrences",
            "experiments",
            "trials",
            "events",
            "workspace_audit",
        )
    }
    fail_append(monkeypatch)
    with pytest.raises(ValueError, match="Audit write failed"):
        schedules.tick()
    for name, rows in before.items():
        assert table_rows(store, name) == rows


def test_fixture_polling_and_noop_saves_produce_no_audit_noise(workspace):
    settings, store = workspace
    audit = AuditLog(settings)
    before = audit.verify()["count"]
    for _ in range(3):
        store.ensure_fixture_systems()
        store.save_collection(Collection.model_validate(store.get_collection("collection")))
        public = store.get_system("fixture-reference")
        public.pop("configured")
        store.save_system(System.model_validate(public))
    assert audit.verify()["count"] == before
    assert audit.verify()["valid"]


def test_human_label_is_distinct_from_local_audit_actor(workspace):
    settings, store = workspace
    original = store.create_experiment(request())
    store.cancel_experiment(original["id"])
    review = ReviewStore(settings).add(
        original["id"],
        original["trials"][0]["id"],
        "Unverified reviewer label",
        "clarity",
        0.7,
        "Reason.",
    )
    record = next(item for item in AuditLog(settings).list() if item["action"] == "review.added")
    assert record["details"]["reviewer_label"] == review["reviewer"]
    assert record["actor"] != review["reviewer"]
    assert record["details"]["review_hash"] == content_hash(
        {key: val for key, val in review.items() if key != "latest"}
    )
