"""Public comparison summaries retain failed work, costs and original denominators."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmarks.report import control_metrics, number, summarize  # noqa: E402


def outcome(
    resolved: bool = False,
    *,
    cost: str = "1",
    state: str | None = None,
    cost_complete: bool = True,
    duration: float | None = 10,
) -> dict:
    return {
        "resolved": resolved,
        "known_cost_usd": cost,
        "cost_complete": cost_complete,
        "duration_seconds": duration,
        "outcome": state or ("resolved" if resolved else "submitted_unresolved"),
        "provider_calls": 2,
        "known_input_tokens": 100,
        "known_output_tokens": 50,
    }


def row(baseline: dict, everval: dict, *, admitted: bool = True) -> dict:
    return {"admitted": admitted, "arms": {"baseline": baseline, "everval": everval}}


def test_cost_per_resolution_includes_failed_attempt_costs() -> None:
    report = summarize(
        [
            row(outcome(True, cost="2"), outcome(True, cost="3")),
            row(outcome(False, cost="4"), outcome(True, cost="5")),
        ]
    )
    baseline = report["arms"]["baseline"]
    everval = report["arms"]["everval"]
    assert Decimal(baseline["cost_per_resolved_usd"]) == Decimal("6")
    assert Decimal(everval["cost_per_resolved_usd"]) == Decimal("4")
    assert baseline["selected_resolution_rate"] == 0.5
    assert everval["selected_resolution_rate"] == 1.0
    assert report["paired"]["everval_only_resolved"] == 1
    assert report["paired"]["baseline_only_resolved"] == 0


def test_excluded_tasks_stay_in_selected_denominator() -> None:
    report = summarize(
        [
            row(outcome(True), outcome(True)),
            row(
                outcome(state="excluded", cost="0", duration=None),
                outcome(state="excluded", cost="0", duration=None),
                admitted=False,
            ),
        ]
    )
    assert report["complete"] is True
    for arm in report["arms"].values():
        assert arm["selected_tasks"] == 2
        assert arm["admitted_tasks"] == 1
        assert arm["selected_resolution_rate"] == 0.5
        assert arm["admitted_resolution_rate"] == 1.0
        assert arm["latency_observations"] == 1
        assert arm["outcome_counts"] == {"excluded": 1, "resolved": 1}


def test_unknown_cost_does_not_become_a_price_per_success() -> None:
    report = summarize(
        [
            row(outcome(True), outcome(True)),
            row(outcome(cost="0.1", cost_complete=False), outcome()),
        ]
    )
    assert report["arms"]["baseline"]["cost_complete"] is False
    assert report["arms"]["baseline"]["cost_per_resolved_usd"] is None
    assert Decimal(report["arms"]["baseline"]["known_cost_usd"]) == Decimal("1.1")


def test_unfinished_pair_is_not_a_final_comparison() -> None:
    report = summarize(
        [
            row(outcome(True), outcome(state="not_run", cost="0", duration=None)),
        ]
    )
    assert report["complete"] is False
    assert report["paired"]["comparison_final"] is False
    assert report["arms"]["everval"]["not_run"] == 1
    assert report["paired"]["completed_pairs"] == 0
    assert report["paired"]["pending_pairs"] == 1
    assert report["paired"]["baseline_only_resolved"] == 0
    assert report["paired"]["neither_resolved"] == 0
    assert report["arms"]["everval"]["cost_per_resolved_usd"] is None


def test_a_loss_and_win_remain_distinct_instead_of_averaging_them_away() -> None:
    report = summarize(
        [
            row(outcome(True), outcome(False)),
            row(outcome(False), outcome(True)),
            row(outcome(False), outcome(False)),
        ]
    )
    assert report["paired"] == {
        "admitted_pairs": 3,
        "completed_pairs": 3,
        "pending_pairs": 0,
        "everval_only_resolved": 1,
        "baseline_only_resolved": 1,
        "both_resolved": 0,
        "neither_resolved": 1,
        "resolution_difference": 0,
        "comparison_final": True,
    }


def test_invalid_or_missing_latency_is_unknown() -> None:
    for value in (None, True, -1, float("nan"), float("inf"), "10"):
        assert number(value) is None
    assert number(0) == 0.0
    assert number(1.25) == 1.25


def test_control_export_preserves_required_counts_without_hidden_material() -> None:
    task = {"FAIL_TO_PASS": ["private_bug_case"], "PASS_TO_PASS": ["private_regression"]}
    control = {
        "valid": True,
        "base": {
            "test_statuses": {
                "private_bug_case": "FAILED",
                "private_regression": "PASSED",
                "private_additional_case": "ERROR",
            },
            "status": "infrastructure_error",
            "setup_error_signals": ["private installer detail"],
            "all_required_tests_observed": True,
            "all_required_tests_passed": False,
            "cleanup_confirmed": True,
            "test_suite_exit_code": 1,
            "log": "private evaluator content",
        },
        "gold": {
            "test_statuses": {"private_bug_case": "PASSED", "private_regression": "PASSED"},
            "all_required_tests_observed": True,
            "all_required_tests_passed": True,
            "cleanup_confirmed": True,
            "test_suite_exit_code": 0,
        },
    }
    result = control_metrics(control, task)
    assert result["valid"] is True
    assert result["required_counts"] == {"fail_to_pass": 1, "pass_to_pass": 1}
    assert result["base"]["required_status_counts"] == {
        "fail_to_pass": {"FAILED": 1},
        "pass_to_pass": {"PASSED": 1},
    }
    assert result["gold"]["required_status_counts"] == {
        "fail_to_pass": {"PASSED": 1},
        "pass_to_pass": {"PASSED": 1},
    }
    assert result["base"]["status"] == "infrastructure_error"
    assert result["base"]["setup_error_detected"] is True
    assert result["base"]["observed_status_counts"] == {"ERROR": 1, "FAILED": 1, "PASSED": 1}
    assert result["base"]["test_suite_exit_code"] == 1
    assert result["gold"]["test_suite_exit_code"] == 0
    assert "private" not in json.dumps(result)


def test_control_export_retains_missing_and_unknown_cases_as_counts() -> None:
    task = {"FAIL_TO_PASS": ["unobserved"], "PASS_TO_PASS": ["unexpected_status"]}
    result = control_metrics(
        {"valid": False, "base": {"test_statuses": {"unexpected_status": "private text"}}}, task
    )
    assert result["valid"] is False
    assert result["base"]["required_status_counts"] == {
        "fail_to_pass": {"MISSING": 1},
        "pass_to_pass": {"UNKNOWN": 1},
    }
    assert result["base"]["cleanup_confirmed"] is False
    assert result["base"]["observed_status_counts"] == {"UNKNOWN": 1}
    assert result["base"]["setup_error_detected"] is None
    assert result["gold"] is None
    assert "private" not in json.dumps(result)
