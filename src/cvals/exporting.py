"""Portable reports generated exclusively from recorded experiment evidence."""

from __future__ import annotations

import csv
import html
import io
import json
from typing import Any

from cvals.reports import build_report


def export_experiment(experiment: dict[str, Any], format: str) -> tuple[str, str]:
    report = build_report(experiment)
    if format == "json":
        payload = {**experiment, "report": report, "export_schema_version": 1}
        return json.dumps(
            payload, indent=2, ensure_ascii=False, allow_nan=False
        ), "application/json"
    if format == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        fields = [
            "scenario_id",
            "scenario_name",
            "domain",
            "system_id",
            "system_name",
            "repetition",
            "status",
            "passed",
            "score",
            "duration_ms",
            "cost_usd",
            "error",
        ]
        writer.writerow(fields)
        for trial in experiment.get("trials", []):
            cells = []
            for key in fields:
                value = trial.get(key)
                cell = "" if value is None else str(value)
                if cell.lstrip().startswith(("=", "+", "-", "@")):
                    cell = "'" + cell
                cells.append(cell)
            writer.writerow(cells)
        return out.getvalue(), "text/csv"
    if format != "html":
        raise ValueError("Report format must be json, csv, or html.")

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else "Unavailable"))

    rows = []
    for system in report.get("systems", []):
        rate = system.get("pass_rate")
        pct = "Unavailable" if rate is None else f"{rate:.1%}"
        cost = system.get("cost_usd")
        cost_text = "Unavailable" if cost is None else f"${cost:.4f}"
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{esc(v)}</td>"
                for v in [
                    system.get("name"),
                    system.get("completed"),
                    system.get("scheduled"),
                    pct,
                    cost_text,
                    system.get("cost_complete"),
                ]
            )
            + "</tr>"
        )
    trials = []
    for trial in experiment.get("trials", []):
        trials.append(
            "<tr>"
            + "".join(
                f"<td>{esc(v)}</td>"
                for v in [
                    trial.get("scenario_name"),
                    trial.get("system_name"),
                    trial.get("repetition"),
                    trial.get("status"),
                    trial.get("score"),
                    trial.get("error"),
                ]
            )
            + "</tr>"
        )
    warnings = "".join(f"<li>{esc(w)}</li>" for w in report.get("warnings", []))
    title = esc(experiment.get("name", "Experiment"))
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{title} — Everval AI</title><style>
body{{max-width:1100px;margin:48px auto;padding:0 24px;font:16px/1.6 system-ui;color:#202621;background:#fafaf6}}
h1{{font-size:36px;letter-spacing:-1px}}h2{{margin-top:36px}}.meta{{color:#586159}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #dadfd8;vertical-align:top}}th{{background:#eff2ec}}code{{overflow-wrap:anywhere}}
</style><main><p class="meta">EVERVAL AI / EXPERIMENT REPORT</p><h1>{title}</h1>
<p class="meta">Recorded {esc(experiment.get("created_at"))} · Status: {esc(experiment.get("status"))}</p>
<p>Collection fingerprint: <code>{esc(experiment.get("collection_hash"))}</code></p>
<h2>System comparison</h2><table><thead><tr><th>System</th><th>Completed</th><th>Scheduled</th><th>Pass rate</th><th>Recorded cost</th><th>Cost complete</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<h2>Interpretation</h2><ul>{warnings or "<li>Interpret results for the recorded task cohort and configuration.</li>"}</ul>
<h2>Trial ledger</h2><table><thead><tr><th>Scenario</th><th>System</th><th>Repeat</th><th>Status</th><th>Score</th><th>Error</th></tr></thead><tbody>{"".join(trials)}</tbody></table>
<p class="meta">Generated from stored trial evidence. Fixture controls validate the evaluation pipeline and do not measure model performance.</p></main></html>"""
    return document, "text/html"
