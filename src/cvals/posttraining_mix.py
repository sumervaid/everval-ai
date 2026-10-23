"""Inspectable development-only evaluation selection and held-out diagnostics."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

MIX_MAX_ROWS = 100_000
Identity = Annotated[str, Field(min_length=1, max_length=200)]


class MixObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    checkpoint_id: Identity
    task_family: Identity
    eval_id: Identity
    domain: Identity
    delta: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False, strict=True)
    cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)


class MixConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    budget_usd: float = Field(gt=0, allow_inf_nan=False, strict=True)
    protected_domains: list[Identity] = Field(default_factory=list, max_length=100)
    seed: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)
    practical_threshold: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _rows(values: list[dict[str, Any]] | list[MixObservation]) -> list[MixObservation]:
    if not 0 < len(values) <= MIX_MAX_ROWS:
        raise ValueError("mix observations must contain between 1 and 100000 rows")
    rows = [MixObservation.model_validate(value) for value in values]
    identities = [(row.checkpoint_id, row.task_family, row.eval_id) for row in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate checkpoint/task-family/evaluation observation")
    domains: dict[str, str] = {}
    for row in rows:
        if domains.setdefault(row.eval_id, row.domain) != row.domain:
            raise ValueError("an evaluation cannot change domains")
    return sorted(rows, key=lambda r: (r.checkpoint_id, r.task_family, r.eval_id))


def _means(rows: list[MixObservation]) -> dict[tuple[str, str, str], float | None]:
    grouped: dict[tuple[str, str, str], list[float | None]] = defaultdict(list)
    for row in rows:
        grouped[(row.checkpoint_id, row.domain, row.eval_id)].append(row.delta)
    return {
        key: sum(v for v in values if v is not None) / len(values)
        if all(v is not None for v in values)
        else None
        for key, values in grouped.items()
    }


def select_mix(
    development: list[dict[str, Any]] | list[MixObservation],
    config: dict[str, Any] | MixConfig,
) -> dict[str, Any]:
    """Freeze a greedy selection, plus two seeded baselines at its cost ceiling.

    Every development cell needs complete score and positive measured cost. Cost
    is the mean total cost of each eval across development checkpoints. Selection
    minimizes domain-level magnitude error plus disagreement about whether a
    domain crosses the negative practical threshold. It is a transparent heuristic,
    not an optimum, a significance test, or an estimate of causal training effects.
    """
    rows, policy = _rows(development), MixConfig.model_validate(config)
    if any(row.delta is None or row.cost_usd is None for row in rows):
        raise ValueError(
            "development selection requires complete effects and measured positive costs"
        )
    domains = {row.eval_id: row.domain for row in rows}
    if set(policy.protected_domains) - set(domains.values()):
        raise ValueError("a protected domain has no development evaluations")
    if len(set(policy.protected_domains)) != len(policy.protected_domains):
        raise ValueError("duplicate protected domain")
    checkpoints = sorted({row.checkpoint_id for row in rows})
    families: dict[str, set[str]] = defaultdict(set)
    totals: dict[str, float] = defaultdict(float)
    observed: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        families[row.eval_id].add(row.task_family)
        observed[row.checkpoint_id, row.eval_id].add(row.task_family)
        assert row.cost_usd is not None
        totals[row.eval_id] += row.cost_usd
    for checkpoint in checkpoints:
        for evaluation, expected in families.items():
            if observed[checkpoint, evaluation] != expected:
                raise ValueError(
                    "development checkpoint/evaluation task-family coverage is incomplete"
                )
    costs = {evaluation: totals[evaluation] / len(checkpoints) for evaluation in domains}
    if len(domains) > 64 or len(checkpoints) > 256:
        raise ValueError("selection is bounded to 64 evaluations and 256 development checkpoints")
    means = {key: value for key, value in _means(rows).items() if value is not None}
    domain_evals = {
        domain: sorted(evaluation for evaluation in domains if domains[evaluation] == domain)
        for domain in sorted(set(domains.values()))
    }
    threshold = policy.practical_threshold

    def loss(chosen: set[str]) -> float:
        total = 0.0
        for checkpoint in checkpoints:
            for domain, evaluations in domain_evals.items():
                available = chosen.intersection(evaluations)
                if not available:
                    total += 3.0
                    continue
                reference = sum(means[checkpoint, domain, e] for e in evaluations) / len(
                    evaluations
                )
                estimate = sum(means[checkpoint, domain, e] for e in available) / len(available)
                total += abs(reference - estimate) + float(
                    (reference < -threshold) != (estimate < -threshold)
                )
        return total / (len(checkpoints) * len(domain_evals))

    mandatory = {
        min(domain_evals[domain], key=lambda e: (costs[e], e))
        for domain in policy.protected_domains
    }
    if sum(costs[e] for e in mandatory) > policy.budget_usd:
        raise ValueError("budget cannot cover the cheapest evaluation in every protected domain")
    chosen = set(mandatory)
    while True:
        spent, current = sum(costs[e] for e in chosen), loss(chosen)
        candidates = [
            e for e in domains if e not in chosen and spent + costs[e] <= policy.budget_usd
        ]
        if not candidates:
            break
        best = min(candidates, key=lambda e: (-(current - loss(chosen | {e})) / costs[e], e))
        if current - loss(chosen | {best}) <= 0:
            break
        chosen.add(best)
    if not chosen:
        raise ValueError("budget is too small for any evaluation")
    matched_budget = sum(costs[e] for e in chosen)

    def fill(order: list[str]) -> list[str]:
        selected = set(mandatory)
        for evaluation in order:
            if (
                evaluation not in selected
                and sum(costs[e] for e in selected) + costs[evaluation] <= matched_budget
            ):
                selected.add(evaluation)
        return sorted(selected)

    rng = random.Random(policy.seed)
    random_order = sorted(domains)
    rng.shuffle(random_order)
    stratified: list[str] = []
    pools = {domain: list(evaluations) for domain, evaluations in domain_evals.items()}
    for pool in pools.values():
        rng.shuffle(pool)
    while any(pools.values()):
        for domain in sorted(pools):
            if pools[domain]:
                stratified.append(pools[domain].pop())
    strategies = {
        "selected": sorted(chosen),
        "equal_budget_random": fill(random_order),
        "equal_budget_domain_stratified": fill(stratified),
        "full_suite": sorted(domains),
    }
    result: dict[str, Any] = {
        "schema_version": 1,
        "method": "greedy_development_domain_loss_v1",
        "config": policy.model_dump(),
        "development_hash": _hash([r.model_dump() for r in rows]),
        "development_checkpoints": checkpoints,
        "development_task_families": sorted({row.task_family for row in rows}),
        "domains": domains,
        "development_costs_usd": costs,
        "strategies": strategies,
        "strategy_development_cost_usd": {
            name: sum(costs[e] for e in evaluations) for name, evaluations in strategies.items()
        },
        "strategy_development_loss": {
            name: loss(set(evaluations)) for name, evaluations in strategies.items()
        },
        "matched_budget_usd": matched_budget,
        "limitations": [
            "Development deltas are supplied measurements; no model calls or training are performed.",
            "Selection is greedy, order-deterministic, and can overfit the development run.",
            "Random baselines are single seeded draws, with the same cost ceiling and protected coverage; actual costs can differ.",
            "Protected domains receive coverage, not a guarantee against missed safety failures.",
            "Full-suite reference is a measurement, not ground truth; crossings are descriptive, not significance claims.",
            "Held-out checkpoints and task families must both be disjoint; independent training runs are still required for generalization.",
        ],
    }
    result["selection_hash"] = _hash(result)
    return result


def evaluate_mix(
    selection: dict[str, Any],
    validation: list[dict[str, Any]] | list[MixObservation],
) -> dict[str, Any]:
    """Measure a frozen selection on checkpoint- AND family-disjoint observations.

    Validation explicitly declares its expected family sets by observed eval IDs.
    Every evaluation in the full reference must occur. Each checkpoint must then
    contain every declared evaluation/family cell, including null rows for missing
    results. Entirely unreported validation families cannot be detected here; the
    caller must bind these rows to a frozen validation manifest.
    """
    frozen = dict(selection)
    expected_hash = frozen.pop("selection_hash", None)
    if not expected_hash or expected_hash != _hash(frozen):
        raise ValueError("frozen selection integrity check failed")
    rows = _rows(validation)
    if {row.checkpoint_id for row in rows} & set(selection["development_checkpoints"]):
        raise ValueError("validation checkpoint overlaps development")
    if {row.task_family for row in rows} & set(selection["development_task_families"]):
        raise ValueError("validation task family overlaps development")
    domains = selection["domains"]
    if {row.eval_id for row in rows} != set(domains):
        raise ValueError("validation must declare all and only frozen full-suite evaluations")
    if any(domains[row.eval_id] != row.domain for row in rows):
        raise ValueError("validation changed a frozen evaluation domain")
    checkpoints = sorted({row.checkpoint_id for row in rows})
    families: dict[str, set[str]] = defaultdict(set)
    indexed = {}
    for row in rows:
        families[row.eval_id].add(row.task_family)
        indexed[row.checkpoint_id, row.eval_id, row.task_family] = row
    means: dict[tuple[str, str], float | None] = {}
    costs: dict[tuple[str, str], float | None] = {}
    for checkpoint in checkpoints:
        for evaluation in domains:
            cells = [
                indexed.get((checkpoint, evaluation, family)) for family in families[evaluation]
            ]
            effects = [row.delta for row in cells if row is not None and row.delta is not None]
            measured_costs = [
                row.cost_usd for row in cells if row is not None and row.cost_usd is not None
            ]
            means[checkpoint, evaluation] = (
                sum(effects) / len(cells) if len(effects) == len(cells) else None
            )
            costs[checkpoint, evaluation] = (
                sum(measured_costs) if len(measured_costs) == len(cells) else None
            )
    threshold = selection["config"]["practical_threshold"]
    results = {}
    for name, evaluations in selection["strategies"].items():
        comparisons = []
        for checkpoint in checkpoints:
            for domain in sorted(set(domains.values())):
                reference_evals = [e for e in domains if domains[e] == domain]
                selected_evals = [e for e in evaluations if domains[e] == domain]
                ref_values = [means[checkpoint, e] for e in reference_evals]
                own_values = [means[checkpoint, e] for e in selected_evals]
                reference = (
                    sum(v for v in ref_values if v is not None) / len(ref_values)
                    if all(v is not None for v in ref_values)
                    else None
                )
                estimate = (
                    sum(v for v in own_values if v is not None) / len(own_values)
                    if own_values and all(v is not None for v in own_values)
                    else None
                )
                comparisons.append(
                    {
                        "checkpoint_id": checkpoint,
                        "domain": domain,
                        "reference_delta": reference,
                        "estimated_delta": estimate,
                    }
                )
        known = [
            row
            for row in comparisons
            if row["reference_delta"] is not None and row["estimated_delta"] is not None
        ]
        reference_regressions = sum(
            row["reference_delta"] is not None and row["reference_delta"] < -threshold
            for row in comparisons
        )
        reference_nonregressions = sum(
            row["reference_delta"] is not None and row["reference_delta"] >= -threshold
            for row in comparisons
        )
        detected = sum(
            row["reference_delta"] < -threshold and row["estimated_delta"] < -threshold
            for row in known
        )
        false_alerts = sum(
            row["reference_delta"] >= -threshold and row["estimated_delta"] < -threshold
            for row in known
        )
        known_regressions = sum(row["reference_delta"] < -threshold for row in known)
        known_nonregressions = len(known) - known_regressions
        costs_list = [costs[checkpoint, e] for checkpoint in checkpoints for e in evaluations]
        complete_cost = all(v is not None for v in costs_list)
        results[name] = {
            "evaluations": evaluations,
            "cells": len(comparisons),
            "comparable_cells": len(known),
            "unknown_cells": len(comparisons) - len(known),
            "reference_regressions": reference_regressions,
            "comparable_reference_regressions": known_regressions,
            "unknown_regression_cells": reference_regressions - known_regressions,
            "detected_regressions": detected,
            "detection_recall": detected / known_regressions if known_regressions else None,
            "reference_nonregressions": reference_nonregressions,
            "comparable_reference_nonregressions": known_nonregressions,
            "unknown_nonregression_cells": reference_nonregressions - known_nonregressions,
            "false_alerts": false_alerts,
            "false_alert_rate": false_alerts / known_nonregressions
            if known_nonregressions
            else None,
            "mean_absolute_delta_error": sum(
                abs(row["reference_delta"] - row["estimated_delta"]) for row in known
            )
            / len(known)
            if known
            else None,
            "cost_usd": sum(v for v in costs_list if v is not None) if complete_cost else None,
            "known_cost_lower_bound_usd": sum(
                row.cost_usd
                for row in rows
                if row.eval_id in evaluations and row.cost_usd is not None
            ),
            "cost_complete": complete_cost,
            "comparisons": comparisons,
        }
    return {
        "selection_hash": expected_hash,
        "validation_hash": _hash([row.model_dump() for row in rows]),
        "validation_checkpoints": checkpoints,
        "validation_task_families": sorted({row.task_family for row in rows}),
        "strategies": results,
        "interpretation": "Descriptive held-out threshold crossings against the full-suite reference; "
        "rates condition on comparable cells, and unknown denominators are reported separately. "
        "No confidence or production-scale claim is made. One seeded baseline draw is not a stability study.",
    }
