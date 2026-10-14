"""Failure investigation, timing, and cumulative accounting across real store transitions."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from cvals.config import Settings
from cvals.diagnostics import Diagnostics
from cvals.economics import analyze
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System
from cvals.providers import Submission
from cvals.reports import build_report
from cvals.spend_limits import SpendLimits, admit
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.save_collection(
        Collection(
            id="tasks",
            name="Explicit test cases",
            scenarios=[
                Scenario(
                    id="sum",
                    name="Sum",
                    domain="data",
                    instruction="Return a total.",
                    inputs={"rows": [2, 3]},
                    checks=[Check(name="Total", kind="equals", path="total", expected=5)],
                    reference_output={"total": 5},
                ),
            ],
        )
    )
    store.ensure_fixture_systems()
    store.save_system(
        System(
            id="live",
            name="Local mock",
            kind="http",
            model="test",
            base_url="http://127.0.0.1:9910",
            input_cost_per_million=1,
            output_cost_per_million=1,
        )
    )
    return settings, store


def create(store, system_ids=None):
    return store.create_experiment(
        ExperimentCreate(
            name="Explicit test run",
            collection_id="tasks",
            system_ids=system_ids or ["live"],
            budget_usd=1,
        )
    )


def test_regrade_keeps_original_evidence_and_records_stage_timing(workspace, monkeypatch):
    settings, store = workspace
    calls = []

    def submit(*args):
        calls.append(True)
        return Submission({"total": 4}, 2, 2, 0.000004, metadata={"timing": {"provider_ms": 1}})

    monkeypatch.setattr("cvals.engine.submit", submit)
    result = Engine(store, settings).run_experiment(create(store)["id"])
    trial = result["trials"][0]
    assert trial["timing"]["submission_ms"] >= 0
    assert trial["timing"]["grading_ms"] >= 0
    assert trial["timing"]["provider_ms"] == 1
    assert trial["timing"]["total_ms"] == trial["duration_ms"]
    diagnostics = Diagnostics(settings)
    assert diagnostics.triage(result["id"])["counts"] == {"incorrect_result": 1}
    original = copy.deepcopy(store.get_experiment(result["id"]))
    regrade = diagnostics.regrade(result["id"], trial["id"])
    assert regrade["passed"] is False and regrade["uses_provider"] is False
    assert len(calls) == 1
    assert store.get_experiment(result["id"]) == original
    detail = diagnostics.diagnose(result["id"], trial["id"])
    assert detail["regrades"][0]["id"] == regrade["id"]
    assert detail["reproduction"]["available"]
    with pytest.raises(KeyError):
        diagnostics.diagnose(result["id"], "wrong-trial")


def test_partial_tool_cost_is_retained_in_report(workspace, monkeypatch):
    settings, store = workspace
    monkeypatch.setattr(
        "cvals.engine.submit",
        lambda *a: Submission(
            None,
            error="Provider usage unavailable",
            metadata={"known_cost_usd": 0.02, "budget_uncertain": True},
        ),
    )
    result = Engine(store, settings).run_experiment(create(store)["id"])
    summary = build_report(result)["systems"][0]
    assert summary["cost_usd"] is None
    assert summary["known_cost_usd"] == 0.02
    assert summary["cost_per_pass"] is None
    assert not analyze(result)["observed_frontier"]
    assert analyze(result)["systems"][0]["known_spend_on_nonpassing_attempts"] == 0.02


def test_cumulative_limit_applies_across_experiments(workspace, monkeypatch):
    settings, store = workspace
    SpendLimits(settings).configure(daily_usd=0.01)
    monkeypatch.setattr("cvals.engine.reserve_cost", lambda *a: 0.007)
    calls = []

    def submit(*args):
        calls.append(True)
        return Submission({"total": 5}, 1, 1, 0.007)

    monkeypatch.setattr("cvals.engine.submit", submit)
    first = Engine(store, settings).run_experiment(create(store)["id"])
    second = Engine(store, settings).run_experiment(create(store)["id"])
    assert first["trials"][0]["passed"]
    assert second["trials"][0]["status"] == "budget_exceeded"
    assert len(calls) == 1
    assert SpendLimits(settings).list()[0]["usage"]["daily_usd"] == 0.007


def test_unknown_billing_does_not_release_cumulative_reservation(workspace, monkeypatch):
    settings, store = workspace
    SpendLimits(settings).configure(lifetime_usd=0.01)
    monkeypatch.setattr("cvals.engine.reserve_cost", lambda *a: 0.006)
    monkeypatch.setattr(
        "cvals.engine.submit", lambda *a: Submission(None, error="Provider transport failed")
    )
    first = Engine(store, settings).run_experiment(create(store)["id"])
    second = Engine(store, settings).run_experiment(create(store)["id"])
    assert first["budget_uncertain"]
    assert second["trials"][0]["status"] == "budget_exceeded"
    assert SpendLimits(settings).list()[0]["usage"]["lifetime_usd"] == 0.006


def test_reservations_are_atomic(workspace):
    settings, store = workspace
    SpendLimits(settings).configure(lifetime_usd=0.01)

    def reserve(index):
        with store._connect(write=True) as connection:
            return admit(connection, "test-run", "trial-" + str(index), "workspace", 0.007)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert outcomes.count(None) == 1
    assert sum(isinstance(x, str) for x in outcomes) == 1


def test_existing_spend_counts_when_limit_added(workspace, monkeypatch):
    settings, store = workspace
    monkeypatch.setattr("cvals.engine.reserve_cost", lambda *a: 0.007)
    monkeypatch.setattr("cvals.engine.submit", lambda *a: Submission({"total": 5}, 1, 1, 0.007))
    Engine(store, settings).run_experiment(create(store)["id"])
    # Emulate a workspace created before the spend-ledger schema was introduced.
    with store._connect(write=True) as connection:
        connection.execute("DELETE FROM spend_ledger")
    SpendLimits(settings).configure(monthly_usd=0.01)
    second = Engine(store, settings).run_experiment(create(store)["id"])
    assert second["trials"][0]["status"] == "budget_exceeded"


def test_frontier_excludes_controls_and_unknown_costs(workspace, monkeypatch):
    settings, store = workspace
    monkeypatch.setattr("cvals.engine.submit", lambda *a: Submission({"total": 5}, 1, 1, 0.007))
    result = Engine(store, settings).run_experiment(
        create(store, ["live", "fixture-reference"])["id"]
    )
    economics = analyze(result)
    assert economics["observed_frontier"] == ["live"]
    assert economics["systems"][1]["eligible_for_frontier"] is False
