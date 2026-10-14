"""Conservative comparisons of immutable experiment cohorts.

Differences are right minus left. Repeated attempts are averaged within each
scenario before constructing a bounded, task-level uncertainty interval.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from cvals.models import content_hash
from cvals.reports import _bounded_interval, build_report

_FINALIZED = {"completed", "error", "cancelled", "interrupted", "budget_exceeded"}


def evidence_issues(experiment: dict[str, Any]) -> list[str]:
    """Reject incomplete record shapes instead of silently shrinking denominators."""
    issues = []
    snapshot = experiment.get("snapshot")
    if not isinstance(snapshot, dict):
        return ["The frozen experiment snapshot is missing"]
    if snapshot.get("schema_version") != 1:
        issues.append("The experiment snapshot schema is unsupported")
    if experiment.get("snapshot_hash") != content_hash(snapshot):
        issues.append("The experiment snapshot fingerprint does not match its content")
    collection = snapshot.get("collection")
    systems = snapshot.get("systems")
    if not isinstance(collection, dict) or not isinstance(collection.get("scenarios"), list):
        return issues + ["The frozen task collection is missing"]
    if not isinstance(systems, list) or not all(
        isinstance(s, dict) and s.get("id") for s in systems
    ):
        return issues + ["The frozen system configurations are missing"]
    if experiment.get("collection_hash") != content_hash(collection):
        issues.append("The collection fingerprint does not match the frozen collection")
    if experiment.get("systems") != systems:
        issues.append("The reported systems differ from the frozen configurations")
    scenarios = collection["scenarios"]
    if not scenarios or not all(isinstance(s, dict) and s.get("id") for s in scenarios):
        return issues + ["The frozen collection has no valid scenario identities"]
    task_ids = [s["id"] for s in scenarios]
    system_ids = [s["id"] for s in systems]
    if len(set(task_ids)) != len(task_ids) or len(set(system_ids)) != len(system_ids):
        return issues + ["Frozen task or system identities are duplicated"]
    repetitions = snapshot.get("repetitions")
    if type(repetitions) is not int or repetitions < 1:
        return issues + ["The frozen repetition count is invalid"]
    expected = Counter(
        (system_id, task_id, repetition)
        for system_id in system_ids
        for task_id in task_ids
        for repetition in range(1, repetitions + 1)
    )
    trials = experiment.get("trials")
    if not isinstance(trials, list) or not all(isinstance(t, dict) for t in trials):
        return issues + ["Individual trial evidence is missing"]
    observed = Counter(
        (t.get("system_id"), t.get("scenario_id"), t.get("repetition")) for t in trials
    )
    if observed != expected:
        issues.append(
            "Trial identities do not cover the complete frozen task/system/repetition grid"
        )
    if experiment.get("total_trials") != sum(expected.values()):
        issues.append("The declared trial denominator differs from the frozen schedule")
    for trial in trials:
        if trial.get("status") not in _FINALIZED | {"queued", "running"}:
            issues.append("A trial has an unsupported execution state")
            break
        if trial.get("status") != "completed" and trial.get("passed") is True:
            issues.append("An ungraded trial claims a passing result")
            break
        for metric in ("cost_usd", "duration_ms"):
            number = trial.get(metric)
            if number is not None and (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                or number < 0
            ):
                issues.append(f"Trial {metric} is not a finite nonnegative measurement")
                break
        if trial.get("status") == "completed" and type(trial.get("passed")) is not bool:
            issues.append("A completed trial is missing a boolean grading result")
            break
    return issues


def _images(experiment: dict[str, Any]) -> set[str]:
    return {
        check["actual"]["image_digest"]
        for trial in experiment.get("trials", [])
        for check in trial.get("checks", [])
        if isinstance(check.get("actual"), dict)
        and isinstance(check["actual"].get("image_digest"), str)
    }


def _task_rates(experiment: dict[str, Any], system_id: str) -> dict[str, float]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in experiment["trials"]:
        if trial["system_id"] == system_id:
            groups[trial["scenario_id"]].append(trial)
    return {
        key: sum(t["status"] == "completed" and t.get("passed") is True for t in trials)
        / len(trials)
        for key, trials in groups.items()
    }


def compare_experiments(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare shared system identities only; never infer a model pairing by name."""
    reasons = [f"Left: {reason}" for reason in evidence_issues(left)] + [
        f"Right: {reason}" for reason in evidence_issues(right)
    ]
    warnings = [
        "Differences are right minus left. All scheduled attempts remain in pass-rate denominators.",
        "Intervals assume representative independent tasks; source dependence and selection bias are not corrected.",
        "Intervals are unadjusted for multiple comparisons; exploratory results are not a confirmatory ranking.",
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "left_experiment_id": left.get("id"),
        "right_experiment_id": right.get("id"),
        "left_snapshot_hash": left.get("snapshot_hash"),
        "right_snapshot_hash": right.get("snapshot_hash"),
        "left_evidence_hash": content_hash(
            {"snapshot_hash": left.get("snapshot_hash"), "trials": left.get("trials")}
        ),
        "right_evidence_hash": content_hash(
            {"snapshot_hash": right.get("snapshot_hash"), "trials": right.get("trials")}
        ),
        "direction": "right_minus_left",
        "compatible": False,
        "incompatibilities": reasons,
        "comparisons": [],
        "system_changes": [],
        "warnings": warnings,
    }
    if reasons:
        return result
    left_snapshot, right_snapshot = left["snapshot"], right["snapshot"]
    if left["collection_hash"] != right["collection_hash"]:
        reasons.append(
            "Frozen collection fingerprints differ; task changes cannot be attributed to system performance"
        )
    for key in ("grader_revision", "harness_revision", "protocol", "sandbox_image_requested"):
        if not left_snapshot.get(key) or not right_snapshot.get(key):
            reasons.append(f"The {key} execution contract is missing")
        elif left_snapshot[key] != right_snapshot[key]:
            reasons.append(f"Execution contract changed: {key}")
    if _images(left) != _images(right):
        reasons.append("Observed verifier image identities differ or are unavailable on one side")
    if reasons:
        return result
    result["compatible"] = True
    left_systems = {s["id"]: s for s in left_snapshot["systems"]}
    right_systems = {s["id"]: s for s in right_snapshot["systems"]}
    common = sorted(left_systems.keys() & right_systems.keys())
    result["unmatched_left_systems"] = sorted(left_systems.keys() - right_systems.keys())
    result["unmatched_right_systems"] = sorted(right_systems.keys() - left_systems.keys())
    if not common:
        warnings.append("No shared system identities; no cross-run pairing was inferred")
    left_report = {s["system_id"]: s for s in build_report(left)["systems"]}
    right_report = {s["system_id"]: s for s in build_report(right)["systems"]}
    for identity in common:
        a, b = left_report[identity], right_report[identity]
        before, after = _task_rates(left, identity), _task_rates(right, identity)
        task_ids = sorted(before)
        differences = [after[task] - before[task] for task in task_ids]
        low, high = _bounded_interval(differences, -1, 1)
        fixture = a["fixture_control"] or b["fixture_control"]
        replay = a["submission_replay"] or b["submission_replay"]
        finalized = all(
            t["status"] in _FINALIZED
            for e in (left, right)
            for t in e["trials"]
            if t["system_id"] == identity
        )
        independent = left.get("id") != right.get("id") and not replay
        supported = (
            finalized and independent and not fixture and a["completed"] > 0 and b["completed"] > 0
        )
        conclusion = "insufficient evidence"
        if supported and low is not None and high is not None:
            if high < 0:
                conclusion = "right lower"
            elif low > 0:
                conclusion = "right higher"
        if fixture:
            conclusion = "fixture controls only"
        elif replay:
            conclusion = "recorded submissions; no new independent execution evidence"
        elif not finalized:
            conclusion = "execution incomplete"
        changed = content_hash(left_systems[identity]) != content_hash(right_systems[identity])
        result["system_changes"].append(
            {
                "system_id": identity,
                "configuration_changed": changed,
                "left_hash": content_hash(left_systems[identity]),
                "right_hash": content_hash(right_systems[identity]),
            }
        )
        result["comparisons"].append(
            {
                "system_id": identity,
                "left_name": a["name"],
                "right_name": b["name"],
                "fixture_control": fixture,
                "submission_replay": replay,
                "configuration_changed": changed,
                "paired_tasks": len(task_ids),
                "left_repetitions": left_snapshot["repetitions"],
                "right_repetitions": right_snapshot["repetitions"],
                "left_pass_rate": a["pass_rate"],
                "right_pass_rate": b["pass_rate"],
                "difference": sum(differences) / len(differences),
                "ci_low": low,
                "ci_high": high,
                "interval_method": "paired task-cluster Hoeffding 95% (unadjusted)",
                "inference_supported": supported,
                "conclusion": conclusion,
                "left_completion_rate": a["completed"] / a["scheduled"],
                "right_completion_rate": b["completed"] / b["scheduled"],
                "left_cost_usd": a["cost_usd"],
                "right_cost_usd": b["cost_usd"],
                "cost_difference_usd": b["cost_usd"] - a["cost_usd"]
                if a["cost_usd"] is not None and b["cost_usd"] is not None
                else None,
            }
        )
    return result
