"""Observed economic tradeoffs with explicit eligibility and timing coverage."""

from __future__ import annotations

import math
from typing import Any

from cvals.comparison import evidence_issues
from cvals.reports import build_report


def analyze(experiment: dict[str, Any]) -> dict[str, Any]:
    report = build_report(experiment)
    issues = evidence_issues(experiment)
    rows = []
    for summary in report["systems"]:
        trials = [t for t in experiment["trials"] if t["system_id"] == summary["system_id"]]
        timing_complete = bool(trials) and all(t.get("duration_ms") is not None for t in trials)
        finalized = all(t["status"] not in {"queued", "running"} for t in trials)
        eligible = (
            not issues
            and finalized
            and timing_complete
            and summary["cost_complete"]
            and summary["cost_per_pass"] is not None
            and not summary["fixture_control"]
            and not summary["submission_replay"]
        )
        costs = [t for t in trials if t.get("cost_usd") is not None]
        failed_spend = 0.0
        for trial in trials:
            if trial.get("passed") is True:
                continue
            value = trial.get("cost_usd")
            if value is None:
                value = trial.get("provider_metadata", {}).get("known_cost_usd")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            ):
                failed_spend += value
        rows.append(
            {
                **summary,
                "eligible_for_frontier": bool(eligible),
                "cost_per_scheduled_attempt": summary["cost_usd"] / summary["scheduled"]
                if summary["cost_usd"] is not None and summary["scheduled"]
                else None,
                "known_spend_on_nonpassing_attempts": failed_spend,
                "timing_complete": timing_complete,
                "expensive_attempts": [
                    {
                        "trial_id": t["id"],
                        "scenario_name": t["scenario_name"],
                        "cost_usd": t["cost_usd"],
                        "passed": t.get("passed"),
                    }
                    for t in sorted(costs, key=lambda t: t["cost_usd"], reverse=True)[:5]
                ],
                "tool_calls": sum(
                    bool(step.get("tool"))
                    for t in trials
                    for step in t.get("provider_metadata", {}).get("trace", [])
                    if isinstance(step, dict)
                ),
            }
        )
    frontier = []
    for row in rows:
        row["dominated_by"] = []
        if not row["eligible_for_frontier"]:
            continue
        for other in rows:
            if other["system_id"] == row["system_id"] or not other["eligible_for_frontier"]:
                continue
            if (
                other["pass_rate"] >= row["pass_rate"]
                and other["cost_per_pass"] <= row["cost_per_pass"]
                and other["p95_duration_ms"] <= row["p95_duration_ms"]
                and (
                    other["pass_rate"] > row["pass_rate"]
                    or other["cost_per_pass"] < row["cost_per_pass"]
                    or other["p95_duration_ms"] < row["p95_duration_ms"]
                )
            ):
                row["dominated_by"].append(other["system_id"])
        if not row["dominated_by"]:
            frontier.append(row["system_id"])
    return {
        "experiment_id": experiment["id"],
        "snapshot_hash": experiment.get("snapshot_hash"),
        "systems": rows,
        "observed_frontier": frontier,
        "evidence_issues": issues,
        "notes": [
            "The frontier uses observed pass rate, cost per passing attempt, and p95 duration on this frozen cohort.",
            "It is descriptive, not a confidence statement, routing policy, or recommendation for unobserved workloads.",
            "Fixtures, replays, incomplete accounting, missing timing, and unfinished runs are excluded from the frontier.",
            "Submission timing includes provider and tool stages; overlapping stages must not be added together.",
            "Adapter timing covers the external service request; internal adapter stages are self-reported.",
        ],
    }


def decision_brief(experiment: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    report = build_report(experiment)
    return {
        "experiment_id": experiment["id"],
        "name": experiment["name"],
        "snapshot_hash": experiment["snapshot_hash"],
        "status": experiment["status"],
        "scheduled": report["total_trials"],
        "completed": report["completed_trials"],
        "systems": report["systems"],
        "decisions": [
            {
                key: row.get(key)
                for key in (
                    "id",
                    "created_at",
                    "policy_id",
                    "policy",
                    "policy_hash",
                    "evidence_hash",
                    "status",
                    "systems",
                    "excluded_systems",
                    "issues",
                    "warnings",
                )
            }
            for row in decisions
        ],
        "warnings": report["warnings"],
        "review_basis": "Frozen task cohort, observed outcomes, declared policies, and retained accounting.",
        "contains_task_content": False,
    }
