"""Render the frozen fresh-repair counts without regrading model outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

MODELS = ("gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol")
LABELS = ("GPT-6 Luna", "GPT-6.1 Terra", "GPT-6.1 Sol")
CATEGORIES = (
    ("verified", "Verified repair", "#147D77"),
    ("unresolved", "Executed unresolved", "#EF9B5B"),
    ("unknown", "Required-test ERROR (unclassified)", "#E8BD4E"),
    ("rejected", "Rejected patch", "#B4A1D2"),
    ("empty", "Explicit empty patch", "#DFE5EB"),
)
SOURCE_REVISION = "f306e5448772d0ba3636f4abfa4e21732a506422"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_counts(directory: Path) -> list[dict[str, Any]]:
    results_path = directory / "fresh-results.json"
    results = json.loads(results_path.read_text())
    operations = json.loads((directory / "fresh-operations.json").read_text())
    if operations["source_analysis_sha256"] != sha256(results_path):
        raise ValueError("Operations do not identify this exact frozen analysis")
    if set(results["models"]) != set(MODELS):
        raise ValueError("The figure requires the three frozen model identities")
    rows = []
    for model in MODELS:
        result = results["models"][model]
        operation = operations["model_operations"][model]
        categories = result["categories"]
        if operation["frozen_outcome_categories"] != categories:
            raise ValueError("Operations and frozen outcome categories differ")
        counts = {
            "verified": categories.get("verified_resolved", 0),
            "unresolved": categories.get("verified_unresolved", 0),
            "unknown": categories.get("unknown_execution", 0),
            "rejected": operation["grade_status_counts"].get("invalid_patch", 0),
            "empty": operation["empty_patch_submissions"],
        }
        if (
            any(type(value) is not int or value < 0 for value in counts.values())
            or result["planned_tasks"] != 50
            or sum(counts.values()) != 50
            or counts["rejected"] + counts["empty"]
            != categories.get("invalid_or_absent_submission", 0)
            or counts["verified"] + counts["unresolved"] + counts["unknown"]
            != operation["grade_status_counts"].get("graded", 0)
        ):
            raise ValueError("Counts do not partition all 50 planned tasks")
        rows.append({"model": model, **counts})
    if sum(row["rejected"] + row["empty"] for row in rows) != 134:
        raise ValueError("The published title does not match the frozen count")
    return rows


def render(directory: Path) -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    if matplotlib.__version__ != "3.11.2":
        raise ValueError("Reproduce this figure with matplotlib==3.11.2")
    rows = frozen_counts(directory)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "text.color": "#1D3042",
            "axes.labelcolor": "#33485C",
            "xtick.color": "#596B7C",
            "ytick.color": "#1D3042",
            "svg.fonttype": "none",
            "svg.hashsalt": "everval-fresh-repair-2026-10-27",
        }
    )
    fig, axis = plt.subplots(figsize=(12.4, 6.5), facecolor="white")
    fig.subplots_adjust(left=0.19, right=0.96, top=0.72, bottom=0.37)
    fig.text(
        0.055,
        0.935,
        "134 of 150 repair requests produced no usable patch",
        fontsize=21,
        fontweight="bold",
        ha="left",
        va="top",
    )
    fig.text(
        0.055,
        0.855,
        "Same 50 tasks per model  ·  110 explicit abstentions  ·  24 rejected patches  ·  16 executed",
        fontsize=11.5,
        color="#53697C",
        ha="left",
        va="top",
    )
    for index, row in enumerate(rows):
        left = 0
        for key, _, color in CATEGORIES:
            count = row[key]
            if count:
                axis.barh(
                    index,
                    count,
                    left=left,
                    height=0.57,
                    color=color,
                    edgecolor="white",
                    linewidth=1.0,
                    zorder=3,
                )
                axis.text(
                    left + count / 2,
                    index,
                    str(count),
                    ha="center",
                    va="center",
                    color="white" if key == "verified" else "#24394C",
                    fontsize=10.5,
                    fontweight="bold",
                    zorder=4,
                )
            left += count
    axis.set_yticks(range(len(rows)), LABELS, fontsize=12, fontweight="bold")
    axis.invert_yaxis()
    axis.set_xlim(0, 50)
    axis.set_xticks(range(0, 51, 10))
    axis.set_xlabel("Requests per model", labelpad=10)
    axis.set_axisbelow(True)
    axis.grid(axis="x", color="#EDF0F3", linewidth=1)
    axis.tick_params(axis="y", length=0, pad=14)
    axis.tick_params(axis="x", length=0, pad=8)
    for spine in axis.spines.values():
        spine.set_visible(False)
    legend = [
        Patch(facecolor=color, edgecolor="none", label=label) for _, label, color in CATEGORIES
    ]
    fig.legend(
        handles=legend,
        loc="lower center",
        bbox_to_anchor=(0.515, 0.155),
        ncol=3,
        frameon=False,
        fontsize=10.5,
        handlelength=1.4,
        columnspacing=2,
        labelspacing=0.9,
    )
    fig.text(
        0.055,
        0.085,
        "One shot, bounded source excerpts, no tools or test feedback. Counts share 50 tasks; they are not 150 independent tasks.",
        fontsize=9.6,
        color="#53697C",
        ha="left",
    )
    fig.text(
        0.055,
        0.045,
        "The ERROR was an observed required-test outcome. Its frozen classification stays unclassified; no request or charge is missing.",
        fontsize=9.6,
        color="#53697C",
        ha="left",
    )
    stem = directory / "fresh-repair-delivery"
    png = stem.with_suffix(".png")
    svg = stem.with_suffix(".svg")
    fig.savefig(png, dpi=160, metadata={"Software": "Everval research figures"})
    fig.savefig(svg, metadata={"Date": None, "Creator": "Everval research figures"})
    plt.close(fig)
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    sources = (
        "fresh-results.json",
        "fresh-operations.json",
        "fresh-admission-audit.json",
        "fresh-independent-audit.json",
        "fresh-output-attribution.json",
        "fresh-power.json",
    )
    provenance = {
        "schema_version": 1,
        "frozen_analysis_source_revision": SOURCE_REVISION,
        "source_files_sha256": {name: sha256(directory / name) for name in sources},
        "publisher_sha256": sha256(Path(__file__)),
        "matplotlib_version": matplotlib.__version__,
        "figure_counts": rows,
        "figures": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in (png, svg)
        },
        "scope": "Descriptive rendering of the unchanged frozen counts; no regrading, new model calls or inferential changes.",
        "power_record_scope_note": "The retained prospective planning text mentions five planned repository clusters. The actual frozen cohort has four. Its independent-pair sensitivity calculation does not adjust for clustering.",
        "reproduction_command": "uv run --no-project --python 3.12 --with matplotlib==3.11.2 python scripts/judge_study/publish_fresh.py --directory docs/benchmarks/judge-study-2026-10-27",
    }
    (directory / "fresh-publication.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.directory), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
