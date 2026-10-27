"""Post-hoc finite-cohort delivered-agreement bounds, never confidence intervals."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any

LABELS = {"A", "B", "tie"}
STATUSES = {"completed", "abstained", "invalid", "error", "missing"}


def identification_bounds(
    protocol: dict[str, Any], observations: list[dict[str, Any]], *, partition: str = "holdout"
) -> dict[str, Any]:
    """Bound unattempted outcomes while retaining every frozen scheduled cell."""
    items = {item["item_id"]: item for item in protocol["items"] if item["partition"] == partition}
    judges = {judge["id"]: judge for judge in protocol["judges"]}
    if not items or len(items) != sum(item["partition"] == partition for item in protocol["items"]):
        raise ValueError("A nonempty unique frozen item cohort is required")
    repetitions = protocol["repetitions"]
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("Frozen repetitions must be positive")
    rows = {}
    for row in observations:
        key = (row["item_id"], row["judge_id"], row["repetition"], tuple(row["order"]))
        if (
            key in rows
            or key[0] not in items
            or key[1] not in judges
            or type(key[2]) is not int
            or not 0 <= key[2] < repetitions
            or key[3] not in (("A", "B"), ("B", "A"))
            or row["status"] not in STATUSES
        ):
            raise ValueError("Observations must belong uniquely to the frozen judgment grid")
        if row["status"] == "completed" and row.get("preference") not in LABELS:
            raise ValueError("Completed observations need a canonical label")
        rows[key] = row
    summaries = []
    for judge_id, judge in judges.items():
        lower, upper = defaultdict(list), defaultdict(list)
        counts = Counter()
        for item_id, item in items.items():
            reference = item["reference"]
            if protocol["primary_endpoint"] == "human_votes":
                if reference["source"] != "human_collected":
                    raise ValueError("Human-vote bounds need declared human references")
                votes = [
                    row["label"] for row in reference["reviewer_labels"] if row["label"] in LABELS
                ]
            else:
                votes = [reference["label"]] if reference["label"] in LABELS else []
            if not votes:
                raise ValueError("A missing reference cannot receive an invented agreement bound")
            maximum = max(Counter(votes).values()) / len(votes)
            observed_credit, missing = 0.0, 0
            for repetition in range(repetitions):
                for order in (("A", "B"), ("B", "A")):
                    row = rows.get((item_id, judge_id, repetition, order))
                    status = row["status"] if row is not None else "missing"
                    counts[status] += 1
                    if status == "missing":
                        missing += 1
                    elif status == "completed":
                        observed_credit += mean(row["preference"] == vote for vote in votes)
            denominator = 2 * repetitions
            lower[item["family_id"]].append(observed_credit / denominator)
            upper[item["family_id"]].append((observed_credit + missing * maximum) / denominator)
        summaries.append(
            {
                "judge_id": judge_id,
                "model_id": judge["model"],
                "identification_bounds": [
                    mean(mean(v) for v in lower.values()),
                    mean(mean(v) for v in upper.values()),
                ],
                "scheduled_judgments": len(items) * repetitions * 2,
                "unattempted_judgments": counts["missing"],
                "status_counts": {status: counts[status] for status in sorted(STATUSES)},
            }
        )
    by_judge = {row["judge_id"]: row for row in summaries}
    contrasts = []
    for pair in protocol.get("primary_contrasts", []):
        left, right = (
            by_judge[pair["left"]]["identification_bounds"],
            by_judge[pair["right"]]["identification_bounds"],
        )
        contrasts.append(
            {
                "left": pair["left"],
                "right": pair["right"],
                "direction": "right_minus_left",
                "identification_bounds": [right[0] - left[1], right[1] - left[0]],
            }
        )
    return {
        "analysis_role": "post_hoc_descriptive_identification_sensitivity",
        "partition": partition,
        "items": len(items),
        "families": len({item["family_id"] for item in items.values()}),
        "endpoint": protocol["primary_endpoint"],
        "judges": summaries,
        "paired_contrasts": contrasts,
        "method": "Observed attempted outcomes are fixed; invalid, abstained and error deliveries score zero. Each unattempted judgment ranges from zero to its question's maximum reference-vote share, with original scheduled denominators. Items are averaged within frozen families, then families receive equal weight.",
        "interpretation": "Finite-cohort identification uncertainty, not a confidence interval, statistical-significance test, population claim or replacement for the frozen primary. Equal endpoints identify a fixed-cohort value only. No independence assumption or imputation is used.",
    }
