"""Plot the frozen judge-confirmation aggregates without inference calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

LABELS = {
    "terra-standard": "GPT-6.1 Terra\nStandard rubric",
    "terra-checklist": "GPT-6.1 Terra\nChecklist rubric",
    "sol-standard": "GPT-6.1 Sol\nStandard rubric",
}
COLORS = {"terra-standard": "#087F72", "terra-checklist": "#A8B5BF", "sol-standard": "#41556C"}


def render(results_path: Path, output: Path) -> None:
    raw = results_path.read_bytes()
    expected = "07a2da7ecac70344d183d9c792f2f7a55e698d454fb89f70492d712a47d2d9f8"
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Use the original frozen results for this study figure.")
    result = json.loads(raw)
    if not result["complete_frozen_grid"] or result["unattempted_calls"]:
        raise ValueError("This figure requires complete frozen-grid coverage.")
    arms = result["arms"]
    if len(arms) != 3 or any(arm["primary_scheduled_calls"] != 2000 for arm in arms):
        raise ValueError("Expected the frozen three-arm, 2,000-judgment-per-arm study.")
    comparison = next(row for row in result["comparisons"] if row["id"] == "B")
    saving = 100 * comparison["estimated_saving_fraction_lower_accounting"]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "svg.hashsalt": "everval-judge-confirmation-2026-10-27",
            "axes.unicode_minus": False,
        }
    )
    fig = plt.figure(figsize=(12.5, 6.6), facecolor="#FAFBFC")
    fig.text(
        0.055, 0.93, "EVERVAL  /  JUDGE CALIBRATION", color="#526171", fontsize=10, weight="bold"
    )
    fig.text(
        0.055,
        0.853,
        f"{saving:.1f}% lower estimated judging cost",
        color="#152B3C",
        fontsize=25,
        weight="bold",
    )
    fig.text(
        0.055,
        0.796,
        "Terra standard vs Sol standard  ·  1,000 conversation families  ·  6,000 judgments",
        color="#526171",
        fontsize=11,
    )
    ax = fig.add_axes((0.265, 0.27, 0.485, 0.40), facecolor="#FAFBFC")
    costs = [float(arm["primary_cost_upper_usd"]) for arm in arms]
    ax.barh(range(3), costs, height=0.46, color=[COLORS[arm["arm"]] for arm in arms], zorder=3)
    ax.set_yticks([])
    ax.set_ylim(2.6, -0.6)
    ax.set_xlim(0, 11)
    ax.set_xticks([0, 2, 4, 6, 8, 10], ["$0", "$2", "$4", "$6", "$8", "$10"])
    ax.tick_params(axis="x", labelsize=10, colors="#667789", length=0, pad=10)
    ax.grid(axis="x", color="#E3E9ED", linewidth=0.8, zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(0.265, 0.704, "COST FOR 2,000 JUDGMENTS", fontsize=9, color="#526171", weight="bold")
    fig.text(0.827, 0.704, "HUMAN-VOTE\nAGREEMENT", fontsize=9, color="#526171", weight="bold")
    for index, arm in enumerate(arms):
        y = fig.transFigure.inverted().transform(ax.transData.transform((0, index)))[1]
        fig.text(0.055, y, LABELS[arm["arm"]], va="center", color="#152B3C", fontsize=12)
        ax.text(
            costs[index] + 0.18,
            index,
            "$" + f"{costs[index]:.2f}",
            va="center",
            color="#152B3C",
            fontsize=12,
            weight="bold",
        )
        fig.text(
            0.827,
            y,
            f"{100 * arm['delivered_agreement']:.2f}%",
            va="center",
            color=COLORS["terra-standard"] if index == 0 else "#152B3C",
            fontsize=19,
            weight="bold",
        )
    fig.text(
        0.055,
        0.163,
        "Both preregistered cost and quality gates passed.",
        fontsize=12,
        weight="bold",
        color="#152B3C",
    )
    fig.text(
        0.055,
        0.12,
        "Quality gate: agreement loss below a prespecified 1 percentage point, using both paired confidence methods.",
        fontsize=10,
        color="#526171",
    )
    fig.text(
        0.055,
        0.077,
        "Costs use reported tokens and frozen rates, not invoices. Agreement uses curated historical human votes.",
        fontsize=9,
        color="#526171",
    )
    fig.text(
        0.055,
        0.043,
        "Results apply to these configurations and this cohort. Observed higher agreement is not a universal superiority claim.",
        fontsize=9,
        color="#526171",
    )
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / "cost-quality.svg", metadata={"Date": None}, facecolor=fig.get_facecolor())
    fig.savefig(output / "cost-quality.png", dpi=180, facecolor=fig.get_facecolor())
    svg = output / "cost-quality.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.results, args.output)


if __name__ == "__main__":
    main()
