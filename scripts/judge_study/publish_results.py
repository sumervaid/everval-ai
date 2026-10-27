"""Publish allowlisted study aggregates and reproducible scientific figures, offline.

Usage: python scripts/judge_study/publish_results.py --input analysis/public-results.json
       --output docs/benchmarks/judge-study-2026-10-27
The input is the retained aggregate from analyze.py. No model calls occur here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

COHORTS = ("human", "execution")
STATUSES = ("completed", "abstained", "invalid", "error", "missing")
COLORS = {"standard": "#405EA6", "reference_context": "#087F75"}
METRIC_NUMBERS = (
    "estimate",
    "families",
    "observations",
    "alpha",
    "planned_comparisons",
    "comparison_alpha",
    "resamples",
    "seed",
    "min_families",
)


def number(value: Any, *, optional: bool = True) -> float | int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Aggregate statistics must be finite numbers")
    return value


def interval(value: Any) -> list[float | int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Intervals require two endpoints")
    result = [number(value[0], optional=False), number(value[1], optional=False)]
    if result[0] > result[1]:
        raise ValueError("Interval endpoints must be ordered")
    return result


def identifier(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value) is None:
        raise ValueError("Expected a public model identifier")
    return value


def metric(value: dict[str, Any]) -> dict[str, Any]:
    result = {key: number(value.get(key)) for key in METRIC_NUMBERS}
    result["interval"] = interval(value.get("interval"))
    result["bounded_sensitivity_interval"] = interval(value.get("bounded_sensitivity_interval"))
    reason = value.get("undefined_reason")
    result["undefined_reason"] = identifier(reason) if reason else None
    if "complete_cohort" in value:
        if type(value["complete_cohort"]) is not bool:
            raise ValueError("Cohort completeness must be explicit")
        result["complete_cohort"] = value["complete_cohort"]
        if not result["complete_cohort"] and result["interval"] is not None:
            raise ValueError("An incomplete primary cohort cannot have an inferential interval")
    return result


def contrast(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "left": identifier(value["left"]),
        "right": identifier(value["right"]),
        "direction": "right_minus_left",
        "expected_items": number(value["expected_items"], optional=False),
        "matched_items": number(value["matched_items"], optional=False),
        "missing_item_count": len(value["missing_items"]),
        "complete_cohort": value["complete_cohort"] is True,
        "difference": metric(value["difference"]),
    }


def count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Study counts must be nonnegative integers")
    return value


def coverage(value: dict[str, Any], scheduled: int) -> dict[str, Any]:
    scheduled = count(scheduled)
    counts = {key: count(value.get(key, 0)) for key in STATUSES}
    if any(count < 0 for count in counts.values()) or sum(counts.values()) != scheduled:
        raise ValueError("Status counts must retain the full scheduled denominator")
    attempted = sum(counts[key] for key in STATUSES if key != "missing")
    return {
        "scheduled": scheduled,
        "attempted": attempted,
        "valid": counts["completed"],
        "statuses": counts,
    }


def original_cohort(report: dict[str, Any]) -> dict[str, Any]:
    analysis = report["analysis"]
    judges = []
    for row in analysis["judges"]:
        judges.append(
            {
                "judge_id": identifier(row["judge_id"]),
                "model_id": identifier(row["model_id"]),
                "primary": metric(row["primary"]),
                "complete_items": number(row["complete_items"], optional=False),
                "coverage": coverage(row["status_counts"], count(row["scheduled"])),
                "conditional_reference_agreement": number(row["agreement"].get("raw_agreement")),
                "reference_kappa": number(row["agreement"].get("kappa")),
                "human_vote_kappa": number(row.get("human_vote_agreement", {}).get("kappa")),
                "conditional_pooled_human_vote_agreement": number(
                    row.get("human_vote_agreement", {}).get("raw_agreement")
                ),
                "swap_inconsistency": metric(row["order"]["swap_inconsistency"]),
                "directional_position_effect": metric(row["order"]["directional_position_effect"]),
                "same_order_disagreement": metric(row["repeatability"]["same_order_disagreement"]),
                "excess_swap_disagreement": metric(
                    row["repeatability"]["excess_swap_disagreement"]
                ),
                "self_preference_eligible_items": number(
                    row["self_preference"]["eligible_items"], optional=False
                ),
            }
        )
    return {
        "items": number(analysis["items"], optional=False),
        "families": number(analysis["families"], optional=False),
        "primary_endpoint": identifier(report["primary_endpoint"]),
        "human_human_nominal_alpha": number(analysis["human_reliability"]["alpha"].get("alpha")),
        "judges": judges,
        "paired_contrasts": [contrast(row) for row in report["primary_contrasts"]],
    }


def repository_sensitivity(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "repository_count": count(value["repository_count"]),
        "judge_scores": [
            {"judge_id": identifier(row["judge_id"]), "metric": metric(row["metric"])}
            for row in value["judge_scores"]
        ],
        "paired_contrasts": [contrast(row) for row in value["paired_contrasts"]],
        "interpretation": "Equal repository weighting is a diagnostic sensitivity; too few independent repositories cannot support a population-of-repositories claim.",
    }


def secondary_cohort(report: dict[str, Any]) -> dict[str, Any]:
    judges = []
    for row in report["judges"]:
        observed = row["coverage"]
        counts = coverage(observed["statuses"], count(observed["scheduled"]))
        if counts["attempted"] != observed["attempted"] or counts["valid"] != observed["valid"]:
            raise ValueError("Secondary delivery counts disagree with retained statuses")
        judges.append(
            {
                "judge_id": identifier(row["judge_id"]),
                "model_id": identifier(row["model_id"]),
                "primary": metric(row["primary"]),
                "coverage": counts,
            }
        )
    return {
        "items": number(report["items"], optional=False),
        "families": number(report["families"], optional=False),
        "judges": judges,
        "paired_contrasts": [contrast(row) for row in report["paired_contrasts"]],
        "repository_sensitivity": repository_sensitivity(report.get("repository_sensitivity")),
    }


def money(value: Any) -> str:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Costs must be finite and nonnegative")
    return str(amount)


def summarize(source: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    """Use an allowlist; raw item/reviewer/prompt fields never cross this boundary."""
    costs = source["cost_upper_bound"]
    panel = source["human_panel"]
    original = {name: original_cohort(source["heldout"][name]) for name in COHORTS}
    secondary = {
        name: secondary_cohort(source["secondary_delivered_agreement"][name]) for name in COHORTS
    }
    result = {
        "schema_version": "everval.judge-study-public/v1",
        "source_aggregate_sha256": source_sha256,
        "plan_sha256": identifier(source["plan_sha256"]),
        "planned_requests": number(source["planned_requests"], optional=False),
        "missing_request_count": number(source["missing_request_count"], optional=False),
        "complete_frozen_grid": source["complete_frozen_grid"] is True,
        "original_primary": original,
        "original_repository_sensitivity": repository_sensitivity(
            source.get("repository_sensitivity")
        ),
        "secondary_delivered_agreement": secondary,
        "human_panel": {
            "items": number(panel["items"], optional=False),
            "observed_votes": number(panel["observed_votes"], optional=False),
            "divided_panels": number(panel["divided_panels"], optional=False),
            "observed_panel_vote_agreement_upper_bound": number(
                panel["observed_panel_vote_agreement_upper_bound"], optional=False
            ),
            "human_human_nominal_alpha": original["human"]["human_human_nominal_alpha"],
            "interpretation": "Empirical bound against these fixed historical panels, not a universal model capability ceiling.",
        },
        "cost_upper_bound": {
            "settled_usd": money(costs["conservative_settled_usd"]),
            "committed_usd": money(costs["conservative_committed_usd"]),
            "requests": number(costs["requests"], optional=False),
            "unknown_requests": number(costs["unknown_requests"], optional=False),
            "inference_cap_usd": number(costs["inference_cap_usd"], optional=False),
            "total_user_cap_usd": number(costs["total_user_cap_usd"], optional=False),
            "basis": "Conservative usage-based upper bound: input at 2.5 times standard uncached price, output at standard price. Not a verified invoice.",
        },
        "interpretation": {
            "original_primary": "Frozen human-vote agreement; incomplete valid grids retain descriptive complete-item estimates without a full-cohort interval. Archived execution is a separate diagnostic endpoint.",
            "secondary": "Added after development and frozen before holdout. Attempted invalid, abstained and error outcomes score zero; unattempted outcomes block inference. Original primary remains unchanged.",
            "multiplicity": "Three paired contrasts receive alpha .05/3 within each declared family. Individual-configuration intervals and diagnostics are exploratory. Primary and secondary families do not have joint error control.",
            "archived_execution": "Agreement with archived published test outcomes on selected discordant patches; no new agent repair success claim.",
            "order": "Swap inconsistency is compared with same-order repeat disagreement; neither alone establishes causal position bias.",
        },
    }
    for name in COHORTS:
        if original[name]["items"] != secondary[name]["items"]:
            raise ValueError("Original and amended endpoints must retain the same cohort")
    return result


def _arm(judge_id: str) -> str:
    return "reference_context" if judge_id.endswith("-reference_context") else "standard"


def _model_label(model_id: str) -> str:
    return (
        model_id.replace("gpt-", "GPT-")
        .replace("-luna", " Luna")
        .replace("-terra", " Terra")
        .replace("-sol", " Sol")
    )


def _save(figure: Any, destination: Path, name: str) -> None:
    figure.savefig(destination / (name + ".svg"), bbox_inches="tight", metadata={"Date": None})
    figure.savefig(
        destination / (name + ".png"),
        bbox_inches="tight",
        dpi=180,
        metadata={"Software": "Everval"},
    )


def _point(axis: Any, value: dict[str, Any], y: float, color: str, marker: str = "o") -> None:
    center = value["estimate"]
    if center is None:
        axis.text(2, y, "Unavailable", color="#667085", va="center", fontsize=8)
        return
    bounds = value["interval"]
    if bounds is not None:
        axis.plot([100 * bounds[0], 100 * bounds[1]], [y, y], color=color, linewidth=1.7, zorder=2)
    axis.plot(
        100 * center,
        y,
        marker=marker,
        markersize=6.5,
        color=color,
        markerfacecolor=color if bounds else "white",
        markeredgewidth=1.5,
        zorder=3,
    )


def render(result: dict[str, Any], destination: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.edgecolor": "#CDD3DE",
            "text.color": "#182230",
            "axes.labelcolor": "#344054",
            "xtick.color": "#667085",
            "ytick.color": "#344054",
            "svg.fonttype": "none",
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )
    artifacts = []
    cohort_titles = {
        "human": "Historical human preferences",
        "execution": "Archived repository execution outcomes",
    }
    for field, filename, title in (
        (
            "original_primary",
            "judge-original-primary",
            "Original endpoint: valid complete-item agreement",
        ),
        (
            "secondary_delivered_agreement",
            "judge-delivered-agreement",
            "Secondary endpoint: agreement delivered per scheduled judgment",
        ),
    ):
        figure, axes = plt.subplots(1, 2, figsize=(13.2, 4.9))
        figure.subplots_adjust(left=0.14, right=0.90, bottom=0.23, top=0.74, wspace=0.60)
        for axis, name in zip(axes, COHORTS, strict=True):
            cohort = result[field][name]
            models = list(dict.fromkeys(row["model_id"] for row in cohort["judges"]))
            for row in cohort["judges"]:
                arm = _arm(row["judge_id"])
                y = 2 - models.index(row["model_id"]) + (0.15 if arm == "standard" else -0.15)
                _point(axis, row["primary"], y, COLORS[arm], "o" if arm == "standard" else "D")
                count = row["coverage"]
                axis.text(
                    1.03,
                    y,
                    f"{count['valid']}/{count['scheduled']}",
                    transform=axis.get_yaxis_transform(),
                    va="center",
                    fontsize=8,
                    color="#667085",
                )
            axis.set_yticks(
                [2 - i for i in range(len(models))], [_model_label(model) for model in models]
            )
            axis.tick_params(axis="y", length=0, pad=10)
            axis.set_xlim(0, 100)
            axis.set_ylim(-0.45, 2.45)
            axis.set_xticks([0, 25, 50, 75, 100])
            axis.xaxis.set_major_formatter(PercentFormatter())
            axis.grid(axis="x", color="#E9EDF3", linewidth=0.7)
            axis.set_xlabel("Agreement")
            axis.set_title(
                f"{cohort_titles[name]}\nn = {cohort['items']} {'questions' if name == 'human' else 'issues'}",
                loc="left",
                pad=14,
            )
            axis.text(
                1.03,
                1.04,
                "Valid / scheduled",
                transform=axis.transAxes,
                fontsize=7.5,
                color="#667085",
            )
            if (
                name == "human"
                and field == "secondary_delivered_agreement"
                and all(row["primary"].get("complete_cohort") is True for row in cohort["judges"])
            ):
                bound = result["human_panel"]["observed_panel_vote_agreement_upper_bound"] * 100
                axis.axvline(bound, color="#98A2B3", linestyle=(0, (3, 3)), linewidth=1, zorder=1)
                axis.text(
                    bound,
                    -0.40,
                    f"Panel bound {bound:.1f}%",
                    fontsize=7,
                    ha="right",
                    color="#667085",
                )
        figure.suptitle(title, x=0.14, y=0.98, ha="left", fontsize=16, fontweight="bold")
        legend = [
            Line2D(
                [0],
                [0],
                color=color,
                marker="o" if arm == "standard" else "D",
                linestyle="none",
                label="Base rubric" if arm == "standard" else "Rubric + checklist",
            )
            for arm, color in COLORS.items()
        ]
        figure.legend(
            handles=legend, loc="upper left", bbox_to_anchor=(0.135, 0.925), frameon=False, ncol=2
        )
        note = (
            "Open marker: interval unavailable. Complete-item estimates may use different subsets; this is not a full-cohort ranking."
            if field == "original_primary"
            else "Secondary endpoint frozen after development, before holdout. Invalid / abstained / error = 0; unattempted blocks inference."
        )
        figure.text(0.14, 0.105, note, fontsize=8, color="#667085")
        figure.text(
            0.14,
            0.065,
            "Individual-configuration intervals are exploratory. Historical panel bound is not a universal capability ceiling.",
            fontsize=8,
            color="#667085",
        )
        _save(figure, destination, filename)
        plt.close(figure)
        artifacts.extend([filename + ".svg", filename + ".png"])
    for field, filename, title in (
        (
            "original_primary",
            "judge-original-effects",
            "Original endpoint: checklist minus base rubric",
        ),
        (
            "secondary_delivered_agreement",
            "judge-delivered-effects",
            "Secondary endpoint: checklist minus base rubric",
        ),
    ):
        figure, axes = plt.subplots(1, 2, figsize=(12.6, 4.6))
        figure.subplots_adjust(left=0.14, right=0.94, bottom=0.24, top=0.74, wspace=0.55)
        for axis, name in zip(axes, COHORTS, strict=True):
            cohort = result[field][name]
            judges = {row["judge_id"]: row for row in cohort["judges"]}
            rows = cohort["paired_contrasts"]
            extent = (
                max(
                    [0.10]
                    + [
                        abs(float(v))
                        for row in rows
                        for v in (row["difference"]["interval"] or [row["difference"]["estimate"]])
                        if v is not None
                    ]
                )
                * 100
                + 3
            )
            for index, row in enumerate(rows):
                y = len(rows) - index - 1
                _point(axis, row["difference"], y, COLORS["reference_context"], "D")
                axis.text(
                    1.02,
                    y,
                    f"{row['matched_items']}/{row['expected_items']}",
                    transform=axis.get_yaxis_transform(),
                    fontsize=8,
                    va="center",
                    color="#667085",
                )
            axis.set_yticks(
                list(reversed(range(len(rows)))),
                [_model_label(judges[row["left"]]["model_id"]) for row in rows],
            )
            axis.tick_params(axis="y", length=0, pad=10)
            axis.set_xlim(-extent, extent)
            axis.set_ylim(-0.5, len(rows) - 0.5)
            axis.axvline(0, color="#98A2B3", linestyle=(0, (3, 3)), linewidth=1)
            axis.grid(axis="x", color="#E9EDF3", linewidth=0.7)
            axis.set_xlabel("Change in agreement (percentage points)")
            axis.set_title(cohort_titles[name], loc="left", pad=16)
            axis.text(
                1.02, 1.06, "Paired items", transform=axis.transAxes, fontsize=7.5, color="#667085"
            )
        figure.suptitle(title, x=0.14, y=0.96, ha="left", fontsize=16, fontweight="bold")
        figure.text(
            0.14,
            0.14,
            "Paired family bootstrap; 5,000 resamples; alpha .05/3 within each declared family. Open marker: interval unavailable.",
            fontsize=8,
            color="#667085",
        )
        figure.text(
            0.14,
            0.09,
            "Human primary and amended secondary have separate error-control families; repository effects remain diagnostic.",
            fontsize=8,
            color="#667085",
        )
        _save(figure, destination, filename)
        plt.close(figure)
        artifacts.extend([filename + ".svg", filename + ".png"])
    figure, axes = plt.subplots(1, 2, figsize=(13.2, 5.8))
    figure.subplots_adjust(left=0.18, right=0.95, bottom=0.20, top=0.78, wspace=0.70)
    for axis, name in zip(axes, COHORTS, strict=True):
        rows = result["original_primary"][name]["judges"]
        labels = []
        for index, row in enumerate(rows):
            y = len(rows) - index - 1
            labels.append(
                _model_label(row["model_id"])
                + (" · checklist" if _arm(row["judge_id"]) != "standard" else " · base")
            )
            _point(axis, row["swap_inconsistency"], y + 0.10, "#6F56A5", "o")
            _point(axis, row["same_order_disagreement"], y - 0.10, "#667085", "D")
        axis.set_yticks(list(reversed(range(len(rows)))), labels, fontsize=8)
        axis.tick_params(axis="y", length=0, pad=8)
        axis.set_xlim(0, 100)
        axis.xaxis.set_major_formatter(PercentFormatter())
        axis.grid(axis="x", color="#E9EDF3", linewidth=0.7)
        axis.set_xlabel("Conditional disagreement rate")
        axis.set_title(cohort_titles[name], loc="left", pad=16)
    figure.suptitle(
        "Order changes versus ordinary repeat noise",
        x=0.18,
        y=0.97,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    figure.legend(
        handles=[
            Line2D([0], [0], color="#6F56A5", marker="o", linestyle="none", label="Swapped order"),
            Line2D(
                [0], [0], color="#667085", marker="D", linestyle="none", label="Same-order repeat"
            ),
        ],
        loc="upper left",
        bbox_to_anchor=(0.175, 0.925),
        frameon=False,
        ncol=2,
    )
    figure.text(
        0.18,
        0.10,
        "Exploratory diagnostics on complete available items. Swap inconsistency alone does not establish position bias.",
        fontsize=8,
        color="#667085",
    )
    _save(figure, destination, "judge-order-diagnostics")
    plt.close(figure)
    artifacts.extend(["judge-order-diagnostics.svg", "judge-order-diagnostics.png"])
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Reproduce figures directly from the published results.json; write no aggregate.",
    )
    args = parser.parse_args()
    if args.input.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Aggregate exceeds 32 MiB publication bound")
    raw = args.input.read_bytes()
    source = json.loads(raw)
    if args.render_only:
        if source.get("schema_version") != "everval.judge-study-public/v1":
            raise ValueError("Render-only mode requires the published aggregate schema")
        args.output.mkdir(parents=True, exist_ok=True)
        print(json.dumps({"figures": render(source, args.output)}))
        return
    result = summarize(source, hashlib.sha256(raw).hexdigest())
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = render(result, args.output)
    result["artifacts"] = artifacts
    result["figure_dependency"] = "matplotlib==3.11.2"
    result["publisher_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["reproduction"] = (
        "python scripts/judge_study/publish_results.py --render-only --input docs/benchmarks/judge-study-2026-10-27/results.json --output ./reproduced-figures"
    )
    (args.output / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"aggregate": str(args.output / "results.json"), "figures": len(artifacts)}))


if __name__ == "__main__":
    main()
