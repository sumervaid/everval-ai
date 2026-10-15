from pathlib import Path

import pytest

from cvals.budgeting import BudgetPlanner
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System
from cvals.spend_limits import SpendLimits
from cvals.store import Store


def baseline(path: Path) -> tuple[Settings, Store, str]:
    settings = Settings(data_dir=path)
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    collection = Collection(
        name="Budget controls",
        scenarios=[
            Scenario(
                name="Simple",
                domain="data",
                instruction="Return a value",
                reference_output={"value": 1},
                checks=[Check(name="Value", kind="equals", path="value", expected=1)],
            )
        ],
    )
    store.save_collection(collection)
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Controls", collection_id=collection.id, system_ids=["fixture-reference"]
        )
    )
    Engine(store, settings).run_pending_once()
    return settings, store, experiment["id"]


def test_controls_are_never_a_live_cost_projection(tmp_path: Path) -> None:
    settings, _, identifier = baseline(tmp_path)
    result = BudgetPlanner(settings).project(identifier)
    assert result["expected_cost_usd"] is None
    assert result["full"]["uses_installation_controls_or_replay"] is True
    assert result["all_escalations_admission_budget_usd"] == 0
    assert BudgetPlanner(settings).status()["external_service_cost_usd"] is None


def test_forecast_uses_frozen_prices_and_known_live_attempts(tmp_path: Path) -> None:
    settings, store, identifier = baseline(tmp_path)
    # Retain a complete synthetic accounting observation without making an HTTP call.
    from cvals.models import canonical_json, content_hash

    with store._connect(write=True) as connection:
        snapshot, state = store._read_experiment(connection, identifier)
        system = System(
            id="fixture-reference",
            name="Observed local test double",
            kind="http",
            base_url="http://127.0.0.1:9",
            model="test",
            input_cost_per_million=1,
            output_cost_per_million=2,
            pricing_version="test-rates",
        )
        snapshot["systems"] = [system.model_dump(mode="json")]
        snapshot["budget_usd"] = state["budget_usd"] = 1
        state["snapshot_hash"] = content_hash(snapshot)
        connection.execute(
            "UPDATE experiments SET snapshot=?,snapshot_hash=?,state=? WHERE id=?",
            (canonical_json(snapshot), content_hash(snapshot), canonical_json(state), identifier),
        )
        row = connection.execute(
            "SELECT data FROM trials WHERE experiment_id=?", (identifier,)
        ).fetchone()
        import json

        trial = json.loads(row[0])
        trial.update(cost_usd=0.25, duration_ms=200)
        store._save_trial(connection, trial)
    SpendLimits(settings).configure(lifetime_usd=2)
    result = BudgetPlanner(settings).project(identifier, runs=3)
    assert result["expected_cost_usd"] == 0.75
    assert result["expected_sequential_attempt_seconds"] == pytest.approx(0.6)
    assert result["available_budget_now_usd"] == 1.75
    assert result["fully_reserved_cycles_affordable_now"] == 1
    assert result["full"]["pricing"][0]["version"] == "test-rates"


@pytest.mark.parametrize(
    "values",
    [
        {"runs": True},
        {"runs": 0},
        {"escalation_rate": float("nan")},
        {"cap_usd": float("inf")},
        {"cap_usd": -1},
        {"cap_usd": True},
        {"escalation_rate": 0.5},
    ],
)
def test_invalid_projection_never_admits_work(tmp_path: Path, values: dict) -> None:
    settings, store, identifier = baseline(tmp_path)
    with pytest.raises(ValueError):
        BudgetPlanner(settings).project(identifier, **values)
    assert len(store.list_experiments()) == 1


def test_projection_respects_project_cap(tmp_path: Path, monkeypatch) -> None:
    settings, _, identifier = baseline(tmp_path)
    planner = BudgetPlanner(settings)
    basis = planner._basis(identifier)
    basis.update(budget_scope="project:example", per_run_admission_budget_usd=2)
    monkeypatch.setattr(planner, "_basis", lambda _: basis)
    monkeypatch.setattr(
        planner,
        "status",
        lambda: {
            "unsettled_reservations": 0,
            "limits": [
                {"scope": "workspace", "lifetime_usd": 200, "usage": {"lifetime_usd": 0}},
                {"scope": "project:example", "lifetime_usd": 5, "usage": {"lifetime_usd": 2}},
                {"scope": "project:unrelated", "lifetime_usd": 0, "usage": {"lifetime_usd": 0}},
            ],
        },
    )
    result = planner.project(identifier, runs=30)
    assert result["available_budget_now_usd"] == 3
    assert result["fully_reserved_cycles_affordable_now"] == 1
    assert result["governing_scopes"] == ["project:example", "workspace"]
