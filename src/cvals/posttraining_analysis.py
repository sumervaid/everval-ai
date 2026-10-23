"""Conservative paired monitoring of bounded, frozen evaluation measurements."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

MAX_PAIRS = 100_000


def _integer(value: Any, name: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _finite(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return result


def analyze_regression(
    *,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    expected_pairs: list[dict[str, Any]],
    planned_looks: int,
    planned_domains: int,
    min_clusters: int,
    practical_threshold: float,
    alpha: float = 0.05,
    look_index: int = 1,
) -> dict[str, Any]:
    """Compare candidate minus baseline with an equal-family Hoeffding interval.

    The caller must freeze the family assignment, comparison baseline, evaluation
    revisions, alpha allocation, and maximum looks before observing candidate
    evidence. It must persist consumed looks and prevent adaptive reset/reuse.
    A row is exactly one scenario/repetition. A scenario has one family. Repeats
    are averaged within scenarios, scenarios within families, families equally.
    Family means must be independent; that assumption cannot be checked here.
    """
    _integer(planned_looks, "planned_looks")
    _integer(planned_domains, "planned_domains")
    _integer(min_clusters, "min_clusters", 2)
    _integer(look_index, "look_index")
    alpha = _finite(alpha, "alpha", 0, 1)
    if alpha in {0, 1}:
        raise ValueError("alpha must be strictly between zero and one")
    threshold = _finite(practical_threshold, "practical_threshold", 0, 1)
    if not 0 < len(expected_pairs) <= MAX_PAIRS:
        raise ValueError("expected_pairs must contain between 1 and 100000 rows")
    if len(baseline) > MAX_PAIRS or len(candidate) > MAX_PAIRS:
        raise ValueError("evidence exceeds 100000 rows")
    allocation = alpha / (planned_looks * planned_domains)
    result: dict[str, Any] = {
        "decision": "invalid",
        "valid": False,
        "reasons": [],
        "expected_pairs": len(expected_pairs),
        "matched_pairs": 0,
        "clusters": 0,
        "alpha": alpha,
        "comparison_alpha": allocation,
        "planned_looks": planned_looks,
        "planned_domains": planned_domains,
        "look_index": look_index,
        "practical_threshold": threshold,
        "interval": None,
        "delta": None,
        "baseline_mean": None,
        "candidate_mean": None,
        "method": "paired_equal_family_hoeffding_bonferroni_v1",
        "estimand": "Equal-weight mean over frozen task families of the equal-scenario mean "
        "candidate-minus-baseline expected score, averaging repetitions within each scenario.",
        "assumptions": [
            "Task-family means are independent; related tasks must share a family.",
            "Scores are bounded in [0,1], and higher scores are better.",
            "Families, scenarios, graders, settings, baseline, and policy were frozen before results.",
            "All planned pairs are observed; missing or failed attempts are not silently dropped.",
            "The planned comparison budget is not reset after inspecting evidence.",
            "Checkpoints were not selected or tuned using these same evaluation-family outcomes; "
            "adaptive benchmark reuse is not corrected by the planned-look allocation.",
            "Coverage is conditional on these assumptions; it does not establish causal attribution "
            "or representativeness beyond the frozen measurement population.",
        ],
    }
    reasons: list[str] = result["reasons"]
    if look_index > planned_looks:
        reasons.append("planned comparison budget exhausted")

    def key(row: dict[str, Any]) -> tuple[str, int]:
        scenario = row.get("scenario_id")
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError("scenario_id must be a nonempty string")
        return scenario, _integer(row.get("repetition"), "repetition", 0)

    expected: dict[tuple[str, int], str] = {}
    scenario_families: dict[str, str] = {}
    for row in expected_pairs:
        identity = key(row)
        family = row.get("cluster_id")
        if not isinstance(family, str) or not family.strip():
            raise ValueError("every expected pair requires a nonempty cluster_id")
        if identity in expected:
            raise ValueError("duplicate pair in frozen manifest")
        if identity[0] in scenario_families and scenario_families[identity[0]] != family:
            raise ValueError("one scenario cannot belong to multiple task families")
        expected[identity] = family
        scenario_families[identity[0]] = family

    def index(rows: list[dict[str, Any]], side: str) -> dict[tuple[str, int], float]:
        indexed: dict[tuple[str, int], float] = {}
        seen = set()
        for row in rows:
            try:
                identity = key(row)
                if identity in seen:
                    raise ValueError("duplicate pair")
                seen.add(identity)
                if identity not in expected:
                    raise ValueError("unplanned pair")
                if row.get("cluster_id", expected[identity]) != expected[identity]:
                    raise ValueError("task-family assignment differs from frozen manifest")
                if row.get("status", "completed") != "completed":
                    raise ValueError("attempt is not completed")
                indexed[identity] = _finite(row.get("score"), "score", 0, 1)
            except (ValueError, TypeError) as exc:
                reasons.append(f"{side}: {exc}")
        missing = len(expected.keys() - indexed.keys())
        if missing:
            reasons.append(f"{side}: {missing} planned pairs lack complete valid scores")
        return indexed

    before, after = index(baseline, "baseline"), index(candidate, "candidate")
    result["matched_pairs"] = len(before.keys() & after.keys())
    result["clusters"] = len(set(expected.values()))
    if reasons:
        return result

    scenario_scores: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for identity in expected:
        scenario_scores[identity[0]].append((before[identity], after[identity]))
    families: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for scenario, scores in scenario_scores.items():
        families[scenario_families[scenario]].append(
            (sum(v[0] for v in scores) / len(scores), sum(v[1] for v in scores) / len(scores))
        )
    family_scores = [
        (sum(v[0] for v in scores) / len(scores), sum(v[1] for v in scores) / len(scores))
        for scores in families.values()
    ]
    count = len(family_scores)
    before_mean = sum(value[0] for value in family_scores) / count
    after_mean = sum(value[1] for value in family_scores) / count
    delta = after_mean - before_mean
    # Independent family differences lie in [-1,1]; range width is two.
    radius = math.sqrt(2 * math.log(2 / allocation) / count)
    low, high = max(-1.0, delta - radius), min(1.0, delta + radius)
    result.update(
        valid=True,
        baseline_mean=before_mean,
        candidate_mean=after_mean,
        delta=delta,
        interval=[low, high],
        radius=radius,
        decision="inconclusive",
    )
    if count < min_clusters:
        reasons.append(f"only {count} independent families; minimum is {min_clusters}")
    elif high < -threshold:
        result["decision"] = "regression"
    elif low > threshold:
        result["decision"] = "improvement"
    elif low >= -threshold and high <= threshold:
        result["decision"] = "no_material_change"
    else:
        reasons.append("interval does not establish a practically meaningful change or equivalence")
    return result
