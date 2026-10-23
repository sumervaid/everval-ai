"""Render retained checkpoint-study analysis with standard scientific plotting.

Use a separate plotting environment; never add dependencies to the frozen model
runtime. Input is the verified report emitted by analyze_real_study.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

COLORS = {17: "#7545A1", 29: "#176DA6", 43: "#087F75"}
MARKERS = {17: "o", 29: "s", 43: "^"}
LINES = {17: "--", 29: "-", 43: ":"}
LABELS = {17: "Seed 17 · development", 29: "Seed 29 · validation", 43: "Seed 43 · validation"}
DOMAINS = ["routing", "extraction", "arithmetic", "injection"]
STEPS = [0, 80, 160, 320]


def save(fig, directory: Path, name: str, report_hash: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        fig.savefig(
            directory / f"{name}.{extension}",
            dpi=170,
            facecolor=fig.get_facecolor(),
            metadata={"Description": f"Verified analysis SHA256 {report_hash}"}
            if extension == "svg"
            else {},
        )
        if extension == "svg":
            path = directory / f"{name}.{extension}"
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
    plt.close(fig)


def style_axis(axis) -> None:
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines[["bottom", "left"]].set_color("#CDD5DF")
    axis.tick_params(colors="#374151", labelsize=10)
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#E4E9EF", linewidth=0.8)


def render(report: dict, directory: Path, report_hash: str) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "text.color": "#182332",
            "axes.labelcolor": "#374151",
            "axes.titleweight": "bold",
            "figure.facecolor": "#F8FAFC",
            "axes.facecolor": "#FFFFFF",
            "svg.fonttype": "none",
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.8), sharex=True, sharey=True)
    for domain, axis in zip(DOMAINS, axes.flat, strict=True):
        for seed in (17, 29, 43):
            indexed = {
                r["step"]: r
                for r in report["trajectories"]
                if r["seed"] == seed and r["domain"] == domain
            }
            values = [
                indexed[step]["complete_pass_rate"] * 100
                if step in indexed and indexed[step]["complete_pass_rate"] is not None
                else float("nan")
                for step in STEPS
            ]
            axis.plot(
                STEPS,
                values,
                marker=MARKERS[seed],
                linestyle=LINES[seed],
                color=COLORS[seed],
                linewidth=2,
                markersize=6,
                label=LABELS[seed],
            )
        if domain == "arithmetic":
            baseline = [
                r["complete_pass_rate"]
                for r in report["trajectories"]
                if r["domain"] == domain and r["step"] == 0
            ]
            if any(value == 0 for value in baseline):
                axis.text(
                    0.04,
                    0.92,
                    "Floor effects limit retention claims.",
                    transform=axis.transAxes,
                    va="top",
                    fontsize=9,
                    color="#526071",
                )
        axis.set_title(domain.capitalize(), loc="left", pad=12)
        axis.set_xticks(STEPS)
        axis.set_ylim(-3, 103)
        axis.yaxis.set_major_formatter(PercentFormatter())
        style_axis(axis)
    for axis in axes[1]:
        axis.set_xlabel("Training updates")
    for axis in axes[:, 0]:
        axis.set_ylabel("Exact JSON-contract pass rate")
    fig.suptitle(
        "Structured-output performance during training",
        x=0.07,
        y=0.97,
        ha="left",
        fontsize=23,
        fontweight="bold",
    )
    fig.text(
        0.07,
        0.924,
        "Qwen2.5-0.5B-Instruct · local 4-bit LoRA · 3 training seeds · 12 actual checkpoints",
        fontsize=11,
    )
    fig.legend(
        *axes[0, 0].get_legend_handles_labels(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.115),
        ncol=3,
        frameon=False,
    )
    fig.text(
        0.07,
        0.085,
        "Generated diagnostics: 32 record families × 2 paraphrases per domain. Score jointly measures formatting and exact answers.",
        fontsize=9,
    )
    fig.text(
        0.07,
        0.06,
        "Validation seeds share the same held-out records. Missing evidence creates gaps; paired uncertainty is shown separately.",
        fontsize=9,
    )
    fig.text(
        0.07,
        0.035,
        "This is a small-model measurement study, not an enterprise population or frontier-model benchmark.",
        fontsize=9,
        color="#526071",
    )
    fig.subplots_adjust(left=0.09, right=0.965, top=0.865, bottom=0.225, hspace=0.29, wspace=0.15)
    save(fig, directory, "checkpoint-trajectories", report_hash)

    fig, axis = plt.subplots(figsize=(12, 9.1))
    ticks, labels = [], []
    for d_index, domain in enumerate(DOMAINS):
        for offset, seed in enumerate((17, 29, 43)):
            y = 13 - (d_index * 3.4 + offset)
            ticks.append(y)
            labels.append(f"{domain.capitalize()} · {seed}")
            cell = next(
                r
                for r in report["comparisons"]
                if r["domain"] == domain and r["seed"] == seed and r["step"] == 320
            )
            if cell.get("delta") is None or cell.get("interval") is None:
                axis.text(-95, y, "Missing / incomplete evidence", fontsize=10, va="center")
                continue
            delta = cell["delta"] * 100
            low, high = [value * 100 for value in cell["interval"]]
            axis.errorbar(
                delta,
                y,
                xerr=[[delta - low], [high - delta]],
                fmt=MARKERS[seed],
                color=COLORS[seed],
                capsize=4,
                linewidth=1.6,
                markersize=6,
            )
            axis.annotate(
                f"{delta:+.1f} pp",
                (delta, y),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=COLORS[seed],
            )
    axis.axvspan(-10, 10, color="#E9EEF3", alpha=0.8)
    axis.axvline(0, color="#657587", linewidth=1)
    for threshold in (-10, 10):
        axis.axvline(threshold, color="#9AA7B5", linestyle="--", linewidth=1)
    axis.set_yticks(ticks, labels)
    axis.set_xlim(-103, 103)
    axis.set_xticks([-100, -50, -10, 0, 10, 50, 100])
    axis.set_xlabel("Final checkpoint minus baseline · percentage points", labelpad=12)
    axis.set_ylim(0.2, 14.1)
    style_axis(axis)
    axis.grid(False, axis="y")
    axis.grid(True, axis="x", color="#E4E9EF", linewidth=0.7)
    fig.suptitle(
        "Observed changes, with uncertainty left visible",
        x=0.06,
        y=0.97,
        ha="left",
        fontsize=23,
        fontweight="bold",
    )
    fig.text(
        0.06,
        0.925,
        "Step 320 versus step 0 · paired generated-record estimates · shaded band: ±10 percentage points",
        fontsize=11,
    )
    fig.text(
        0.06,
        0.095,
        "Bounded Hoeffding intervals adjust for 3 seeds × 3 looks × 4 domains (total alpha 0.05), conditional on independent records.",
        fontsize=9,
    )
    fig.text(
        0.06,
        0.069,
        "Records share two templates. The template-cluster sensitivity has too few clusters for a reliable decision.",
        fontsize=9,
    )
    fig.text(
        0.06,
        0.043,
        "Wide intervals and inconclusive decisions are retained. These results do not certify deployment readiness.",
        fontsize=9,
        color="#526071",
    )
    fig.subplots_adjust(left=0.21, right=0.96, top=0.87, bottom=0.19)
    save(fig, directory, "checkpoint-paired-changes", report_hash)

    fig, axis = plt.subplots(figsize=(11, 7.8))
    strategies = report["mix_validation"]
    for name, marker, color, label in (
        ("random-", "o", "#8C9BAD", "Random covered subsets · 32 fixed draws"),
        ("stratified-", "^", "#A897C6", "One per domain · 32 fixed draws"),
    ):
        values = [v for k, v in strategies.items() if k.startswith(name)]
        points = [
            v
            for v in values
            if v["wall_seconds"] is not None and v["mean_absolute_delta_error"] is not None
        ]
        axis.scatter(
            [v["wall_seconds"] for v in points],
            [v["mean_absolute_delta_error"] * 100 for v in points],
            marker=marker,
            color=color,
            alpha=0.65,
            s=45,
            label=label,
        )
    for key, marker, color, label in (
        ("full_suite", "s", "#334155", "Full suite reference"),
        ("selected", "*", "#087F75", "Selection frozen before validation"),
    ):
        value = strategies[key]
        if value["wall_seconds"] is not None and value["mean_absolute_delta_error"] is not None:
            axis.scatter(
                [value["wall_seconds"]],
                [value["mean_absolute_delta_error"] * 100],
                marker=marker,
                color=color,
                s=220 if key == "selected" else 80,
                edgecolors="#FFFFFF",
                linewidths=1,
                label=label,
                zorder=4,
            )
    axis.set_xlabel("Observed generation-call time for included evaluations · seconds", labelpad=12)
    axis.set_ylabel("Mean absolute error in domain change · percentage points", labelpad=12)
    style_axis(axis)
    axis.legend(loc="upper right", frameon=False, fontsize=9)
    fig.suptitle(
        "Can fewer evaluations preserve the useful signal?",
        x=0.08,
        y=0.96,
        ha="left",
        fontsize=21,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.91,
        "Frozen development selection, evaluated on two validation seeds at step 320 · lower error is better",
        fontsize=10,
    )
    fig.text(
        0.08,
        0.102,
        "All 3,072 generations were actually executed. Subset time savings are counterfactual; runtime excludes training and model setup.",
        fontsize=9,
    )
    fig.text(
        0.08,
        0.075,
        "Baselines share the same development ceiling, not identical realized cost. Repeated subset draws are not independent model runs.",
        fontsize=9,
    )
    fig.text(
        0.08,
        0.048,
        "Only eight validation domain-by-seed cells; apparent differences are descriptive. Local provider fees $0; hardware and energy unpriced.",
        fontsize=9,
        color="#526071",
    )
    fig.text(
        0.08,
        0.022,
        "No reference regressions occurred: detection recall is undefined (0 events).",
        fontsize=9,
        color="#526071",
    )
    fig.subplots_adjust(left=0.11, right=0.96, top=0.85, bottom=0.225)
    save(fig, directory, "checkpoint-mix-validation", report_hash)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    payload = args.report.read_bytes()
    report = json.loads(payload)
    if report["schema_version"] != 1 or report["scheduled_generations"] != 3072:
        raise ValueError("Expected the complete verified checkpoint-study report")
    render(report, args.output_dir, hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    main()
