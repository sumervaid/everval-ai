"""Reproducible summaries with declared denominators and task-level uncertainty."""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations
from statistics import mean, median
from typing import Any


def wilson(
    successes: int, count: int, z: float = 1.959963984540054
) -> tuple[float | None, float | None]:
    if not count:
        return None, None
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _bounded_interval(
    values: list[float], lower: float, upper: float
) -> tuple[float | None, float | None]:
    """95% Hoeffding interval; task clusters, no independent-repeat assumption."""
    if not values:
        return None, None
    radius = (upper - lower) * math.sqrt(math.log(40) / (2 * len(values)))
    return max(lower, mean(values) - radius), min(upper, mean(values) + radius)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    a, b = math.floor(position), math.ceil(position)
    return ordered[a] + (ordered[b] - ordered[a]) * (position - a)


def build_report(experiment: dict[str, Any]) -> dict[str, Any]:
    trials = experiment.get("trials", [])
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        groups[trial["system_id"]].append(trial)
    systems = []
    domains = []
    warnings = [
        "Pass rate counts all scheduled trials; errors and incomplete trials remain in its denominator."
    ]
    task_rates: dict[str, dict[str, float]] = {}
    for system in experiment.get("systems", []):
        rows = groups[system["id"]]
        completed = [r for r in rows if r["status"] == "completed"]
        passed = sum(r.get("passed") is True for r in rows)
        n = len(rows)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["scenario_id"]].append(row)
        task_rates[system["id"]] = {
            key: sum(r.get("passed") is True for r in rs) / len(rs) for key, rs in grouped.items()
        }
        repeats = any(len(rs) > 1 for rs in grouped.values())
        low, high = (
            _bounded_interval(list(task_rates[system["id"]].values()), 0, 1)
            if repeats
            else wilson(passed, n)
        )
        durations = [r["duration_ms"] for r in rows if r.get("duration_ms") is not None]
        costs = [r.get("cost_usd") for r in rows]
        cost_complete = bool(rows) and all(cost is not None for cost in costs)
        known_cost = sum(cost for cost in costs if cost is not None)
        total_cost = known_cost if cost_complete else None
        scores = [r["score"] for r in completed if r.get("score") is not None]
        systems.append(
            {
                "system_id": system["id"],
                "name": system["name"],
                "kind": system["kind"],
                "fixture_control": system["kind"] in {"reference", "incomplete"},
                "submission_replay": system["kind"] == "replay",
                "scheduled": n,
                "completed": len(completed),
                "passed": passed,
                "failed": sum(r.get("passed") is False for r in completed),
                "errors": sum(r["status"] in {"error", "interrupted"} for r in rows),
                "incomplete": n - len(completed),
                "pass_rate": passed / n if n else None,
                "score": mean(scores) if scores else None,
                "ci_low": low,
                "ci_high": high,
                "interval_method": "task-cluster Hoeffding 95%" if repeats else "Wilson 95%",
                "cost_usd": total_cost,
                "known_cost_usd": known_cost,
                "cost_complete": cost_complete,
                "cost_per_pass": total_cost / passed if passed and total_cost is not None else None,
                "duration_scope": "all attempts with recorded duration, including errors",
                "median_duration_ms": median(durations) if durations else None,
                "p95_duration_ms": _percentile(durations, 0.95),
            }
        )
        for domain in sorted({r["domain"] for r in rows}):
            subset = [r for r in rows if r["domain"] == domain]
            domains.append(
                {
                    "domain": domain,
                    "system_id": system["id"],
                    "trials": len(subset),
                    "pass_rate": sum(r.get("passed") is True for r in subset) / len(subset),
                }
            )
        if system["kind"] in {"reference", "incomplete"}:
            warnings.append(
                f"{system['name']} is a fixture control; these results do not measure model performance."
            )
        if system["kind"] == "replay":
            warnings.append(
                f"{system['name']} regrades external submissions. Reported cost, duration, and traces are supplied by the importing harness, not independently measured here. Replaying these outputs produces no new independent execution evidence."
            )
        if not cost_complete:
            warnings.append(f"{system['name']}: cost is incomplete; known costs are a lower bound.")
    comparisons = []
    for left, right in combinations(systems, 2):
        a, b = task_rates[left["system_id"]], task_rates[right["system_id"]]
        keys = sorted(a.keys() & b.keys())
        differences = [a[key] - b[key] for key in keys]
        low, high = _bounded_interval(differences, -1, 1)
        conclusion = "insufficient evidence"
        if low is not None and high is not None and not low <= 0 <= high:
            conclusion = "left higher" if low > 0 else "right higher"
        comparisons.append(
            {
                "left": left["system_id"],
                "right": right["system_id"],
                "paired_tasks": len(keys),
                "difference": mean(differences) if differences else None,
                "ci_low": low,
                "ci_high": high,
                "interval_method": "paired task-cluster Hoeffding 95% (unadjusted)",
                "conclusion": conclusion,
            }
        )
    if len(comparisons) > 1:
        warnings.append(
            "Pairwise intervals are unadjusted for multiple comparisons; exploratory comparisons are not a confirmatory ranking."
        )
    if any(r["status"] != "completed" for r in trials):
        warnings.append(
            "At least one trial has no valid graded result; inspect completion coverage before comparing systems."
        )
    warnings.append(
        "Intervals assume representative independent tasks; they do not correct benchmark selection bias or shared-source dependence."
    )
    return {
        "schema_version": 1,
        "experiment_id": experiment["id"],
        "total_trials": len(trials),
        "completed_trials": sum(r["status"] == "completed" for r in trials),
        "incomplete_trials": sum(r["status"] != "completed" for r in trials),
        "systems": systems,
        "comparisons": comparisons,
        "domains": domains,
        "warnings": warnings,
    }


def export_report(experiment: dict[str, Any], format: str) -> str:
    """Compatibility wrapper around the shared export renderer."""
    from cvals.exporting import export_experiment

    return export_experiment(experiment, format)[0]
