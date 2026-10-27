"""Explicitly bounded continuation after a provider policy rejection.

Retains the unknown charge at its full reservation and quarantines the entire
source item. Only frozen, untouched, unrelated jobs can be dispatched.
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
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from scripts.benchmarks.budget import BudgetError, RecoveryContext
from scripts.benchmarks.model import private_json
from scripts.judge_study import driver, parallel
from scripts.judge_study.recovery_budget import ReviewedConcurrentLedger

REJECTED_JOB = "021c503a876520cb9be48124d330aa3ef56ff04bf6610b8a524b59acd7337473"
REJECTED_REQUEST = "cc1180d53b70412b8fc472f0cceef52b"
POLICY_ITEM = "mtbench-160"
RESERVATION = Decimal("0.0036065")
RECOVERY_SOURCES = [
    "scripts/judge_study/recovery.py",
    "scripts/judge_study/recovery_budget.py",
    "scripts/judge_study/analyze_recovered.py",
]


def context(plan: dict[str, Any], continuation: dict[str, Any], model: str) -> RecoveryContext:
    pairs = tuple(sorted(tuple(pair) for pair in continuation["allowed_attempts"][model]))
    return RecoveryContext(
        plan_sha256=driver.digest(plan),
        amendment_sha256=driver.digest(continuation),
        allowed_attempts=pairs,
    )


def accounting(
    root: Path, plan: dict[str, Any], continuation: dict[str, Any]
) -> dict[str, ReviewedConcurrentLedger]:
    return {
        model: ReviewedConcurrentLedger(
            root / "accounting" / (model + ".sqlite3"),
            driver.configuration(plan, model),
            create=False,
            recovery_context=context(plan, continuation, model),
            approved_request=continuation["approved_request"]
            if model == continuation["model"]
            else None,
        )
        for model in driver.PROFILES
    }


def load(root: Path) -> dict[str, Any]:
    plan = driver.load_plan(root)
    parallel.amendment(root)
    value = driver.read(root / "policy-continuation.json")
    frozen = driver.read(root / "policy-continuation-freeze.json")
    if driver.digest(value) != frozen["sha256"] or value["base_plan_sha256"] != driver.digest(plan):
        raise ValueError("Policy continuation changed")
    if value["policy_item"] != POLICY_ITEM or value["approved_request"]["id"] != REJECTED_REQUEST:
        raise ValueError("Continuation is outside the reviewed policy event")
    for name, expected in value["source_sha256"].items():
        if hashlib.sha256((driver.ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Policy continuation source changed")
    for name, expected in value["evidence_sha256"].items():
        if hashlib.sha256((root / "requests" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Policy evidence or retained completed result changed")
    result = driver.read(root / "requests" / (REJECTED_JOB + ".result.json"))
    if driver.digest(result) != value["derived_policy_observation_sha256"]:
        raise ValueError("Derived policy-failure observation changed")
    indexed = {j["job_id"]: j for j in plan["jobs"]}
    allowed = set(value["allowed_jobs"])
    if len(allowed) != len(value["allowed_jobs"]):
        raise ValueError("Duplicate continuation job")
    for model, pairs in value["allowed_attempts"].items():
        for job_id, attempt_hash in pairs:
            job = indexed[job_id]
            if (
                job_id not in allowed
                or job["item_id"] == POLICY_ITEM
                or job["body"]["model"] != model
            ):
                raise ValueError("Continuation allowlist crosses quarantined item or model")
            if attempt_hash != driver.digest({"job": job_id, "request": job["request_sha256"]}):
                raise ValueError("Continuation attempt hash changed")
    if set(job for pairs in value["allowed_attempts"].values() for job, _ in pairs) != allowed:
        raise ValueError("Continuation allowlists differ")
    return value


def freeze(root: Path) -> dict[str, Any]:
    plan = driver.load_plan(root)
    parallel.amendment(root)
    path = root / "policy-continuation.json"
    if path.exists():
        raise ValueError("Policy continuation is already frozen")
    ledgers = driver.ledgers(root, plan)
    states = {model: ledger.status() for model, ledger in ledgers.items()}
    if any(s["pending_requests"] for s in states.values()):
        raise BudgetError("Wait for every in-flight request before continuation")
    uncertain = [
        (model, row)
        for model, state in states.items()
        for row in state["requests"]
        if row["state"] == "uncertain"
    ]
    if len(uncertain) != 1:
        raise BudgetError("Exactly the reviewed policy uncertainty must be present")
    model, row = uncertain[0]
    if (
        row["id"] != REJECTED_REQUEST
        or row["attempt_id"] != REJECTED_JOB
        or Decimal(row["reserved_usd"]) != RESERVATION
    ):
        raise BudgetError("Policy request identity or bounded charge differs")
    job = next(j for j in plan["jobs"] if j["job_id"] == REJECTED_JOB)
    if job["item_id"] != POLICY_ITEM or job["body"]["model"] != model:
        raise ValueError("Policy source item/model differs")
    dispatched = driver.read(root / "requests" / (REJECTED_JOB + ".request.json"))
    if (
        row["request_hash"] != job["request_sha256"]
        or row["input_ceiling"] != job["input_ceiling"]
        or row["output_ceiling"] != driver.PROFILES[model]["output_tokens"]
        or dispatched.get("request_id") != row["id"]
        or dispatched.get("request_sha256") != job["request_sha256"]
        or driver.digest(dispatched.get("body")) != job["request_sha256"]
        or Decimal(str(dispatched.get("reserved_upper_bound_usd", "-1"))) != RESERVATION
    ):
        raise ValueError("Retained dispatched request differs from frozen job and reservation")
    response_path = root / "requests" / (REJECTED_JOB + ".response.json")
    response = driver.read(response_path)
    error = response.get("response", {}).get("error", {})
    if (
        response.get("http_status") != 400
        or error.get("code") != "invalid_prompt"
        or error.get("type") != "invalid_request_error"
        or response["response"].get("usage") is not None
    ):
        raise ValueError("Evidence is not the approved policy refusal without usage")
    admitted = {r["attempt_id"] for s in states.values() for r in s["requests"]}
    untouched = [
        j for j in plan["jobs"] if j["partition"] == "holdout" and j["job_id"] not in admitted
    ]
    quarantined = [j["job_id"] for j in untouched if j["item_id"] == POLICY_ITEM]
    allowed = [j for j in untouched if j["item_id"] != POLICY_ITEM]
    with ledgers[model]._connection() as db:
        events = [dict(event) for event in db.execute("SELECT * FROM events ORDER BY sequence")]
    approved = ledgers[model]._review_snapshot(row)
    result = {
        "observation": {
            "job_id": REJECTED_JOB,
            "response": None,
            "error": "Provider policy rejection; usage unknown; full reservation retained. Never retried.",
            "cost_usd": None,
            "reservation_usd": float(RESERVATION),
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
        },
        "request_id": REJECTED_REQUEST,
        "returned_model": None,
        "cost_basis": "Unknown billing retained at full frozen reservation; no token usage or zero charge inferred.",
        "derived_from_retained_policy_response": True,
    }
    evidence_names = [p.name for p in sorted((root / "requests").glob("*.result.json"))]
    evidence_names.extend([REJECTED_JOB + ".response.json", REJECTED_JOB + ".request.json"])
    value = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "base_plan_sha256": driver.digest(plan),
        "prior_amendment_sha256": driver.read(root / "operational-amendment-freeze.json")["sha256"],
        "decision": "Retain entire unknown reservation; permanently quarantine rejected attempt and all untouched jobs for its source item; continue only frozen untouched unrelated jobs.",
        "billing_complete": False,
        "model": model,
        "policy_item": POLICY_ITEM,
        "approved_request": approved,
        "event_prefix": {"count": len(events), "sha256": ledgers[model]._events_digest(events)},
        "allowed_jobs": [j["job_id"] for j in allowed],
        "allowed_attempts": {
            m: sorted(
                [
                    [
                        j["job_id"],
                        driver.digest({"job": j["job_id"], "request": j["request_sha256"]}),
                    ]
                    for j in allowed
                    if j["body"]["model"] == m
                ]
            )
            for m in driver.PROFILES
        },
        "quarantined_unattempted_jobs": quarantined,
        "retained_request_count": sum(len(s["requests"]) for s in states.values()),
        "cost_before_continuation": driver.snapshot(root, plan),
        "unchanged": [
            "prompts",
            "model configurations",
            "prices",
            "caps",
            "original primary analysis",
            "pre-holdout secondary analysis",
        ],
        "analysis_deviation": "Original human grid stays incomplete. Any exclusion of the policy-quarantined question is an explicitly post hoc sensitivity, never a replacement confirmatory result. Execution cohort unchanged.",
        "source_sha256": {
            name: hashlib.sha256((driver.ROOT / name).read_bytes()).hexdigest()
            for name in RECOVERY_SOURCES
        },
        "evidence_sha256": {
            name: hashlib.sha256((root / "requests" / name).read_bytes()).hexdigest()
            for name in evidence_names
        },
        "derived_policy_observation_sha256": driver.digest(result),
    }
    private_json(path, value)
    private_json(
        root / "policy-continuation-freeze.json",
        {"sha256": driver.digest(value), "created_at": value["created_at"]},
    )
    private_json(root / "requests" / (REJECTED_JOB + ".result.json"), result)
    recovered = accounting(root, plan, value)[model]
    recovered.record_bounded_review(
        REJECTED_REQUEST,
        request_hash=row["request_hash"],
        reserved_usd=RESERVATION,
        decision_sha256=driver.digest(value),
        evidence_sha256=hashlib.sha256(response_path.read_bytes()).hexdigest(),
        event_prefix_count=len(events),
        event_prefix_sha256=ledgers[model]._events_digest(events),
    )
    load(root)
    return value


def run(root: Path, partition: str) -> None:
    plan = driver.load_plan(root)
    continuation = load(root)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is required")
    service = driver.JudgeCalibration(driver.Settings(root / "workspace"))
    if partition == "holdout":
        for identifier in plan["campaigns"].values():
            if not service.get(identifier)["state"]["holdout_released"]:
                raise ValueError("Holdout selection must be frozen before dispatch")
    budgets = accounting(root, plan, continuation)
    if any(ledger.status()["admissions_blocked"] for ledger in budgets.values()):
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
        ledger = budgets[model]
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
            private_json(
                output / (job["job_id"] + ".response.json"),
                {"http_status": http_status, "response": raw},
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
            texts = [
                c["text"]
                for message in raw.get("output", [])
                if message.get("type") == "message"
                for c in message.get("content", [])
                if c.get("type") == "output_text" and isinstance(c.get("text"), str)
            ]
            error = (
                None
                if http_status == 200 and raw.get("status") == "completed" and texts
                else "Provider response incomplete or unavailable"
            )
            observation = {
                "job_id": job["job_id"],
                "response": "\n".join(texts) if texts and error is None else None,
                "error": error,
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
                and j["job_id"] in continuation["allowed_jobs"]
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
    state["bounded_reviewed_unknown_requests"] = 1
    state["billing_complete"] = False
    state["policy_continuation_sha256"] = driver.digest(continuation)
    private_json(root / ("budget-status-" + str(time.time_ns()) + ".json"), state)
    print(json.dumps(state, indent=2), flush=True)
    if failures:
        private_json(root / ("stopped-" + str(time.time_ns()) + ".json"), failures)
        raise RuntimeError("Study stopped; retained accounting requires review")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["freeze", "run"])
    args = parser.parse_args()
    if args.command == "freeze":
        value = freeze(args.root)
        print(
            json.dumps(
                {
                    "sha256": driver.digest(value),
                    "created_at": value["created_at"],
                    "allowed_jobs": len(value["allowed_jobs"]),
                    "quarantined_unattempted_jobs": len(value["quarantined_unattempted_jobs"]),
                }
            )
        )
    else:
        run(args.root, "holdout")


if __name__ == "__main__":
    main()
