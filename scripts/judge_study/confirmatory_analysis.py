"""Prospective paired non-inferiority and cost gates for frozen judge campaigns.

All scheduled orders are averaged within a question family before inference.
Historical human votes are a fallible reference. No model calls occur here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from statistics import NormalDist
from typing import Any

LABELS = {"A", "B", "tie"}
ATTEMPTED = {"completed", "invalid", "abstained", "error"}
DEFAULT_SETTINGS = {
    "margin": 0.01,
    "alpha": 0.05,
    "resamples": 20_000,
    "seed": 27102027,
    "min_families": 30,
    "min_discordant_families": 20,
    "contrasts": [
        {"id": "A", "candidate": "terra-standard", "baseline": "terra-checklist"},
        {"id": "B", "candidate": "terra-standard", "baseline": "sol-standard"},
    ],
}


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _integer(value: Any, name: str, low: int, high: int = 1_000_000) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _money(value: Any, name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an exact decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return amount


def _beta_fraction(a: float, b: float, x: float) -> float:
    # Modified Lentz continued fraction for the regularized incomplete beta.
    small = 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c = 1.0
    d = 1 - qab * x / qap
    d = 1 / (d if abs(d) > small else small)
    result = d
    for m in range(1, 401):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        c = 1 + aa / c
        d = 1 / (d if abs(d) > small else small)
        c = c if abs(c) > small else small
        result *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        c = 1 + aa / c
        d = 1 / (d if abs(d) > small else small)
        c = c if abs(c) > small else small
        change = d * c
        result *= change
        if abs(change - 1) < 3e-14:
            return result
    raise ArithmeticError("Incomplete-beta continued fraction did not converge")


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        return front * _beta_fraction(a, b, x) / a
    return 1 - front * _beta_fraction(b, a, 1 - x) / b


@lru_cache(maxsize=512)
def student_t_critical(degrees: int, alpha: float = 0.05) -> float:
    """Positive one-sided Student-t quantile, with no external dependency."""
    _integer(degrees, "degrees", 1)
    alpha = _finite(alpha, "alpha")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")

    def upper_tail(value: float) -> float:
        return 0.5 * _regularized_beta(degrees / (degrees + value * value), degrees / 2, 0.5)

    low, high = 0.0, 2.0
    while upper_tail(high) > alpha:
        high *= 2
    for _ in range(80):
        middle = (low + high) / 2
        if upper_tail(middle) > alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _quantile(ordered: list[float], probability: float) -> float:
    index = probability * (len(ordered) - 1)
    low = math.floor(index)
    high = math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _bootstrap_means(values: list[float], resamples: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    counts = sorted(Counter(values).items())
    n = len(values)
    if len(counts) <= 32:
        # Exact multinomial resampling of empirical atoms is the ordinary
        # nonparametric bootstrap, without expanding thousands of equal votes.
        result = []
        for _ in range(resamples):
            remaining_n = remaining_weight = n
            total = 0.0
            for value, weight in counts[:-1]:
                draw = rng.binomialvariate(remaining_n, weight / remaining_weight)
                total += value * draw
                remaining_n -= draw
                remaining_weight -= weight
            total += counts[-1][0] * remaining_n
            result.append(total / n)
        return sorted(result)
    return sorted(math.fsum(rng.choices(values, k=n)) / n for _ in range(resamples))


def paired_interval(
    values: Sequence[float],
    *,
    alpha: float = 0.05,
    resamples: int = 20_000,
    seed: int = 27102027,
    min_families: int = 30,
) -> dict[str, Any]:
    """Return two one-sided bounds; neither supplies exact finite-sample coverage."""
    alpha = _finite(alpha, "alpha")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    _integer(resamples, "resamples", 200)
    _integer(seed, "seed", 0, 2**63 - 1)
    _integer(min_families, "min_families", 2)
    sample = [_finite(v, "family difference") for v in values]
    n = len(sample)
    if not n:
        raise ValueError("A paired interval needs at least one family")
    estimate = math.fsum(sample) / n
    variance = math.fsum((v - estimate) ** 2 for v in sample) / (n - 1) if n > 1 else 0.0
    result: dict[str, Any] = {
        "families": n,
        "estimate": estimate,
        "standard_deviation": math.sqrt(variance),
        "alpha_one_sided": alpha,
        "resamples": resamples,
        "seed": seed,
        "bootstrap_lower": None,
        "bootstrap_upper": None,
        "t_lower": None,
        "t_upper": None,
        "undefined_reason": None,
    }
    if n < min_families:
        result["undefined_reason"] = "insufficient_independent_families"
        return result
    if variance == 0:
        result["undefined_reason"] = "degenerate_family_distribution"
        return result
    boot = _bootstrap_means(sample, resamples, seed)
    if boot[0] == boot[-1]:
        result["undefined_reason"] = "degenerate_bootstrap_distribution"
        return result
    half_width = student_t_critical(n - 1, alpha) * math.sqrt(variance / n)
    result.update(
        bootstrap_lower=_quantile(boot, alpha),
        bootstrap_upper=_quantile(boot, 1 - alpha),
        t_lower=estimate - half_width,
        t_upper=estimate + half_width,
    )
    return result


def quality_interval(
    values: Sequence[float], *, min_discordant_families: int = 20, **options: Any
) -> dict[str, Any]:
    """Withhold approximate inference when too few families carry a difference."""
    _integer(min_discordant_families, "min_discordant_families", 1)
    interval = paired_interval(values, **options)
    positives = sum(value > 0 for value in values)
    negatives = sum(value < 0 for value in values)
    interval.update(
        positive_family_differences=positives,
        negative_family_differences=negatives,
        nonzero_family_differences=positives + negatives,
        minimum_discordant_families=min_discordant_families,
    )
    if positives + negatives < min_discordant_families:
        interval.update(
            bootstrap_lower=None,
            bootstrap_upper=None,
            t_lower=None,
            t_upper=None,
            undefined_reason="insufficient_discordant_families",
        )
    return interval


def power_sensitivity(
    families: int, *, margin: float = 0.01, alpha: float = 0.05
) -> dict[str, Any]:
    _integer(families, "families", 2)
    margin = _finite(margin, "margin")
    alpha = _finite(alpha, "alpha")
    if not 0 < margin < 1 or not 0 < alpha < 0.5:
        raise ValueError("Invalid margin or alpha")
    normal = NormalDist()
    z = normal.inv_cdf(1 - alpha)
    rows = []
    for sd in (0.05, 0.10, 0.15, 0.20, 0.25):
        rows.append(
            {
                "assumed_family_difference_sd": sd,
                "power_at_true_zero_difference": normal.cdf(margin * math.sqrt(families) / sd - z),
                "families_for_80_percent_power": math.ceil(
                    ((z + normal.inv_cdf(0.8)) * sd / margin) ** 2
                ),
            }
        )
    return {
        "method": "Normal approximation for a paired family mean, assuming sufficient discordant families; sensitivity, not guaranteed power",
        "families": families,
        "margin": margin,
        "alpha_one_sided": alpha,
        "assumed_true_difference": 0,
        "assumptions": rows,
    }


def _settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    settings = dict(raw)
    for key in (
        "margin",
        "alpha",
        "resamples",
        "seed",
        "min_families",
        "min_discordant_families",
        "contrasts",
    ):
        if key not in settings:
            raise ValueError(f"Frozen analysis settings lack {key}")
    margin, alpha = _finite(settings["margin"], "margin"), _finite(settings["alpha"], "alpha")
    if not 0 < margin < 1 or not 0 < alpha < 0.5:
        raise ValueError("Invalid margin or alpha")
    _integer(settings["resamples"], "resamples", 200)
    _integer(settings["seed"], "seed", 0, 2**63 - 1)
    _integer(settings["min_families"], "min_families", 2)
    _integer(settings["min_discordant_families"], "min_discordant_families", 1)
    contrasts = settings["contrasts"]
    if not isinstance(contrasts, list) or not contrasts:
        raise ValueError("A fixed sequence of contrasts is required")
    if len({c["id"] for c in contrasts}) != len(contrasts):
        raise ValueError("Contrast identifiers must be unique")
    return settings


def _score(votes: list[str], observation: dict[str, Any] | None) -> float | None:
    if observation is None or observation["status"] == "missing":
        return None
    if observation["status"] == "completed":
        preference = observation["preference"]
        return sum(vote == preference for vote in votes) / len(votes)
    return 0.0


def _bounds_pass(interval: Mapping[str, Any], threshold: float, *, lower: bool) -> bool:
    keys = ("bootstrap_lower", "t_lower") if lower else ("bootstrap_upper", "t_upper")
    return all(
        interval[key] is not None
        and (interval[key] > threshold if lower else interval[key] < threshold)
        for key in keys
    )


def analyze_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Analyze normalized private records; return aggregate-only evidence.

    jobs must enumerate every scheduled slot before any attempt. Observations use
    canonical preferences, exact decimal cost bounds and those frozen job IDs.
    """
    settings = _settings(bundle["settings"])
    arms = bundle["arms"]
    if not isinstance(arms, list) or not arms or len(set(arms)) != len(arms):
        raise ValueError("Arms must be distinct")
    families = {}
    for family in bundle["families"]:
        identifier, votes = family["family_id"], family["human_votes"]
        if identifier in families:
            raise ValueError("Duplicate family reference")
        if not votes or any(vote not in LABELS for vote in votes):
            raise ValueError("Every family needs observed canonical human votes")
        families[identifier] = family
    if not families:
        raise ValueError("The frozen family cohort is empty")
    jobs: dict[str, dict[str, Any]] = {}
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    slots: set[tuple[str, str, str, tuple[str, ...], int]] = set()
    for job in bundle["jobs"]:
        identifier, family, arm = job["job_id"], job["family_id"], job["arm_id"]
        if identifier in jobs or family not in families or arm not in arms:
            raise ValueError("Duplicate or unknown planned job")
        kind = job.get("kind", "primary")
        if kind not in {"primary", "mirror"}:
            raise ValueError("Unknown scheduled slot kind")
        order = job["order"]
        if order not in [["A", "B"], ["B", "A"]]:
            raise ValueError("Each order must contain both canonical responses")
        repetition = _integer(job.get("repetition", 0), "repetition", 0, 100)
        slot = (family, arm, kind, tuple(order), repetition)
        if slot in slots:
            raise ValueError("Duplicate family/arm/order/repetition slot")
        slots.add(slot)
        jobs[identifier] = job
        if kind == "primary":
            cells[(family, arm)].append(job)
    cohort_signature = None
    for family in families:
        reference_slots = None
        for arm in arms:
            current = cells[(family, arm)]
            if not current:
                raise ValueError("Every family/arm needs its full frozen primary grid")
            signature = sorted((tuple(j["order"]), j.get("repetition", 0)) for j in current)
            if reference_slots is not None and signature != reference_slots:
                raise ValueError("Paired arms must have identical scheduled orders and repetitions")
            reference_slots = signature
        if cohort_signature is not None and reference_slots != cohort_signature:
            raise ValueError("Every family must use the same frozen primary slot design")
        cohort_signature = reference_slots
    observations: dict[str, dict[str, Any]] = {}
    for observation in bundle["observations"]:
        identifier = observation["job_id"]
        if identifier not in jobs or identifier in observations:
            raise ValueError("Unknown or duplicate observation")
        status = observation["status"]
        if status not in ATTEMPTED | {"missing"}:
            raise ValueError("Unknown observation status")
        preference = observation.get("preference")
        if status == "completed" and preference not in LABELS:
            raise ValueError("Completed observations need a canonical preference")
        if status != "completed" and preference not in {None, "abstain"}:
            raise ValueError("Non-completed observations cannot contain a scored preference")
        lower = _money(observation["cost_lower_usd"], "cost_lower_usd")
        upper = _money(observation["cost_upper_usd"], "cost_upper_usd")
        if upper < lower:
            raise ValueError("Cost upper bound is below its lower bound")
        if "displayed_preference" in observation and status == "completed":
            displayed = observation["displayed_preference"]
            order = jobs[identifier]["order"]
            mapped = order[0] if displayed == "A" else order[1] if displayed == "B" else displayed
            if mapped != preference:
                raise ValueError("Displayed preference disagrees with canonical order mapping")
        observations[identifier] = {**observation, "_lower": lower, "_upper": upper}
    missing = sum(
        identifier not in observations or observations[identifier]["status"] == "missing"
        for identifier in jobs
    )
    total_lower = sum((row["_lower"] for row in observations.values()), Decimal(0))
    total_upper = sum((row["_upper"] for row in observations.values()), Decimal(0))
    calibration_upper = _money(
        bundle.get("calibration_cost_upper_usd", str(total_upper)), "calibration_cost_upper_usd"
    )
    if calibration_upper < total_upper:
        raise ValueError("Calibration cost cannot omit retained observation costs")
    scores: dict[str, dict[str, float | None]] = {arm: {} for arm in arms}
    family_costs: dict[str, dict[str, tuple[Decimal, Decimal]]] = {arm: {} for arm in arms}
    summaries = []
    for arm in arms:
        statuses: Counter[str] = Counter()
        order_pairs, swap_changes, position_values = 0, 0, []
        for family, reference in sorted(families.items()):
            current = cells[(family, arm)]
            values = []
            lower, upper = Decimal(0), Decimal(0)
            choices: dict[tuple[str, ...], str] = {}
            for job in current:
                row = observations.get(job["job_id"])
                statuses[row["status"] if row else "missing"] += 1
                values.append(_score(reference["human_votes"], row))
                if row:
                    lower += row["_lower"]
                    upper += row["_upper"]
                    if row["status"] == "completed":
                        choices[tuple(job["order"])] = row["preference"]
                        position_values.append(
                            0
                            if row["preference"] == "tie"
                            else 1
                            if row["preference"] == job["order"][0]
                            else -1
                        )
            scores[arm][family] = (
                math.fsum(v for v in values if v is not None) / len(values)
                if all(v is not None for v in values)
                else None
            )
            family_costs[arm][family] = (lower, upper)
            if ("A", "B") in choices and ("B", "A") in choices:
                order_pairs += 1
                swap_changes += choices[("A", "B")] != choices[("B", "A")]
        complete_scores = [v for v in scores[arm].values() if v is not None]
        lower_agreement = math.fsum(0.0 if v is None else v for v in scores[arm].values()) / len(
            families
        )
        upper_agreement = math.fsum(
            max(Counter(families[f]["human_votes"]).values()) / len(families[f]["human_votes"])
            if v is None
            else v
            for f, v in scores[arm].items()
        ) / len(families)
        summaries.append(
            {
                "arm": arm,
                "families": len(families),
                "complete_families": len(complete_scores),
                "primary_scheduled_calls": sum(statuses.values()),
                "primary_statuses": dict(sorted(statuses.items())),
                "delivered_agreement": (
                    math.fsum(complete_scores) / len(families)
                    if len(complete_scores) == len(families)
                    else None
                ),
                "full_cohort_agreement_bounds": [lower_agreement, upper_agreement],
                "primary_cost_lower_usd": str(
                    sum((v[0] for v in family_costs[arm].values()), Decimal(0))
                ),
                "primary_cost_upper_usd": str(
                    sum((v[1] for v in family_costs[arm].values()), Decimal(0))
                ),
                "order_diagnostic": {
                    "valid_two_order_families": order_pairs,
                    "canonical_swap_disagreements": swap_changes,
                    "swap_inconsistency": swap_changes / order_pairs if order_pairs else None,
                    "first_position_effect": (
                        math.fsum(position_values) / len(position_values)
                        if position_values
                        else None
                    ),
                    "interpretation": "Descriptive; no same-order repeats to isolate stochastic variation.",
                },
            }
        )
    ordered_families = sorted(families)
    comparisons = []
    sequence_open = missing == 0
    interval_options = {k: settings[k] for k in ("alpha", "resamples", "seed", "min_families")}
    for index, pair in enumerate(settings["contrasts"]):
        candidate, baseline = pair["candidate"], pair["baseline"]
        if candidate not in arms or baseline not in arms or candidate == baseline:
            raise ValueError("A contrast must name two different frozen arms")
        complete = all(
            scores[candidate][f] is not None and scores[baseline][f] is not None
            for f in ordered_families
        )
        differences = (
            [scores[candidate][f] - scores[baseline][f] for f in ordered_families]
            if complete
            else []
        )
        quality = (
            quality_interval(
                differences,
                min_discordant_families=settings["min_discordant_families"],
                **interval_options,
            )
            if complete
            else {
                "families": len(families),
                "estimate": None,
                "bootstrap_lower": None,
                "bootstrap_upper": None,
                "t_lower": None,
                "t_upper": None,
                "undefined_reason": "incomplete_frozen_cohort",
            }
        )
        cost_differences = [
            float(family_costs[candidate][f][1] - family_costs[baseline][f][0])
            for f in ordered_families
        ]
        cost = paired_interval(cost_differences, **interval_options)
        quality_pass = complete and _bounds_pass(quality, -settings["margin"], lower=True)
        cost_pass = missing == 0 and _bounds_pass(cost, 0.0, lower=False)
        eligible = sequence_open
        if missing:
            decision, reason = "inconclusive", "incomplete_frozen_grid"
        elif not eligible:
            decision, reason = "descriptive_only", "earlier_fixed_sequence_gate_not_passed"
        elif quality["undefined_reason"] or cost["undefined_reason"]:
            decision, reason = "inconclusive", "uncertainty_not_estimable"
        elif quality_pass and cost_pass:
            decision, reason = "adopt", "quality_and_cost_gates_passed"
        else:
            decision, reason = "do_not_adopt", "quality_or_cost_gate_not_passed"
        sequence_open = sequence_open and decision == "adopt"
        candidate_upper = sum((v[1] for v in family_costs[candidate].values()), Decimal(0))
        baseline_lower = sum((v[0] for v in family_costs[baseline].values()), Decimal(0))
        saving = baseline_lower - candidate_upper
        saving_per_family = saving / len(families)
        primary_calls = len(cells[(ordered_families[0], candidate)])
        savings_ratio = float(saving / baseline_lower) if baseline_lower > 0 else None
        payback_families = (
            int((calibration_upper / saving_per_family).to_integral_value(rounding="ROUND_CEILING"))
            if decision == "adopt" and saving_per_family > 0
            else None
        )
        domains = {}
        for domain in sorted({f.get("domain", "unclassified") for f in families.values()}):
            ids = [
                f for f in ordered_families if families[f].get("domain", "unclassified") == domain
            ]
            available = [
                f
                for f in ids
                if scores[candidate][f] is not None and scores[baseline][f] is not None
            ]
            domains[domain] = {
                "families": len(ids),
                "complete_families": len(available),
                "difference": (
                    math.fsum(scores[candidate][f] - scores[baseline][f] for f in ids) / len(ids)
                    if len(available) == len(ids)
                    else None
                ),
                "interpretation": "Descriptive subgroup estimate; not a subgroup non-inferiority claim.",
            }
        comparisons.append(
            {
                **pair,
                "sequence_position": index + 1,
                "confirmatory_eligible": eligible,
                "decision": decision,
                "reason": reason,
                "quality_difference_candidate_minus_baseline": quality,
                "noninferiority_margin": settings["margin"],
                "quality_gate_passed": quality_pass,
                "cost_difference_candidate_upper_minus_baseline_lower": cost,
                "cost_gate_passed": cost_pass,
                "candidate_primary_cost_upper_usd": str(candidate_upper),
                "baseline_primary_cost_lower_usd": str(baseline_lower),
                "estimated_saving_fraction_lower_accounting": savings_ratio,
                "saving_per_evaluated_question_usd": str(saving_per_family),
                "primary_calls_per_evaluated_question": primary_calls,
                "calibration_cost_upper_usd": str(calibration_upper),
                "payback_evaluated_questions": payback_families,
                "payback_provider_judgments": (
                    payback_families * primary_calls if payback_families is not None else None
                ),
                "domains": domains,
            }
        )
    return {
        "schema_version": 1,
        "families": len(families),
        "scheduled_calls": len(jobs),
        "unattempted_calls": missing,
        "complete_frozen_grid": missing == 0,
        "endpoint": "Delivered individual-human-vote agreement; equal question-family weight.",
        "independence_unit": "Frozen prompt family; orders and calls do not increase N.",
        "settings": settings,
        "arms": summaries,
        "comparisons": comparisons,
        "cost_lower_usd": str(total_lower),
        "cost_upper_usd": str(total_upper),
        "calibration_cost_upper_usd": str(calibration_upper),
        "power_sensitivity": power_sensitivity(
            len(families), margin=settings["margin"], alpha=settings["alpha"]
        ),
        "interpretation": (
            "Adoption requires both paired-bootstrap and Student-t one-sided bounds to pass "
            "the predeclared quality and cost thresholds, in fixed sequence. Intervals are "
            "approximate under independent sampled families. Non-inferiority within the stated "
            "margin is not exact equivalence, objective correctness or population-wide proof. "
            "Costs are configured-rate estimates, not invoice charges. Payback excludes engineering, "
            "hardware, electricity and historical human-label collection costs."
        ),
    }


def _wilson(successes: int, count: int) -> list[float]:
    z = NormalDist().inv_cdf(0.975)
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def simulate_operating_characteristics(
    *,
    families: int = 1000,
    simulations: int = 400,
    resamples: int = 2000,
    seed: int = 27102028,
    min_discordant_families: int = 20,
) -> dict[str, Any]:
    """Seeded bounded-data checks at/beyond the NI null, not a coverage theorem."""
    _integer(families, "families", 30, 100_000)
    _integer(simulations, "simulations", 20, 100_000)
    _integer(resamples, "resamples", 200, 100_000)
    _integer(seed, "seed", 0, 2**63 - 1)
    cases = {
        "null_boundary_balanced": ([-0.5, 0.0, 0.5], [0.06, 0.90, 0.04]),
        "null_boundary_rare_loss": ([-1.0, 0.0], [0.01, 0.99]),
        "inferior_two_points": ([-0.5, 0.0, 0.5], [0.07, 0.90, 0.03]),
        "equal_quality": ([-0.5, 0.0, 0.5], [0.05, 0.90, 0.05]),
    }
    output = []
    for offset, (name, (values, weights)) in enumerate(cases.items()):
        rng = random.Random(seed + offset)
        true_difference = math.fsum(v * w for v, w in zip(values, weights, strict=True))
        passes, covers, estimable = 0, 0, 0
        for repetition in range(simulations):
            sample = rng.choices(values, weights=weights, k=families)
            interval = quality_interval(
                sample,
                resamples=resamples,
                seed=seed + offset * simulations + repetition,
                min_discordant_families=min_discordant_families,
            )
            passes += _bounds_pass(interval, -0.01, lower=True)
            if interval["bootstrap_lower"] is not None:
                estimable += 1
                covers += min(interval["bootstrap_lower"], interval["t_lower"]) <= true_difference
        output.append(
            {
                "scenario": name,
                "values": values,
                "probabilities": weights,
                "true_difference": true_difference,
                "replicates": simulations,
                "estimable_intervals": estimable,
                "quality_gate_passes": passes,
                "quality_gate_pass_rate": passes / simulations,
                "gate_pass_rate_monte_carlo_95_interval": _wilson(passes, simulations),
                "conjunction_lower_bound_coverage_when_estimable": covers / estimable
                if estimable
                else None,
                "coverage_or_inconclusive_fraction": (covers + simulations - estimable)
                / simulations,
                "undefined_intervals": simulations - estimable,
                "undefined_intervals_counted_as_pass": False,
            }
        )
    return {
        "families": families,
        "seed": seed,
        "bootstrap_resamples": resamples,
        "min_discordant_families": min_discordant_families,
        "scenarios": output,
        "interpretation": (
            "Monte Carlo validation under explicitly specified independent bounded distributions. "
            "The boundary scenarios measure false adoption under the NI null; equal_quality measures "
            "power. This does not establish universal confidence coverage. Simulation resamples may "
            "differ from the frozen production-analysis count and are reported explicitly."
        ),
    }


def load_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind normalized evidence to the frozen product plan and exact ledger."""
    from cvals.judge_calibration import normalize_response
    from cvals.review_queues import Rubric
    from scripts.judge_study import confirmatory_run as runner

    plan = runner.load_plan(root)
    protocol = runner.read(root / "approved-protocol.json")
    settings = _settings(plan["configuration"]["analysis"])
    if settings["resamples"] < 20_000:
        raise ValueError("This confirmatory protocol requires at least 20000 resamples")
    rubric = Rubric.model_validate(protocol["rubric"])
    accounting = runner.ledgers(root, plan)
    runner.verify_prefix(root, plan, accounting, require_settled=False)
    ledger_rows = {}
    for model, ledger in accounting.items():
        with ledger._connection() as db:
            for raw in db.execute("SELECT * FROM requests"):
                row = dict(raw)
                if row["attempt_id"] in ledger_rows:
                    raise ValueError("One request is recorded in more than one ledger")
                ledger_rows[row["attempt_id"]] = {**row, "_model": model}
    jobs = {job["job_id"]: job for job in plan["jobs"]}
    if not set(ledger_rows).issubset(jobs):
        raise ValueError("The ledger contains a request outside the frozen cohort")
    paths = {
        path.name.removesuffix(".result.json"): path
        for path in (root / "requests").glob("*.result.json")
    }
    if not set(paths).issubset(ledger_rows):
        raise ValueError("A result lacks an admitted ledger request")
    observations, result_hashes = [], []
    for identifier, path in sorted(paths.items()):
        result = runner.read(path)
        ledger, job = ledger_rows[identifier], jobs[identifier]
        if (
            ledger["_model"] != job["model"]
            or ledger["request_hash"] != job["request_sha256"]
            or result["job_id"] != identifier
            or result["request_id"] != ledger["id"]
            or result["request_sha256"] != job["request_sha256"]
        ):
            raise ValueError("Retained evidence is not bound to its frozen request")
        reserved = _money(result["reservation_usd"], "reservation_usd")
        if reserved != Decimal(ledger["reserved_usd"]):
            raise ValueError("Result reservation disagrees with the ledger")
        known = Decimal(ledger["known_cost_usd"])
        if ledger["state"] == "settled":
            if result["settled"] is not True or _money(result["cost_usd"], "cost_usd") != known:
                raise ValueError("Result settlement disagrees with the ledger")
            lower = upper = known
        else:
            if result["settled"] is not False or result["cost_usd"] is not None:
                raise ValueError("Unsettled usage cannot be reported as a known charge")
            lower, upper = known, max(reserved, known)
        raw_observation = result["observation"]
        normalized = (
            {"status": "error", "preference": None}
            if raw_observation.get("error")
            else normalize_response(raw_observation.get("response"), job["order"], rubric)
        )
        if any(result["normalized"].get(k) != normalized.get(k) for k in ("status", "preference")):
            raise ValueError("Stored normalized judgment disagrees with retained response")
        if normalized["status"] == "missing":
            raise ValueError("An attempted result cannot masquerade as an unattempted slot")
        observation = {
            "job_id": identifier,
            "status": normalized["status"],
            "preference": normalized["preference"],
            "cost_lower_usd": str(lower),
            "cost_upper_usd": str(upper),
        }
        if normalized.get("presented_preference") is not None:
            observation["displayed_preference"] = normalized["presented_preference"]
        observations.append(observation)
        result_hashes.append([identifier, hashlib.sha256(path.read_bytes()).hexdigest()])
    manifest_name = plan["configuration"].get("analysis_manifest_file", "eligible-manifest.json")
    if manifest_name not in plan["evidence_sha256"]:
        raise ValueError("The analysis reference manifest must be frozen evidence")
    manifest = runner.read(runner.within(root, manifest_name))
    selected = manifest["selected"]
    metadata = {row["item_id"]: row for row in selected}
    if len(metadata) != len(selected):
        raise ValueError("Duplicate source-manifest item")
    families = []
    for item in protocol["items"]:
        if item["item_id"] not in metadata:
            raise ValueError("A protocol item lacks frozen source metadata")
        domain = metadata[item["item_id"]]["domain"]
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("A source domain must be a nonempty string")
        labels = [row["label"] for row in item["reference"]["reviewer_labels"]]
        if item["reference"]["source"] != "human_collected" or item["partition"] != "holdout":
            raise ValueError("Confirmation requires held-out historical human votes")
        families.append(
            {
                "family_id": item["family_id"],
                "human_votes": labels,
                "domain": domain,
            }
        )
    accounting_summary = runner.summary(accounting)
    accounting_summary.pop("recorded_at", None)
    bundle = {
        "settings": settings,
        "families": families,
        "arms": [judge["id"] for judge in protocol["judges"]],
        "jobs": [{**job, "arm_id": job["judge_id"]} for job in plan["jobs"]],
        "observations": observations,
        "calibration_cost_upper_usd": accounting_summary["new_committed_usd"],
    }
    provenance = {
        "plan_sha256": runner.digest(plan),
        "protocol_sha256": plan["protocol_sha256"],
        "results_manifest_sha256": runner.digest(result_hashes),
        "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "accounting": accounting_summary,
        "model_configurations": plan["configuration"]["profiles"],
    }
    return bundle, provenance


def analyze_root(root: Path) -> dict[str, Any]:
    bundle, provenance = load_study(root)
    report = analyze_bundle(bundle)
    report["provenance"] = provenance
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--families", type=int, default=1000)
    parser.add_argument("--simulations", type=int, default=400)
    parser.add_argument("--resamples", type=int, default=2000)
    args = parser.parse_args()
    if args.simulate:
        report = simulate_operating_characteristics(
            families=args.families, simulations=args.simulations, resamples=args.resamples
        )
    elif args.root:
        report = analyze_root(args.root)
    elif args.bundle:
        data = args.bundle.read_bytes()
        report = analyze_bundle(json.loads(data))
        report["normalized_bundle_sha256"] = hashlib.sha256(data).hexdigest()
    else:
        parser.error("Provide --root, --bundle or --simulate")
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output.exists() and args.output.read_text() != payload:
        raise ValueError("Refusing to replace a different retained analysis")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload)
    print(
        json.dumps(
            {"output": str(args.output), "sha256": hashlib.sha256(payload.encode()).hexdigest()}
        )
    )


if __name__ == "__main__":
    main()
