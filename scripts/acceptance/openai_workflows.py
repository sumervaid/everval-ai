#!/usr/bin/env python3
"""Optional live OpenAI Autopilot and protected-holdout acceptance.

Uses the SAME workspace and cumulative ledger as openai_live.py. Planning is
free. --execute performs paid calls without automatic retry. Original fixtures
test lifecycle behavior; they do not establish representative model quality.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import time
from dataclasses import replace
from pathlib import Path

from cvals.audit import AuditLog
from cvals.autopilot import Autopilot
from cvals.budgeting import BudgetPlanner
from cvals.config import Settings
from cvals.domains import validate_collection
from cvals.engine import Engine
from cvals.models import (
    Check,
    Collection,
    ExperimentCreate,
    Scenario,
    System,
    content_hash,
    utc_now,
)
from cvals.optimization import Optimizer
from cvals.packs import starter_collections
from cvals.providers import reserve_cost
from cvals.store import Store


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    path.chmod(0o600)


def assert_frozen_systems(store: Store, models: list[System]) -> None:
    with store._connect() as connection:
        for model in models:
            current = System.model_validate(Store._get(connection, "systems", model.id))
            if content_hash(current.model_dump()) != content_hash(
                System.model_validate(model.model_dump()).model_dump()
            ):
                raise ValueError(
                    "Stored system differs from the frozen OpenAI plan; no calls permitted"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    phase_lock = (root / "acceptance.lock").open("a")
    try:
        fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit("Another live acceptance phase owns this cumulative ledger") from exc
    if args.execute and (root / "workflows-started.json").exists():
        raise SystemExit("This stage already started; retain evidence, never automatically retry")
    manifest = json.loads((root / "manifest.json").read_text())
    settings = replace(
        Settings.from_env(), data_dir=root / "workspace", allow_remote_providers=True
    )
    store = Store(settings)
    store.init()
    budget = BudgetPlanner(settings)
    models = [System.model_validate(value) for value in manifest["models"]]
    # Never change frozen prices or use another provider in this acceptance run.
    if any(m.base_url != "https://api.openai.com/v1" or m.kind != "http" for m in models):
        raise SystemExit("Only frozen OpenAI configurations are allowed")
    assert_frozen_systems(store, models)
    statuses = budget.status()
    workspace_limits = [item for item in statuses["limits"] if item["scope"] == "workspace"]
    if len(workspace_limits) != 1 or workspace_limits[0]["lifetime_usd"] != 90:
        raise SystemExit("Expected the existing cumulative $90 admission ceiling")
    selected = [s for c in starter_collections() if c.domain != "coding" for s in c.scenarios[:2]]
    full = Collection(
        id="acceptance-live-operations-v1",
        name="Live lifecycle installation fixtures",
        created_at="2026-10-15T00:00:00+00:00",
        domain="mixed",
        benchmark_partition="dev",
        scenarios=selected,
    )
    definitions = {
        "dev": [
            [("settled", 12000, 1000), ("cancelled", 90000, 0), ("settled", 5000, 500)],
            [("pending", 30000, 0), ("settled", 11000, 11000), ("settled", 2500, 0)],
            [("settled", 9001, 1), ("cancelled", 10, 10), ("settled", 101, 51)],
            [("settled", 1050, 50), ("settled", 2050, 150), ("pending", 1900, 0)],
        ],
        "holdout": [
            [("settled", 17003, 3), ("pending", 80000, 0), ("settled", 257, 57)],
            [("cancelled", 70000, 0), ("settled", 500, 500), ("settled", 9999, 9)],
        ],
    }
    collections = {}
    for partition, cases in definitions.items():
        scenarios = []
        for index, rows in enumerate(cases):
            expected = {
                "currency": "EUR",
                "settled_count": sum(row[0] == "settled" for row in rows),
                "net_cents": sum(
                    gross - refund for status, gross, refund in rows if status == "settled"
                ),
            }
            scenarios.append(
                Scenario(
                    id=f"acceptance-selection-{partition}-{index}",
                    name="Settlement contract",
                    domain="data",
                    instruction="Include only settled payments. Subtract each included refund_cents from gross_cents. Return JSON currency, settled_count and net_cents. Keep integer cents; a fully refunded settled payment still counts.",
                    inputs={
                        "currency": "EUR",
                        "payments": [
                            {"status": s, "gross_cents": g, "refund_cents": r} for s, g, r in rows
                        ],
                    },
                    checks=[
                        Check(id=key, name=key, kind="equals", path=key, expected=value)
                        for key, value in expected.items()
                    ],
                    reference_output=expected,
                    tags=["original-fixture", "lifecycle-acceptance"],
                    provenance={
                        "origin": "original_fixture",
                        "license": "Apache-2.0",
                        "purpose": "Lifecycle acceptance, not representative capability",
                        "negative_controls": [
                            {},
                            {**expected, "net_cents": expected["net_cents"] + 1},
                        ],
                    },
                )
            )
        collection = Collection(
            id="acceptance-selection-" + partition,
            name="Selection " + partition,
            created_at="2026-10-15T00:00:00+00:00",
            domain="data",
            benchmark_partition=partition,
            scenarios=scenarios,
        )
        collections[partition] = collection

    plan_path = root / "workflows-plan.json"
    collection_hashes = {c.id: content_hash(c.model_dump()) for c in [full, *collections.values()]}
    models_hash = content_hash(manifest["models"])
    if plan_path.exists():
        prior = json.loads(plan_path.read_text())
        if "models_hash" not in prior:
            raise SystemExit(
                "Frozen workflow plan predates model binding; retain it and plan a new campaign"
            )
        if prior["models_hash"] != models_hash:
            raise SystemExit("Frozen workflow plan changed: model configurations differ")
        if prior.get("collection_hashes") != collection_hashes:
            raise SystemExit("Frozen workflow task definitions changed")
    for collection in [full, *collections.values()]:
        store.save_collection(collection)

    def reserve(tasks):
        return math.fsum(reserve_cost(task, m) for task in tasks for m in models)

    # Full+screen baselines, then one screen+full occurrence; screen has at most2tasks.
    full_ceiling = reserve(full.scenarios)
    max_screen = sum(sorted([reserve([task]) for task in full.scenarios], reverse=True)[:2])
    optimizer = Optimizer(settings)
    projection = optimizer.project(
        collections["dev"].id,
        collections["holdout"].id,
        [m.id for m in models],
        budget_usd=5,
        repetitions=1,
    )
    plan = {
        "schema": "everval.openai-workflows.v1",
        "created_at": utc_now(),
        "package_version": importlib.metadata.version("everval-ai"),
        "full_task_count": len(full.scenarios),
        "screen_task_count": 2,
        "autopilot_maximum_calls": (len(full.scenarios) + 2) * len(models) * 2,
        "autopilot_conservative_reservation_usd": 2 * (full_ceiling + max_screen),
        "optimizer_maximum_calls": len(collections["dev"].scenarios) * len(models)
        + len(collections["holdout"].scenarios),
        "optimizer_budget_usd": 5,
        "optimizer_projection": projection,
        "cumulative_admission_ceiling_usd": 90,
        "collection_hashes": collection_hashes,
        "models_hash": models_hash,
        "scope": "Small original-fixture lifecycle verification using real OpenAI responses; not a representative quality comparison.",
    }
    plan_path = root / "workflows-plan.json"
    if plan_path.exists():
        prior = json.loads(plan_path.read_text())
        frozen_keys = (
            "collection_hashes",
            "models_hash",
            "autopilot_maximum_calls",
            "autopilot_conservative_reservation_usd",
            "optimizer_maximum_calls",
            "optimizer_budget_usd",
            "cumulative_admission_ceiling_usd",
        )
        if any(key not in prior or prior[key] != plan[key] for key in frozen_keys):
            raise SystemExit("Frozen workflow plan changed")
        plan = prior
    else:
        write(plan_path, plan)
    print(
        json.dumps(
            {
                "event": "plan",
                "autopilot_maximum_calls": plan["autopilot_maximum_calls"],
                "optimizer_maximum_calls": plan["optimizer_maximum_calls"],
                "conservative_additional_budget_usd": plan["autopilot_conservative_reservation_usd"]
                + 5,
            }
        ),
        flush=True,
    )
    if not args.execute:
        return
    if (root / "workflows-started.json").exists():
        raise SystemExit("This stage already started; retain evidence, never automatically retry")
    if not AuditLog(settings).verify()["valid"]:
        raise SystemExit("Invalid audit chain; paid acceptance admission blocked")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is unset")

    def settled() -> dict:
        value = budget.status()
        write(root / "budget.json", value)
        if value["unsettled_reservations"]:
            raise RuntimeError("Unknown billing; further live calls halted")
        return value

    settled()
    (root / "workflows-started.json").touch(mode=0o600, exist_ok=False)
    archive = root / ("source-" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest() + ".py")
    if not archive.exists():
        archive.write_bytes(Path(__file__).read_bytes())
        archive.chmod(0o600)
    write(
        root / "workflows-started.json", {"started_at": utc_now(), "plan_hash": content_hash(plan)}
    )
    checks = []

    def check(name: str, condition: bool, **evidence: object) -> None:
        checks.append(
            {"name": name, "status": "passed" if condition else "failed", "evidence": evidence}
        )
        write(root / "workflows-progress.json", {"checks": checks, "budget": budget.status()})
        if not condition:
            raise AssertionError(name)

    policy_id = None
    try:
        for collection in [full, *collections.values()]:
            controls = validate_collection(collection, settings)
            write(root / (collection.id + "-controls-private.json"), controls)
            check("positive and negative controls: " + collection.id, controls["valid"])
        engine = Engine(store, settings)

        def run(identifier: str) -> dict:
            assert_frozen_systems(store, models)
            snapshot = store.get_experiment(identifier)["snapshot"]
            permitted = {model.id: content_hash(model.model_dump()) for model in models}
            for current in snapshot["systems"]:
                if permitted.get(current["id"]) != content_hash(
                    System.model_validate(current).model_dump()
                ):
                    raise RuntimeError(
                        "Experiment configuration differs from the frozen OpenAI plan"
                    )
            settled()
            if not AuditLog(settings).verify()["valid"]:
                raise RuntimeError("Invalid audit chain; further live calls blocked")
            value = engine.run_experiment(identifier)
            status = settled()
            print(
                json.dumps(
                    {
                        "event": "experiment_completed",
                        "trials": value["total_trials"],
                        "status": value["status"],
                        "cumulative_cost_usd": status["known_provider_cost_usd"],
                    }
                ),
                flush=True,
            )
            check(
                "all attempts retained and metered: " + identifier,
                all(
                    t["cost_usd"] is not None and t["status"] == "completed"
                    for t in value["trials"]
                ),
                scheduled=value["total_trials"],
                passed=sum(t["passed"] is True for t in value["trials"]),
            )
            return value

        baseline = store.create_experiment(
            ExperimentCreate(
                name="Live lifecycle baseline",
                collection_id=full.id,
                system_ids=[m.id for m in models],
                budget_usd=full_ceiling + 0.01,
            )
        )
        baseline = run(baseline["id"])
        autopilot = Autopilot(settings, store)
        screen = autopilot.screen(baseline["id"], size=2)
        screen = run(screen["id"])
        policy = autopilot.create(
            "Live release acceptance",
            baseline["id"],
            screen["id"],
            lifetime_budget_usd=baseline["budget_usd"] + screen["budget_usd"],
            trigger="change",
            escalate="always",
            enabled=True,
            config={"revision": "before"},
        )
        policy_id = policy["id"]
        autopilot.configure(policy["id"], config={"revision": "after"})
        deadline = time.monotonic() + 240
        completed_full = None
        try:
            while time.monotonic() < deadline:
                for identifier in autopilot.tick():
                    run(identifier)
                history = autopilot.history(policy["id"])
                if history and history[0].get("full_experiment_id"):
                    completed_full = store.get_experiment(history[0]["full_experiment_id"])
                    if completed_full["status"] in {"completed", "failed", "cancelled"}:
                        break
                time.sleep(2)
        finally:
            autopilot.configure(policy["id"], enabled=False)
        autopilot.tick()
        history = autopilot.history(policy["id"])
        write(
            root / "workflows-autopilot-private.json",
            {"policy": autopilot.get(policy["id"]), "history": history},
        )
        check(
            "one source observation admits screening then full",
            len(history) == 1 and completed_full is not None,
            occurrences=len(history),
            policy_paused=not autopilot.get(policy["id"])["enabled"],
        )
        check("autopilot charges settled", autopilot.budget(policy["id"])["uncertain"] is False)
        study = optimizer.create(
            "Live explicit candidates",
            collections["dev"].id,
            collections["holdout"].id,
            [m.id for m in models],
            budget_usd=5,
            min_pass_rate=1,
            max_p95_ms=120000,
        )
        refused = False
        try:
            store.create_experiment(
                ExperimentCreate(
                    name="Forbidden holdout reuse",
                    collection_id=collections["holdout"].id,
                    system_ids=[models[0].id],
                    budget_usd=1,
                )
            )
        except ValueError as exc:
            refused = "reserved" in str(exc)
        check("reserved holdout rejects unrelated admission", refused)
        run(study["development_experiment_id"])
        study = optimizer.advance(study["id"])
        check(
            "development selects one eligible candidate",
            study["status"] == "holdout_queued",
            selected_system_id=study.get("selected_system_id"),
        )
        run(study["holdout_experiment_id"])
        study = optimizer.advance(study["id"])
        check(
            "one holdout produces an undeployed review proposal",
            study["status"] == "ready_for_review"
            and study["proposal"]["deployment_performed"] is False,
            selected_system_id=study.get("selected_system_id"),
        )
        count = len(store.list_experiments())
        optimizer.advance(study["id"])
        check("final holdout advance is idempotent", len(store.list_experiments()) == count)
        write(root / "workflows-optimizer-private.json", study)
        audit = AuditLog(settings).verify()
        check("audit chain verifies", audit["valid"])
        final = {
            "schema": "everval.openai-workflows-results.v1",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "status": "passed",
            "checks": checks,
            "package_version": importlib.metadata.version("everval-ai"),
            "budget": settled(),
            "audit": audit,
            "autopilot_occurrences": len(history),
            "selected_model": study["selected_system_id"],
            "deployment_performed": False,
            "scope": plan["scope"],
        }
        write(root / "workflows-results.json", final)
        print(json.dumps(final), flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        write(
            root / "workflows-results.json",
            {
                "schema": "everval.openai-workflows-results.v1",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "checks": checks,
                "budget": budget.status(),
                "scope": plan["scope"],
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
        )
        raise
    finally:
        if policy_id is not None:
            cleanup = Autopilot(settings, store)
            if cleanup.get(policy_id)["enabled"]:
                cleanup.configure(policy_id, enabled=False)
        write(root / "budget.json", budget.status())


if __name__ == "__main__":
    main()
