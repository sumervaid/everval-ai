"""Read-only checkpoint monitoring over the shared post-training service."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Button, DataTable, RichLog, Select, Static

from cvals.tui.dialogs import EvidenceDialog
from cvals.tui.presentation import human_text, money, percent, plain


def evaluation_lag(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def training_provenance(kind: str) -> str:
    if kind == "synthetic_control":
        return "SYNTHETIC CONTROL · not model performance"
    return "DECLARED TRAINING · weight identity not independently verified"


def checkpoint_details(report: dict[str, Any], checkpoint: dict[str, Any]) -> str:
    run = report["run"]
    lines = [
        plain(run["name"]) + " · step " + str(checkpoint["step"]),
        training_provenance(run["provenance_kind"]),
        "Training identity: " + plain(run["training_run_id"]),
        "Inference scope: " + plain(run["protocol"].get("inference_scope"), "not declared"),
        "Checkpoint: " + plain(checkpoint["id"]),
        "Weight SHA-256: " + plain(checkpoint["weight_sha256"]),
        "System: " + plain(checkpoint["system_id"]),
        "Frozen configuration: " + plain(checkpoint["system_hash"]),
        "Recorded: " + plain(checkpoint["created_at"]),
        "State: " + plain(checkpoint["status"]),
        "Evaluation lag: " + evaluation_lag(checkpoint.get("lag_seconds")),
        "Known spend: "
        + ("at least " if checkpoint.get("unknown_cost_count") else "")
        + money(checkpoint.get("known_cost_usd")),
        f"Unknown costs: {checkpoint.get('unknown_cost_count', 0)}",
        "",
        "Evaluation lag runs from checkpoint registration to evaluation completion,",
        "or to now while unfinished. It is not queue wait time.",
        "",
    ]
    metrics = {metric["domain"]: metric for metric in checkpoint.get("metrics", [])}
    for domain in run["protocol"]["domains"]:
        name = domain["name"]
        metric = metrics.get(name)
        lines.append(plain(name).upper())
        if not metric:
            lines += ["Not scheduled · no score", ""]
            continue
        lines += [
            "Observed score: " + percent(metric.get("value")),
            f"Graded: {metric['n']}/{metric['expected_n']} · " + plain(metric["status"]),
            "Reported interval: "
            + percent(metric.get("lower"))
            + "–"
            + percent(metric.get("upper")),
            "Method: " + plain(metric.get("interval_method"), "interval unavailable"),
            f"Errors: {metric['errors']} · Missing: {metric['missing']}",
            "Cost: "
            + ("" if metric.get("cost_complete") else "at least ")
            + money(metric.get("known_cost_usd")),
            "Experiment: " + plain(metric.get("experiment_id"), "not queued"),
            "",
        ]
    lines += [
        "Checkpoint intervals are descriptive. Use paired comparisons for decisions.",
        "Intervals assume independent families and a frozen comparison policy.",
        "Adaptive tuning on reused families can invalidate coverage.",
        "Results do not certify deployment readiness.",
    ]
    for key, title in [
        ("comparisons", "RUN REGRESSION DECISIONS"),
        ("alerts", "RUN ALERTS"),
        ("investigations", "RUN INVESTIGATIONS"),
    ]:
        lines += ["", title]
        records = report.get(key, [])
        if not records:
            lines.append("No retained records. Absence of an alert does not establish equivalence.")
        for record in records:
            if record.get("stale"):
                lines.append(
                    "STALE EVIDENCE · source results changed; recheck before using this decision."
                )
            lines.append(human_text(record))
    return "\n".join(lines)


class TrainingMixin:
    selected: dict[str, Any] | None
    training_run_id: str | None = None
    training_checkpoint_id: str | None = None
    _training_service: Any = None
    _training_detail = ""

    def training_fetch(self: Any) -> dict[str, Any]:
        if self._training_service is None:
            from cvals.posttraining import PostTraining

            self._training_service = PostTraining(self.settings)
        runs = self._training_service.list_runs()
        identity = next(
            (run["id"] for run in runs if run["id"] == self.training_run_id),
            runs[0]["id"] if runs else None,
        )
        return {
            "runs": runs,
            "dashboard": self._training_service.dashboard(identity) if identity else None,
        }

    def render_training(self: Any, payload: dict[str, Any]) -> None:
        runs, report = payload["runs"], payload["dashboard"]
        selector = self.query_one("#training-run", Select)
        options = [
            (plain(run["name"]) + " · " + plain(run["provenance_kind"]), run["id"]) for run in runs
        ]
        if options != getattr(self, "_training_options", None):
            self._training_options = options
            selector.set_options(options)
        selector.disabled = not runs
        self.training_run_id = report["run"]["id"] if report else None
        if self.training_run_id:
            selector.value = self.training_run_id
        previous = self.training_checkpoint_id
        self.rows = sorted(report["checkpoints"], key=lambda row: row["step"]) if report else []
        table = self.query_one("#ledger", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.fixed_columns = 1
        domains = report["run"]["protocol"]["domains"] if report else []
        table.add_column("STEP / STATE", width=20)
        for domain in domains:
            table.add_column(Text(plain(domain["name"])), width=26)
        table.add_column("LAG / COST", width=24)
        for index, checkpoint in enumerate(self.rows):
            metrics = {item["domain"]: item for item in checkpoint["metrics"]}
            cells = []
            for domain in domains:
                metric = metrics.get(domain["name"])
                if metric is None:
                    cells.append(Text("—\nnot scheduled", style="#e6bd76"))
                else:
                    cells.append(
                        Text(
                            percent(metric.get("value"))
                            + f" · {metric['n']}/{metric['expected_n']} graded\n"
                            + plain(metric["status"]),
                            style="#e6bd76" if metric["n"] < metric["expected_n"] else "#dce5ea",
                        )
                    )
            table.add_row(
                Text(f"Step {checkpoint['step']}\n" + plain(checkpoint["status"])),
                *cells,
                Text(
                    evaluation_lag(checkpoint.get("lag_seconds"))
                    + "\n"
                    + ("≥ " if checkpoint.get("unknown_cost_count") else "")
                    + money(checkpoint.get("known_cost_usd"))
                    + f" · {checkpoint.get('unknown_cost_count', 0)} unknown"
                ),
                key=str(index),
                height=2,
            )
        if report:
            budget = report["budget"]
            self.query_one("#training-summary", Static).update(
                training_provenance(report["run"]["provenance_kind"])
                + f"\n{len(self.rows)} checkpoints · {len(domains)} domains · "
                + ("≥ " if budget["unknown_cost_count"] else "")
                + money(budget["known_cost_usd"])
                + f" known · {budget['unknown_cost_count']} unknown costs"
                + "\nSnapshot: "
                + plain(report.get("generated_at"), "timestamp unavailable")
            )
        else:
            self.query_one("#training-summary", Static).update("No registered training runs.")
        table.display = bool(self.rows)
        self.query_one("#empty").display = not self.rows
        self.query_one("#empty", Static).update(
            "No checkpoints registered.\n"
            "Use the training CLI to freeze a protocol, register exact checkpoint identities, "
            "and queue evaluations. This monitor never creates demonstration scores."
        )
        if self.rows:
            index = next(
                (i for i, row in enumerate(self.rows) if row["id"] == previous), len(self.rows) - 1
            )
            table.move_cursor(row=index)
            self.select_training_checkpoint(index)
            if self._focus_after_load and len(self.screen_stack) == 1:
                self._focus_after_load = False
                table.focus()
        else:
            self.selected = None
            self.training_checkpoint_id = None
            self._training_detail = "No checkpoint evidence available."
            self.query_one("#detail", RichLog).clear()
            self.query_one("#tool-training_details", Button).disabled = True
        self.query_one("#tool-training_protocol", Button).disabled = report is None
        self.query_one("#notice").display = False
        if report and report["budget"].get("admission_blocked"):
            self.query_one("#notice", Static).update(
                "New paid admissions blocked by accounting review. "
                + "; ".join(
                    plain(reason) for reason in report["budget"].get("blocking_reasons", [])
                )
            )
            self.query_one("#notice").display = True

    def select_training_checkpoint(self: Any, index: int) -> None:
        if not 0 <= index < len(self.rows):
            return
        checkpoint = self.rows[index]
        self.selected = checkpoint
        self.training_checkpoint_id = checkpoint["id"]
        self._training_detail = checkpoint_details(self.payload["dashboard"], checkpoint)
        self.query_one("#detail-heading", Static).update(f"CHECKPOINT / STEP {checkpoint['step']}")
        log = self.query_one("#detail", RichLog)
        log.clear()
        log.write(Text(self._training_detail))
        self.query_one("#tool-training_details", Button).disabled = False

    def training_run_changed(self: Any, event: Select.Changed) -> None:
        if (
            self.page == "training"
            and isinstance(event.value, str)
            and event.value != self.training_run_id
        ):
            self.training_run_id = event.value
            self.training_checkpoint_id = None
            self._generation += 1
            self._focus_after_load = True
            self.reload()

    def training_command(self: Any, name: str) -> None:
        if name == "training_details" and self.selected:
            self.push_screen(EvidenceDialog("Checkpoint evidence", self._training_detail))
        elif name == "training_protocol" and self.payload.get("dashboard"):
            report = self.payload["dashboard"]
            self.push_screen(
                EvidenceDialog(
                    "Frozen training protocol",
                    human_text(report["run"])
                    + "\n\nEVALUATION QUALIFICATIONS\n"
                    + human_text({"records": report.get("qualifications", [])})
                    + "\n\nEVALUATION MIX STUDIES\n"
                    + human_text({"records": report.get("mix_studies", [])}),
                )
            )
