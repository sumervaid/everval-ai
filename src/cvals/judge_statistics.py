"""Auditable agreement, paired diagnostics and prospective sample-size planning.

Labels are canonical response identities, never their displayed positions. Repeated
judgments stay within frozen task families. Human references are fallible observations,
not certificates of truth. This module never calls a model or changes stored judgments.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from itertools import combinations
from statistics import NormalDist, mean
from typing import Any, Literal

MAX_POWER_PAIRS = 2000
REFERENCE_SOURCES = {"human_collected", "execution", "scripted", "unverified"}
STATUSES = {"completed", "abstained", "invalid", "error", "missing"}


def _integer(value: Any, name: str, low: int = 1, high: int = 100_000) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return float(value)


def _categories(labels: Sequence[str]) -> list[str]:
    result = list(labels)
    if (
        not 2 <= len(result) <= 32
        or any(not isinstance(v, str) or not v.strip() for v in result)
        or len(set(result)) != len(result)
        or "abstain" in result
    ):
        raise ValueError(
            "labels must contain 2–32 distinct nonempty strings; abstain is reserved for missing judgments"
        )
    return result


def _label(value: Any, labels: list[str]) -> str | None:
    if value is None or value == "abstain":
        return None
    if not isinstance(value, str) or value not in labels:
        raise ValueError("label is outside the frozen category set")
    return value


def agreement_summary(
    reference: Sequence[str | None],
    observed: Sequence[str | None],
    *,
    labels: Sequence[str] = ("A", "B", "tie"),
    weights: Literal["linear", "quadratic"] | None = None,
) -> dict[str, Any]:
    """Describe paired labels; missing/abstaining rows remain in coverage counts.

    Confusion rows are reference labels and columns are observed labels. Kappa is
    undefined when expected disagreement is zero, including all-one-class agreement.
    Weighted kappa requires a scientifically meaningful frozen category ordering.
    """
    categories = _categories(labels)
    if len(reference) != len(observed):
        raise ValueError("reference and observed labels must have the same length")
    if weights not in {None, "linear", "quadratic"}:
        raise ValueError("weights must be linear, quadratic or None")
    ref = [_label(v, categories) for v in reference]
    obs = [_label(v, categories) for v in observed]
    pairs = [(a, b) for a, b in zip(ref, obs, strict=True) if a is not None and b is not None]
    matrix = {a: {b: 0 for b in categories} for a in categories}
    for a, b in pairs:
        matrix[a][b] += 1
    count = len(pairs)
    matched = sum(a == b for a, b in pairs)
    row = {a: sum(matrix[a].values()) for a in categories}
    column = {b: sum(matrix[a][b] for a in categories) for b in categories}
    observed_disagreement = expected_disagreement = 0.0
    if count:
        for i, a in enumerate(categories):
            for j, b in enumerate(categories):
                distance = float(a != b)
                if weights is not None:
                    distance = abs(i - j) / (len(categories) - 1)
                    if weights == "quadratic":
                        distance *= distance
                observed_disagreement += distance * matrix[a][b] / count
                expected_disagreement += distance * row[a] * column[b] / count**2
    kappa = (
        1 - observed_disagreement / expected_disagreement
        if count and expected_disagreement > 0
        else None
    )
    classes = {}
    for label in categories:
        tp = matrix[label][label]
        fn, fp = row[label] - tp, column[label] - tp
        tn = count - tp - fn - fp
        classes[label] = {
            "reference_count": row[label],
            "observed_count": column[label],
            "true_positive": tp,
            "false_negative": fn,
            "false_positive": fp,
            "true_negative": tn,
            "sensitivity": tp / (tp + fn) if tp + fn else None,
            "specificity": tn / (tn + fp) if tn + fp else None,
            "precision": tp / (tp + fp) if tp + fp else None,
        }
    return {
        "scheduled": len(ref),
        "paired": count,
        "missing_reference": sum(v is None for v in ref),
        "missing_observation": sum(v is None for v in obs),
        "matches": matched,
        "raw_agreement": matched / count if count else None,
        "paired_coverage": count / len(ref) if ref else None,
        "confusion": matrix,
        "classes": classes,
        "kappa": kappa,
        "kappa_weights": weights,
        "kappa_undefined_reason": (
            "no_paired_labels"
            if not count
            else "no_expected_disagreement"
            if kappa is None
            else None
        ),
        "interpretation": "Descriptive agreement among observed labels; missing labels are not agreements.",
    }


def krippendorff_alpha(
    ratings: Sequence[Sequence[str | None]],
    *,
    labels: Sequence[str] = ("A", "B", "tie"),
    level: Literal["nominal", "ordinal"] = "nominal",
) -> dict[str, Any]:
    """Compute coincidence-based alpha with variable rater counts and missingness.

    Rows are items. Only items with at least two observed ratings contribute to
    coincidence counts. Ordinal distances use cumulative coincidence marginals,
    not squared arbitrary numeric category codes. Category order must be frozen.
    """
    categories = _categories(labels)
    if level not in {"nominal", "ordinal"}:
        raise ValueError("level must be nominal or ordinal")
    rows = [[v for raw in row if (v := _label(raw, categories)) is not None] for row in ratings]
    eligible = [row for row in rows if len(row) >= 2]
    coincidences = {a: {b: 0.0 for b in categories} for a in categories}
    for row in eligible:
        counts = Counter(row)
        for a, na in counts.items():
            for b, nb in counts.items():
                coincidences[a][b] += na * (nb - (a == b)) / (len(row) - 1)
    marginals = {a: sum(coincidences[a].values()) for a in categories}
    count = sum(marginals.values())
    observed = expected = 0.0
    if count > 1:
        for i, a in enumerate(categories):
            for j, b in enumerate(categories):
                if i == j:
                    continue
                distance = 1.0
                if level == "ordinal":
                    low, high = sorted((i, j))
                    between = sum(marginals[c] for c in categories[low : high + 1])
                    distance = (between - (marginals[a] + marginals[b]) / 2) ** 2
                observed += coincidences[a][b] * distance / count
                expected += marginals[a] * marginals[b] * distance / (count * (count - 1))
    value = 1 - observed / expected if expected > 0 else None
    return {
        "alpha": value,
        "level": level,
        "categories": categories,
        "items": len(rows),
        "eligible_items": len(eligible),
        "items_with_fewer_than_two_ratings": len(rows) - len(eligible),
        "observed_ratings": sum(len(row) for row in rows),
        "coincidence_ratings": count,
        "coincidences": coincidences,
        "observed_disagreement": observed if count else None,
        "expected_disagreement": expected if count else None,
        "undefined_reason": (
            "no_pairable_ratings"
            if not count
            else "no_expected_disagreement"
            if value is None
            else None
        ),
    }


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    location = probability * (len(ordered) - 1)
    low, high = math.floor(location), math.ceil(location)
    return ordered[low] + (location - low) * (ordered[high] - ordered[low])


def family_mean_interval(
    values: Mapping[str, Sequence[float]],
    *,
    alpha: float = 0.05,
    planned_comparisons: int = 1,
    lower: float = 0.0,
    upper: float = 1.0,
    resamples: int = 2000,
    seed: int = 17,
    min_families: int = 20,
) -> dict[str, Any]:
    """Equal-family mean with approximate cluster bootstrap and bounded sensitivity.

    Values within a family are first averaged. Families, not judgments or repeats,
    are resampled. Degenerate bootstrap distributions never become zero-width
    confidence claims. The Hoeffding sensitivity is conditional on independence.
    """
    alpha = _number(alpha, "alpha", 0, 1)
    if alpha in {0, 1}:
        raise ValueError("alpha must be strictly between zero and one")
    planned_comparisons = _integer(planned_comparisons, "planned_comparisons", high=10000)
    resamples = _integer(resamples, "resamples", low=100, high=100_000)
    min_families = _integer(min_families, "min_families", low=2)
    _integer(seed, "seed", low=0, high=2**63 - 1)
    if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        raise ValueError("finite lower < upper bounds are required")
    means = []
    for family, rows in sorted(values.items()):
        if not isinstance(family, str) or not family.strip() or not rows:
            raise ValueError("families require a nonempty identity and at least one value")
        means.append(mean(_number(v, "family value", lower, upper) for v in rows))
    count = len(means)
    allocation = alpha / planned_comparisons
    result: dict[str, Any] = {
        "estimate": mean(means) if means else None,
        "families": count,
        "observations": sum(len(v) for v in values.values()),
        "alpha": alpha,
        "planned_comparisons": planned_comparisons,
        "comparison_alpha": allocation,
        "estimand": "Equal-weight family mean of within-family item means.",
        "interval": None,
        "method": "family_percentile_bootstrap",
        "resamples": resamples,
        "seed": seed,
        "min_families": min_families,
        "undefined_reason": None,
        "bounded_sensitivity_interval": None,
        "assumptions": [
            "Family assignments and analysis parameters were frozen before results.",
            "Family means are independent and represent the declared sampling population.",
            "Bootstrap coverage is approximate and does not correct selection bias.",
            "The comparison allocation must include every confirmatory statistic inspected.",
        ],
    }
    if not count:
        result["undefined_reason"] = "no_observed_families"
        return result
    center = mean(means)
    radius = (upper - lower) * math.sqrt(math.log(2 / allocation) / (2 * count))
    result["bounded_sensitivity_interval"] = [
        max(lower, center - radius),
        min(upper, center + radius),
    ]
    if count < min_families:
        result["undefined_reason"] = "too_few_independent_families"
        return result
    if min(means) == max(means):
        result["undefined_reason"] = "degenerate_family_distribution"
        return result
    # Require at least ten expected bootstrap draws in each reported tail.
    if resamples * allocation / 2 < 10:
        result["undefined_reason"] = "insufficient_resamples_for_allocated_alpha"
        return result
    generator = random.Random(seed)
    samples = [mean(generator.choices(means, k=count)) for _ in range(resamples)]
    low, high = _quantile(samples, allocation / 2), _quantile(samples, 1 - allocation / 2)
    if low == high:
        result["undefined_reason"] = "degenerate_bootstrap_interval"
    else:
        result["interval"] = [low, high]
    return result


def _binomial_probabilities(n: int, p: float) -> list[float]:
    if p == 0:
        return [1.0] + [0.0] * n
    if p == 1:
        return [0.0] * n + [1.0]
    mode = min(n, math.floor((n + 1) * p))
    values = [0.0] * (n + 1)
    values[mode] = math.exp(
        math.lgamma(n + 1)
        - math.lgamma(mode + 1)
        - math.lgamma(n - mode + 1)
        + mode * math.log(p)
        + (n - mode) * math.log1p(-p)
    )
    for k in range(mode, 0, -1):
        values[k - 1] = values[k] * k / (n - k + 1) * (1 - p) / p
    for k in range(mode, n):
        values[k + 1] = values[k] * (n - k) / (k + 1) * p / (1 - p)
    total = sum(values)
    return [v / total for v in values]


@lru_cache(maxsize=32)
def _critical_tails(n: int, alpha: float) -> tuple[int, ...]:
    cuts = []
    for discordant in range(n + 1):
        cumulative, cut = 0.0, -1
        for wins, probability in enumerate(_binomial_probabilities(discordant, 0.5)):
            if wins > discordant // 2:
                break
            cumulative += probability
            if 2 * cumulative <= alpha:
                cut = wins
        cuts.append(cut)
    return tuple(cuts)


def exact_mcnemar_power(
    pairs: int,
    discordance: float,
    difference: float,
    *,
    alpha: float = 0.05,
    planned_comparisons: int = 1,
) -> float:
    """Exact unconditional power under a declared independent paired-Bernoulli model.

    Discordance is p01+p10 and difference is p10-p01. Conditional on D discordant
    pairs, W has Binomial(D, (discordance+difference)/(2*discordance)). Rejection
    uses the exact two-sided McNemar test, without mid-p or continuity adjustment.
    """
    pairs = _integer(pairs, "pairs", high=MAX_POWER_PAIRS)
    discordance = _number(discordance, "discordance", 0, 1)
    difference = _number(difference, "difference", -discordance, discordance)
    alpha = _number(alpha, "alpha", 0, 1)
    if alpha in {0, 1}:
        raise ValueError("alpha must be strictly between zero and one")
    comparisons = _integer(planned_comparisons, "planned_comparisons", high=10000)
    if discordance == 0:
        return 0.0
    probability = (discordance + difference) / (2 * discordance)
    cuts = _critical_tails(pairs, alpha / comparisons)
    total = 0.0
    for discordant, pd in enumerate(_binomial_probabilities(pairs, discordance)):
        cut = cuts[discordant]
        if cut < 0 or pd == 0:
            continue
        wins = _binomial_probabilities(discordant, probability)
        total += pd * (sum(wins[: cut + 1]) + sum(wins[discordant - cut :]))
    return min(1.0, max(0.0, total))


def paired_power_plan(
    pairs: int,
    discordance: float,
    *,
    target_power: float = 0.8,
    difference: float = 0.1,
    alpha: float = 0.05,
    planned_comparisons: int = 1,
) -> dict[str, Any]:
    """Prospective MDE for one paired binary outcome per independent task/family."""
    target = _number(target_power, "target_power", 0, 1)
    if target in {0, 1}:
        raise ValueError("target_power must be strictly between zero and one")
    power = exact_mcnemar_power(
        pairs, discordance, difference, alpha=alpha, planned_comparisons=planned_comparisons
    )

    def at(delta: float) -> float:
        return exact_mcnemar_power(
            pairs, discordance, delta, alpha=alpha, planned_comparisons=planned_comparisons
        )

    maximum = at(discordance)
    detectable = None
    if maximum >= target:
        low, high = 0.0, discordance
        for _ in range(32):
            middle = (low + high) / 2
            if at(middle) >= target:
                high = middle
            else:
                low = middle
        detectable = high
    return {
        "method": "exact_unconditional_two_sided_mcnemar",
        "independent_pairs": pairs,
        "assumed_discordance": discordance,
        "assumed_difference": difference,
        "power_at_assumed_difference": power,
        "target_power": target,
        "minimum_detectable_difference": detectable,
        "maximum_power_at_feasible_difference": maximum,
        "alpha": alpha,
        "planned_comparisons": planned_comparisons,
        "comparison_alpha": alpha / planned_comparisons,
        "assumptions": [
            "One prespecified paired binary outcome per independent task/family.",
            "Discordance is a prospective assumption, not inferred from the confirmatory result.",
            "Repeated ratings and extra models do not increase the independent pair count.",
            "This design does not apply directly to fractional family scores or ordinal agreement.",
            "Missing outcomes, family dependence, exclusions and adaptive stopping require a new design.",
        ],
    }


def _human_reliability(items: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    human = [i for i in items if i["reference"].get("source") == "human_collected"]
    ratings = []
    overlaps: dict[tuple[str, str], tuple[list[str | None], list[str | None]]] = {}
    for item in human:
        by_reviewer: dict[str, str | None] = {}
        rows = item["reference"].get("reviewer_labels", [])
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("at most 100 human ratings per item are supported")
        for row in rows:
            reviewer = row.get("reviewer_id")
            if not isinstance(reviewer, str) or not reviewer.strip() or reviewer in by_reviewer:
                raise ValueError("human reviewer identities must be distinct within each item")
            by_reviewer[reviewer] = _label(row.get("label"), labels)
        ratings.append(list(by_reviewer.values()))
        for left, right in combinations(sorted(by_reviewer), 2):
            if (left, right) not in overlaps:
                if len(overlaps) >= 10000:
                    raise ValueError("human reliability exceeds 10000 distinct reviewer pairs")
                overlaps[left, right] = ([], [])
            a, b = overlaps[left, right]
            a.append(by_reviewer[left])
            b.append(by_reviewer[right])
    pairs = [
        {
            "left_reviewer": left,
            "right_reviewer": right,
            **agreement_summary(*values, labels=labels),
        }
        for (left, right), values in sorted(overlaps.items())
    ]
    return {
        "human_source_items": len(human),
        "alpha": krippendorff_alpha(ratings, labels=labels),
        "reviewer_pairs": pairs,
        "interpretation": "Pre-adjudication human agreement; source and independence are caller-declared.",
    }


def analyze_calibration(
    items: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    labels: Sequence[str] = ("A", "B", "tie"),
    repetitions: int = 1,
    alpha: float = 0.05,
    planned_comparisons: int = 1,
    judge_models: dict[str, str] | None = None,
    primary_endpoint: Literal["reference", "human_votes"] = "reference",
    resamples: int = 2000,
    seed: int = 17,
    min_families: int = 20,
) -> dict[str, Any]:
    """Analyze one frozen partition of canonical judgments without modifying it.

    Items need item_id, task_id, family_id and reference{source,label,reviewer_labels}.
    Observations need item_id, judge_id, repetition (zero-based), order [A,B]/[B,A],
    status and canonical preference. Supply missing rows or judge_models to retain
    judges with no observations. Both display orders are scheduled for every repeat.
    """
    categories = _categories(labels)
    if primary_endpoint not in {"reference", "human_votes"}:
        raise ValueError("primary_endpoint must be reference or human_votes")
    if set(categories) != {"A", "B", "tie"}:
        raise ValueError("paired calibration requires canonical A, B and tie labels")
    repetitions = _integer(repetitions, "repetitions", high=100)
    _integer(len(items), "items", high=100_000)
    _integer(len(observations), "observations", low=0, high=1_000_000)
    # Validate interval settings even when no observations are available.
    interval_options: dict[str, Any] = {
        "alpha": alpha,
        "planned_comparisons": planned_comparisons,
        "resamples": resamples,
        "seed": seed,
        "min_families": min_families,
    }
    family_mean_interval({}, **interval_options)
    indexed: dict[str, dict[str, Any]] = {}
    task_families: dict[str, str] = {}
    partitions = set()
    for item in items:
        for field in ("item_id", "task_id", "family_id"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"items require a nonempty {field}")
        identity, task, family = item["item_id"], item["task_id"], item["family_id"]
        if identity in indexed:
            raise ValueError("duplicate item in frozen cohort")
        if task in task_families and task_families[task] != family:
            raise ValueError("one task cannot belong to different families")
        task_families[task] = family
        reference = item.get("reference")
        if not isinstance(reference, dict) or reference.get("source") not in REFERENCE_SOURCES:
            raise ValueError("reference source must be explicit")
        _label(reference.get("label"), categories)
        indexed[identity] = item
        partitions.add(item.get("partition", "unspecified"))
    if len(partitions) != 1:
        raise ValueError("development and holdout partitions must be analyzed separately")
    models = judge_models or {}
    judges = set(models)
    rows: dict[tuple[str, str, int, tuple[str, ...]], dict[str, Any]] = {}
    orders = (("A", "B"), ("B", "A"))
    for row in observations:
        identity, judge = row.get("item_id"), row.get("judge_id")
        if identity not in indexed or not isinstance(judge, str) or not judge.strip():
            raise ValueError("observation has an unplanned item or invalid judge identity")
        repetition = _integer(row.get("repetition"), "repetition", low=0, high=repetitions - 1)
        order = tuple(row.get("order", ()))
        if order not in orders or row.get("status") not in STATUSES:
            raise ValueError("observation has an unsupported order or status")
        key = identity, judge, repetition, order
        if key in rows:
            raise ValueError("duplicate judgment in frozen grid")
        if row["status"] == "completed" and _label(row.get("preference"), categories) is None:
            raise ValueError("completed judgments require an observed canonical preference")
        rows[key] = row
        judges.add(judge)
    sources = Counter(item["reference"]["source"] for item in items)
    summaries = []
    for judge in sorted(judges):
        reference_labels, predictions = [], []
        human_labels, human_predictions = [], []
        item_scores = []
        vote_accuracy: dict[str, list[float]] = defaultdict(list)
        observed_vote_items = 0
        status_counts: Counter[str] = Counter()
        item_accuracy: dict[str, list[float]] = defaultdict(list)
        swap: dict[str, list[float]] = defaultdict(list)
        repeat: dict[str, list[float]] = defaultdict(list)
        position: dict[str, list[float]] = defaultdict(list)
        excess: dict[str, list[float]] = defaultdict(list)
        self_residual: dict[str, list[float]] = defaultdict(list)
        self_choices, reference_self_choices = [], []
        strata: dict[str, tuple[list[str | None], list[str | None]]] = {
            source: ([], []) for source in sources
        }
        complete_items = 0
        observed_reference_items = 0
        matched_swaps = repeat_pairs = 0
        selected_first = selected_non_tie = 0
        self_items = 0
        for identity, item in indexed.items():
            family = item["family_id"]
            reference = _label(item["reference"].get("label"), categories)
            votes = (
                [
                    vote
                    for rating in item["reference"].get("reviewer_labels", [])
                    if (vote := _label(rating.get("label"), categories)) is not None
                ]
                if item["reference"]["source"] == "human_collected"
                else []
            )
            grid: dict[tuple[int, tuple[str, ...]], str | None] = {}
            for rep in range(repetitions):
                for order in orders:
                    observed_row = rows.get((identity, judge, rep, order))
                    status = observed_row["status"] if observed_row is not None else "missing"
                    status_counts[status] += 1
                    observed = (
                        observed_row["preference"]
                        if observed_row is not None and status == "completed"
                        else None
                    )
                    grid[rep, order] = observed
                    reference_labels.append(reference)
                    predictions.append(observed)
                    human_labels.extend(votes)
                    human_predictions.extend([observed] * len(votes))
                    strata[item["reference"]["source"]][0].append(reference)
                    strata[item["reference"]["source"]][1].append(observed)
            complete = all(value is not None for value in grid.values())
            complete_items += complete
            reference_score = vote_score = None
            if complete and reference is not None:
                observed_reference_items += 1
                reference_score = mean(value == reference for value in grid.values())
                item_accuracy[family].append(reference_score)
            if complete and votes:
                observed_vote_items += 1
                vote_score = mean(value == vote for value in grid.values() for vote in votes)
                vote_accuracy[family].append(vote_score)
            item_scores.append(
                {
                    "item_id": identity,
                    "task_id": item["task_id"],
                    "family_id": family,
                    "reference_score": reference_score,
                    "human_vote_score": vote_score,
                    "score": reference_score if primary_endpoint == "reference" else vote_score,
                    "human_votes": len(votes),
                    "complete_grid": complete,
                }
            )
            item_swaps, item_position = [], []
            for rep in range(repetitions):
                left, right = grid[rep, orders[0]], grid[rep, orders[1]]
                if left is None or right is None:
                    continue
                matched_swaps += 1
                item_swaps.append(float(left != right))
                scores = []
                for value, order in ((left, orders[0]), (right, orders[1])):
                    scores.append(0.0 if value == "tie" else 1.0 if value == order[0] else -1.0)
                    if value != "tie":
                        selected_non_tie += 1
                        selected_first += value == order[0]
                item_position.append(mean(scores))
            item_repeats = []
            for order in orders:
                for a, b in combinations(range(repetitions), 2):
                    left, right = grid[a, order], grid[b, order]
                    if left is not None and right is not None:
                        repeat_pairs += 1
                        item_repeats.append(float(left != right))
            if complete:
                swap[family].append(mean(item_swaps))
                position[family].append(mean(item_position))
                if item_repeats:
                    repeat[family].append(mean(item_repeats))
                    excess[family].append(mean(item_swaps) - mean(item_repeats))
            generators = item.get("generator_models", {})
            own = [
                label
                for label in ("A", "B")
                if models.get(judge) and generators.get(label) == models[judge]
            ]
            if complete and reference is not None and len(own) == 1:
                self_items += 1
                actual = mean(0.5 if v == "tie" else float(v == own[0]) for v in grid.values())
                expected = 0.5 if reference == "tie" else float(reference == own[0])
                self_choices.append(actual)
                reference_self_choices.append(expected)
                self_residual[family].append(actual - expected)
        scheduled = len(items) * repetitions * 2
        full = status_counts["completed"] == scheduled
        reference_complete = observed_reference_items == len(items)
        accuracy = family_mean_interval(item_accuracy, **interval_options)
        accuracy["complete_cohort"] = full and reference_complete
        if not accuracy["complete_cohort"]:
            accuracy["interval"] = None
            accuracy["bounded_sensitivity_interval"] = None
            accuracy["undefined_reason"] = "incomplete_frozen_cohort"
        source_mode = next(iter(sources)) if len(sources) == 1 else "mixed_sources"
        if source_mode in {"mixed_sources", "scripted", "unverified"}:
            accuracy["interval"] = None
            accuracy["bounded_sensitivity_interval"] = None
            accuracy["undefined_reason"] = "reference_provenance_not_empirical_or_mixed"
        vote_metric = family_mean_interval(vote_accuracy, **interval_options)
        vote_metric["complete_cohort"] = (
            full and observed_vote_items == len(items) and source_mode == "human_collected"
        )
        if not vote_metric["complete_cohort"]:
            vote_metric["interval"] = None
            vote_metric["bounded_sensitivity_interval"] = None
            vote_metric["undefined_reason"] = "incomplete_human_vote_cohort"
        primary = accuracy if primary_endpoint == "reference" else vote_metric
        summaries.append(
            {
                "judge_id": judge,
                "model_id": models.get(judge),
                "status_counts": {status: status_counts[status] for status in sorted(STATUSES)},
                "scheduled": scheduled,
                "complete_items": complete_items,
                "items": len(items),
                "reference_mode": source_mode,
                "reference_strata": {
                    source: agreement_summary(*values, labels=categories)
                    for source, values in sorted(strata.items())
                },
                "agreement": agreement_summary(reference_labels, predictions, labels=categories),
                "family_agreement": accuracy,
                "human_vote_agreement": agreement_summary(
                    human_labels, human_predictions, labels=categories
                ),
                "family_human_vote_agreement": vote_metric,
                "primary_endpoint": primary_endpoint,
                "primary": primary,
                "item_scores": item_scores,
                "interval_scope": "Individual-judge intervals and bias diagnostics are exploratory; confirmatory paired contrasts require a separate frozen allocation.",
                "order": {
                    "matched_order_pairs": matched_swaps,
                    "swap_inconsistency": family_mean_interval(swap, **interval_options),
                    "first_position_choices": selected_first,
                    "non_tie_choices_in_matched_pairs": selected_non_tie,
                    "first_position_choice_rate": selected_first / selected_non_tie
                    if selected_non_tie
                    else None,
                    "directional_position_effect": family_mean_interval(
                        position, lower=-1, upper=1, **interval_options
                    ),
                    "complete_cohort": full,
                },
                "repeatability": {
                    "same_order_pairs": repeat_pairs,
                    "same_order_disagreement": family_mean_interval(repeat, **interval_options),
                    "excess_swap_disagreement": family_mean_interval(
                        excess, lower=-1, upper=1, **interval_options
                    ),
                    "interpretation": "Swap inconsistency is not proof of position bias; compare same-order noise and directional effects.",
                },
                "self_preference": {
                    "eligible_items": self_items,
                    "own_choice_rate": mean(self_choices) if self_choices else None,
                    "reference_own_choice_rate": mean(reference_self_choices)
                    if reference_self_choices
                    else None,
                    "reference_conditioned_difference": family_mean_interval(
                        self_residual, lower=-1, upper=1, **interval_options
                    ),
                    "interpretation": "Descriptive exact-model association conditional on supplied generator identities and fallible references; not causal proof of bias.",
                },
            }
        )
    return {
        "schema_version": 1,
        "partition": next(iter(partitions)),
        "items": len(items),
        "tasks": len(task_families),
        "families": len(set(task_families.values())),
        "reference_sources": dict(sorted(sources.items())),
        "human_reliability": _human_reliability(items, categories),
        "judges": summaries,
        "analysis": {
            **interval_options,
            "labels": categories,
            "repetitions": repetitions,
            "primary_endpoint": primary_endpoint,
            "judge_interval_scope": "exploratory",
        },
        "limitations": [
            "Human labels, blinding and generator provenance are declared; agreement does not prove truth.",
            "Scripted and unverified references cannot establish human validity.",
            "Repeated judgments and extra models do not increase the independent family count.",
            "Raw agreement and kappa pool observed labels; family agreement weights families equally.",
            "Human-vote agreement retains disagreements: voters are equal within each item, then items within families.",
            "Reference-label and human-vote endpoints answer different questions and must not be switched after seeing results.",
            "Order, repeatability and self-preference diagnostics condition on complete available items.",
            "Missing observations remain explicit; incomplete primary cohorts have no inferential interval.",
            "Planned comparisons and sampling assumptions must be frozen before results, not selected afterward.",
        ],
    }


def compare_calibrated_judges(
    report: dict[str, Any],
    left_id: str,
    right_id: str,
    *,
    alpha: float = 0.05,
    planned_comparisons: int = 1,
    resamples: int = 5000,
    seed: int = 17,
    min_families: int = 20,
) -> dict[str, Any]:
    """Paired right-minus-left difference on one prespecified primary endpoint.

    The caller must freeze the chosen contrasts and total allocation before judge
    outcomes. An analysis report cannot itself establish prospective registration.
    """
    if left_id == right_id:
        raise ValueError("a paired contrast requires different judge configurations")
    indexed = {row["judge_id"]: row for row in report.get("judges", [])}
    if left_id not in indexed or right_id not in indexed:
        raise ValueError("both judge identities must exist in the analysis")
    left, right = indexed[left_id], indexed[right_id]
    if left.get("primary_endpoint") != right.get("primary_endpoint"):
        raise ValueError("paired judges must use the same prespecified endpoint")

    def scores(judge: dict[str, Any]) -> dict[str, dict[str, Any]]:
        values = {}
        for row in judge.get("item_scores", []):
            identity = row["item_id"]
            if identity in values:
                raise ValueError("duplicate item in judge analysis")
            values[identity] = row
        return values

    a, b = scores(left), scores(right)
    if set(a) != set(b) or len(a) != report.get("items"):
        raise ValueError("paired judges must cover the identical frozen cohort")
    differences: dict[str, list[float]] = defaultdict(list)
    incomplete = []
    for identity in sorted(a):
        before, after = a[identity], b[identity]
        if before["family_id"] != after["family_id"] or before["task_id"] != after["task_id"]:
            raise ValueError("paired task/family assignments differ")
        if before.get("score") is None or after.get("score") is None:
            incomplete.append(identity)
            continue
        difference = _number(after["score"], "score", 0, 1) - _number(
            before["score"], "score", 0, 1
        )
        differences[before["family_id"]].append(difference)
    metric = family_mean_interval(
        differences,
        lower=-1,
        upper=1,
        alpha=alpha,
        planned_comparisons=planned_comparisons,
        resamples=resamples,
        seed=seed,
        min_families=min_families,
    )
    complete = (
        not incomplete
        and left.get("primary", {}).get("complete_cohort") is True
        and right.get("primary", {}).get("complete_cohort") is True
        and left.get("reference_mode") in {"human_collected", "execution"}
        and right.get("reference_mode") == left.get("reference_mode")
    )
    if not complete:
        metric["interval"] = None
        metric["bounded_sensitivity_interval"] = None
        metric["undefined_reason"] = "incomplete_or_nonempirical_paired_cohort"
    return {
        "left": left_id,
        "right": right_id,
        "direction": "right_minus_left",
        "primary_endpoint": left.get("primary_endpoint"),
        "expected_items": report["items"],
        "matched_items": len(a) - len(incomplete),
        "missing_items": incomplete,
        "complete_cohort": complete,
        "difference": metric,
        "interpretation": "Conditional paired comparison; prospective contrast selection, multiplicity and family independence are caller-declared.",
    }


def paired_mean_power_plan(
    families: int,
    difference_sd: float,
    *,
    difference: float = 0.1,
    target_power: float = 0.8,
    alpha: float = 0.05,
    planned_comparisons: int = 1,
) -> dict[str, Any]:
    """Approximate prospective power for a mean paired family-score difference.

    The SD is an explicit planning assumption, not estimated from holdout data.
    This normal approximation is suitable for sensitivity planning of fractional
    agreement scores; it is not an exact small-sample guarantee or a bootstrap CI.
    """
    families = _integer(families, "families", low=2)
    sd = _number(difference_sd, "difference_sd", 0, 1)
    if sd == 0:
        raise ValueError("difference_sd must be positive; zero pilot variance is not certainty")
    delta = _number(difference, "difference", -1, 1)
    target = _number(target_power, "target_power", 0, 1)
    total_alpha = _number(alpha, "alpha", 0, 1)
    comparisons = _integer(planned_comparisons, "planned_comparisons", high=10000)
    if target in {0, 1} or total_alpha in {0, 1}:
        raise ValueError("alpha and target_power must be strictly between zero and one")
    allocation = total_alpha / comparisons
    normal = NormalDist()
    threshold = normal.inv_cdf(1 - allocation / 2)
    standard_error = sd / math.sqrt(families)

    def power(effect: float) -> float:
        shift = effect / standard_error
        return normal.cdf(-threshold - shift) + normal.cdf(shift - threshold)

    detectable = None
    if power(1) >= target:
        low, high = 0.0, 1.0
        for _ in range(40):
            middle = (low + high) / 2
            if power(middle) >= target:
                high = middle
            else:
                low = middle
        detectable = high
    return {
        "method": "paired_family_mean_normal_approximation",
        "independent_families": families,
        "assumed_paired_difference_sd": sd,
        "assumed_difference": delta,
        "power_at_assumed_difference": power(delta),
        "target_power": target,
        "minimum_detectable_difference": detectable,
        "standard_error": standard_error,
        "alpha": total_alpha,
        "planned_comparisons": comparisons,
        "comparison_alpha": allocation,
        "assumptions": [
            "One paired family mean per independent representative family.",
            "Paired-difference SD is prespecified or estimated only from separate development data.",
            "Normal approximation and assumed SD can be inaccurate for small or saturated populations.",
            "Report sensitivity to plausible SD values; this is not a guarantee of achieved power.",
            "The frozen evaluation's bootstrap interval is a separate analysis method.",
        ],
    }
