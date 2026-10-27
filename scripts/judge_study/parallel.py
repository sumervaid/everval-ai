"""Operational concurrency amendment: same frozen jobs, prices, caps and analysis.

Four durable reservations per model are allowed only within one live process.
Unknown usage stops new dispatch; already reserved work is awaited and retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cvals.review_queues import Rubric
from scripts.benchmarks.budget import BudgetError
from scripts.benchmarks.model import private_json
from scripts.judge_study import driver
from scripts.judge_study.concurrent_budget import ConcurrentLedger
from scripts.judge_study.responses import judge_response, preserve_policy_stop

AMENDED_SOURCES = [
    "scripts/judge_study/responses.py",
    "scripts/judge_study/parallel.py",
    "scripts/judge_study/concurrent_budget.py",
    "scripts/judge_study/analyze.py",
    "src/cvals/judge_calibration.py",
    "src/cvals/judge_calibration_models.py",
    "src/cvals/judge_statistics.py",
]


def amendment(root: Path, *, create: bool = False) -> dict[str, Any]:
    plan = driver.load_plan(root)
    path = root / "operational-amendment.json"
    if create:
        if path.exists():
            raise ValueError("Operational amendment is already frozen")
        state = driver.snapshot(root, plan)
        if any(
            (root / "requests" / (job["job_id"] + ".request.json")).exists()
            for job in plan["jobs"]
            if job["partition"] == "holdout"
        ):
            raise ValueError("Secondary endpoint must be frozen before any holdout request")
        if state["unknown_requests"]:
            raise BudgetError("Cannot amend while requests are pending or uncertain")
        prefix = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / "requests").glob("*.result.json"))
        }
        value = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "base_plan_sha256": driver.digest(plan),
            "change": "Four calls per model (twelve total), plus a separately labeled pre-holdout secondary endpoint.",
            "unchanged": [
                "job identities",
                "cohorts",
                "prompts",
                "prices",
                "caps",
                "original primary analysis",
                "holdout selection",
            ],
            "max_inflight_per_model": 4,
            "max_total_inflight": 12,
            "secondary_endpoint_amendment": {
                "reason": "Development invalid/abstained judgments prevent strict primary complete-grid inference; original primary retained unchanged.",
                "before_any_holdout": True,
                "endpoint": "Delivered agreement over scheduled attempted judgments: valid categorical vote/reference agreement; observed abstain/invalid/error zero; missing/unattempted block inference.",
                "allocation": "Secondary exploratory contrasts alpha=.05/3, individual configurations alpha=.05/6; not joint primary-plus-secondary familywise control.",
                "no_salvage_or_retries": True,
            },
            "additional_descriptive_analysis": [
                "Observed human-panel vote agreement maximum; not a universal capability ceiling.",
                "Execution repository sensitivity: equal repository means, frozen alpha/resamples/minimum families; no population-of-repositories inference.",
            ],
            "graceful_stop": "Stop new admissions and await already reserved calls.",
            "known_completed_prefix": prefix,
            "cost_before_amendment": state,
            "source_sha256": {
                p: hashlib.sha256((driver.ROOT / p).read_bytes()).hexdigest()
                for p in AMENDED_SOURCES
            },
        }
        private_json(path, value)
        private_json(
            root / "operational-amendment-freeze.json",
            {
                "sha256": driver.digest(value),
                "created_at": value["created_at"],
            },
        )
        return value
    value = driver.read(path)
    if driver.read(root / "operational-amendment-freeze.json")["sha256"] != driver.digest(value):
        raise ValueError("Operational amendment changed")
    if value["base_plan_sha256"] != driver.digest(plan) or value["max_inflight_per_model"] != 4:
        raise ValueError("Operational amendment differs from the approved scope")
    for path_text, expected in value["source_sha256"].items():
        if hashlib.sha256((driver.ROOT / path_text).read_bytes()).hexdigest() != expected:
            raise ValueError("Amended source changed")
    for name, expected in value["known_completed_prefix"].items():
        if hashlib.sha256((root / "requests" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Pre-amendment result prefix changed")
    return value


def run(root: Path, partition: str) -> None:
    if (root / "STOP").exists():
        raise BudgetError("Shared study stop is active")
    plan = driver.load_plan(root)
    amendment(root)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is required")
    service = driver.JudgeCalibration(driver.Settings(root / "workspace"))
    rubrics = {
        cohort: Rubric.model_validate(driver.read(root / "approved" / (cohort + ".json"))["rubric"])
        for cohort in plan["campaigns"]
    }
    if partition == "holdout":
        for identifier in plan["campaigns"].values():
            if not service.get(identifier)["state"]["holdout_released"]:
                raise ValueError("Holdout selection must be frozen before dispatch")
    accounting = {
        model: ConcurrentLedger(
            root / "accounting" / (model + ".sqlite3"),
            driver.configuration(plan, model),
            create=False,
        )
        for model in driver.PROFILES
    }
    if any(ledger.status()["admissions_blocked"] for ledger in accounting.values()):
        raise BudgetError("Unsettled usage blocks further calls")
    output = root / "requests"
    output.mkdir(exist_ok=True, mode=0o700)
    stop = threading.Event()
    dispatch_lock = threading.Lock()
    failures: list[dict[str, Any]] = []
    completed = 0
    original_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: stop.set())

    def execute(job: dict[str, Any]) -> None:
        nonlocal completed
        model = job["body"]["model"]
        ledger = accounting[model]
        request_id = None
        settled = False
        start = time.monotonic()
        try:
            request_hash = driver.digest(job["body"])
            if request_hash != job["request_sha256"]:
                raise ValueError("Frozen request hash changed")
            ceiling = int(driver.PROFILES[model]["output_tokens"])
            with dispatch_lock:
                if stop.is_set() or (root / "STOP").exists():
                    stop.set()
                    return
                ledger.register_attempt(
                    job["job_id"], driver.digest({"job": job["job_id"], "request": request_hash})
                )
                request_id = ledger.reserve(
                    job["job_id"], request_hash, job["input_ceiling"], ceiling
                )
            reserved = ledger.config.cost(job["input_ceiling"], ceiling)
            private_json(
                output / (job["job_id"] + ".request.json"),
                {
                    "request_id": request_id,
                    "request_sha256": request_hash,
                    "body": job["body"],
                    "reserved_upper_bound_usd": str(reserved),
                    "sent_at": datetime.now(UTC).isoformat(),
                },
            )
            with (
                httpx.Client(
                    timeout=httpx.Timeout(180, connect=20), follow_redirects=False, trust_env=False
                ) as client,
                client.stream(
                    "POST",
                    driver.ENDPOINT,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    content=driver.canonical(job["body"]),
                ) as response,
            ):
                raw_bytes = bytearray()
                for chunk in response.iter_bytes():
                    raw_bytes.extend(chunk)
                    if len(raw_bytes) > driver.MAX_RESPONSE_BYTES:
                        raise ValueError("Provider response exceeded evidence bound")
                http_status = response.status_code
            raw = json.loads(raw_bytes.decode().replace(key, "[REDACTED]"))
            handling = judge_response(
                {"http_status": http_status, "response": raw}, rubrics[job["cohort"]]
            )
            if handling.classification in ("provider_policy_rejection", "provider_refusal"):
                stop.set()
                preserve_policy_stop(root, handling, job)
            private_json(
                output / (job["job_id"] + ".response.json"),
                {
                    "http_status": http_status,
                    "response": raw,
                    "response_handling": handling.metadata(),
                },
            )
            usage = raw.get("usage")
            if (
                not isinstance(usage, dict)
                or type(usage.get("input_tokens")) is not int
                or type(usage.get("output_tokens")) is not int
            ):
                raise ValueError("Missing or invalid provider usage")
            if raw.get("model") != model or raw.get("service_tier", "default") not in {
                None,
                "default",
            }:
                raise ValueError("Unpriced returned model or service tier")
            cost = ledger.settle(request_id, usage["input_tokens"], usage["output_tokens"])
            settled = True
            observation = {
                "job_id": job["job_id"],
                "response": handling.text,
                "error": handling.error,
                "cost_usd": float(cost),
                "reservation_usd": float(reserved),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "duration_ms": (time.monotonic() - start) * 1000,
            }
            private_json(
                output / (job["job_id"] + ".result.json"),
                {
                    "observation": observation,
                    "request_id": request_id,
                    "returned_model": raw["model"],
                    "cost_basis": plan["cost_basis"],
                    "provider_usage": usage,
                    "response_handling": handling.metadata(),
                },
            )
            with dispatch_lock:
                completed += 1
                if completed % 50 == 0:
                    print("Completed", completed, "new requests", flush=True)
        except BaseException as error:
            with dispatch_lock:
                stop.set()
                if request_id is not None and not settled:
                    ledger.uncertain(request_id, type(error).__name__)
                failures.append(
                    {"model": model, "job_id": job["job_id"], "error_class": type(error).__name__}
                )
            print(model, "STOPPED", type(error).__name__, flush=True)

    queues = {
        model: iter(
            [
                j
                for j in plan["jobs"]
                if j["partition"] == partition
                and j["body"]["model"] == model
                and not (output / (j["job_id"] + ".result.json")).exists()
            ]
        )
        for model in driver.PROFILES
    }
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            active = {}
            for model, queue in queues.items():
                for _ in range(4):
                    job = next(queue, None)
                    if job is not None:
                        active[pool.submit(execute, job)] = model
            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    model = active.pop(future)
                    future.result()
                    if not stop.is_set():
                        job = next(queues[model], None)
                        if job is not None:
                            active[pool.submit(execute, job)] = model
    finally:
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)
    driver.collect(root, plan, partition)
    state = driver.snapshot(root, plan)
    private_json(root / ("budget-status-" + str(time.time_ns()) + ".json"), state)
    print(json.dumps(state, indent=2), flush=True)
    if failures:
        private_json(root / ("stopped-" + str(time.time_ns()) + ".json"), failures)
        raise RuntimeError("Study stopped; retained accounting requires review")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["amend", "run"])
    parser.add_argument("--phase", choices=["dev", "holdout"], default="holdout")
    args = parser.parse_args()
    if args.command == "amend":
        print(json.dumps(amendment(args.root, create=True), indent=2))
    else:
        run(args.root, args.phase)


if __name__ == "__main__":
    main()
