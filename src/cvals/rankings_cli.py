"""Read-only rankings across frozen workspace benchmarks."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

import typer
from rich.table import Table
from rich.text import Text

from cvals.cli import console, fail, output, workspace
from cvals.rankings import build_rankings
from cvals.tui.presentation import plain


class Metric(StrEnum):
    quality = "quality"
    cost = "cost"
    latency = "latency"


def render_matrix(data: dict[str, Any], metric: Metric) -> Table:
    table = Table(title="Everval · " + metric.value.capitalize(), show_lines=True)
    table.add_column("Model / configuration", min_width=18)
    for benchmark in data["benchmarks"]:
        table.add_column(
            Text(
                f"{plain(benchmark['name'])}\nv{plain(benchmark['version'])} · {benchmark['id'][:8]}"
            )
        )
    cells = {(cell["benchmark_id"], cell["system_id"]): cell for cell in data["cells"]}
    for system in data["systems"]:
        label = f"{plain(system['name'])}\n{plain(system['model'])} · {system['id'][:8]}"
        if system["fixture_control"]:
            label += "\nFixture control"
        if system["submission_replay"]:
            label += "\nRecorded submission"
        row = [Text(label)]
        for benchmark in data["benchmarks"]:
            cell = cells.get((benchmark["id"], system["id"]))
            if cell is None:
                row.append(Text("—\nNo run", style="dim"))
                continue
            rank = cell["ranks"][metric.value]
            if metric is Metric.quality:
                value = "—" if cell["pass_rate"] is None else f"{cell['pass_rate']:.1%}"
            elif metric is Metric.cost:
                value = (
                    "unknown"
                    if cell["cost_per_success_usd"] is None
                    else f"${cell['cost_per_success_usd']:.4f}/pass"
                )
            else:
                value = (
                    "—"
                    if cell["p95_duration_ms"] is None
                    else f"{cell['p95_duration_ms'] / 1000:.2f}s P95"
                )
            title = f"#{rank}" if rank is not None else "Unranked"
            row.append(
                Text(
                    f"{title} · {value}\n{cell['completed']}/{cell['scheduled']} graded",
                    style="green" if rank == 1 else "",
                )
            )
        table.add_row(*row)
    return table


def register(parent: typer.Typer) -> None:
    @parent.command("rankings")
    def rankings(
        metric: Annotated[
            Metric, typer.Option(help="Within-benchmark ranking metric.")
        ] = Metric.quality,
        limit: Annotated[int, typer.Option(min=1, max=500, help="Recent runs to scan.")] = 100,
        as_json: Annotated[
            bool, typer.Option("--json", help="Full metadata and evidence links.")
        ] = False,
    ) -> None:
        """Compare saved model configurations across frozen benchmarks; no model calls."""
        try:
            _, store = workspace()
            data = build_rankings(store, limit=limit)
            if as_json:
                output(data)
                return
            if not data["benchmarks"]:
                console.print("No saved benchmark runs. Create an experiment to populate Rankings.")
            else:
                console.print(render_matrix(data, metric))
            console.print(Text(data["metric_definitions"][metric.value]))
            console.print(
                f"Scanned {data['window']['scanned_runs']} recent runs (limit {limit}). "
                "Latest evidence per cell; equal values share ranks. No combined overall score.",
                markup=False,
            )
            if data["window"]["truncated"]:
                console.print(
                    "Older evidence is outside this window; use --limit up to 500.", markup=False
                )
            if data["unverified_experiment_ids"]:
                console.print(
                    "Some recent evidence could not be verified; ranks are withheld.", markup=False
                )
            console.print(
                "Controls and incomplete runs are unranked. Use --json or TUI Ctrl+L for evidence.",
                markup=False,
            )
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)
