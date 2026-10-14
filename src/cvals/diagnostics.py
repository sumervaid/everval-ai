"""Evidence-driven failure investigation and separately retained local regrades."""

from __future__ import annotations

import difflib
import json
import time
from collections import Counter
from dataclasses import replace
from typing import Any

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.evaluators import EvaluationError, evaluate
from cvals.models import Scenario, canonical_json, content_hash, new_id, utc_now
from cvals.store import TERMINAL, Store


def category(trial: dict[str, Any]) -> str:
    state, error = trial["status"], (trial.get("error") or "").lower()
    if state == "completed":
        return "passed" if trial.get("passed") else "incorrect_result"
    if state in {"queued", "running", "cancelled", "budget_exceeded", "interrupted"}:
        return str(state)
    if error.startswith("verifier unavailable") or "grading" in error:
        return "grading_environment"
    if "benchmark admission" in error:
        return "benchmark_admission"
    if any(word in error for word in ("provider", "adapter", "agent", "billing", "usage")):
        return "provider_or_harness"
    return "unclassified_error"


_ACTIONS = {
    "incorrect_result": "Inspect failed checks and output differences; regrade the recorded output to confirm.",
    "grading_environment": "Check the pinned verifier runtime and dependencies, then regrade without calling a model.",
    "benchmark_admission": "Review release provenance and local validation attestations.",
    "provider_or_harness": "Inspect provider metadata and traces; resolve usage or protocol errors before a fresh paid run.",
    "budget_exceeded": "Inspect recorded charges and reservations before creating a separately budgeted run.",
    "interrupted": "Review retained evidence; interrupted paid attempts are not automatically retried.",
    "cancelled": "Review cancellation and any recorded usage; a new run requires explicit admission.",
    "queued": "Inspect worker and queue health.",
    "running": "This attempt is still running; finalized evidence is not available.",
    "unclassified_error": "Inspect the exact error and local runtime logs; no root cause has been inferred.",
    "passed": "No required check failed.",
}


class Diagnostics:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        with self.store._connect(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS diagnostic_regrades(
                id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, trial_id TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL
            )""")

    def triage(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.store.get_experiment(experiment_id)
        rows = [
            {
                "trial_id": t["id"],
                "scenario_id": t["scenario_id"],
                "scenario_name": t["scenario_name"],
                "system_id": t["system_id"],
                "system_name": t["system_name"],
                "category": category(t),
                "status": t["status"],
                "error": t.get("error"),
                "next_action": _ACTIONS[category(t)],
                "cost_usd": t.get("cost_usd"),
                "duration_ms": t.get("duration_ms"),
                "failed_checks": sum(not c.get("passed") for c in t.get("checks", [])),
            }
            for t in experiment["trials"]
        ]
        return {
            "experiment_id": experiment_id,
            "snapshot_hash": experiment["snapshot_hash"],
            "counts": dict(Counter(r["category"] for r in rows)),
            "failures": [r for r in rows if r["category"] != "passed"],
            "classification_basis": "execution states, check outcomes, and explicit error labels",
        }

    def _trial(
        self, experiment_id: str, trial_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], Scenario]:
        experiment = self.store.get_experiment(experiment_id)
        trial = next((t for t in experiment["trials"] if t["id"] == trial_id), None)
        if trial is None:
            raise KeyError(trial_id)
        scenario = Scenario.model_validate(
            next(
                s
                for s in experiment["snapshot"]["collection"]["scenarios"]
                if s["id"] == trial["scenario_id"]
            )
        )
        return experiment, trial, scenario

    def diagnose(self, experiment_id: str, trial_id: str) -> dict[str, Any]:
        experiment, trial, scenario = self._trial(experiment_id, trial_id)
        before = scenario.inputs.get("files", {})
        output = trial.get("output") or {}
        patches = output.get("files", {})
        diffs = []
        truncated = False
        if isinstance(before, dict) and isinstance(patches, dict):
            after = patches if output.get("replace_files") is True else {**before, **patches}
            changed = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
            for path in changed[:30]:
                a, b = before.get(path, ""), after.get(path, "")
                if not isinstance(a, str) or not isinstance(b, str):
                    continue
                limited = len(a) > 64000 or len(b) > 64000
                a, b = a[:64000], b[:64000]
                lines = list(
                    difflib.unified_diff(
                        a.splitlines(keepends=True),
                        b.splitlines(keepends=True),
                        fromfile="starting/" + path,
                        tofile="submitted/" + path,
                        n=3,
                    )
                )
                patch = "".join(lines)
                diffs.append(
                    {
                        "path": path,
                        "patch": patch[:32000],
                        "truncated": limited or len(patch) > 32000,
                    }
                )
            truncated = len(changed) > 30
        with self.store._connect() as connection:
            regrades = [
                json.loads(r[0])
                for r in connection.execute(
                    "SELECT data FROM diagnostic_regrades WHERE experiment_id=? AND trial_id=? ORDER BY created_at DESC",
                    (experiment_id, trial_id),
                )
            ]
        return {
            "experiment_id": experiment_id,
            "trial_id": trial_id,
            "category": category(trial),
            "next_action": _ACTIONS[category(trial)],
            "trial": trial,
            "file_diffs": diffs,
            "diffs_truncated": truncated,
            "regrades": regrades,
            "reproduction": {
                "command": ["everval", "diagnostics", "regrade", experiment_id, trial_id],
                "uses_provider": False,
                "snapshot_hash": experiment["snapshot_hash"],
                "output_hash": content_hash(output),
                "available": trial["status"] in TERMINAL and trial.get("output") is not None,
                "note": "Regrades the stored submission against frozen checks; original results remain intact.",
            },
        }

    def regrade(self, experiment_id: str, trial_id: str) -> dict[str, Any]:
        experiment, trial, scenario = self._trial(experiment_id, trial_id)
        if trial["status"] not in TERMINAL or trial.get("output") is None:
            raise ValueError("Only finalized attempts with recorded outputs can be regraded")
        from cvals.benchmarks import assert_collection_admitted

        with self.store._connect() as connection:
            assert_collection_admitted(connection, experiment["snapshot"]["collection"])
        started = time.perf_counter()
        record: dict[str, Any] = {
            "id": new_id(),
            "experiment_id": experiment_id,
            "trial_id": trial_id,
            "created_at": utc_now(),
            "snapshot_hash": experiment["snapshot_hash"],
            "output_hash": content_hash(trial["output"]),
            "uses_provider": False,
            "checks": [],
            "passed": None,
            "error": None,
        }
        try:
            runtime = replace(
                self.settings,
                sandbox_image=experiment["snapshot"].get(
                    "sandbox_image_requested", self.settings.sandbox_image
                ),
            )
            record["checks"] = evaluate(scenario, trial["output"], runtime)
            required = [c for c in record["checks"] if c["required"]] or record["checks"]
            record["passed"] = bool(required) and all(c["passed"] for c in required)
        except (EvaluationError, ValueError, OSError) as exc:
            record["error"] = str(exc)
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        with self.store._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO diagnostic_regrades VALUES(?,?,?,?,?)",
                (
                    record["id"],
                    experiment_id,
                    trial_id,
                    record["created_at"],
                    canonical_json(record),
                ),
            )
            append_audit(
                connection,
                "trial.regraded",
                trial_id,
                {
                    "regrade_id": record["id"],
                    "output_hash": record["output_hash"],
                    "snapshot_hash": record["snapshot_hash"],
                    "passed": record["passed"],
                },
            )
        return record
