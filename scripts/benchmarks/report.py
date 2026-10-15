"""Export a metadata-only paired report; raw tasks and solver traces stay private."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from .campaign import (
    ARMS,
    attempt_identity,
    campaign_lock,
    open_campaign_ledger,
    read_json,
    reconcile_accounting,
    validate_result,
    verify_plan,
)


def number(value: Any) -> float | None:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def control_metrics(control: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """Publish control coverage without exporting hidden test names or logs."""
    groups = {
        "fail_to_pass": task["FAIL_TO_PASS"],
        "pass_to_pass": task["PASS_TO_PASS"],
    }
    result: dict[str, Any] = {
        "valid": control.get("valid") is True,
        "required_counts": {name: len(tests) for name, tests in groups.items()},
    }
    statuses = {"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS", "MISSING"}
    for name in ("base", "gold"):
        cycle = control.get(name)
        if not isinstance(cycle, dict):
            result[name] = None
            continue
        observed = cycle.get("test_statuses", {})
        if not isinstance(observed, dict):
            observed = {}
        counts = {}
        for group, tests in groups.items():
            values = [observed.get(test, "MISSING") for test in tests]
            counts[group] = dict(
                sorted(
                    Counter(
                        value if isinstance(value, str) and value in statuses else "UNKNOWN"
                        for value in values
                    ).items()
                )
            )
        cycle_status = cycle.get("status")
        signals = cycle.get("setup_error_signals")
        result[name] = {
            "status": cycle_status
            if isinstance(cycle_status, str)
            and cycle_status in {"graded", "infrastructure_error", "invalid_patch", "not_prepared"}
            else None,
            "setup_error_detected": bool(signals) if isinstance(signals, list) else None,
            "observed_status_counts": dict(
                sorted(
                    Counter(
                        value if isinstance(value, str) and value in statuses else "UNKNOWN"
                        for value in observed.values()
                    ).items()
                )
            ),
            "required_status_counts": counts,
            "all_required_tests_observed": cycle.get("all_required_tests_observed") is True,
            "all_required_tests_passed": cycle.get("all_required_tests_passed") is True,
            "cleanup_confirmed": cycle.get("cleanup_confirmed") is True,
            "test_suite_exit_code": cycle.get("test_suite_exit_code")
            if type(cycle.get("test_suite_exit_code")) is int
            else None,
        }
    return result


def arm_metrics(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    values = [row["arms"][arm] for row in rows]
    resolved = sum(value["resolved"] for value in values)
    costs = [Decimal(value["known_cost_usd"]) for value in values]
    cost = sum(costs, Decimal(0))
    latencies = [
        value["duration_seconds"] for value in values if value["duration_seconds"] is not None
    ]
    complete = all(value["cost_complete"] for value in values)
    admitted = sum(row["admitted"] for row in rows)
    return {
        "selected_tasks": len(rows),
        "admitted_tasks": admitted,
        "resolved": resolved,
        "not_run": sum(value["outcome"] == "not_run" for value in values),
        "excluded": sum(value["outcome"] == "excluded" for value in values),
        "outcome_counts": dict(sorted(Counter(value["outcome"] for value in values).items())),
        "selected_resolution_rate": resolved / len(rows) if rows else None,
        "admitted_resolution_rate": resolved / admitted if admitted else None,
        "known_cost_usd": str(cost),
        "cost_complete": complete,
        "cost_per_resolved_usd": str(cost / resolved) if resolved and complete else None,
        "median_attempt_seconds": statistics.median(latencies) if latencies else None,
        "latency_observations": len(latencies),
        "provider_calls": sum(value["provider_calls"] for value in values),
        "known_input_tokens": sum(value["known_input_tokens"] for value in values),
        "known_output_tokens": sum(value["known_output_tokens"] for value in values),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = all(value["outcome"] != "not_run" for row in rows for value in row["arms"].values())
    admitted = [row for row in rows if row["admitted"]]
    paired = [
        row
        for row in admitted
        if all(value["outcome"] != "not_run" for value in row["arms"].values())
    ]
    wins = sum(
        row["arms"]["everval"]["resolved"] and not row["arms"]["baseline"]["resolved"]
        for row in paired
    )
    losses = sum(
        row["arms"]["baseline"]["resolved"] and not row["arms"]["everval"]["resolved"]
        for row in paired
    )
    return {
        "complete": complete,
        "arms": {arm: arm_metrics(rows, arm) for arm in ARMS},
        "paired": {
            "admitted_pairs": len(admitted),
            "completed_pairs": len(paired),
            "pending_pairs": len(admitted) - len(paired),
            "everval_only_resolved": wins,
            "baseline_only_resolved": losses,
            "both_resolved": sum(
                all(value["resolved"] for value in row["arms"].values()) for row in paired
            ),
            "neither_resolved": sum(
                not any(value["resolved"] for value in row["arms"].values()) for row in paired
            ),
            "resolution_difference": wins - losses,
            "comparison_final": complete,
        },
    }


def build_report(plan_path: Path) -> dict[str, Any]:
    plan, plan_hash = verify_plan(plan_path)
    root = plan_path.parent
    with campaign_lock(root):
        ledger = open_campaign_ledger(root, plan, plan_hash, create=False)
        status = reconcile_accounting(root, plan, plan_hash, ledger)
        return _build_report_locked(root, plan, plan_hash, status)


def _build_report_locked(root: Path, plan: dict, plan_hash: str, status: dict) -> dict[str, Any]:
    by_attempt = {value["attempt_id"]: value for value in status["attempts"]}
    rows = []
    for item in plan["tasks"]:
        task = read_json(Path(item["task_path"]))
        control = read_json(Path(item["control_path"]))
        row: dict[str, Any] = {
            "instance_id": item["instance_id"],
            "repository": task["repo"],
            "split": item["split"],
            "base_commit": task["base_commit"],
            "task_sha256": item["task_sha256"],
            "image_id": item["image_id"],
            "admitted": item["admitted"],
            "control_sha256": item["control_sha256"],
            "controls": control_metrics(control, task),
            "exclusion_reason": item.get("exclusion_reason"),
            "arms": {},
        }
        for arm in ARMS:
            attempt_id, attempt_hash = attempt_identity(plan_hash, item, arm)
            path = root / "attempts" / attempt_id / "result.json"
            accounting = by_attempt.get(attempt_id, {})
            requests = [value for value in status["requests"] if value["attempt_id"] == attempt_id]
            result = read_json(path) if path.exists() else None
            if result is not None:
                validate_result(result, item, arm, attempt_hash)
            cost_complete = (
                not accounting.get("pending_requests", 0)
                and not accounting.get("uncertain_requests", 0)
                and not accounting.get("recovery_required", False)
            )
            grade = result.get("grade") if result else None
            outcome = "excluded" if not item["admitted"] else "not_run"
            if result:
                if result["resolved"]:
                    outcome = "resolved"
                elif grade and grade.get("status") == "infrastructure_error":
                    outcome = "infrastructure_error"
                elif result["submitted"]:
                    outcome = "submitted_unresolved"
                else:
                    outcome = "incomplete"
            row["arms"][arm] = {
                "outcome": outcome,
                "resolved": bool(result and result["resolved"]),
                "submitted": bool(result and result["submitted"]),
                "exit_status": result.get("status") if result else None,
                "known_cost_usd": accounting.get("known_request_cost_usd", "0"),
                "cost_complete": bool(cost_complete),
                "provider_calls": len(requests),
                "known_input_tokens": sum(value["input_tokens"] or 0 for value in requests),
                "known_output_tokens": sum(value["output_tokens"] or 0 for value in requests),
                "duration_seconds": number(result.get("duration_seconds")) if result else None,
                "steps": result.get("steps") if result else None,
                "review_checkpoint_used": result.get("review_checkpoint_used") if result else None,
                "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if result else None,
                "required_tests_observed": grade.get("all_required_tests_observed")
                if grade
                else None,
                "required_tests_passed": grade.get("all_required_tests_passed") if grade else None,
                "grading_status": grade.get("status") if grade else None,
                "test_suite_exit_code": grade.get("test_suite_exit_code") if grade else None,
                "container_script_returncode": grade.get("container_script_returncode")
                if grade
                else None,
                "observed_test_status_counts": grade.get("observed_status_counts")
                if grade
                else None,
            }
        rows.append(row)
    return {
        "schema_version": 1,
        "campaign_id": plan["campaign_id"],
        "plan_sha256": plan_hash,
        "selection_sha256": plan["selection_sha256"],
        "model": plan["model"],
        "pricing": {
            key: plan["budget"][key]
            for key in ("pricing_version", "input_rate_per_million", "output_rate_per_million")
        },
        "execution": plan["execution"],
        "source_sha256": plan["source_sha256"],
        "accounting": {
            key: status[key]
            for key in (
                "opening_spend_usd",
                "known_spend_usd",
                "committed_spend_usd",
                "pending_requests",
                "uncertain_requests",
                "cost_complete",
                "evidence_reconciled",
                "admission_ceiling_usd",
                "user_ceiling_usd",
            )
        },
        "development": summarize([row for row in rows if row["split"] == "development"]),
        "test": summarize([row for row in rows if row["split"] == "test"]),
        "tasks": rows,
        "limitations": [
            "Purposive Python subset: 12 test tasks and 3 development tasks; not an official leaderboard score.",
            "Public tasks may have appeared in model training.",
            "One attempt per arm does not establish repeatability or statistical superiority.",
            "Native ARM images and shared edit policy differ from upstream leaderboard environments.",
            "Costs use conservative configured token rates, not a provider invoice.",
            "Attempt duration includes solver setup and cleanup, but excludes grading.",
            "Provider model is an alias; future behavior may differ.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.plan.expanduser().resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "accounting": report["accounting"],
                "test": report["test"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
