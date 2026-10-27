"""Plot post-hoc full-cohort bounds without inventing a midpoint or confidence interval."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

COLORS = {"standard": "#405EA6", "reference_context": "#087F75"}


def prepare(
    audit: dict[str, Any], published: dict[str, Any], *, variant: str = "frozen"
) -> dict[str, Any]:
    if variant not in {"frozen", "phase_aware"}:
        raise ValueError("Unknown transport sensitivity")
    if audit["plan_sha256"] != published["plan_sha256"]:
        raise ValueError("Plot sources must describe the same frozen plan")
    cohort = audit["heldout_identification_bounds"]["human"][variant]
    panel = published["human_panel"]
    if cohort["items"] != panel["items"] or cohort["endpoint"] != "human_votes":
        raise ValueError("Full human cohort and retained panel must match")
    rows, identities = [], set()
    for row in cohort["judges"]:
        identity = row["judge_id"]
        arm = next((arm for arm in COLORS if identity.endswith("-" + arm)), None)
        if identity in identities or arm is None:
            raise ValueError("Judges must be unique with an explicit frozen arm")
        identities.add(identity)
        bounds = row["identification_bounds"]
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)
            or not 0 <= bounds[0] <= bounds[1] <= 1
        ):
            raise ValueError("Finite ordered agreement bounds are required")
        counts = row["status_counts"]
        if set(counts) != {"completed", "abstained", "invalid", "error", "missing"}:
            raise ValueError("Only declared coverage statuses may be published")
        if any(type(x) is not int or x < 0 for x in counts.values()):
            raise ValueError("Coverage counts must be nonnegative integers")
        if (
            sum(counts.values()) != row["scheduled_judgments"]
            or counts["missing"] != row["unattempted_judgments"]
        ):
            raise ValueError("Coverage must retain every scheduled slot")
        rows.append(
            {
                "judge_id": identity,
                "model_id": row["model_id"],
                "arm": arm,
                "bounds": bounds,
                "scheduled": row["scheduled_judgments"],
                "unattempted": row["unattempted_judgments"],
                "status_counts": counts,
            }
        )
    ceiling = panel["observed_panel_vote_agreement_upper_bound"]
    if type(ceiling) not in (int, float) or not math.isfinite(ceiling) or not 0 <= ceiling <= 1:
        raise ValueError("The fixed-panel ceiling must be finite")
    if any(row["bounds"][1] > ceiling + 1e-12 for row in rows):
        raise ValueError("Human agreement bounds cannot exceed their fixed-panel ceiling")
    if any(
        {row["arm"] for row in rows if row["model_id"] == model} != set(COLORS)
        for model in {row["model_id"] for row in rows}
    ):
        raise ValueError("Both frozen arms must be retained for every model")
    return {
        "schema_version": "everval.full-cohort-bound-chart/v1",
        "plan_sha256": audit["plan_sha256"],
        "analysis_role": "post_hoc_finite_cohort_identification_bounds",
        "transport": "original_frozen" if variant == "frozen" else "phase_aware_offline_regrade",
        "frozen_scores_modified": False,
        "items": cohort["items"],
        "families": cohort["families"],
        "judges": rows,
        "observed_panel_ceiling": ceiling,
        "method": cohort["method"],
        "interpretation": cohort["interpretation"],
        "no_midpoint_estimated": True,
        "ceiling_interpretation": "Maximum categorical agreement with these retained historical votes, not a universal model capability ceiling.",
    }


def render(data: dict[str, Any], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.fonttype": "none",
            "svg.hashsalt": "everval-full-cohort-bounds-v1",
        }
    )
    models = list(dict.fromkeys(row["model_id"] for row in data["judges"]))
    figure, axis = plt.subplots(figsize=(12.8, 5.3))
    figure.subplots_adjust(left=0.16, right=0.77, bottom=0.30, top=0.73)
    figure.suptitle(
        "Full-cohort delivered-agreement bounds",
        x=0.16,
        y=0.96,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    figure.text(
        0.16,
        0.895,
        f"Post hoc · {data['items']} held-out human questions · "
        + (
            "original frozen transport"
            if data["transport"] == "original_frozen"
            else "phase-aware offline regrade"
        ),
        fontsize=10,
        color="#475467",
    )
    for row in data["judges"]:
        y = (
            len(models)
            - 1
            - models.index(row["model_id"])
            + (0.13 if row["arm"] == "standard" else -0.13)
        )
        lo, hi = [100 * value for value in row["bounds"]]
        color = COLORS[row["arm"]]
        axis.plot([lo, hi], [y, y], color=color, linewidth=5, solid_capstyle="butt")
        axis.plot(
            [lo, hi],
            [y, y],
            color=color,
            linestyle="none",
            marker="|",
            markersize=10,
            markeredgewidth=1.5,
        )
        label = f"{lo:.2f}%" if lo == hi else f"{lo:.2f}–{hi:.2f}%"
        axis.text(
            1.025,
            y,
            label + f"; {row['unattempted']} missing",
            transform=axis.get_yaxis_transform(),
            va="center",
            fontsize=9,
            color=color,
        )
    names = {
        "gpt-6-luna": "GPT-6 Luna",
        "gpt-6.1-terra": "GPT-6.1 Terra",
        "gpt-6.1-sol": "GPT-6.1 Sol",
    }
    axis.set_yticks(list(reversed(range(len(models)))), [names.get(x, x) for x in models])
    axis.set_xlim(0, 100)
    axis.set_ylim(-0.45, len(models) - 0.55)
    axis.xaxis.set_major_formatter(PercentFormatter())
    axis.set_xlabel("Credit against retained human votes, with every scheduled slot retained")
    axis.grid(axis="x", color="#E9EDF3", linewidth=0.8)
    axis.tick_params(axis="y", length=0, pad=12)
    for spine in axis.spines.values():
        spine.set_visible(False)
    ceiling = 100 * data["observed_panel_ceiling"]
    axis.axvline(ceiling, color="#98A2B3", linestyle=(0, (3, 3)), linewidth=1)
    axis.text(
        ceiling,
        1.03,
        f"Fixed-panel ceiling {ceiling:.2f}%",
        transform=axis.get_xaxis_transform(),
        ha="center",
        fontsize=8,
        color="#667085",
    )
    figure.legend(
        handles=[
            Line2D(
                [0],
                [0],
                color=COLORS[arm],
                linewidth=4,
                label="Base rubric" if arm == "standard" else "Rubric + checklist",
            )
            for arm in COLORS
        ],
        loc="upper left",
        bbox_to_anchor=(0.153, 0.85),
        ncol=2,
        frameon=False,
    )
    notes = [
        "Ranges cover only unattempted judgments: retained credit through the maximum possible additional missing credit.",
        "Not confidence intervals or population estimates. Attempted invalid, abstained and error deliveries contribute zero.",
        "The panel ceiling describes these historical votes. Original planned analyses remain reported separately.",
    ]
    for y, note in zip((0.16, 0.115, 0.07), notes, strict=True):
        figure.text(0.16, y, note, fontsize=8, color="#667085")
    output.mkdir(parents=True, exist_ok=True)
    for suffix in ("svg", "png"):
        figure.savefig(
            output
            / (
                (
                    "judge-full-cohort-bounds"
                    if data["transport"] == "original_frozen"
                    else "judge-full-cohort-phase-aware-bounds"
                )
                + "."
                + suffix
            ),
            dpi=180,
            bbox_inches="tight",
            facecolor="white",
            metadata={"Creator": "Everval", "Date": None}
            if suffix == "svg"
            else {"Software": "Everval"},
        )
    svg = output / (
        "judge-full-cohort-bounds.svg"
        if data["transport"] == "original_frozen"
        else "judge-full-cohort-phase-aware-bounds.svg"
    )
    svg.write_text("\n".join(line.rstrip(" \t") for line in svg.read_text().splitlines()) + "\n")
    plt.close(figure)


def read(path: Path) -> tuple[dict[str, Any], str]:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Plot input exceeds 32 MiB")
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--variant", choices=("frozen", "phase_aware"), default="frozen")
    args = parser.parse_args()
    source, source_hash = read(args.input)
    if args.render_only:
        if source.get("schema_version") != "everval.full-cohort-bound-chart/v1":
            raise ValueError("Render-only requires the published bounds aggregate")
        render(source, args.output)
        return
    if args.results is None:
        parser.error("--results is required for preparing the aggregate")
    published, published_hash = read(args.results)
    data = prepare(source, published, variant=args.variant)
    aggregate_name = (
        "full-cohort-bounds.json"
        if args.variant == "frozen"
        else "full-cohort-phase-aware-bounds.json"
    )
    data.update(
        source_phase_audit_sha256=source_hash,
        source_results_sha256=published_hash,
        publisher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        figure_dependency="matplotlib==3.11.2",
        reproduction="python -m scripts.judge_study.publish_bounds --render-only --input docs/benchmarks/judge-study-2026-10-27/"
        + aggregate_name
        + " --output ./reproduced-figures",
    )
    render(data, args.output)
    (args.output / aggregate_name).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"aggregate": str(args.output / aggregate_name), "figures": 2}))


if __name__ == "__main__":
    main()
