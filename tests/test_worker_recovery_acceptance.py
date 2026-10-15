"""Regression for a zero-rate HTTP call interrupted in installed-stack acceptance."""

from __future__ import annotations

import sqlite3
import time

from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System
from cvals.store import Store


def test_zero_rate_interruption_retains_unknown_usage_and_blocks_further_http(
    tmp_path, monkeypatch
):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.save_collection(
        Collection(
            id="tasks",
            name="Tasks",
            scenarios=[
                Scenario(
                    id="sum",
                    name="Sum",
                    domain="data",
                    instruction="Sum the values",
                    inputs={"values": [2, 3]},
                    checks=[Check(name="Sum", kind="numeric", path="value", expected=5)],
                )
            ],
        )
    )
    store.save_system(
        System(
            id="local",
            name="Zero-rate local adapter",
            kind="http",
            model="fixture",
            base_url="http://127.0.0.1:1",
            input_cost_per_million=0,
            output_cost_per_million=0,
        )
    )
    original = store.create_experiment(
        ExperimentCreate(
            name="Interrupted", collection_id="tasks", system_ids=["local"], repetitions=2
        )
    )
    assert store.claim("lost-worker", original["id"]) == original["id"]
    store.start_trial(original["id"], "lost-worker", original["trials"][0]["id"], 0)
    # Recovery must use the frozen run's provider kind, not an editable saved System.
    store.delete_system("local")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE worker_lease SET expires_at=?", (time.time() - 1,))

    def unexpected_request(*args, **kwargs):
        raise AssertionError("No HTTP call may follow an interrupted unknown-usage attempt")

    monkeypatch.setattr("cvals.engine.submit", unexpected_request)
    engine = Engine(store, settings)
    assert engine.recover() == 1
    recovered = engine.run_experiment(original["id"])
    assert recovered["budget_uncertain"]
    assert recovered["trials"][0]["status"] == "interrupted"
    assert recovered["trials"][0]["cost_usd"] is None
    assert len(recovered["trials"][0]["attempts"]) == 1
    assert recovered["trials"][1]["status"] == "budget_exceeded"
    assert recovered["trials"][1]["attempts"] == []
    assert engine.recover() == 0
