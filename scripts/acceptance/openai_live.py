"""Opt-in OpenAI compatibility pilot with a cumulative, conservative spending ledger.

Run from an installed Everval environment. Default execution only writes a plan.
Only the built-in original starter fixtures leave the local machine. They are not
a representative customer workload and do not support a general model ranking.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

from cvals.audit import AuditLog
from cvals.budgeting import BudgetPlanner
from cvals.config import Settings
from cvals.demo import builtin_pack, export_summary
from cvals.engine import Engine
from cvals.models import Collection, ExperimentCreate, System, content_hash, utc_now
from cvals.packs import starter_collections
from cvals.providers import payload_for, reserve_cost
from cvals.spend_limits import SpendLimits
from cvals.store import Store

# Standard short-context pricing checked against official model pages 2026-10-15.
# Input uses the published cache-write rate (1.25x uncached input) conservatively.
# This deliberately does not claim invoice reconciliation or cache-discount accuracy.
PRICING = [
    ("gpt-6-luna", 0.125, 0.50),
    ("gpt-6.1-sol", 2.50, 10.00),
    ("gpt-6-astra", 12.50, 50.00),
]
AUTHORIZED_CEILING = 100.0
ADMISSION_CEILING = 90.0
PRICING_VERSION = "openai-standard-short-context-2026-10-15-input-cache-write-upper-bound"


def write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    path.chmod(0o600)


def installed_code_hash() -> str:
    import cvals

    package = Path(cvals.__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("plan", "pilot", "controls", "benchmark"), default="plan"
    )
    parser.add_argument(
        "--sandbox-image",
        help="Already-local approved Python image; resolved to its immutable digest",
    )
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    phase_lock = (root / "acceptance.lock").open("a")
    try:
        fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit("Another live acceptance phase owns this cumulative ledger") from exc
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.phase != "plan" and (root / (args.phase + "-started.json")).exists():
        raise SystemExit("This phase already started; retain evidence, never automatically retry")
    settings = replace(
        Settings.from_env(), data_dir=root / "workspace", allow_remote_providers=True
    )
    if args.sandbox_image:
        settings = replace(settings, sandbox_image=args.sandbox_image)
    if args.phase in {"controls", "benchmark"}:
        from cvals.sandbox import _image

        settings = replace(settings, sandbox_image=_image(settings))
    store = Store(settings)
    store.init()
    limits = SpendLimits(settings)
    if not limits.list():
        limits.configure(lifetime_usd=ADMISSION_CEILING)
    configured = limits.list()
    if (
        len(configured) != 1
        or configured[0]["scope"] != "workspace"
        or configured[0]["lifetime_usd"] != ADMISSION_CEILING
    ):
        raise SystemExit("Unexpected spending policy; refusing to change an existing ledger")
    systems = [
        System(
            id=model,
            name=model,
            kind="http",
            model=model,
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            input_cost_per_million=input_rate,
            output_cost_per_million=output_rate,
            pricing_version=PRICING_VERSION,
            max_tokens=8192,
            timeout_seconds=120,
        )
        for model, input_rate, output_rate in PRICING
    ]
    collections = starter_collections()
    scenarios = [scenario for collection in collections for scenario in collection.scenarios]
    cohort = Collection(
        id="acceptance-openai-starters-v2",
        name="Original starter compatibility acceptance",
        version="2.0.0",
        created_at="2026-10-15T00:00:00+00:00",
        domain="mixed",
        description="Original installation fixtures; not a representative enterprise benchmark.",
        scenarios=scenarios,
    )
    # Freeze the same typed representation that the store persists, including
    # float-valued grader defaults. This keeps plan and experiment hashes equal.
    cohort = Collection.model_validate(cohort.model_dump())
    pilot = builtin_pack("data").collection
    reservations = {
        "pilot": sum(reserve_cost(s, m) for s in pilot.scenarios for m in systems),
        "benchmark": 2 * sum(reserve_cost(s, m) for s in cohort.scenarios for m in systems),
    }
    for scenario in cohort.scenarios + pilot.scenarios:
        for system in systems:
            public = json.loads(payload_for(scenario, system)["messages"][1]["content"])
            if set(public) != {"id", "name", "domain", "instruction", "inputs"}:
                raise SystemExit("Public task envelope unexpectedly contains private fields")
    manifest = {
        "schema": "everval.openai-acceptance.v1",
        "created_at": utc_now(),
        "package_version": importlib.metadata.version("everval-ai"),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "authorized_provider_ceiling_usd": AUTHORIZED_CEILING,
        "cumulative_admission_ceiling_usd": ADMISSION_CEILING,
        "currency": "USD",
        "provider": "OpenAI",
        "models": [system.model_dump() for system in systems],
        "pricing_sources": [
            "https://developers.openai.com/api/docs/models/" + model for model, _, _ in PRICING
        ],
        "cost_basis": "Reported token counts times conservative declared rates; input charged at cache-write upper bound, output at Standard rate. No cache discount. Not an invoice.",
        "cohort_id": cohort.id,
        "cohort_version": cohort.version,
        "starter_versions": {c.id: c.version for c in collections},
        "cohort_hash": content_hash(cohort.model_dump()),
        "task_count": len(cohort.scenarios),
        "domains": {
            domain: sum(s.domain == domain for s in scenarios)
            for domain in sorted({s.domain for s in scenarios})
        },
        "repetitions": 2,
        "scheduled_benchmark_trials": len(scenarios) * len(systems) * 2,
        "reservation_estimates_usd": reservations,
        "scope": "Live compatibility and operational measurement on small, public, original starter fixtures. No general capability ranking or customer-workload claim.",
        "external_development_service_cost_usd": None,
    }
    if sum(reservations.values()) > ADMISSION_CEILING:
        raise SystemExit("Full conservative plan exceeds cumulative admission ceiling")
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        for key in ("models", "cohort_hash", "repetitions", "cumulative_admission_ceiling_usd"):
            if previous[key] != manifest[key]:
                raise SystemExit("Frozen run manifest changed; refusing to resume")
    else:
        write(manifest_path, manifest)
    for system in systems:
        store.save_system(system)
    for collection in (cohort, pilot):
        store.save_collection(collection)
    budget = BudgetPlanner(settings)
    write(root / "budget.json", budget.status())
    if args.phase == "plan":
        print(
            json.dumps(
                {
                    "phase": "plan",
                    "task_count": len(scenarios),
                    "reservations": reservations,
                    "budget": budget.status(),
                }
            ),
            flush=True,
        )
        return
    if args.phase == "controls":
        from cvals.domains import validate_collection

        if (root / "controls-results.json").exists():
            raise SystemExit("Control evidence already exists; retain it rather than overwrite")
        controls_marker = root / "controls-started.json"
        controls_marker.touch(mode=0o600, exist_ok=False)
        write(
            controls_marker,
            {
                "started_at": utc_now(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
        )
        started = time.monotonic()
        controls = validate_collection(cohort, settings)
        write(root / "controls-private.json", controls)
        summary = {
            "status": "passed" if controls["valid"] else "failed",
            "package_version": importlib.metadata.version("everval-ai"),
            "cohort_hash": content_hash(cohort.model_dump()),
            "installed_code_sha256": installed_code_hash(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sandbox_image": settings.sandbox_image,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "task_count": len(controls["scenarios"]),
            "positive_controls_passed": sum(row["valid"] for row in controls["scenarios"]),
            "negative_controls_rejected": sum(
                c.get("rejected", False)
                for row in controls["scenarios"]
                for c in row["negative_controls"]
            ),
            "provider_calls": 0,
            "failures": [
                {"id": row["id"], "errors": row["errors"]}
                for row in controls["scenarios"]
                if not row["valid"]
            ],
        }
        write(root / "controls-results.json", summary)
        print(json.dumps(summary), flush=True)
        if not controls["valid"]:
            raise SystemExit("Control validation failed; live cohort admission blocked")
        return
    if not AuditLog(settings).verify()["valid"]:
        raise SystemExit("Invalid audit chain; paid acceptance admission blocked")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is unset; no provider call made")
    if budget.status()["unsettled_reservations"]:
        raise SystemExit("Unsettled provider usage; all further live calls blocked")
    run_marker = root / (args.phase + "-started.json")
    if run_marker.exists():
        raise SystemExit(
            "This phase was already started. Inspect retained evidence; automatic retries are disabled"
        )
    if args.phase == "benchmark":
        pilot_result = json.loads((root / "pilot-results.json").read_text())
        controls = json.loads((root / "controls-results.json").read_text())
        if (
            controls["status"] != "passed"
            or controls["cohort_hash"] != manifest["cohort_hash"]
            or controls["sandbox_image"] != settings.sandbox_image
            or controls["installed_code_sha256"] != installed_code_hash()
        ):
            raise SystemExit("Validated cohort/runtime does not match this run")
        if (
            pilot_result["status"] != "completed"
            or not pilot_result["all_trials_completed"]
            or not pilot_result.get("audit", {}).get("valid", False)
        ):
            raise SystemExit("Pilot did not complete cleanly; full run blocked")
    chosen = pilot if args.phase == "pilot" else cohort
    repetitions = 1 if args.phase == "pilot" else 2
    run_marker.touch(mode=0o600, exist_ok=False)
    write(
        run_marker,
        {
            "started_at": utc_now(),
            "status": "preparing",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    experiment = store.create_experiment(
        ExperimentCreate(
            name="OpenAI " + args.phase + " | original compatibility fixtures",
            collection_id=chosen.id,
            system_ids=[s.id for s in systems],
            repetitions=repetitions,
            budget_usd=min(ADMISSION_CEILING, reservations[args.phase] + 0.01),
        )
    )
    write(
        run_marker,
        {
            "experiment_id": experiment["id"],
            "started_at": utc_now(),
            "snapshot_hash": experiment["snapshot_hash"],
        },
    )
    stopping = threading.Event()

    def monitor() -> None:
        while not stopping.wait(15):
            status = budget.status()
            write(root / "budget.json", status)
            state = store.get_experiment(experiment["id"])
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "phase": args.phase,
                        "completed": state["completed_trials"],
                        "scheduled": state["total_trials"],
                        "known_cost_usd": status["known_provider_cost_usd"],
                        "committed_usd": status["committed_provider_usd"],
                        "unsettled": status["unsettled_reservations"],
                    }
                ),
                flush=True,
            )

    archive = root / ("source-" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest() + ".py")
    if not archive.exists():
        archive.write_bytes(Path(__file__).read_bytes())
        archive.chmod(0o600)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    start = time.monotonic()
    print(
        json.dumps(
            {
                "event": "started",
                "phase": args.phase,
                "scheduled": experiment["total_trials"],
                "maximum_reserved_for_phase_usd": reservations[args.phase],
            }
        ),
        flush=True,
    )
    try:
        result = Engine(store, settings).run_experiment(experiment["id"])
    finally:
        stopping.set()
        thread.join(timeout=2)
        write(root / "budget.json", budget.status())
    # Detailed private evidence stays in this private workspace.
    write(root / (args.phase + "-private.json"), result)
    summary = {
        "phase": args.phase,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "installed_code_sha256": installed_code_hash(),
        "package_version": importlib.metadata.version("everval-ai"),
        "sandbox_image": settings.sandbox_image,
        "status": result["status"],
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "experiment_id": result["id"],
        "snapshot_hash": result["snapshot_hash"],
        "collection_hash": result["collection_hash"],
        "total_trials": result["total_trials"],
        "all_trials_completed": all(t["status"] == "completed" for t in result["trials"]),
        "systems": result["report"]["systems"],
        "domains": result["report"]["domains"],
        "comparisons": result["report"]["comparisons"],
        "budget": budget.status(),
        "audit": AuditLog(settings).verify(),
        "failures": [
            {
                "system": t["system_id"],
                "scenario": t["scenario_id"],
                "status": t["status"],
                "error": t["error"],
                "passed": t["passed"],
            }
            for t in result["trials"]
            if t.get("passed") is not True
        ],
        "limitations": [
            manifest["scope"],
            manifest["cost_basis"],
            "Request order is fixed; latency is descriptive and not a randomized service-level study.",
            "Published task fixtures may have been seen during model training.",
        ],
    }
    write(root / (args.phase + "-results.json"), summary)
    export_summary(settings, result["id"], root / (args.phase + "-share"))
    print(json.dumps(summary), flush=True)
    if budget.status()["unsettled_reservations"]:
        raise SystemExit("Stopped: provider billing is uncertain; no automatic retry is permitted")
    if (
        result["status"] != "completed"
        or not summary["all_trials_completed"]
        or not summary["audit"]["valid"]
    ):
        raise SystemExit(
            "Acceptance failed: inspect retained protocol, grading, or audit failures. Completed incorrect answers remain valid evidence."
        )


if __name__ == "__main__":
    main()
