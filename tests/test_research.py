"""Research safety, frozen evidence, budget admission and holdout lifecycle."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System, canonical_json
from cvals.optimization import Optimizer
from cvals.providers import Submission
from cvals.research import ResearchLab
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    for partition, target in (("dev", 5), ("holdout", 7)):
        store.save_collection(
            Collection(
                id=partition,
                name=partition,
                benchmark_partition=partition,
                scenarios=[
                    Scenario(
                        id=partition + "-sum",
                        name="Sum",
                        domain="data",
                        instruction="Return the target total.",
                        inputs={"target": target},
                        checks=[
                            Check(
                                id="total-check",
                                name="Total",
                                kind="numeric",
                                path="total",
                                expected=target,
                            )
                        ],
                        reference_output={"total": target},
                    )
                ],
            )
        )
    for identity, rate in (("cheap", 1), ("other", 2)):
        store.save_system(
            System(
                id=identity,
                name=identity,
                kind="http",
                model="test",
                base_url="http://127.0.0.1:9910",
                input_cost_per_million=rate,
                output_cost_per_million=rate,
            )
        )
    store.ensure_fixture_systems()
    return settings, store


def study(workspace, **kwargs):
    settings, _ = workspace
    return Optimizer(settings).create(
        "Finite selection", "dev", "holdout", ["cheap", "other"], budget_usd=1, **kwargs
    )


def mock_provider(monkeypatch, *, wrong=False):
    calls = []

    def submit(scenario, system, *_args):
        calls.append((scenario.id, system.id, system.model))
        return Submission(
            {
                "total": 0 if wrong else scenario.inputs["target"],
                "irrelevant": "lots of extra material",
            },
            1,
            1,
            0.001 if system.id == "cheap" else 0.002,
        )

    monkeypatch.setattr("cvals.engine.submit", submit)
    return calls


def run_trial(workspace, monkeypatch, *, wrong=False):
    settings, store = workspace
    calls = mock_provider(monkeypatch, wrong=wrong)
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Recorded observation",
            collection_id="dev",
            system_ids=["cheap"],
            budget_usd=1,
        )
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    return result, calls


def test_repeated_grades_leave_original_and_never_resubmit(workspace, monkeypatch):
    settings, store = workspace
    result, calls = run_trial(workspace, monkeypatch, wrong=True)
    original = copy.deepcopy(store.get_experiment(result["id"]))
    lab = ResearchLab(settings)
    record = lab.repeat_regrade(result["id"], result["trials"][0]["id"], 3)
    assert record["status"] == "completed"
    assert record["conclusion"] == "no_disagreement_observed"
    assert record["valid_repetitions"] == 3
    assert not record["uses_provider"]
    assert len(calls) == 1
    assert store.get_experiment(result["id"]) == original
    assert lab.get_record(record["id"]) == record


def test_regrade_disagreement_is_grading_evidence(workspace, monkeypatch):
    settings, _ = workspace
    result, _ = run_trial(workspace, monkeypatch, wrong=True)
    outcomes = iter([True, False])

    def regrade(*_args):
        passed = next(outcomes)
        return {
            "id": str(passed),
            "passed": passed,
            "error": None,
            "checks": [{"id": "x", "passed": passed, "message": str(passed)}],
        }

    monkeypatch.setattr("cvals.diagnostics.Diagnostics.regrade", regrade)
    record = ResearchLab(settings).repeat_regrade(result["id"], result["trials"][0]["id"], 2)
    assert record["grading_intermittency_observed"]
    assert "does not measure model nondeterminism" in record["interpretation"]


def test_minimization_preserves_failure_not_missing_path(workspace, monkeypatch):
    settings, store = workspace
    result, calls = run_trial(workspace, monkeypatch, wrong=True)
    record = ResearchLab(settings).minimize_output(result["id"], result["trials"][0]["id"])
    assert record["minimized_output"] == {"total": 0}
    assert record["locally_minimal_under_deletion_grammar"]
    assert record["minimized_bytes"] < record["original_bytes"]
    assert len(calls) == 1
    assert store.get_experiment(result["id"])["trials"][0]["output"]["irrelevant"]
    assert record["reproduction"]["private_material"]


def test_minimization_budget_and_success_rejection(workspace, monkeypatch):
    settings, _ = workspace
    result, _ = run_trial(workspace, monkeypatch, wrong=True)
    lab = ResearchLab(settings)
    record = lab.minimize_output(result["id"], result["trials"][0]["id"], 2)
    assert record["evaluation_count"] <= 2
    assert record["budget_exhausted"]
    result, _ = run_trial(workspace, monkeypatch)
    with pytest.raises(ValueError, match="reproducible failing"):
        lab.minimize_output(result["id"], result["trials"][0]["id"])


def test_executable_grading_is_rejected_by_reducer(workspace, monkeypatch):
    settings, store = workspace
    scenario = Scenario(
        id="code",
        name="Code",
        domain="coding",
        instruction="Repair",
        inputs={"files": {"a.py": "raise SystemExit"}},
        checks=[
            Check(
                name="Test", kind="python_tests", path="files", expected={"tests": "assert False"}
            )
        ],
    )
    store.save_collection(Collection(id="code", name="Code", scenarios=[scenario]))
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Recorded",
            collection_id="code",
            system_ids=["fixture-incomplete"],
        )
    )
    trial = experiment["trials"][0]
    with store._connect(write=True) as connection:
        trial.update(status="error", output={"files": {"a.py": "raise SystemExit"}})
        store._save_trial(connection, trial)

    def never(*args):
        pytest.fail("Code grader must not execute")

    monkeypatch.setattr("cvals.research.evaluate", never)
    with pytest.raises(ValueError, match="pure JSON"):
        ResearchLab(settings).minimize_output(experiment["id"], trial["id"])


def test_quality_detects_empty_acceptance_and_duplicates_without_providers(workspace, monkeypatch):
    settings, store = workspace
    a = Scenario(
        id="a",
        name="A",
        domain="data",
        instruction="Give any response.",
        checks=[Check(name="Weak", kind="json_schema", expected={})],
        reference_output={},
    )
    b = a.model_copy(update={"id": "b", "name": "Renamed"})
    store.save_collection(Collection(id="weak", name="Weak collection", scenarios=[a, b]))
    monkeypatch.setattr("cvals.engine.submit", lambda *_: pytest.fail("No paid calls"))
    record = ResearchLab(settings).quality("weak")
    assert record["duplicates"][0]["exact"]
    assert any(f["code"] == "empty_submission_accepted" for f in record["findings"])
    assert all(s["status"] == "completed" for s in record["scenarios"])
    assert record["status"] == "completed"


def test_record_tampering_fails_integrity(workspace):
    settings, store = workspace
    lab = ResearchLab(settings)
    record = lab.quality("dev")
    with store._connect(write=True) as connection:
        connection.execute(
            "UPDATE research_events SET hash='tampered' WHERE record_id=? AND sequence=1",
            (record["id"],),
        )
    with pytest.raises(ValueError, match="integrity"):
        lab.get_record(record["id"])


def test_optimizer_queues_one_frozen_holdout_and_review_proposal(workspace, monkeypatch):
    settings, store = workspace
    calls = mock_provider(monkeypatch)
    optimizer = Optimizer(settings)
    value = study(workspace)
    assert value["development_budget_usd"] + value["holdout_budget_usd"] <= value["budget_usd"]
    store.save_system(
        System(
            id="cheap",
            name="Changed after admission",
            kind="http",
            model="changed",
            base_url="http://127.0.0.1:9910",
            input_cost_per_million=9,
            output_cost_per_million=9,
        )
    )
    Engine(store, settings).run_experiment(value["development_experiment_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        advanced = list(pool.map(lambda _: optimizer.advance(value["id"]), range(2)))
    assert advanced[0]["holdout_experiment_id"] == advanced[1]["holdout_experiment_id"]
    value = advanced[0]
    assert value["selected_system_id"] == "cheap"
    assert len(store.list_experiments()) == 2
    holdout = Engine(store, settings).run_experiment(value["holdout_experiment_id"])
    assert holdout["systems"][0]["model"] == "test"
    assert holdout["system_ids"] == ["cheap"]
    value = optimizer.tick()[0]
    assert value["status"] == "ready_for_review"
    assert value["proposal"]["deployment_performed"] is False
    assert value["proposal"]["system"]["model"] == "test"
    assert value["known_cost_usd"] == pytest.approx(0.004)
    assert len(calls) == 3
    assert optimizer.advance(value["id"]) == value
    assert optimizer.tick() == []


def test_reserved_holdout_blocks_replay_and_renamed_task(workspace):
    settings, store = workspace
    value = study(workspace)
    for identity in ("holdout", "clone"):
        if identity == "clone":
            clone = store.get_collection("holdout")
            clone["id"] = "clone"
            clone["scenarios"][0]["id"] = "fresh-id"
            store.save_collection(Collection.model_validate(clone))
        with pytest.raises(ValueError, match="reserved or consumed"):
            store.create_experiment(
                ExperimentCreate(
                    name="Leak",
                    collection_id=identity,
                    system_ids=["fixture-reference"],
                )
            )
    with pytest.raises(ValueError, match="reserved or consumed"):
        study(workspace)
    assert Optimizer(settings).get(value["id"])["status"] == "development_queued"


def test_holdout_must_be_unused_in_entire_history(workspace):
    _, store = workspace
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Prior holdout",
            collection_id="holdout",
            system_ids=["fixture-reference"],
        )
    )
    store.cancel_experiment(experiment["id"])
    with pytest.raises(ValueError, match="already appeared"):
        study(workspace)


def test_overlap_and_unpartitioned_are_rejected(workspace):
    _, store = workspace
    value = store.get_collection("dev")
    value.update(id="overlap", benchmark_partition="holdout")
    value["scenarios"][0]["id"] = "different-id"
    store.save_collection(Collection.model_validate(value))
    with pytest.raises(ValueError, match="overlap"):
        Optimizer(workspace[0]).create("Overlap", "dev", "overlap", ["cheap"], 1)
    value.update(id="plain", benchmark_partition=None)
    store.save_collection(Collection.model_validate(value))
    with pytest.raises(ValueError, match="benchmark_partition=dev"):
        Optimizer(workspace[0]).create("Plain", "plain", "holdout", ["cheap"], 1)


def test_unknown_cost_blocks_final_holdout(workspace, monkeypatch):
    settings, store = workspace
    monkeypatch.setattr(
        "cvals.engine.submit",
        lambda *_: Submission(
            {"total": 5},
            error="Usage unavailable",
            metadata={"known_cost_usd": 0.02, "budget_uncertain": True},
        ),
    )
    value = study(workspace)
    Engine(store, settings).run_experiment(value["development_experiment_id"])
    value = Optimizer(settings).advance(value["id"])
    assert value["status"] == "blocked"
    assert value["known_cost_usd"] == 0.02
    assert not value["cost_complete"]
    assert value["holdout_experiment_id"] is None
    assert len(store.list_experiments()) == 1


def test_failing_dev_does_not_touch_holdout(workspace, monkeypatch):
    settings, store = workspace
    mock_provider(monkeypatch, wrong=True)
    value = study(workspace)
    Engine(store, settings).run_experiment(value["development_experiment_id"])
    value = Optimizer(settings).advance(value["id"])
    assert value["status"] == "rejected"
    assert value["holdout_experiment_id"] is None


def test_final_failure_never_retries_a_new_candidate(workspace, monkeypatch):
    settings, store = workspace
    mock_provider(monkeypatch)
    value = study(workspace)
    Engine(store, settings).run_experiment(value["development_experiment_id"])
    optimizer = Optimizer(settings)
    value = optimizer.advance(value["id"])
    mock_provider(monkeypatch, wrong=True)
    Engine(store, settings).run_experiment(value["holdout_experiment_id"])
    value = optimizer.advance(value["id"])
    assert value["status"] == "rejected"
    assert value["proposal"] is None
    assert optimizer.tick() == []
    with pytest.raises(ValueError, match="reserved or consumed"):
        store.rerun_experiment(value["holdout_experiment_id"])


def test_cancel_keeps_holdout_reserved_and_retains_work(workspace):
    settings, store = workspace
    value = study(workspace)
    cancelled = Optimizer(settings).cancel(value["id"])
    assert cancelled["status"] == "cancelled"
    assert store.get_experiment(value["development_experiment_id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="reserved or consumed"):
        study(workspace)


def test_only_explicit_live_candidates_admitted(workspace):
    settings, _ = workspace
    with pytest.raises(ValueError, match="live HTTP"):
        Optimizer(settings).create("Fixture", "dev", "holdout", ["fixture-reference"], 1)
    with pytest.raises(ValueError, match="finite"):
        Optimizer(settings).create("NaN", "dev", "holdout", ["cheap"], float("nan"))


def test_missing_timing_prevents_selection(workspace, monkeypatch):
    settings, store = workspace
    mock_provider(monkeypatch)
    value = study(workspace)
    result = Engine(store, settings).run_experiment(value["development_experiment_id"])
    with store._connect(write=True) as connection:
        for trial in result["trials"]:
            trial["duration_ms"] = None
            store._save_trial(connection, trial)
    value = Optimizer(settings).advance(value["id"])
    assert value["status"] == "rejected"
    assert value["holdout_experiment_id"] is None


def test_holdout_reservation_hash_is_checked(workspace):
    settings, store = workspace
    study(workspace)
    with store._connect(write=True) as connection:
        connection.execute(
            "UPDATE optimization_holdouts SET data=?", (canonical_json({"task_keys": []}),)
        )
    with pytest.raises(ValueError, match="integrity"):
        store.create_experiment(
            ExperimentCreate(
                name="Tampered",
                collection_id="holdout",
                system_ids=["fixture-reference"],
            )
        )


def test_projection_does_not_reserve_or_make_calls(workspace, monkeypatch):
    settings, store = workspace
    monkeypatch.setattr("cvals.engine.submit", lambda *_: pytest.fail("Projection makes no calls"))
    optimizer = Optimizer(settings)
    result = optimizer.project("dev", "holdout", ["cheap", "other"], 20, 2)
    assert result["development_budget_usd"] == 12
    assert result["holdout_budget_usd"] == 8
    assert result["expected_provider_bill_usd"] is None
    assert result["combined_reservation_estimate_usd"] > 0
    assert result["all_development_attempts_fit"]
    assert store.list_experiments() == []
    assert optimizer.list() == []
    value = study(workspace)
    assert value["projection"]["expected_provider_bill_usd"] is None


def test_development_task_holdout_markers_are_rejected(workspace):
    settings, store = workspace
    collection = store.get_collection("dev")
    collection["version"] = "2.0.0"
    collection["scenarios"][0]["tags"] = ["holdout"]
    store.save_collection(Collection.model_validate(collection))
    with pytest.raises(ValueError, match="marked as holdout"):
        study(workspace)


def test_regrade_errors_remain_inconclusive(workspace, monkeypatch):
    settings, _ = workspace
    result, _ = run_trial(workspace, monkeypatch, wrong=True)
    monkeypatch.setattr(
        "cvals.diagnostics.Diagnostics.regrade",
        lambda *_: {"id": "unavailable", "passed": None, "error": "Missing runtime", "checks": []},
    )
    record = ResearchLab(settings).repeat_regrade(result["id"], result["trials"][0]["id"], 2)
    assert record["conclusion"] == "inconclusive"
    assert record["infrastructure_errors"] == 2
    assert record["valid_repetitions"] == 0
    assert not record["grading_intermittency_observed"]


@pytest.mark.parametrize("phase", ["development", "holdout"])
def test_cancelled_phase_never_selects_or_proposes(workspace, monkeypatch, phase):
    settings, store = workspace
    mock_provider(monkeypatch)
    optimizer = Optimizer(settings)
    value = study(workspace)
    if phase == "holdout":
        Engine(store, settings).run_experiment(value["development_experiment_id"])
        value = optimizer.advance(value["id"])
    identifier = value[phase + "_experiment_id"]
    finish = store.finish_trial

    def cancel_after_first(*args):
        finish(*args)
        store.cancel_experiment(identifier)

    monkeypatch.setattr(store, "finish_trial", cancel_after_first)
    result = Engine(store, settings).run_experiment(identifier)
    assert result["status"] == "cancelled"
    assert any(t["passed"] is True for t in result["trials"])
    value = optimizer.advance(value["id"])
    assert value["status"] == "cancelled"
    assert value["proposal"] is None
    if phase == "development":
        assert value["holdout_experiment_id"] is None


def test_expensive_schema_is_rejected_before_research_grading(workspace, monkeypatch):
    settings, store = workspace
    collection = store.get_collection("dev")
    scenario = collection["scenarios"][0]
    scenario["checks"] = [
        Check(
            name="Expensive",
            kind="json_schema",
            path="total",
            expected={"type": "array", "uniqueItems": True},
        ).model_dump(mode="json")
    ]
    scenario["reference_output"] = {"total": [1, 2]}
    collection.update(id="schema", benchmark_partition=None)
    store.save_collection(Collection.model_validate(collection))
    monkeypatch.setattr(
        "cvals.research.evaluate", lambda *_: pytest.fail("Unbounded schema must not execute")
    )
    record = ResearchLab(settings).quality("schema")
    assert record["scenarios"][0]["status"] == "grading_error"
    assert "unbounded validators" in record["scenarios"][0]["error"]


def test_cancelled_study_reports_completed_and_late_settlement_costs(workspace, monkeypatch):
    settings, store = workspace
    mock_provider(monkeypatch)
    optimizer = Optimizer(settings)
    value = study(workspace)
    finish = store.finish_trial

    def cancel_after_charge(*args):
        finish(*args)
        optimizer.cancel(value["id"])

    monkeypatch.setattr(store, "finish_trial", cancel_after_charge)
    Engine(store, settings).run_experiment(value["development_experiment_id"])
    result = optimizer.get(value["id"])
    assert result["status"] == "cancelled"
    assert result["known_cost_usd"] == pytest.approx(0.001)
    assert result["cost_complete"]
    assert optimizer.list()[0]["known_cost_usd"] == pytest.approx(0.001)
    # A retained in-flight trial can settle after the controller's cancellation event.
    with store._connect(write=True) as connection:
        experiment = store.get_experiment(value["development_experiment_id"])
        trial = experiment["trials"][0]
        trial["cost_usd"] = 0.004
        store._save_trial(connection, trial)
    assert optimizer.get(value["id"])["known_cost_usd"] == pytest.approx(0.004)
    assert optimizer.advance(value["id"])["known_cost_usd"] == pytest.approx(0.004)
