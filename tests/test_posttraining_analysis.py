"""Controls for clustered inference, calibration, and held-out selection."""

from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from cvals.posttraining_analysis import analyze_regression
from cvals.posttraining_mix import evaluate_mix, select_mix
from cvals.posttraining_qualification import qualify_evaluation


def evidence(n=100, repeats=1, before=1.0, after=0.0):
    expected, baseline, candidate = [], [], []
    for family in range(n):
        for repetition in range(repeats):
            pair = {
                "scenario_id": f"task-{family}",
                "repetition": repetition,
                "cluster_id": f"family-{family}",
            }
            expected.append(pair)
            baseline.append({**pair, "score": before})
            candidate.append({**pair, "score": after})
    return {
        "expected_pairs": expected,
        "baseline": baseline,
        "candidate": candidate,
        "planned_looks": 1,
        "planned_domains": 1,
        "min_clusters": 2,
        "practical_threshold": 0.1,
    }


def test_known_bounded_regression_and_improvement():
    regression = analyze_regression(**evidence())
    improvement = analyze_regression(**evidence(before=0.0, after=1.0))
    assert regression["decision"] == "regression"
    assert regression["delta"] == -1
    assert improvement["decision"] == "improvement"
    assert improvement["interval"][0] > 0.1
    assert regression["radius"] == pytest.approx(math.sqrt(2 * math.log(40) / 100))


def test_repeated_identical_measurements_do_not_create_independent_samples():
    once = analyze_regression(**evidence(n=100, repeats=1))
    repeated = analyze_regression(**evidence(n=100, repeats=20))
    assert once["interval"] == repeated["interval"]
    assert once["clusters"] == repeated["clusters"] == 100
    assert repeated["matched_pairs"] == 2000


def test_equal_scenario_weight_despite_repetition_count():
    args = evidence(n=2)
    for repetition in range(10):
        pair = {"scenario_id": "extra", "repetition": repetition, "cluster_id": "family-0"}
        args["expected_pairs"].append(pair)
        args["baseline"].append({**pair, "score": 0.0})
        args["candidate"].append({**pair, "score": 1.0})
    result = analyze_regression(**args)
    assert result["delta"] == -0.5
    assert result["clusters"] == 2


def test_null_is_not_equivalence_without_precision():
    result = analyze_regression(**evidence(n=2, before=0.0, after=0.0))
    assert result["decision"] == "inconclusive"
    assert result["interval"] == [-1, 1]
    assert (
        analyze_regression(**evidence(n=1000, before=0.5, after=0.5))["decision"]
        == "no_material_change"
    )
    assert (
        analyze_regression(**evidence(n=1000, before=0.5, after=0.4))["decision"] == "inconclusive"
    )


def test_planned_multiplicity_and_minimum_families():
    args = evidence()
    initial = analyze_regression(**args)
    args.update(planned_looks=10, planned_domains=5)
    corrected = analyze_regression(**args)
    assert corrected["comparison_alpha"] == 0.001
    assert corrected["radius"] > initial["radius"]
    assert analyze_regression(**{**args, "look_index": 11})["decision"] == "invalid"
    assert analyze_regression(**{**args, "min_clusters": 101})["decision"] == "inconclusive"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "extra",
        "failed",
        "nan",
        "null",
        "family",
        "bool",
        "infinity",
        "range",
    ],
)
def test_invalid_evidence_never_establishes_regression(change):
    args = evidence()
    rows = args["candidate"]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "extra":
        rows.append({"scenario_id": "unplanned", "repetition": 0, "score": 0.0})
    elif change == "failed":
        rows[0]["status"] = "infrastructure_error"
    elif change == "family":
        rows[0]["cluster_id"] = "other"
    else:
        rows[0]["score"] = {
            "nan": float("nan"),
            "null": None,
            "bool": True,
            "infinity": float("inf"),
            "range": -0.1,
        }[change]
    result = analyze_regression(**args)
    assert result["decision"] == "invalid"
    assert result["interval"] is None
    assert result["reasons"]


def test_manifest_rejects_duplicates_and_conflicting_families():
    args = evidence()
    args["expected_pairs"].append(args["expected_pairs"][0])
    with pytest.raises(ValueError, match="duplicate"):
        analyze_regression(**args)
    args["expected_pairs"][-1] = {
        "scenario_id": "task-0",
        "repetition": 1,
        "cluster_id": "different",
    }
    with pytest.raises(ValueError, match="multiple"):
        analyze_regression(**args)


def qualification():
    return {
        "measurement_claim": "Detect whether an answer satisfies a frozen rubric.",
        "owner": "evaluation-owner",
        "population": "Reviewed internal questions",
        "limitations": "Reference labels may be disputed; small declared sample.",
        "task_version": "tasks-v1",
        "grader_version": "grader-v1",
        "semantic_rubric": "The answer must establish the requested behavior.",
        "adjudication_limitations": "Reviewers may share biases; adjudication is not truth.",
        "controls": [
            {"id": "positive", "kind": "positive", "expected_pass": True, "observed_pass": True},
            {"id": "negative", "kind": "negative", "expected_pass": False, "observed_pass": False},
            {"id": "mutation", "kind": "mutation", "expected_pass": False, "observed_pass": False},
        ],
        "repeated_grades": [{"item_id": f"repeat-{i}", "grades": [True, True]} for i in range(5)],
        "human_judgments": [
            {
                "item_id": f"human-{i}",
                "grader_pass": i % 2 == 0,
                "human_pass": i % 2 == 0,
                "blinded": True,
                "adjudicated": False,
            }
            for i in range(10)
        ],
        "saturation_scores": [0.0, 1.0, 0.5],
    }


def test_complete_evidence_is_only_ready_for_review():
    result = qualify_evaluation(qualification())
    assert result["status"] == "ready_for_review"
    assert result["automatic_trust"] is False
    assert result["gaps"] == []


def test_always_positive_grader_is_detected_despite_repeatability():
    value = qualification()
    for row in value["human_judgments"]:
        row["grader_pass"] = True
        row["adjudicated"] = True
    result = qualify_evaluation(value)
    assert result["repeatability"]["all_repetitions_agree_fraction"] == 1
    assert result["calibration"]["false_positives"] == 5
    assert result["calibration"]["specificity"] == 0
    assert result["status"] == "gaps_found"


def test_one_class_agreement_does_not_qualify():
    value = qualification()
    value["human_judgments"] = [row for row in value["human_judgments"] if row["human_pass"]]
    result = qualify_evaluation(value)
    assert result["calibration"]["agreement"] == 1
    assert result["calibration"]["specificity"] is None
    assert any("negative examples" in gap for gap in result["gaps"])


def test_unblinded_unadjudicated_and_semantic_gaps_are_retained():
    value = qualification()
    value["semantic_rubric"] = ""
    value["human_judgments"][0].update(blinded=False, grader_pass=False)
    result = qualify_evaluation(value)
    assert result["calibration"]["unblinded_items"] == 1
    assert result["calibration"]["unadjudicated_disagreements"] == 1
    assert len(result["gaps"]) >= 4


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_floor_and_ceiling_saturation(score):
    value = qualification()
    value["saturation_scores"] = [score] * 20
    result = qualify_evaluation(value)
    assert result["saturation"]["flagged"] is True
    assert result["status"] == "gaps_found"


def test_duplicates_cannot_inflate_calibration_sample():
    value = qualification()
    value["human_judgments"].append(value["human_judgments"][0])
    with pytest.raises(ValueError, match="duplicate"):
        qualify_evaluation(value)


def test_failed_controls_and_nondeterminism_are_gaps():
    value = qualification()
    value["controls"][1]["observed_pass"] = True
    value["controls"].pop()
    value["repeated_grades"][0]["grades"] = [True, False]
    result = qualify_evaluation(value)
    assert result["controls"]["failed_ids"] == ["negative"]
    assert result["repeatability"]["inconsistent_items"] == 1
    assert result["status"] == "gaps_found"


def mix_rows(split="development"):
    return [
        {
            "checkpoint_id": f"{split}-{step}",
            "task_family": f"{split}-{domain}",
            "eval_id": evaluation,
            "domain": domain,
            "cost_usd": cost,
            "delta": -0.5 if step == 1 else 0.0,
        }
        for step in range(3)
        for evaluation, domain, cost in [
            ("reasoning-a", "reasoning", 1.0),
            ("reasoning-b", "reasoning", 2.0),
            ("safety-a", "safety", 1.0),
        ]
    ]


def mix_config(**kwargs):
    return {"budget_usd": 3.0, "protected_domains": ["safety"], "seed": 7, **kwargs}


def test_frozen_mix_deterministic_and_protected():
    result = select_mix(mix_rows(), mix_config())
    assert result == select_mix(list(reversed(mix_rows())), mix_config())
    assert "safety-a" in result["strategies"]["selected"]
    assert result["matched_budget_usd"] <= 3.0
    assert (
        result["strategy_development_cost_usd"]["equal_budget_random"]
        <= result["matched_budget_usd"]
    )
    heldout = evaluate_mix(result, mix_rows("validation"))
    assert heldout["strategies"]["selected"]["detection_recall"] == 1
    assert heldout["strategies"]["selected"]["false_alert_rate"] == 0
    assert heldout["strategies"]["selected"]["mean_absolute_delta_error"] == 0


def test_mix_requires_complete_development_cost_and_cells():
    rows = mix_rows()
    rows[0]["cost_usd"] = None
    with pytest.raises(ValueError, match="complete"):
        select_mix(rows, mix_config())
    rows = mix_rows()
    rows.pop()
    with pytest.raises(ValueError, match="incomplete"):
        select_mix(rows, mix_config())
    with pytest.raises(ValueError, match="protected"):
        select_mix(mix_rows(), mix_config(budget_usd=0.5))


@pytest.mark.parametrize("field", ["checkpoint_id", "task_family"])
def test_checkpoint_and_family_leakage_rejected(field):
    result = select_mix(mix_rows(), mix_config())
    validation = mix_rows("validation")
    validation[0][field] = mix_rows()[0][field]
    with pytest.raises(ValueError, match="overlap"):
        evaluate_mix(result, validation)


def test_frozen_configuration_tampering_rejected():
    result = select_mix(mix_rows(), mix_config())
    result["config"]["practical_threshold"] = 0.5
    with pytest.raises(ValueError, match="integrity"):
        evaluate_mix(result, mix_rows("validation"))


def test_heldout_never_changes_selection_and_unknowns_stay_unknown():
    result = select_mix(mix_rows(), mix_config())
    frozen = copy.deepcopy(result)
    validation = mix_rows("validation")
    validation[0]["delta"] = None
    validation[0]["cost_usd"] = None
    heldout = evaluate_mix(result, validation)
    assert result == frozen
    full = heldout["strategies"]["full_suite"]
    assert full["unknown_cells"] == 1
    assert full["cost_usd"] is None
    assert full["known_cost_lower_bound_usd"] == 11.0


def test_domain_change_and_missing_full_suite_rejected():
    result = select_mix(mix_rows(), mix_config())
    validation = mix_rows("validation")
    validation[0]["domain"] = "invented"
    with pytest.raises(ValueError, match="domain"):
        evaluate_mix(result, validation)
    validation = [row for row in mix_rows("validation") if row["eval_id"] != "safety-a"]
    with pytest.raises(ValueError, match="full-suite"):
        evaluate_mix(result, validation)


def test_missed_heldout_regression_is_exposed():
    result = select_mix(mix_rows(), mix_config())
    assert "reasoning-b" not in result["strategies"]["selected"]
    validation = mix_rows("validation")
    for row in validation:
        row["delta"] = -1.0 if row["eval_id"] == "reasoning-b" else 0.0
    selected = evaluate_mix(result, validation)["strategies"]["selected"]
    assert selected["reference_regressions"] == 3
    assert selected["detected_regressions"] == 0
    assert selected["detection_recall"] == 0
    assert selected["mean_absolute_delta_error"] == 0.25


def test_zero_regressions_gives_unknown_recall():
    result = select_mix(mix_rows(), mix_config())
    validation = mix_rows("validation")
    for row in validation:
        row["delta"] = 0.0
    assert evaluate_mix(result, validation)["strategies"]["selected"]["detection_recall"] is None


def test_nonfinite_mix_and_calibration_inputs_rejected():
    rows = mix_rows()
    rows[0]["delta"] = float("nan")
    with pytest.raises(ValidationError):
        select_mix(rows, mix_config())
    value = qualification()
    value["saturation_scores"] = [float("inf")]
    with pytest.raises(ValidationError):
        qualify_evaluation(value)
