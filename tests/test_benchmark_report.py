"""Public comparison summaries retain failed work, costs and original denominators."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmarks import campaign  # noqa: E402
from scripts.benchmarks import report as report_module
from scripts.benchmarks.campaign import attempt_identity  # noqa: E402
from scripts.benchmarks.report import (  # noqa: E402
    _build_report_locked,
    control_metrics,
    number,
    summarize,
)


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
        "grading_status": "graded" if state in {None, "resolved", "submitted_unresolved"} else None,
        "required_tests_observed": True
        if state in {None, "resolved", "submitted_unresolved"}
        else None,
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
        "quality_comparable_pairs": 3,
        "unevaluable_pairs": 0,
        "quality_comparison_complete": True,
        "resolution_outcome_scope": "quality_comparable_pairs",
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


@pytest.mark.parametrize("ungraded_arm", ["baseline", "everval"])
def test_unknown_usage_is_terminal_but_cannot_lose_a_quality_comparison(ungraded_arm) -> None:
    arms = {"baseline": outcome(True), "everval": outcome(True)}
    arms[ungraded_arm] = outcome(state="incomplete", cost="0", cost_complete=False, duration=90)
    report = summarize([row(**arms)])
    paired = report["paired"]
    assert report["complete"] is True
    assert paired["comparison_final"] is True
    assert paired["completed_pairs"] == 1
    assert paired["pending_pairs"] == 0
    assert paired["quality_comparable_pairs"] == 0
    assert paired["unevaluable_pairs"] == 1
    assert paired["quality_comparison_complete"] is False
    for key in (
        "everval_only_resolved",
        "baseline_only_resolved",
        "both_resolved",
        "neither_resolved",
        "resolution_difference",
    ):
        assert paired[key] == 0
    uncertain = report["arms"][ungraded_arm]
    assert uncertain["outcome_counts"] == {"incomplete": 1}
    assert uncertain["cost_complete"] is False
    assert uncertain["cost_per_resolved_usd"] is None


@pytest.mark.parametrize("arm", ["baseline", "everval"])
@pytest.mark.parametrize(
    ("grading_status", "observed"),
    [
        (None, True),
        ("infrastructure_error", True),
        ("invalid_patch", True),
        ("graded", False),
        ("graded", None),
        ("graded", 1),
        ("graded", "true"),
    ],
)
def test_missing_or_invalid_grading_cannot_establish_a_quality_pair(
    arm, grading_status, observed
) -> None:
    arms = {"baseline": outcome(True), "everval": outcome(True)}
    arms[arm] = {
        **outcome(False),
        "grading_status": grading_status,
        "required_tests_observed": observed,
    }
    paired = summarize([row(**arms)])["paired"]
    assert paired["completed_pairs"] == 1
    assert paired["quality_comparable_pairs"] == 0
    assert paired["unevaluable_pairs"] == 1
    assert paired["everval_only_resolved"] == paired["baseline_only_resolved"] == 0


@pytest.mark.parametrize("missing", ["grading_status", "required_tests_observed"])
def test_absent_grade_metadata_fails_closed(missing) -> None:
    baseline = outcome(False)
    baseline.pop(missing)
    paired = summarize([row(baseline, outcome(True))])["paired"]
    assert paired["quality_comparable_pairs"] == 0
    assert paired["unevaluable_pairs"] == 1
    assert paired["everval_only_resolved"] == 0


def test_quality_outcome_partition_distinguishes_pending_excluded_and_unevaluable() -> None:
    rows = [
        row(outcome(True), outcome(True)),
        row(outcome(True), outcome(False)),
        row(outcome(False), outcome(True)),
        row(outcome(False), outcome(False)),
        row(outcome(state="incomplete"), outcome(state="infrastructure_error")),
        row(outcome(True), outcome(state="not_run", duration=None)),
        row(
            outcome(state="excluded", duration=None),
            outcome(state="excluded", duration=None),
            admitted=False,
        ),
    ]
    report = summarize(rows)
    paired = report["paired"]
    assert report["complete"] is False
    assert paired == {
        "admitted_pairs": 6,
        "completed_pairs": 5,
        "pending_pairs": 1,
        "quality_comparable_pairs": 4,
        "unevaluable_pairs": 1,
        "quality_comparison_complete": False,
        "resolution_outcome_scope": "quality_comparable_pairs",
        "everval_only_resolved": 1,
        "baseline_only_resolved": 1,
        "both_resolved": 1,
        "neither_resolved": 1,
        "resolution_difference": 0,
        "comparison_final": False,
    }
    assert report["arms"]["baseline"]["selected_tasks"] == 7
    assert report["arms"]["baseline"]["admitted_tasks"] == 6
    assert report["arms"]["baseline"]["resolved"] == 3
    assert report["arms"]["baseline"]["selected_resolution_rate"] == 3 / 7


def test_valid_quality_comparison_does_not_imply_complete_costs() -> None:
    report = summarize(
        [row(outcome(False, cost="0.1", cost_complete=False), outcome(True, cost="0.2"))]
    )
    assert report["paired"]["quality_comparable_pairs"] == 1
    assert report["paired"]["quality_comparison_complete"] is True
    assert report["paired"]["everval_only_resolved"] == 1
    assert report["arms"]["baseline"]["cost_complete"] is False
    assert report["arms"]["baseline"]["known_cost_usd"] == "0.1"
    assert report["arms"]["baseline"]["cost_per_resolved_usd"] is None


def test_metadata_projection_preserves_unknown_reservation_without_a_quality_win(
    tmp_path: Path,
) -> None:
    plan_hash = "a" * 64
    plan = {
        "campaign_id": "original-reporting-fixture",
        "selection_sha256": "b" * 64,
        "model": {"model_name": "original-fixture"},
        "budget": {
            "pricing_version": "fixture",
            "input_rate_per_million": "2.50",
            "output_rate_per_million": "10",
        },
        "execution": {"step_limit": 3},
        "source_sha256": {},
        "tasks": [],
    }
    for index, admitted in enumerate((True, False)):
        identifier = f"original__fixture-{index}"
        task = {
            "repo": "original/fixture",
            "base_commit": "c" * 40,
            "FAIL_TO_PASS": ["private-required-case"],
            "PASS_TO_PASS": ["private-regression-case"],
        }
        task_path = tmp_path / f"task-{index}.json"
        task_path.write_text(json.dumps(task))
        control_path = tmp_path / f"control-{index}.json"
        control_path.write_text(json.dumps({"valid": admitted}))
        plan["tasks"].append(
            {
                "instance_id": identifier,
                "task_path": str(task_path),
                "control_path": str(control_path),
                "split": "test",
                "task_sha256": "d" * 64,
                "control_sha256": "e" * 64,
                "image_id": "sha256:" + "f" * 64,
                "admitted": admitted,
                "exclusion_reason": None if admitted else "control_contract_failed",
            }
        )
    status = {
        "opening_spend_usd": "0.50",
        "known_spend_usd": "1.25",
        "committed_spend_usd": "1.75",
        "pending_requests": 0,
        "uncertain_requests": 1,
        "cost_complete": False,
        "evidence_reconciled": True,
        "admission_ceiling_usd": "190",
        "user_ceiling_usd": "200",
        "attempts": [],
        "requests": [],
    }
    item = plan["tasks"][0]
    for arm in ("baseline", "everval"):
        attempt_id, attempt_hash = attempt_identity(plan_hash, item, arm)
        uncertain = arm == "baseline"
        grade = (
            None
            if uncertain
            else {
                "status": "graded",
                "resolved": True,
                "all_required_tests_observed": True,
                "all_required_tests_passed": True,
            }
        )
        result = {
            "instance_id": item["instance_id"],
            "split": "test",
            "arm": arm,
            "attempt_hash": attempt_hash,
            "status": "UnknownUsage" if uncertain else "Submitted",
            "submitted": not uncertain,
            "resolved": not uncertain,
            "grade": grade,
            "patch": "private-repair-content",
        }
        path = tmp_path / "attempts" / attempt_id / "result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(result))
        status["attempts"].append(
            {
                "attempt_id": attempt_id,
                "known_request_cost_usd": "0.25" if uncertain else "0.50",
                "uncertain_requests": int(uncertain),
            }
        )
        status["requests"].append(
            {"attempt_id": attempt_id, "input_tokens": 100, "output_tokens": 20}
        )
        if uncertain:
            status["requests"].append(
                {"attempt_id": attempt_id, "input_tokens": None, "output_tokens": None}
            )

    report = _build_report_locked(tmp_path, plan, plan_hash, status)
    assert report["schema_version"] == 2
    assert report["accounting"] == {
        key: value for key, value in status.items() if key not in {"attempts", "requests"}
    }
    baseline = report["tasks"][0]["arms"]["baseline"]
    assert baseline["exit_status"] == "UnknownUsage"
    assert baseline["outcome"] == "incomplete"
    assert baseline["grading_status"] is baseline["required_tests_observed"] is None
    assert baseline["known_cost_usd"] == "0.25"
    assert baseline["known_input_tokens"] == 100
    assert baseline["cost_complete"] is False
    assert baseline["provider_calls"] == 2
    paired = report["test"]["paired"]
    assert paired["completed_pairs"] == 1
    assert paired["quality_comparable_pairs"] == 0
    assert paired["unevaluable_pairs"] == 1
    assert paired["everval_only_resolved"] == 0
    assert report["test"]["arms"]["baseline"]["cost_complete"] is False
    assert report["test"]["arms"]["baseline"]["cost_per_resolved_usd"] is None
    assert report["test"]["arms"]["everval"]["selected_resolution_rate"] == 0.5
    assert report["test"]["arms"]["everval"]["admitted_resolution_rate"] == 1.0
    assert "private-" not in json.dumps(report)


@pytest.mark.parametrize("amended", [False, True])
def test_report_loading_preserves_frozen_identity_and_validates_recovery_under_lock(
    tmp_path: Path, monkeypatch, amended
) -> None:
    plan_path = tmp_path / "plan.json"
    amendment = tmp_path / "amendment.json" if amended else None
    frozen_sources = {"runner.py": "1" * 64, "unchanged.py": "2" * 64}
    plan = {"source_sha256": frozen_sources}
    plan_hash = "3" * 64
    context = object()
    ledger = object()
    active = False
    calls = []
    status = {
        "recovery_amendment_sha256": "4" * 64,
        "reviewed_uncertain_requests": 1,
        "unreviewed_uncertain_requests": 0,
        "bounded_reviewed_reservation_usd": "0.5",
        "quarantined_attempts": ["original__fixture.baseline"],
    }
    metadata = {
        "schema_version": 1,
        "kind": "bounded-accounting-recovery",
        "amendment_sha256": "4" * 64,
        "original_plan_sha256": "5" * 64,
        "original_plan_canonical_sha256": plan_hash,
        "source_overrides": {
            "runner.py": {"before": "1" * 64, "after": "6" * 64},
            "recovery.py": {"before": None, "after": "7" * 64},
        },
        "allowed_attempts": [
            {"attempt_id": "original__fixture.baseline", "attempt_hash": "8" * 64}
        ],
        "original_event_prefix": {"count": 3, "sha256": "9" * 64},
        "retained_attempt_count": 1,
    }
    projected = {
        "schema_version": 2,
        "source_sha256": frozen_sources.copy(),
        "accounting": {
            "known_spend_usd": "1.25",
            "committed_spend_usd": "1.75",
            "cost_complete": False,
        },
    }

    @contextmanager
    def locked(root):
        nonlocal active
        assert root == tmp_path
        active = True
        try:
            yield
        finally:
            active = False

    def verify(path, **kwargs):
        assert path == plan_path
        assert kwargs == ({"amendment": amendment} if amended else {})
        calls.append("verify")
        return plan, plan_hash

    def load(path, supplied_amendment, supplied_plan):
        assert active
        assert (path, supplied_amendment, supplied_plan) == (plan_path, amendment, plan)
        calls.append("load")
        return context

    def open_ledger(root, supplied_plan, supplied_hash, *, create, **kwargs):
        assert active and create is False
        assert (root, supplied_plan, supplied_hash) == (tmp_path, plan, plan_hash)
        assert kwargs == ({"recovery_context": context} if amended else {})
        calls.append("ledger")
        return ledger

    def reconcile(root, supplied_plan, supplied_hash, supplied_ledger):
        assert active
        assert (root, supplied_plan, supplied_hash, supplied_ledger) == (
            tmp_path,
            plan,
            plan_hash,
            ledger,
        )
        calls.append("reconcile")
        return status

    def project(root, supplied_plan, supplied_hash, supplied_status):
        assert active
        assert (root, supplied_plan, supplied_hash, supplied_status) == (
            tmp_path,
            plan,
            plan_hash,
            status,
        )
        return projected.copy()

    def public(supplied_amendment, path):
        assert active
        assert (supplied_amendment, path) == (amendment, plan_path)
        calls.append("metadata")
        return metadata.copy()

    recovery = ModuleType("scripts.benchmarks.recovery")
    recovery.public_recovery_metadata = public
    monkeypatch.setitem(sys.modules, "scripts.benchmarks.recovery", recovery)
    monkeypatch.setattr(campaign, "load_recovery_context", load, raising=False)
    monkeypatch.setattr(report_module, "verify_plan", verify)
    monkeypatch.setattr(report_module, "campaign_lock", locked)
    monkeypatch.setattr(report_module, "open_campaign_ledger", open_ledger)
    monkeypatch.setattr(report_module, "reconcile_accounting", reconcile)
    monkeypatch.setattr(report_module, "_build_report_locked", project)

    report = report_module.build_report(plan_path, amendment=amendment)
    assert report["source_sha256"] == frozen_sources
    assert report["accounting"] == projected["accounting"]
    assert not active
    if amended:
        assert calls == ["verify", "load", "ledger", "reconcile", "metadata"]
        assert report["source_sha256_scope"] == "original_frozen_campaign"
        assert report["runtime_source_sha256"] == {
            "runner.py": "6" * 64,
            "unchanged.py": "2" * 64,
            "recovery.py": "7" * 64,
        }
        assert report["recovery"] == {
            **metadata,
            **{key: value for key, value in status.items() if key != "recovery_amendment_sha256"},
        }
        assert "original_plan_canonical_sha256" in report["recovery"]
    else:
        assert calls == ["verify", "ledger", "reconcile"]
        assert "recovery" not in report
        assert "runtime_source_sha256" not in report


def test_report_rejects_recovery_metadata_that_disagrees_with_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    plan = {"source_sha256": {}}
    monkeypatch.setattr(report_module, "verify_plan", lambda *a, **k: (plan, "a" * 64))
    monkeypatch.setattr(campaign, "load_recovery_context", lambda *a: object(), raising=False)
    monkeypatch.setattr(report_module, "open_campaign_ledger", lambda *a, **k: object())
    monkeypatch.setattr(
        report_module,
        "reconcile_accounting",
        lambda *a: {"recovery_amendment_sha256": "b" * 64},
    )
    monkeypatch.setattr(report_module, "_build_report_locked", lambda *a: {})
    recovery = ModuleType("scripts.benchmarks.recovery")
    recovery.public_recovery_metadata = lambda *a: {
        "amendment_sha256": "c" * 64,
        "source_overrides": {},
    }
    monkeypatch.setitem(sys.modules, "scripts.benchmarks.recovery", recovery)
    with pytest.raises(ValueError, match="recovery provenance"):
        report_module.build_report(tmp_path / "plan.json", amendment=tmp_path / "amendment.json")


@pytest.mark.parametrize("amended", [False, True])
def test_report_cli_forwards_only_explicit_amendment(tmp_path: Path, monkeypatch, amended) -> None:
    plan = tmp_path / "plan.json"
    amendment = tmp_path / "amendment.json"
    output = tmp_path / "summary.json"
    arguments = ["report", "--plan", str(plan), "--output", str(output)]
    if amended:
        arguments.extend(["--amendment", str(amendment)])
    monkeypatch.setattr(sys, "argv", arguments)

    def build(path, **kwargs):
        assert path == plan
        assert kwargs == ({"amendment": amendment} if amended else {})
        return {"accounting": {"cost_complete": False}, "test": {"complete": True}}

    monkeypatch.setattr(report_module, "build_report", build)
    report_module.main()
    assert json.loads(output.read_text())["accounting"]["cost_complete"] is False
