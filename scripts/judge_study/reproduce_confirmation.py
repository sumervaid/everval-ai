"""Reproduce the confirmation from public family aggregates, without model calls.

Requires Python 3.12+ and the source checkout; no third-party packages, credentials
or private human/reference text are needed. The tested runtime was Python 3.12.14.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from scripts.judge_study.confirmatory_analysis import (
    DEFAULT_SETTINGS,
    paired_interval,
    quality_interval,
)


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Aggregate input exceeds eight megabytes")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("Duplicate JSON object key")
            values[key] = value
        return values

    def finite(value: str) -> None:
        raise ValueError("Non-finite JSON number: " + value)

    value = json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=finite)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def money(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Costs require exact decimal strings")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal cost") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Costs must be finite and nonnegative")
    return amount


def reproduce(data: Mapping[str, Any]) -> dict[str, Any]:
    source = Path(__file__).with_name("confirmatory_analysis.py")
    if hashlib.sha256(source.read_bytes()).hexdigest() != data["frozen_analyzer_sha256"]:
        raise ValueError("Use the frozen analyzer revision identified by the data")
    if data["schema_version"] != 1 or data["settings"] != DEFAULT_SETTINGS:
        raise ValueError("The schema and analysis settings must match the frozen confirmation")
    settings, arms, rows = data["settings"], data["arms"], data["families"]
    if (
        not isinstance(arms, list)
        or len(arms) != 3
        or set(arms) != {"terra-standard", "terra-checklist", "sol-standard"}
    ):
        raise ValueError("Declare exactly the three frozen configurations")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 10_000:
        raise ValueError("Invalid family cohort")
    count = len(rows)
    grid = data["aggregation"]
    if (
        grid["families"] != count
        or grid["scheduled_judgments"] != count * 6
        or grid["observed_judgments"] != count * 6
        or grid["unattempted_judgments"] != 0
    ):
        raise ValueError("A complete two-order grid is required")
    indexed = {}
    for row in rows:
        if set(row) != {"family_id", "domain", "arms"}:
            raise ValueError("Family records must contain only the public aggregate fields")
        identifier = row["family_id"]
        if not isinstance(identifier, str) or not identifier.strip() or identifier in indexed:
            raise ValueError("Family identities must be nonempty and unique")
        if not isinstance(row["domain"], str) or not row["domain"].strip():
            raise ValueError("Every family needs a domain")
        if set(row["arms"]) != set(arms):
            raise ValueError("Every family needs all three arms")
        for values in row["arms"].values():
            if set(values) != {"delivered_agreement", "estimated_cost_usd", "judgments"}:
                raise ValueError("Arm records must contain only the public aggregate fields")
            score = values["delivered_agreement"]
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not 0 <= score <= 1
            ):
                raise ValueError("Agreement must be a finite fraction")
            if type(values["judgments"]) is not int or values["judgments"] != 2:
                raise ValueError("Every arm requires both response orders")
            money(values["estimated_cost_usd"])
        indexed[identifier] = row
    families = sorted(indexed)
    scores = {
        arm: [float(indexed[f]["arms"][arm]["delivered_agreement"]) for f in families]
        for arm in arms
    }
    costs = {
        arm: [money(indexed[f]["arms"][arm]["estimated_cost_usd"]) for f in families]
        for arm in arms
    }
    totals = {arm: sum(costs[arm], Decimal(0)) for arm in arms}
    total = sum(totals.values(), Decimal(0))
    calibration = money(data["calibration_cost_upper_usd"])
    if calibration < total:
        raise ValueError("Calibration cost cannot omit any evaluated arm")
    options = {key: settings[key] for key in ("alpha", "resamples", "seed", "min_families")}
    comparisons = []
    eligible = True
    for position, contrast in enumerate(settings["contrasts"], 1):
        candidate, baseline = contrast["candidate"], contrast["baseline"]
        differences = [a - b for a, b in zip(scores[candidate], scores[baseline], strict=True)]
        quality = quality_interval(
            differences, min_discordant_families=settings["min_discordant_families"], **options
        )
        cost = paired_interval(
            [float(a - b) for a, b in zip(costs[candidate], costs[baseline], strict=True)],
            **options,
        )
        quality_pass = all(
            quality[key] is not None and quality[key] > -settings["margin"]
            for key in ("bootstrap_lower", "t_lower")
        )
        cost_pass = all(
            cost[key] is not None and cost[key] < 0 for key in ("bootstrap_upper", "t_upper")
        )
        if not eligible:
            decision, reason = "descriptive_only", "earlier_fixed_sequence_gate_not_passed"
        elif quality["undefined_reason"] or cost["undefined_reason"]:
            decision, reason = "inconclusive", "uncertainty_not_estimable"
        elif quality_pass and cost_pass:
            decision, reason = "adopt", "quality_and_cost_gates_passed"
        else:
            decision, reason = "do_not_adopt", "quality_or_cost_gate_not_passed"
        saving = totals[baseline] - totals[candidate]
        per_question = saving / count
        payback = (
            int((calibration / per_question).to_integral_value(rounding=ROUND_CEILING))
            if decision == "adopt" and per_question > 0
            else None
        )
        domains = {}
        for domain in sorted({row["domain"] for row in rows}):
            selected = [
                value
                for family, value in zip(families, differences, strict=True)
                if indexed[family]["domain"] == domain
            ]
            domains[domain] = {
                "families": len(selected),
                "complete_families": len(selected),
                "difference": math.fsum(selected) / len(selected),
                "interpretation": "Descriptive subgroup estimate; not a subgroup non-inferiority claim.",
            }
        comparisons.append(
            {
                **contrast,
                "sequence_position": position,
                "confirmatory_eligible": eligible,
                "decision": decision,
                "reason": reason,
                "quality_difference_candidate_minus_baseline": quality,
                "noninferiority_margin": settings["margin"],
                "quality_gate_passed": quality_pass,
                "cost_difference_candidate_upper_minus_baseline_lower": cost,
                "cost_gate_passed": cost_pass,
                "candidate_primary_cost_upper_usd": str(totals[candidate]),
                "baseline_primary_cost_lower_usd": str(totals[baseline]),
                "estimated_saving_fraction_lower_accounting": (
                    float(saving / totals[baseline]) if totals[baseline] > 0 else None
                ),
                "saving_per_evaluated_question_usd": str(per_question),
                "primary_calls_per_evaluated_question": 2,
                "calibration_cost_upper_usd": str(calibration),
                "payback_evaluated_questions": payback,
                "payback_provider_judgments": 2 * payback if payback is not None else None,
                "domains": domains,
            }
        )
        eligible = eligible and decision == "adopt"
    return {
        "schema_version": 1,
        "families": count,
        "scheduled_calls": count * 6,
        "unattempted_calls": 0,
        "complete_frozen_grid": True,
        "settings": settings,
        "arms": [
            {
                "arm": arm,
                "families": count,
                "complete_families": count,
                "delivered_agreement": math.fsum(scores[arm]) / count,
                "primary_scheduled_calls": 2 * count,
                "primary_cost_lower_usd": str(totals[arm]),
                "primary_cost_upper_usd": str(totals[arm]),
            }
            for arm in arms
        ],
        "comparisons": comparisons,
        "cost_lower_usd": str(total),
        "cost_upper_usd": str(total),
        "calibration_cost_upper_usd": str(calibration),
    }


def verify_expected(actual: Any, expected: Any, path: str = "report") -> None:
    """Compare the recomputable subset; raw-only diagnostics stay out of scope."""
    if isinstance(actual, dict):
        if not isinstance(expected, dict):
            raise ValueError("Expected object at " + path)
        for key, value in actual.items():
            if key not in expected:
                raise ValueError("Missing published field: " + path + "." + key)
            verify_expected(value, expected[key], path + "." + key)
    elif isinstance(actual, list):
        if not isinstance(expected, list) or len(actual) != len(expected):
            raise ValueError("List mismatch at " + path)
        for index, (a, b) in enumerate(zip(actual, expected, strict=True)):
            verify_expected(a, b, f"{path}[{index}]")
    elif isinstance(actual, float):
        if (
            isinstance(expected, bool)
            or not isinstance(expected, (int, float))
            or not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12)
        ):
            raise ValueError("Numerical mismatch at " + path)
    elif actual != expected or type(actual) is not type(expected):
        raise ValueError("Value mismatch at " + path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Published family-metrics.json")
    parser.add_argument("--expected", type=Path, help="Published results.json to verify")
    parser.add_argument("--output", type=Path, help="Save the reproduced statistics")
    args = parser.parse_args()
    data = read_json(args.data)
    report = reproduce(data)
    if args.expected:
        if hashlib.sha256(args.expected.read_bytes()).hexdigest() != data["source_results_sha256"]:
            raise ValueError("The expected-results file differs from the recorded source")
        verify_expected(report, read_json(args.expected))
    report["reproduction"] = {
        "family_metrics_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "analyzer_sha256": data["frozen_analyzer_sha256"],
        "python": platform.python_version(),
        "matches_published_statistics": bool(args.expected),
        "scope": (
            "Recomputes numerical family statistics and economics. Raw judgments, human "
            "annotations, order diagnostics and provider billing require separate evidence."
        ),
    }
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        if args.output.exists() and args.output.read_text() != payload:
            raise ValueError("Refusing to overwrite a different reproduction record")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(
        json.dumps(
            {
                "families": report["families"],
                "scheduled_judgments": report["scheduled_calls"],
                "estimated_calibration_cost_usd": report["calibration_cost_upper_usd"],
                "decisions": [
                    {
                        "comparison": row["id"],
                        "decision": row["decision"],
                        "estimated_saving_percent": (
                            100 * row["estimated_saving_fraction_lower_accounting"]
                            if row["estimated_saving_fraction_lower_accounting"] is not None
                            else None
                        ),
                        "payback_two_order_comparisons": row["payback_evaluated_questions"],
                    }
                    for row in report["comparisons"]
                ],
                "matches_published_statistics": bool(args.expected),
                "output": str(args.output) if args.output else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
