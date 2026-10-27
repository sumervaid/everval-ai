"""Second exact financial review after a retained connection failure.

Preserves the first policy-review authority, narrows its untouched-job scope,
and retains both unknown reservations without retrying either started attempt.
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

from scripts.benchmarks.budget import BudgetError
from scripts.benchmarks.model import private_json
from scripts.judge_study import driver, recovery
from scripts.judge_study.connection_budget import ConnectionReviewedLedger

FAILED_JOB = "0b69ccae3b87b4fab964238d23defc7bdb9b5e251121318a1f5e20f9631aec8c"
FAILED_REQUEST = "c46a5fd2f7234fdab85fe3f96504d675"
CONNECTION_RESERVATION = Decimal("0.0051635")
SOURCES = [
    "scripts/judge_study/connection_recovery.py",
    "scripts/judge_study/connection_budget.py",
    "scripts/judge_study/analyze_connections.py",
]


def accounting(
    root: Path, plan: dict[str, Any], continuation: dict[str, Any]
) -> dict[str, ConnectionReviewedLedger]:
    prior = recovery.load(root)
    return {
        model: ConnectionReviewedLedger(
            root / "accounting" / (model + ".sqlite3"),
            driver.configuration(plan, model),
            create=False,
            recovery_context=recovery.context(plan, prior, model),
            approved_request=prior["approved_request"] if model == prior["model"] else None,
            connection_request=continuation["approved_request"]
            if model == continuation["model"]
            else None,
        )
        for model in driver.PROFILES
    }


def load(root: Path) -> dict[str, Any]:
    plan = driver.load_plan(root)
    prior = recovery.load(root)
    value = driver.read(root / "connection-continuation.json")
    frozen = driver.read(root / "connection-continuation-freeze.json")
    if driver.digest(value) != frozen["sha256"] or value["base_plan_sha256"] != driver.digest(plan):
        raise ValueError("Connection continuation changed")
    if (
        value["prior_policy_sha256"] != driver.digest(prior)
        or value["approved_request"]["id"] != FAILED_REQUEST
    ):
        raise ValueError("Connection review differs from its retained authority")
    if value["quarantined_unattempted_jobs"] != prior["quarantined_unattempted_jobs"]:
        raise ValueError("Original policy quarantine changed")
    if (
        not set(value["allowed_jobs"]).issubset(set(prior["allowed_jobs"]))
        or FAILED_JOB in value["allowed_jobs"]
    ):
        raise ValueError(
            "Connection continuation broadened the prior authority or retried a failure"
        )
    for name, expected in value["source_sha256"].items():
        if hashlib.sha256((driver.ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Connection continuation source changed")
    for name, expected in value["evidence_sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Retained connection evidence changed")
    if (root / "requests" / (FAILED_JOB + ".response.json")).exists():
        raise ValueError(
            "Connection failure had no provider response; later replacement is forbidden"
        )
    if (
        driver.digest(driver.read(root / "requests" / (FAILED_JOB + ".result.json")))
        != value["derived_observation_sha256"]
    ):
        raise ValueError("Derived connection failure record changed")
    ledger = driver.BudgetLedger(
        root / "accounting" / (value["model"] + ".sqlite3"),
        driver.configuration(plan, value["model"]),
        create=False,
    )
    policy_job = prior["approved_request"]["attempt_id"]
    expected_reviews = [
        (
            prior["approved_request"]["id"],
            driver.digest(prior),
            prior["evidence_sha256"][policy_job + ".response.json"],
        ),
        (
            FAILED_REQUEST,
            driver.digest(value),
            value["evidence_sha256"][value["failure_event_file"]],
        ),
    ]
    with ledger._connection() as db:
        for request_id, decision_hash, evidence_hash in expected_reviews:
            events = list(
                db.execute(
                    "SELECT data FROM events WHERE kind='bounded_unknown_reviewed' AND request_id=?",
                    (request_id,),
                )
            )
            if len(events) != 1:
                raise ValueError("A required exact bounded review is missing or duplicated")
            evidence = json.loads(events[0]["data"])
            if (
                evidence.get("decision_sha256") != decision_hash
                or evidence.get("evidence_sha256") != evidence_hash
            ):
                raise ValueError("Bounded review decision/evidence differs from frozen approval")
    return value


def freeze(root: Path) -> dict[str, Any]:
    plan = driver.load_plan(root)
    prior = recovery.load(root)
    path = root / "connection-continuation.json"
    if path.exists():
        raise ValueError("Connection continuation is already frozen")
    previous = recovery.accounting(root, plan, prior)
    states = {model: ledger.status() for model, ledger in previous.items()}
    if any(s["pending_requests"] for s in states.values()):
        raise BudgetError("Wait for all in-flight work")
    if (
        sum(s["reviewed_uncertain_requests"] for s in states.values()) != 1
        or sum(s["unreviewed_uncertain_requests"] for s in states.values()) != 1
    ):
        raise BudgetError("Exactly one retained review and one new unknown are required")
    model, row = next(
        (m, r) for m, s in states.items() for r in s["requests"] if r["id"] == FAILED_REQUEST
    )
    if (
        row["attempt_id"] != FAILED_JOB
        or row["state"] != "uncertain"
        or row["reason"] != "ConnectError"
        or Decimal(row["reserved_usd"]) != CONNECTION_RESERVATION
    ):
        raise BudgetError("New uncertainty differs from the approved connection failure")
    job = next(j for j in plan["jobs"] if j["job_id"] == FAILED_JOB)
    dispatched = driver.read(root / "requests" / (FAILED_JOB + ".request.json"))
    if (
        row["request_hash"] != job["request_sha256"]
        or row["input_ceiling"] != job["input_ceiling"]
        or row["output_ceiling"] != driver.PROFILES[model]["output_tokens"]
        or job["body"]["model"] != model
        or dispatched.get("request_id") != row["id"]
        or dispatched.get("request_sha256") != job["request_sha256"]
        or driver.digest(dispatched.get("body")) != job["request_sha256"]
        or Decimal(str(dispatched.get("reserved_upper_bound_usd", "-1"))) != CONNECTION_RESERVATION
    ):
        raise ValueError("Retained connection request differs from frozen body and reservation")
    if (root / "requests" / (FAILED_JOB + ".response.json")).exists():
        raise ValueError("This approved connection failure has no provider response")
    expected = [{"model": model, "job_id": FAILED_JOB, "error_class": "ConnectError"}]
    failures = [p for p in root.glob("stopped-*.json") if json.loads(p.read_text()) == expected]
    if len(failures) != 1:
        raise ValueError("Exactly one matching retained ConnectError event is required")
    admitted = {r["attempt_id"] for s in states.values() for r in s["requests"]}
    allowed = [
        j["job_id"]
        for j in plan["jobs"]
        if j["job_id"] in prior["allowed_jobs"] and j["job_id"] not in admitted
    ]
    with previous[model]._connection() as db:
        events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY sequence")]
    result = {
        "observation": {
            "job_id": FAILED_JOB,
            "response": None,
            "error": "Recorded ConnectError; usage unknown; full reservation retained. Never retried.",
            "cost_usd": None,
            "reservation_usd": float(CONNECTION_RESERVATION),
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
        },
        "request_id": FAILED_REQUEST,
        "returned_model": None,
        "cost_basis": "Unknown billing remains charged at the full frozen reservation; no zero-cost inference.",
        "derived_from_retained_connection_event": True,
    }
    evidence = [
        p.relative_to(root).as_posix() for p in sorted((root / "requests").glob("*.result.json"))
    ]
    evidence.extend(["requests/" + FAILED_JOB + ".request.json", failures[0].name])
    value = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "base_plan_sha256": driver.digest(plan),
        "prior_policy_sha256": driver.digest(prior),
        "decision": "Retain both complete unknown reservations; never retry either started failure. Continue only the remaining untouched subset of the existing policy-reviewed authority.",
        "model": model,
        "approved_request": previous[model]._review_snapshot(row),
        "billing_complete": False,
        "allowed_jobs": allowed,
        "quarantined_unattempted_jobs": prior["quarantined_unattempted_jobs"],
        "retained_request_count": sum(len(s["requests"]) for s in states.values()),
        "cost_before_continuation": driver.snapshot(root, plan),
        "event_prefix": {"count": len(events), "sha256": previous[model]._events_digest(events)},
        "failure_event_file": failures[0].name,
        "source_sha256": {
            name: hashlib.sha256((driver.ROOT / name).read_bytes()).hexdigest() for name in SOURCES
        },
        "evidence_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in evidence
        },
        "derived_observation_sha256": driver.digest(result),
        "analysis_deviation": "The additional connection failure remains an observed error. Original sampling, prompts, prices, caps, policy quarantine and inference rules are unchanged.",
    }
    private_json(path, value)
    private_json(
        root / "connection-continuation-freeze.json",
        {"sha256": driver.digest(value), "created_at": value["created_at"]},
    )
    private_json(root / "requests" / (FAILED_JOB + ".result.json"), result)
    revised = accounting(root, plan, value)[model]
    revised.record_bounded_review(
        FAILED_REQUEST,
        request_hash=row["request_hash"],
        reserved_usd=CONNECTION_RESERVATION,
        decision_sha256=driver.digest(value),
        evidence_sha256=hashlib.sha256(failures[0].read_bytes()).hexdigest(),
        event_prefix_count=len(events),
        event_prefix_sha256=previous[model]._events_digest(events),
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
    state["bounded_reviewed_unknown_requests"] = 2
    state["billing_complete"] = False
    state["connection_continuation_sha256"] = driver.digest(continuation)
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
                }
            )
        )
    else:
        run(args.root, "holdout")


if __name__ == "__main__":
    main()
