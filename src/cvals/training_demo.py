"""Original provider-free controls for the complete checkpoint workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, Scenario, System, content_hash, new_id
from cvals.posttraining import PostTraining
from cvals.research import ResearchLab
from cvals.store import Store
from cvals.training_ingestion import export_checkpoint, ingest_directory

DISCLAIMER = (
    "Synthetic installation controls only: no model training, model inference, "
    "human calibration study, or real checkpoint performance is represented."
)


def _write(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def _mix_control(service: PostTraining, run_id: str) -> dict[str, Any]:
    """Invented measurements exercise held-out selection, including a missed regression."""
    evaluations = [
        ("data-fast", "data", 0.01),
        ("data-wide", "data", 0.03),
        ("documents-fast", "documents", 0.01),
        ("documents-wide", "documents", 0.03),
    ]
    development = [
        {
            "checkpoint_id": f"development-{step}",
            "task_family": f"development-family-{family}",
            "eval_id": evaluation,
            "domain": domain,
            "delta": -0.3 if step == 2 else 0.0,
            "cost_usd": cost,
        }
        for step in range(3)
        for family in range(2)
        for evaluation, domain, cost in evaluations
    ]
    manifest = [
        {
            "checkpoint_id": f"validation-{step}",
            "task_family": f"validation-family-{family}",
            "eval_id": evaluation,
            "domain": domain,
        }
        for step in range(2)
        for family in range(2)
        for evaluation, domain, _ in evaluations
    ]
    selection = service.select_mix(
        run_id,
        {
            "name": "Synthetic mix controls: invented costs and outcomes, not model evidence",
            "development": development,
            "validation_manifest": manifest,
            "config": {
                "budget_usd": 0.05,
                "protected_domains": ["data", "documents"],
                "seed": 17,
                "practical_threshold": 0.05,
            },
        },
    )
    costs = {evaluation: cost for evaluation, _, cost in evaluations}
    validation = [
        {
            **row,
            "cost_usd": costs[row["eval_id"]],
            "delta": -0.8
            if row["checkpoint_id"] == "validation-1" and row["eval_id"] == "data-wide"
            else 0.0,
        }
        for row in manifest
    ]
    report = service.validate_mix(run_id, selection["id"], {"validation": validation})
    return {
        "selection_id": selection["id"],
        "validation_id": report["id"],
        "selected": report["result"]["strategies"]["selected"],
        "disclaimer": "All mix observations and costs are invented algorithm controls; no study was run.",
    }


def run_training_demo(base: Settings, *, workspace: Path | None = None) -> dict[str, Any]:
    path = (
        workspace.expanduser().absolute()
        if workspace
        else base.data_dir / "training-demos" / new_id()
    )
    if path.resolve() == base.data_dir.resolve():
        raise ValueError("Choose a separate demo directory, not the active workspace")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Training demo requires a new or empty workspace")
    settings = Settings(path, allow_remote_providers=False)
    store = Store(settings)
    store.init()
    domains = []
    for domain in ("data", "documents"):
        scenarios = [
            Scenario(
                id=f"control-{domain}-{i}",
                name=f"Original {domain} control {i}",
                domain=domain,
                instruction=f"Return the integer {i} in the value field.",
                reference_output={"value": i},
                checks=[
                    Check(
                        id="value", name="Declared integer", kind="equals", path="value", expected=i
                    )
                ],
                provenance={"origin": "original-installation-control", "synthetic": True},
            )
            for i in range(24)
        ]
        collection = Collection(
            id=f"training-control-{domain}",
            name=f"{domain.capitalize()} installation controls",
            domain=domain,
            benchmark_partition="dev",
            description=DISCLAIMER,
            scenarios=scenarios,
        )
        store.save_collection(collection)
        domains.append(
            {
                "name": domain,
                "collection_id": collection.id,
                "task_families": {s.id: s.id for s in scenarios},
                "practical_threshold": 0.1,
            }
        )
    reference = System(id="training-control-reference", name="Reference control", kind="reference")
    incomplete = System(
        id="training-control-incomplete", name="Empty submission control", kind="incomplete"
    )
    store.save_system(reference)
    store.save_system(incomplete)
    service = PostTraining(settings, store)
    protocol = {
        "name": "Checkpoint monitoring · synthetic walkthrough",
        "training_run_id": "original-training-control",
        "provenance_kind": "synthetic_control",
        "provenance": DISCLAIMER,
        "training_config_sha256": content_hash({"purpose": "installation-control"}),
        "protocol": {
            "domains": domains,
            "baseline_step": 0,
            "comparison_steps": [10, 20],
            "max_looks": 2,
            "alpha": 0.05,
            "min_clusters": 5,
            "repetitions": 1,
            "budget_per_domain_usd": 0,
            "lifetime_budget_usd": 0,
        },
    }
    run = service.create_run(protocol)
    _write(path / "artifacts" / "training-run.json", protocol)
    manifests = path / "artifacts" / "checkpoint-manifests"
    artifacts = path / "artifacts" / "synthetic-checkpoints"
    manifests.mkdir(mode=0o700)
    artifacts.mkdir(mode=0o700)
    # Deliberately export out of order; ingestion sorts training steps.
    for step, system in ((20, reference), (0, reference), (10, incomplete)):
        weights = artifacts / f"step-{step}.txt"
        weights.write_text(f"Synthetic metadata marker {step}; not model weights.\n")
        export_checkpoint(
            manifests / f"step-{step}.json",
            weights=weights,
            step=step,
            system_id=system.id,
            expected_system_hash=content_hash(system.model_dump(mode="json")),
            revision=f"synthetic-{step}",
            provenance=DISCLAIMER,
        )
    ingested = ingest_directory(service, run["id"], manifests, queue=True)
    engine = Engine(store, settings)
    for item in ingested:
        for link in item["queue"]["experiments"]:
            engine.run_experiment(link["experiment_id"])
    checkpoints = {item["checkpoint"]["step"]: item["checkpoint"] for item in ingested}
    comparisons = [service.analyze(run["id"], checkpoints[step]["id"]) for step in (10, 20)]
    dashboard = service.dashboard(run["id"])
    candidate_metric = dashboard["checkpoints"][1]["metrics"][0]
    experiment = store.get_experiment(candidate_metric["experiment_id"])
    regrade = ResearchLab(settings).repeat_regrade(
        experiment["id"], experiment["trials"][0]["id"], repetitions=3
    )
    investigation = service.investigate(
        run["id"],
        {
            "comparison_id": comparisons[0]["id"],
            "observations": "The empty-submission control failed the same frozen checks passed by the reference control.",
            "hypotheses": ["The deliberately empty control omits the required value."],
            "recommendation": "Installation control detected as intended. Make no claim about trained model quality.",
            "next_checks": [
                "Repeat the fixed-output grade; inspect coverage and frozen execution identities."
            ],
            "regrade_record_ids": [regrade["id"]],
        },
    )
    qualification = service.qualify(
        run["id"],
        {
            "domain": "data",
            "evidence": {
                "measurement_claim": "Exercise checkpoint ingestion and evaluation plumbing on invented integer outputs.",
                "owner": "Installation control",
                "population": "Original synthetic fixtures only",
                "limitations": DISCLAIMER,
                "task_version": "1.0.0",
                "grader_version": "deterministic/1",
                "semantic_rubric": "A passing output includes the declared integer under value.",
                "adjudication_limitations": "No humans participated; production calibration is absent.",
                "controls": [
                    {
                        "id": "positive",
                        "kind": "positive",
                        "expected_pass": True,
                        "observed_pass": True,
                    },
                    {
                        "id": "negative",
                        "kind": "negative",
                        "expected_pass": False,
                        "observed_pass": False,
                    },
                    {
                        "id": "mutation",
                        "kind": "mutation",
                        "expected_pass": False,
                        "observed_pass": False,
                    },
                ],
                "repeated_grades": [
                    {"item_id": experiment["trials"][0]["id"], "grades": [False, False, False]}
                ],
                "human_judgments": [],
                "saturation_scores": [1.0] * 24,
            },
        },
    )
    mix = _mix_control(service, run["id"])
    audit = AuditLog(settings).verify()
    result = {
        "schema_version": 1,
        "disclaimer": DISCLAIMER,
        "workspace": str(path),
        "run_id": run["id"],
        "checkpoint_steps": [0, 10, 20],
        "experiments": sum(len(item["queue"]["experiments"]) for item in ingested),
        "scheduled_attempts": sum(
            store.get_experiment(link["experiment_id"])["total_trials"]
            for item in ingested
            for link in item["queue"]["experiments"]
        ),
        "comparisons": comparisons,
        "investigation_id": investigation["id"],
        "qualification": qualification["result"],
        "mix_control": mix,
        "regrade_id": regrade["id"],
        "audit": audit,
        "model_calls": 0,
        "provider_spend_usd": 0,
    }
    _write(path / "artifacts" / "training-dashboard.json", service.dashboard(run["id"]))
    _write(path / "artifacts" / "training-summary.json", result)
    result["summary_path"] = str(path / "artifacts" / "training-summary.json")
    return result
