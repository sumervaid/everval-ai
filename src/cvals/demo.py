"""A no-key installation walkthrough using retained evaluation evidence."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any, Literal

from cvals.audit import AuditLog
from cvals.community import CommunityPack, PackProvenance, make_pack, pack_summary
from cvals.comparison import compare_experiments
from cvals.config import Settings
from cvals.diagnostics import Diagnostics
from cvals.domains import validate_collection
from cvals.engine import Engine
from cvals.models import (
    Check,
    Collection,
    ExperimentCreate,
    Scenario,
    System,
    canonical_json,
    content_hash,
    new_id,
)
from cvals.store import Store

EXAMPLES = ("coding", "documents", "data")
_CREATED = "2026-10-14T00:00:00+00:00"
DISCLAIMER = "Installation controls only. No model was called; these results do not measure model performance."


def _scenario(
    domain: Literal["coding", "documents", "data"],
    name: str,
    instruction: str,
    inputs: dict[str, Any],
    reference: dict[str, Any],
    checks: list[Check],
    negatives: list[dict[str, Any]],
    limitation: str,
) -> Scenario:
    return Scenario(
        id=f"demo-{domain}",
        name=name,
        domain=domain,
        instruction=instruction,
        inputs=inputs,
        reference_output=reference,
        checks=checks,
        description=limitation,
        tags=["installation-control", "original-fixture"],
        provenance={
            "origin": "original_fixture",
            "license": "Apache-2.0",
            "purpose": "installation-control",
            "negative_controls": negatives,
        },
    )


def builtin_pack(example: str) -> CommunityPack:
    if example not in EXAMPLES:
        raise ValueError("Choose coding, documents, or data")
    if example == "coding":
        before = "def total(items):\n    return sum(item['price'] for item in items)\n"
        after = "def total(items):\n    return sum(item['price'] * item['quantity'] for item in items)\n"
        reference: dict[str, Any] = {"files": {"cart.py": after}}
        scenario = _scenario(
            "coding",
            "Repair quantity-aware cart totals",
            "Update cart.py so total multiplies each item's price by its quantity. Return a files object containing cart.py. This installation fixture checks the canonical edit; use sandbox tests for semantic equivalence.",
            {
                "files": {"cart.py": before},
                "example": {"items": [{"price": 5, "quantity": 3}], "total": 15},
            },
            reference,
            [
                Check(
                    id="canonical-edit",
                    name="Canonical quantity edit",
                    kind="equals",
                    path="files",
                    expected=reference["files"],
                )
            ],
            [{}, {"files": {"cart.py": before}}],
            "Text-only edit contract; equivalent alternate code can fail. It does not execute or certify program behavior.",
        )
    elif example == "documents":
        quote = "Enterprise customers may request a refund within 30 days of purchase."
        reference = {
            "refund_days": 30,
            "citations": [{"source_id": "refund-policy", "quote": quote}],
        }
        scenario = _scenario(
            "documents",
            "Ground a refund answer in current policy",
            "Using only the supplied sources, return refund_days for enterprise customers and citations with source_id and exact supporting quote. The archived policy is superseded.",
            {
                "sources": [
                    {"id": "refund-policy", "text": "Current policy. " + quote},
                    {
                        "id": "archived-policy",
                        "text": "Archived policy: refunds were available within 14 days.",
                    },
                ]
            },
            reference,
            [
                Check(
                    id="refund-days",
                    name="Current refund window",
                    kind="numeric",
                    path="refund_days",
                    expected=30,
                ),
                Check(
                    id="grounded-citation",
                    name="Verifiable current-policy quotation",
                    kind="citation",
                    path="citations",
                    expected={"source_ids": ["refund-policy"], "require_quote": True},
                ),
            ],
            [
                {},
                {
                    "refund_days": 14,
                    "citations": [
                        {
                            "source_id": "archived-policy",
                            "quote": "refunds were available within 14 days.",
                        }
                    ],
                },
                {
                    "refund_days": 30,
                    "citations": [
                        {"source_id": "refund-policy", "quote": "Refunds are always unlimited."}
                    ],
                },
            ],
            "Checks the numeric answer, source identity, and verbatim quotation; semantic entailment is not assessed.",
        )
    else:
        reference = {"currency": "USD", "net_revenue": 140, "paid_orders": 2}
        scenario = _scenario(
            "data",
            "Calculate net revenue without cancelled orders",
            "Return currency, net_revenue, and paid_orders as JSON. Include only paid orders; subtract refunds from gross revenue. Report the count of paid orders.",
            {
                "orders": [
                    {"id": "A", "status": "paid", "gross": 120, "refund": 20},
                    {"id": "B", "status": "cancelled", "gross": 900, "refund": 0},
                    {"id": "C", "status": "paid", "gross": 50, "refund": 10},
                ],
                "currency": "USD",
            },
            reference,
            [
                Check(
                    id="output-shape",
                    name="Typed analytical result",
                    kind="json_schema",
                    expected={
                        "type": "object",
                        "required": ["currency", "net_revenue", "paid_orders"],
                        "additionalProperties": False,
                        "properties": {
                            "currency": {"const": "USD"},
                            "net_revenue": {"type": "number"},
                            "paid_orders": {"type": "integer"},
                        },
                    },
                ),
                Check(
                    id="net-revenue",
                    name="Refund-adjusted paid revenue",
                    kind="numeric",
                    path="net_revenue",
                    expected=140,
                    tolerance=0.005,
                ),
                Check(
                    id="paid-orders",
                    name="Paid order count",
                    kind="numeric",
                    path="paid_orders",
                    expected=2,
                ),
            ],
            [
                {},
                {"currency": "USD", "net_revenue": 170, "paid_orders": 2},
                {"currency": "USD", "net_revenue": 1040, "paid_orders": 3},
            ],
            "A small invented dataset with explicit numeric checks; no live warehouse connection.",
        )
    collection = Collection(
        id=f"demo-{example}-collection",
        name=scenario.name,
        domain=scenario.domain,
        created_at=_CREATED,
        scenarios=[scenario],
        description=DISCLAIMER,
    )
    return make_pack(
        name=scenario.name,
        slug=f"everval-demo-{example}",
        collection=collection,
        provenance=PackProvenance(
            origin="original:everval-installation-examples",
            authors=["Everval contributors"],
            permission_basis="Original project fixtures distributed under Apache-2.0; all business records are invented.",
        ),
        purpose="installation-control",
    )


def builtin_catalog() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision": "2026-10-14.1",
        "network": False,
        "trust": "Bundled examples reviewed with the source release; no remote registry or publisher signature.",
        "packs": [{"key": key, **pack_summary(builtin_pack(key))} for key in EXAMPLES],
    }


def _write_new(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(value)


def run_demo(
    base: Settings, *, example: str = "all", workspace: Path | None = None, sandbox: bool = False
) -> dict[str, Any]:
    if example not in (*EXAMPLES, "all"):
        raise ValueError("Choose all, coding, documents, or data")
    if sandbox and example != "coding":
        raise ValueError("--sandbox requires --example coding")
    path = workspace.expanduser().absolute() if workspace else base.data_dir / "demos" / new_id()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Demo workspace must be a new or empty directory")
    if path.resolve() == base.data_dir.resolve():
        raise ValueError("Choose an isolated demo directory, not the active workspace")
    settings = Settings(
        data_dir=path,
        docker_host=base.docker_host,
        sandbox_image=base.sandbox_image,
        allow_remote_providers=False,
    )
    settings.prepare()
    packs = [builtin_pack(key) for key in EXAMPLES if example in {"all", key}]
    scenarios = [s for pack in packs for s in pack.collection.scenarios]
    if sandbox:
        from cvals.packs import starter_collections

        scenarios = [starter_collections()[0].scenarios[0]]
    collection = Collection(
        id="demo-workload",
        name="Everval installation walkthrough",
        created_at=_CREATED,
        scenarios=scenarios,
        description=DISCLAIMER,
    )
    validation = validate_collection(collection, settings)
    if not validation["valid"]:
        raise ValueError("Installation controls failed: " + canonical_json(validation))
    store = Store(settings)
    store.init()
    store.save_collection(collection)
    engine = Engine(store, settings)
    runs = []
    phases: list[tuple[str, Literal["reference", "incomplete"]]] = [
        ("baseline", "reference"),
        ("changed", "incomplete"),
        ("candidate", "reference"),
    ]
    for phase, kind in phases:
        system = System(
            id="demo-control",
            name="Installation fixture",
            kind=kind,
            provenance={"purpose": "installation-control", "demo_phase": phase},
        )
        store.save_system(system)
        queued = store.create_experiment(
            ExperimentCreate(
                name=f"Demo · {phase}",
                collection_id=collection.id,
                system_ids=[system.id],
                budget_usd=0,
            )
        )
        runs.append(engine.run_experiment(queued["id"]))
    baseline, changed, candidate = runs
    for run, expected in zip(runs, (True, False, True), strict=True):
        if run["status"] != "completed" or any(
            t["status"] != "completed" or t["passed"] is not expected for t in run["trials"]
        ):
            raise ValueError(
                "Demo controls did not produce expected outcomes; inspect retained workspace trials"
            )
    diagnostics = Diagnostics(settings)
    failures = diagnostics.triage(changed["id"])
    reproductions = [diagnostics.regrade(changed["id"], trial["id"]) for trial in changed["trials"]]
    if any(r["passed"] is not False or r["error"] is not None for r in reproductions):
        raise ValueError("Stored failure reproduction did not match the original evidence")
    comparisons = {
        "change": compare_experiments(baseline, changed),
        "candidate": compare_experiments(changed, candidate),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "installation-control",
        "disclaimer": DISCLAIMER,
        "workspace": str(path),
        "example": example,
        "sandbox": sandbox,
        "provider_calls": 0,
        "provider_cost_usd": 0.0,
        "collection_hash": content_hash(collection.model_dump(mode="json")),
        "validation": validation,
        "runs": [
            {
                "phase": phase,
                "id": run["id"],
                "snapshot_hash": run["snapshot_hash"],
                "report": run["report"],
            }
            for phase, run in zip(("baseline", "changed", "candidate"), runs, strict=True)
        ],
        "failures": failures,
        "reproductions": reproductions,
        "comparisons": comparisons,
        "proposal": {
            "status": "demonstration-only",
            "action": "Restore the passing fixture configuration",
            "evidence_experiment_id": candidate["id"],
            "requires_human_review": True,
            "deployed": False,
        },
        "audit": AuditLog(settings).verify(),
        "limitations": [s.description for s in scenarios],
    }
    # This local evidence file contains grader evidence; it is never uploaded.
    _write_new(
        path / "demo-evidence.json",
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
    )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "kind": "installation-control",
        "disclaimer": DISCLAIMER,
        "collection_hash": report["collection_hash"],
        "provider_calls": 0,
        "provider_cost_usd": 0,
        "results": [
            {
                "phase": row["phase"],
                "experiment_id": row["id"],
                "snapshot_hash": row["snapshot_hash"],
                "passed": row["report"]["systems"][0]["passed"],
                "scheduled": row["report"]["systems"][0]["scheduled"],
            }
            for row in report["runs"]
        ],
        "evidence_digest": content_hash(report),
        "limitations": report["limitations"],
    }
    _write_new(path / "summary.json", json.dumps(summary, indent=2) + "\n")
    safe_hash = html.escape(summary["evidence_digest"])
    badge = f'<svg xmlns="http://www.w3.org/2000/svg" width="310" height="28" role="img" aria-label="Everval: installation controls verified"><title>Everval installation controls, not model results. Evidence {safe_hash}</title><rect width="310" height="28" rx="5" fill="#12251f"/><text x="12" y="19" fill="#c0f2d4" font-family="monospace" font-size="12">EVERVAL · installation controls verified</text></svg>\n'
    _write_new(path / "installation-badge.svg", badge)
    report["artifacts"] = {
        "private_evidence": str(path / "demo-evidence.json"),
        "summary": str(path / "summary.json"),
        "badge": str(path / "installation-badge.svg"),
        "uploaded": False,
    }
    return report


def export_summary(settings: Settings, experiment_id: str, directory: Path) -> dict[str, Any]:
    """Write a deliberate, bounded summary without task content or model outputs."""
    store = Store(settings)
    store.init()
    experiment = store.get_experiment(experiment_id)
    metrics = experiment["report"]["systems"]
    summary_rows = []
    fields = (
        "system_id",
        "kind",
        "fixture_control",
        "submission_replay",
        "scheduled",
        "completed",
        "passed",
        "failed",
        "errors",
        "incomplete",
        "pass_rate",
        "cost_usd",
        "known_cost_usd",
        "cost_complete",
        "cost_per_pass",
        "median_duration_ms",
        "p95_duration_ms",
    )
    for row in metrics:
        summary_rows.append({key: row.get(key) for key in fields})
    fixture = any(row["fixture_control"] for row in summary_rows)
    replay = any(row["submission_replay"] for row in summary_rows)
    finalized = experiment["status"] in {"completed", "failed", "cancelled"}
    label = (
        "installation controls"
        if fixture
        else "recorded submissions"
        if replay
        else "evaluation evidence"
    )
    snapshot_and_trials = {
        "snapshot_hash": experiment["snapshot_hash"],
        "trials": experiment["trials"],
    }
    summary = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "created_at": experiment["created_at"],
        "status": experiment["status"],
        "finalized": finalized,
        "fixture_controls_present": fixture,
        "recorded_submissions_present": replay,
        "evidence_kind": label,
        "snapshot_hash": experiment["snapshot_hash"],
        "collection_hash": experiment["collection_hash"],
        "evidence_hash": content_hash(snapshot_and_trials),
        "systems": summary_rows,
        "disclosure": "Counts, costs, durations, opaque identifiers, and content digests only. No tasks, system names, configurations, outputs, checks, traces, or credentials.",
        "interpretation": (
            DISCLAIMER
            if fixture
            else "Recorded usage is reported evidence; missing accounting remains unknown. No ranking or deployment recommendation is implied."
        ),
        "verification": "The evidence hash binds the private snapshot identifier and trial ledger. Independent verification requires access to that retained evidence.",
    }
    directory = directory.expanduser().absolute()
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    _write_new(directory / "summary.json", json.dumps(summary, indent=2, allow_nan=False) + "\n")
    badge_label = label + (" recorded" if finalized else " incomplete")
    safe = html.escape(badge_label)
    digest = html.escape(summary["evidence_hash"])
    badge = f'<svg xmlns="http://www.w3.org/2000/svg" width="380" height="28" role="img" aria-label="Everval: {safe}"><title>Everval: {safe}. Evidence {digest}. See summary.json for limits.</title><a href="summary.json"><rect width="380" height="28" rx="5" fill="#12251f"/><text x="12" y="19" fill="#c0f2d4" font-family="monospace" font-size="12">EVERVAL · {safe}</text></a></svg>\n'
    _write_new(directory / "badge.svg", badge)
    return {
        "directory": str(directory),
        "summary": str(directory / "summary.json"),
        "badge": str(directory / "badge.svg"),
        "evidence_hash": summary["evidence_hash"],
        "uploaded": False,
        "contains_task_content": False,
    }
