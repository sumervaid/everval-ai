"""Model-by-benchmark views over the shared, read-only rankings service."""

from __future__ import annotations

from collections import Counter
from typing import Any

from rich.text import Text
from textual.widgets import Button, DataTable, RichLog, Static

from cvals.tui.dialogs import EvidenceDialog
from cvals.tui.presentation import money, percent, plain

METRICS = {
    "quality": "Quality",
    "cost": "Cost / pass",
    "latency": "P95 latency",
}


def duration(value: Any) -> str:
    if value is None:
        return "unknown"
    seconds = float(value) / 1000
    return f"{seconds / 60:.1f} min" if seconds >= 60 else f"{seconds:.2f} s"


def rank_label(cell: dict[str, Any], metric: str, ties: Counter[tuple[str, int]]) -> str:
    rank = cell.get("ranks", {}).get(metric)
    if rank is None:
        return "unranked"
    suffix = " tie" if ties[(cell["benchmark_id"], rank)] > 1 else ""
    return f"#{rank}{suffix}"


def metric_cell(cell: dict[str, Any] | None, metric: str, ties: Counter[tuple[str, int]]) -> Text:
    """Keep coverage and missing evidence visible without relying on color."""
    if cell is None:
        return Text("—\nnot run", style="#788d9e")
    rank = rank_label(cell, metric, ties)
    scheduled = cell["scheduled"]
    graded = f"{cell['completed']}/{scheduled} graded"
    if metric == "quality":
        value = percent(cell.get("pass_rate"))
        coverage = f"{cell['passed']}/{scheduled} passed · {graded}"
    elif metric == "cost":
        value = money(cell.get("cost_per_success_usd"))
        coverage = graded
        if not cell.get("cost_complete"):
            coverage = f"≥{money(cell.get('known_cost_usd'))} known · {graded}"
        elif cell.get("cost_per_success_usd") is None:
            coverage = "no passing attempts · " + graded
    else:
        value = duration(cell.get("p95_duration_ms"))
        coverage = f"{cell.get('duration_recorded', 0)}/{scheduled} timed · {graded}"
    style = "#83d7c7 on #18382f" if cell.get("ranks", {}).get(metric) == 1 else "#dce5ea"
    if cell.get("ranks", {}).get(metric) is None:
        style = "#e6bd76"
    text = Text(f"{value}  {rank}\n", style=style)
    text.append(coverage, style="#9aaebd")
    return text


def ranking_details(
    report: dict[str, Any],
    system: dict[str, Any],
    benchmark: dict[str, Any] | None,
    cell: dict[str, Any] | None,
    metric: str,
    ties: Counter[tuple[str, int]],
) -> str:
    """A human-readable projection; no outputs, hidden checks or credentials."""
    lines = [
        plain(system["name"]),
        "Model: " + plain(system.get("model")),
        "Configuration: " + plain(system["id"]),
        "Adapter: " + plain(system.get("kind")) + " / " + plain(system.get("harness")),
    ]
    if system.get("fixture_control"):
        lines.append("Fixture control · integration evidence, not model performance.")
    if system.get("submission_replay"):
        lines.append("Imported evidence · not a new independent execution.")
    if benchmark is None:
        return "\n".join(lines + ["", "Move right to select a benchmark."])
    lines += [
        "",
        plain(benchmark["name"]) + " · " + plain(benchmark.get("version")),
        "Frozen cohort: " + plain(benchmark.get("collection_hash")),
        "Execution contract: " + plain(benchmark.get("contract_hash")),
        "Partition: " + plain(benchmark.get("benchmark_partition"), "unpartitioned"),
        f"Tasks: {benchmark.get('scenario_count', '—')} · Repetitions: {benchmark.get('repetitions', '—')}",
    ]
    if cell is None:
        lines += [
            "",
            "Not run for this configuration and benchmark within the scanned window.",
            "A dash means missing evidence, not a zero score or a free run.",
        ]
        return "\n".join(lines)
    lines += [
        "",
        METRICS[metric] + ": " + rank_label(cell, metric, ties),
        f"Quality: {cell['passed']}/{cell['scheduled']} passed ({percent(cell.get('pass_rate'))})",
        f"Grading coverage: {cell['completed']}/{cell['scheduled']} scheduled attempts",
        "95% interval: " + percent(cell.get("ci_low")) + "–" + percent(cell.get("ci_high")),
        "Interval method: " + plain(cell.get("interval_method")),
        "Cost / passing attempt: " + money(cell.get("cost_per_success_usd")),
        "Total cost: " + money(cell.get("cost_usd")),
        "Known spend: "
        + ("" if cell.get("cost_complete") else "at least ")
        + money(cell.get("known_cost_usd")),
        "Cost coverage: " + ("complete" if cell.get("cost_complete") else "incomplete"),
        "Median / P95 duration: "
        + duration(cell.get("median_duration_ms"))
        + " / "
        + duration(cell.get("p95_duration_ms")),
        f"Timing coverage: {cell.get('duration_recorded', 0)}/{cell['scheduled']} scheduled attempts",
        "Duration scope: all recorded attempts, including errors.",
        "",
        "Latest run: " + plain(cell.get("experiment_name")),
        "Run identity: " + plain(cell.get("experiment_id")),
        "Created: " + plain(cell.get("created_at")),
        "State: " + plain(cell.get("status")),
        "Recorded system identity: " + plain(cell.get("source_system_id")),
    ]
    if system.get("external_adapter"):
        lines.append(
            "External adapter timing covers its request; internal stages are self-reported."
        )
    lines += [
        "",
        "Ranks compare eligible configurations within this frozen benchmark only.",
        "Ties share a rank. There is no overall score across different benchmarks.",
        "Latest runs are selected by creation time, never by best outcome.",
    ]
    issues = cell.get("issues", [])
    if issues:
        lines += ["", "Evidence limits", *("• " + plain(issue) for issue in issues)]
    if report.get("warnings"):
        lines += ["", "Workspace notes", *("• " + plain(note) for note in report["warnings"])]
    return "\n".join(lines)


class RankingsMixin:
    """Matrix navigation and presentation; ranking rules stay in the core."""

    selected: dict[str, Any] | None
    ranking_metric = "quality"
    ranking_selection: tuple[str, str] | None = None
    ranking_trial_system_id: str | None = None

    def render_rankings(self: Any, report: dict[str, Any]) -> None:
        self.rows = report.get("systems", [])
        benchmarks = report.get("benchmarks", [])
        self._ranking_cells = {
            (cell["system_id"], cell["benchmark_id"]): cell for cell in report.get("cells", [])
        }
        self._ranking_ties = Counter(
            (cell["benchmark_id"], cell["ranks"][self.ranking_metric])
            for cell in report.get("cells", [])
            if cell.get("ranks", {}).get(self.ranking_metric) is not None
        )
        window = report.get("window", {})
        counts = report.get("counts", {})
        scanned = window.get("scanned_runs", counts.get("experiments_scanned", 0))
        limit = window.get("limit", 100)
        summary = (
            f"{len(self.rows)} configurations · {len(benchmarks)} frozen benchmarks · "
            f"scanned {scanned} of the latest {limit} runs"
        )
        if window.get("truncated"):
            summary += " · older runs omitted"
        self.query_one("#ranking-summary", Static).update(summary)
        self.query_one("#page-title", Static).update("Rankings · " + METRICS[self.ranking_metric])
        for metric in METRICS:
            self.query_one("#tool-ranking_" + metric, Button).variant = (
                "primary" if metric == self.ranking_metric else "default"
            )
        table = self.query_one("#ledger", DataTable)
        table.clear(columns=True)
        table.cursor_type = "cell"
        table.fixed_columns = 1
        table.header_height = 2
        table.add_column(
            Text("MODEL / CONFIGURATION\nExact versions stay separate"), width=28, key="system"
        )
        for benchmark in benchmarks:
            header = Text(plain(benchmark["name"]))
            header.truncate(29, overflow="ellipsis")
            header.append(
                "\n" + plain(benchmark.get("version")) + " · " + plain(benchmark["id"])[:8]
            )
            table.add_column(header, width=30, key=benchmark["id"])
        for index, system in enumerate(self.rows):
            label = Text(plain(system["name"]), style="bold #dce5ea")
            label.truncate(27, overflow="ellipsis")
            category = (
                "CONTROL"
                if system.get("fixture_control")
                else "IMPORTED"
                if system.get("submission_replay")
                else plain(system.get("model"), "configuration")
            )
            sublabel = Text(category)
            sublabel.truncate(16, overflow="ellipsis")
            label.append("\n" + sublabel.plain + " · " + plain(system["id"])[:8], style="#9aaebd")
            table.add_row(
                label,
                *(
                    metric_cell(
                        self._ranking_cells.get((system["id"], benchmark["id"])),
                        self.ranking_metric,
                        self._ranking_ties,
                    )
                    for benchmark in benchmarks
                ),
                height=2,
                key=str(index),
            )
        populated = bool(self.rows and benchmarks)
        table.display = populated
        self.query_one("#empty").display = not populated
        self.query_one("#empty", Static).update(
            "No recorded benchmark runs yet.\n"
            "Create an experiment from a frozen collection to populate this matrix.\n"
            "The view reads saved evidence and makes no model calls."
        )
        if populated:
            system_id, benchmark_id = self.ranking_selection or ("", "")
            row = next((i for i, item in enumerate(self.rows) if item["id"] == system_id), 0)
            column = next(
                (i + 1 for i, item in enumerate(benchmarks) if item["id"] == benchmark_id), 1
            )
            table.move_cursor(row=row, column=column)
            self.select_ranking_cell(row, column)
            if self._focus_after_load and len(self.screen_stack) == 1:
                self._focus_after_load = False
                table.focus()
        else:
            self.selected = None
            self.ranking_selection = None
            self._ranking_detail = (
                "Run a benchmark to inspect model quality, cost and latency here."
            )
            self.query_one("#detail", RichLog).clear()
            self.query_one("#detail", RichLog).write(Text(self._ranking_detail))
            self.update_ranking_actions()

    def select_ranking_cell(self: Any, row: int, column: int) -> None:
        if self.page != "rankings" or row >= len(self.rows) or row < 0:
            return
        benchmarks = self.payload.get("benchmarks", [])
        if column < 0 or column > len(benchmarks):
            return
        system = self.rows[row]
        benchmark = benchmarks[column - 1] if column else None
        benchmark_id = benchmark["id"] if benchmark else ""
        self.ranking_selection = (system["id"], benchmark_id)
        cell = self._ranking_cells.get(self.ranking_selection)
        self.selected = cell
        self._ranking_detail = ranking_details(
            self.payload, system, benchmark, cell, self.ranking_metric, self._ranking_ties
        )
        self.query_one("#detail-heading", Static).update("INSPECT / " + plain(system["name"]))
        log = self.query_one("#detail", RichLog)
        log.clear()
        log.write(Text(self._ranking_detail))
        self.update_ranking_actions()

    def update_ranking_actions(self: Any) -> None:
        self.query_one("#tool-ranking_details", Button).disabled = self.ranking_selection is None
        self.query_one("#tool-ranking_run", Button).disabled = not bool(self.selected)

    def ranking_highlighted(self: Any, event: DataTable.CellHighlighted) -> None:
        if self.page == "rankings":
            self.select_ranking_cell(event.coordinate.row, event.coordinate.column)

    def ranking_opened(self: Any, event: DataTable.CellSelected) -> None:
        if self.page == "rankings":
            self.select_ranking_cell(event.coordinate.row, event.coordinate.column)
            self.command_ranking_details()

    def action_ranking_metric(self: Any, metric: str) -> None:
        if (
            self.page == "rankings"
            and len(self.screen_stack) == 1
            and isinstance(self.payload, dict)
        ):
            self.ranking_metric = metric
            self.render_rankings(self.payload)
            self.query_one("#ledger", DataTable).focus()

    def action_ranking_run(self: Any) -> None:
        if self.page == "rankings" and len(self.screen_stack) == 1:
            self.command_ranking_run()

    def command_ranking_details(self: Any) -> None:
        if self.ranking_selection is not None:
            self.push_screen(EvidenceDialog("Model benchmark evidence", self._ranking_detail))

    def command_ranking_run(self: Any) -> None:
        if self.selected and self.selected.get("experiment_id"):
            self.experiment_id = self.selected["experiment_id"]
            self.ranking_trial_system_id = self.selected.get("source_system_id")
            self.show_page("trials")

    def ranking_command(self: Any, name: str) -> None:
        metric = name.removeprefix("ranking_")
        if metric in METRICS:
            self.action_ranking_metric(metric)
        elif name == "ranking_details":
            self.command_ranking_details()
        elif name == "ranking_run":
            self.command_ranking_run()
