"""Decision semantics under changed cohorts, incomplete evidence, and controls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from cvals.comparison import compare_experiments
from cvals.models import content_hash
from cvals.policies import evaluate_gates


def experiment(
    identifier: str,
    *,
    tasks: int = 30,
    passed: bool = True,
    repetitions: int = 1,
    kind: str = "http",
) -> dict[str, Any]:
    systems = [{"id": "system", "name": "Measured system", "kind": kind, "model": "model-1"}]
    collection = {
        "id": "collection",
        "version": "1",
        "scenarios": [
            {"id": f"task-{i}", "domain": "data", "instruction": "Complete the recorded task"}
            for i in range(tasks)
        ],
    }
    snapshot = {
        "schema_version": 1,
        "collection": collection,
        "systems": systems,
        "repetitions": repetitions,
        "budget_usd": 10,
        "grader_revision": "grader/1",
        "harness_revision": "harness/1",
        "protocol": "all scheduled attempts",
        "sandbox_image_requested": "image",
    }
    trials = [
        {
            "id": f"{identifier}-{i}-{repetition}",
            "system_id": "system",
            "scenario_id": f"task-{i}",
            "domain": "data",
            "repetition": repetition,
            "status": "completed",
            "passed": passed,
            "score": int(passed),
            "duration_ms": 100,
            "cost_usd": 0.01,
            "checks": [],
        }
        for i in range(tasks)
        for repetition in range(1, repetitions + 1)
    ]
    return {
        "id": identifier,
        "status": "completed",
        "snapshot": snapshot,
        "snapshot_hash": content_hash(snapshot),
        "collection_hash": content_hash(collection),
        "systems": systems,
        "total_trials": len(trials),
        "trials": trials,
    }


def reseal(value: dict[str, Any]) -> None:
    value["snapshot_hash"] = content_hash(value["snapshot"])
    value["collection_hash"] = content_hash(value["snapshot"]["collection"])


def test_changed_collection_and_grader_never_produce_performance_conclusion() -> None:
    left, right = experiment("left"), experiment("right", passed=False)
    right["snapshot"]["collection"]["scenarios"][0]["instruction"] = "A different task"
    reseal(right)
    result = compare_experiments(left, right)
    assert not result["compatible"] and not result["comparisons"]
    assert "collection fingerprints differ" in result["incompatibilities"][0]
    right = experiment("right")
    right["snapshot"]["grader_revision"] = "grader/2"
    reseal(right)
    assert not compare_experiments(left, right)["compatible"]


def test_missing_or_duplicate_trial_slots_cannot_shrink_denominators() -> None:
    right = experiment("right")
    right["trials"].pop()
    assert not compare_experiments(experiment("left"), right)["compatible"]
    assert evaluate_gates(right)["status"] == "inconclusive"
    right = experiment("right")
    right["trials"][0] = deepcopy(right["trials"][1])
    assert not compare_experiments(experiment("left"), right)["compatible"]


def test_repetitions_do_not_masquerade_as_independent_tasks() -> None:
    one = compare_experiments(
        experiment("left", tasks=4), experiment("right", tasks=4, passed=False)
    )["comparisons"][0]
    many = compare_experiments(
        experiment("left", tasks=4, repetitions=20),
        experiment("right", tasks=4, passed=False, repetitions=20),
    )["comparisons"][0]
    assert many["paired_tasks"] == one["paired_tasks"] == 4
    assert (one["ci_low"], one["ci_high"]) == (many["ci_low"], many["ci_high"])
    assert one["difference"] == -1


def test_control_and_replay_results_do_not_become_model_readiness() -> None:
    for kind in ("reference", "incomplete", "replay"):
        result = evaluate_gates(experiment("control", kind=kind))
        assert result["status"] == "inconclusive"
        assert not result["systems"] and len(result["excluded_systems"]) == 1
        pair = compare_experiments(experiment("left", kind=kind), experiment("right", kind=kind))[
            "comparisons"
        ][0]
        assert not pair["inference_supported"]
    same = experiment("same")
    assert not compare_experiments(same, same)["comparisons"][0]["inference_supported"]


def test_unknown_cost_and_duration_cannot_pass_dependent_gates() -> None:
    run = experiment("run")
    run["trials"][0]["cost_usd"] = None
    run["trials"][0]["duration_ms"] = None
    result = evaluate_gates(run, {"max_cost_usd": 10, "max_p95_duration_ms": 200})
    checks = {c["metric"]: c for c in result["systems"][0]["checks"]}
    assert checks["cost_usd"]["observed"] is None
    assert checks["cost_usd"]["status"] == checks["p95_duration_ms"]["status"] == "inconclusive"
    assert result["status"] == "inconclusive"
    exceeded = evaluate_gates(run, {"max_cost_usd": 0.1})
    assert exceeded["status"] == "failed"  # Known cost alone already violates this cap.


def test_pending_trials_cannot_pass_and_errors_remain_in_denominators() -> None:
    run = experiment("run")
    run["trials"][0].update(status="running", passed=None, score=None, cost_usd=None)
    result = evaluate_gates(run, {"min_pass_rate": 0.8})
    assert result["status"] == "inconclusive"
    run["trials"][0].update(status="error")
    result = evaluate_gates(run, {"min_pass_rate": 0.8})
    assert result["status"] == "failed"
    checks = {c["metric"]: c for c in result["systems"][0]["checks"]}
    assert checks["pass_rate"]["observed"] == 29 / 30
    assert checks["completion_rate"]["observed"] == 29 / 30
    assert checks["error_rate"]["observed"] == 1 / 30


def test_regression_gate_distinguishes_failure_uncertainty_and_noninferiority() -> None:
    policy = {"max_pass_rate_regression": 0.1}
    assert (
        evaluate_gates(experiment("right", passed=False), policy, experiment("left"))["status"]
        == "failed"
    )
    assert (
        evaluate_gates(experiment("right"), policy, experiment("left"))["status"] == "inconclusive"
    )
    assert (
        evaluate_gates(experiment("right", tasks=1000), policy, experiment("left", tasks=1000))[
            "status"
        ]
        == "passed"
    )
    assert evaluate_gates(experiment("right"), policy)["status"] == "inconclusive"


def test_default_policy_does_not_invent_a_quality_target_and_thresholds_are_strict() -> None:
    result = evaluate_gates(experiment("run", passed=False))
    assert result["status"] == "passed"  # Execution readiness is distinct from quality.
    assert result["policy"]["min_pass_rate"] is None
    assert result["policy_hash"] == content_hash(result["policy"])
    for policy in [
        {"max_cost_usd": float("nan")},
        {"min_pass_rate": True},
        {"mistyped_threshold": 0.8},
    ]:
        with pytest.raises(ValidationError):
            evaluate_gates(experiment("run"), policy)


def test_system_change_is_explicit_but_names_are_never_used_to_pair_runs() -> None:
    left, right = experiment("left"), experiment("right")
    right["systems"][0]["model"] = "model-2"
    reseal(right)
    result = compare_experiments(left, right)
    assert result["compatible"] and result["comparisons"][0]["configuration_changed"]
    right["systems"][0]["id"] = "different-identity"
    for trial in right["trials"]:
        trial["system_id"] = "different-identity"
    reseal(right)
    result = compare_experiments(left, right)
    assert result["compatible"] and not result["comparisons"]
    assert result["unmatched_right_systems"] == ["different-identity"]


def test_saved_policy_versions_and_decisions_are_immutable_and_audited(tmp_path):
    import sqlite3

    from cvals.audit import AuditLog
    from cvals.config import Settings
    from cvals.models import ExperimentCreate
    from cvals.packs import starter_collections
    from cvals.policies import PolicyStore
    from cvals.store import Store

    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    collection = starter_collections()[1]
    store.save_collection(collection)
    run = store.create_experiment(
        ExperimentCreate(
            name="Recorded gate decision",
            collection_id=collection.id,
            system_ids=["fixture-reference"],
        )
    )
    policies = PolicyStore(settings)
    saved = policies.save({"name": "Release readiness", "version": "1", "min_pass_rate": 0.9})
    assert policies.save(saved["policy"])["id"] == saved["id"]
    with pytest.raises(ValueError, match="new version"):
        policies.save({"name": "Release readiness", "version": "1", "min_pass_rate": 0.8})
    decision = policies.evaluate(run, policy_id=saved["id"])
    assert decision["status"] == "inconclusive"
    assert policies.decisions(run["id"])[0] == decision
    assert policies.get(saved["id"]) == saved
    with (
        store._connect(write=True) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute("DELETE FROM gate_decisions")
    actions = {row["action"] for row in AuditLog(settings).list()}
    assert {"policy.version_saved", "experiment.gates_evaluated"} <= actions
    forged = deepcopy(run)
    forged["trials"][0]["passed"] = True
    with pytest.raises(ValueError, match="differs from this workspace"):
        policies.evaluate(forged)
