"""Flat, immutable exception review with one pass over retained file evidence.

All historical manifests, event scopes, request records, prompts and scoring are
preserved. Only the explicitly frozen unrelated unattempted jobs may continue.
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

from cvals.review_queues import Rubric
from scripts.benchmarks.budget import BudgetError, RecoveryContext
from scripts.benchmarks.model import private_json
from scripts.judge_study import driver, recovery
from scripts.judge_study.flat_budget import FlatReviewedLedger
from scripts.judge_study.responses import judge_response, preserve_policy_stop

FAILED_JOB = "1495e2699a01d54e81ee59f5eaaeda023637f33be3d5de3884913924e945b577"
FAILED_REQUEST = "434767f9672d424c8c54fe0d95da3ed6"
POLICY_ITEM = "swe-django__django-14787"
RESERVATION = Decimal("0.00435325")
CONNECTION_REQUEST = "c46a5fd2f7234fdab85fe3f96504d675"
SOURCES = [
    "scripts/judge_study/responses.py",
    "scripts/judge_study/flat_recovery.py",
    "scripts/judge_study/flat_budget.py",
    "scripts/judge_study/analyze_flat.py",
]
HISTORICAL = ("operational-amendment", "policy-continuation", "connection-continuation")


def merge_hashes(target: dict[str, str], incoming: dict[str, str]) -> None:
    for name, value in incoming.items():
        if name in target and target[name] != value:
            raise ValueError("Historical immutable evidence hashes disagree")
        target[name] = value


def legacy(
    root: Path, plan: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Validate manifest relationships without recursively scanning their files."""
    values = {}
    evidence: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name in HISTORICAL:
        value = driver.read(root / (name + ".json"))
        if driver.digest(value) != driver.read(root / (name + "-freeze.json"))["sha256"]:
            raise ValueError("Historical manifest changed")
        if value["base_plan_sha256"] != driver.digest(plan):
            raise ValueError("Historical plan identity changed")
        values[name] = value
        merge_hashes(sources, value["source_sha256"])
    amendment, policy, connection = (values[name] for name in HISTORICAL)
    if amendment["max_inflight_per_model"] != 4:
        raise ValueError("Historical concurrency policy changed")
    if (
        policy["prior_amendment_sha256"] != driver.digest(amendment)
        or policy["policy_item"] != recovery.POLICY_ITEM
        or policy["approved_request"]["id"] != recovery.REJECTED_REQUEST
        or connection["prior_policy_sha256"] != driver.digest(policy)
        or connection["approved_request"]["id"] != CONNECTION_REQUEST
    ):
        raise ValueError("Historical exception identity or authority changed")
    indexed = {job["job_id"]: job for job in plan["jobs"]}
    allowed = set(policy["allowed_jobs"])
    if len(allowed) != len(policy["allowed_jobs"]):
        raise ValueError("Duplicate original continuation job")
    pairs_seen = set()
    for model, pairs in policy["allowed_attempts"].items():
        for job_id, attempt_hash in pairs:
            job = indexed[job_id]
            if (
                job_id not in allowed
                or job["item_id"] == recovery.POLICY_ITEM
                or job["body"]["model"] != model
                or attempt_hash != driver.digest({"job": job_id, "request": job["request_sha256"]})
            ):
                raise ValueError("Original recovery scope changed")
            pairs_seen.add(job_id)
    if pairs_seen != allowed:
        raise ValueError("Original scope allowlists differ")
    if (
        not set(connection["allowed_jobs"]) <= allowed
        or connection["quarantined_unattempted_jobs"] != policy["quarantined_unattempted_jobs"]
        or connection["approved_request"]["attempt_id"] in connection["allowed_jobs"]
    ):
        raise ValueError("Connection authority broadened the first review")
    merge_hashes(
        evidence,
        {"requests/" + name: sha for name, sha in amendment["known_completed_prefix"].items()},
    )
    merge_hashes(
        evidence, {"requests/" + name: sha for name, sha in policy["evidence_sha256"].items()}
    )
    merge_hashes(evidence, connection["evidence_sha256"])
    return values, evidence, sources


def verify_files(
    root: Path,
    evidence: dict[str, str],
    sources: dict[str, str],
    *,
    additional: set[str] | None = None,
    semantic_json_hashes: dict[str, str] | None = None,
) -> dict[str, str]:
    """Each evidence file is read once even when several approvals reference it."""
    for name, expected in sources.items():
        if hashlib.sha256((driver.ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen study source changed")
    observed = {}
    semantic = semantic_json_hashes or {}
    for name in sorted(set(evidence) | (additional or set()) | set(semantic)):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid evidence path")
        payload = (root / name).read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if name in semantic and driver.digest(json.loads(payload)) != semantic[name]:
            raise ValueError("Derived historical error observation changed")
        if name in evidence and actual != evidence[name]:
            raise ValueError("Retained evidence changed")
        observed[name] = actual
    return observed


def derived_hashes(values: dict[str, Any]) -> dict[str, str]:
    policy, connection = (values[name] for name in HISTORICAL[1:])
    return {
        "requests/" + policy["approved_request"]["attempt_id"] + ".result.json": policy[
            "derived_policy_observation_sha256"
        ],
        "requests/" + connection["approved_request"]["attempt_id"] + ".result.json": connection[
            "derived_observation_sha256"
        ],
    }


def bind_request(
    root: Path, plan: dict[str, Any], row: dict[str, Any], model: str
) -> dict[str, Any]:
    job = next(j for j in plan["jobs"] if j["job_id"] == row["attempt_id"])
    sent = driver.read(root / "requests" / (row["attempt_id"] + ".request.json"))
    if (
        job["body"]["model"] != model
        or row["request_hash"] != job["request_sha256"]
        or row["input_ceiling"] != job["input_ceiling"]
        or row["output_ceiling"] != driver.PROFILES[model]["output_tokens"]
        or sent.get("request_id") != row["id"]
        or sent.get("request_sha256") != job["request_sha256"]
        or driver.digest(sent.get("body")) != job["request_sha256"]
        or Decimal(str(sent.get("reserved_upper_bound_usd", "-1"))) != Decimal(row["reserved_usd"])
        or driver.configuration(plan, model).cost(row["input_ceiling"], row["output_ceiling"])
        != Decimal(row["reserved_usd"])
    ):
        raise ValueError("Exact request/body/ceilings/reservation binding failed")
    return job


def review_events(ledger: Any, expected: list[dict[str, Any]]) -> None:
    with ledger._connection() as db:
        for item in expected:
            events = list(
                db.execute(
                    "SELECT data FROM events WHERE kind='bounded_unknown_reviewed' AND request_id=?",
                    (item["request"]["id"],),
                )
            )
            if len(events) != 1:
                raise ValueError("Required exact review event missing or duplicated")
            event = json.loads(events[0]["data"])
            if (
                event.get("decision_sha256") != item["decision_sha256"]
                or event.get("evidence_sha256") != item["evidence_sha256"]
            ):
                raise ValueError("Review decision/evidence binding changed")


def original_reviews(values: dict[str, Any]) -> list[dict[str, Any]]:
    policy, connection = (values[name] for name in HISTORICAL[1:])
    return [
        {
            "model": policy["model"],
            "kind": "policy_rejection",
            "request": policy["approved_request"],
            "decision_sha256": driver.digest(policy),
            "evidence_sha256": policy["evidence_sha256"][
                policy["approved_request"]["attempt_id"] + ".response.json"
            ],
        },
        {
            "model": connection["model"],
            "kind": "connection_failure",
            "request": connection["approved_request"],
            "decision_sha256": driver.digest(connection),
            "evidence_sha256": connection["evidence_sha256"][connection["failure_event_file"]],
        },
    ]


def accounting(
    root: Path, plan: dict[str, Any], value: dict[str, Any]
) -> dict[str, FlatReviewedLedger]:
    indexed = {j["job_id"]: j for j in plan["jobs"]}
    return {
        model: FlatReviewedLedger(
            root / "accounting" / (model + ".sqlite3"),
            driver.configuration(plan, model),
            create=False,
            recovery_context=RecoveryContext(
                plan_sha256=value["original_contexts"][model]["plan_sha256"],
                amendment_sha256=value["original_contexts"][model]["amendment_sha256"],
                allowed_attempts=tuple(
                    tuple(pair) for pair in value["original_contexts"][model]["allowed_attempts"]
                ),
            ),
            approved_requests=[
                item["request"] for item in value["reviewed_exceptions"] if item["model"] == model
            ],
            dispatch_allowlist={
                job_id: indexed[job_id]["request_sha256"]
                for job_id in value["allowed_jobs"]
                if indexed[job_id]["body"]["model"] == model
            },
        )
        for model in driver.PROFILES
    }


def load(root: Path, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = driver.load_plan(root) if plan is None else plan
    values, inherited_evidence, inherited_sources = legacy(root, plan)
    value = driver.read(root / "flat-continuation.json")
    if driver.digest(value) != driver.read(root / "flat-continuation-freeze.json")["sha256"]:
        raise ValueError("Flat continuation changed")
    if value["base_plan_sha256"] != driver.digest(plan):
        raise ValueError("Flat continuation plan changed")
    if value["historical_manifest_sha256"] != {
        name: driver.digest(values[name]) for name in HISTORICAL
    }:
        raise ValueError("Historical approval chain changed")
    if value["reviewed_exceptions"][:2] != original_reviews(values):
        raise ValueError("Historical exception decision or snapshot changed")
    if (
        len(value["reviewed_exceptions"]) != 3
        or value["reviewed_exceptions"][2]["request"]["id"] != FAILED_REQUEST
    ):
        raise ValueError("Only the three reviewed exceptions are authorized")
    merge_hashes(inherited_evidence, value["evidence_sha256"])
    merge_hashes(inherited_sources, value["source_sha256"])
    semantic = derived_hashes(values)
    semantic["requests/" + FAILED_JOB + ".result.json"] = value["derived_observation_sha256"]
    verify_files(root, inherited_evidence, inherited_sources, semantic_json_hashes=semantic)
    policy, connection = (values[name] for name in HISTORICAL[1:])
    expected_contexts = {
        model: {
            "plan_sha256": driver.digest(plan),
            "amendment_sha256": driver.digest(policy),
            "allowed_attempts": sorted(policy["allowed_attempts"][model]),
        }
        for model in driver.PROFILES
    }
    if value["original_contexts"] != expected_contexts:
        raise ValueError("Original review contexts changed")
    indexed = {j["job_id"]: j for j in plan["jobs"]}
    if len(set(value["allowed_jobs"])) != len(value["allowed_jobs"]) or not set(
        value["allowed_jobs"]
    ) <= set(connection["allowed_jobs"]):
        raise ValueError("Dispatch scope is not an untouched prior-authorized subset")
    quarantine = set(value["quarantined_unattempted_jobs"])
    if (
        not set(policy["quarantined_unattempted_jobs"]) <= quarantine
        or set(value["allowed_jobs"]) & quarantine
        or any(
            indexed[job]["item_id"] in {recovery.POLICY_ITEM, POLICY_ITEM}
            for job in value["allowed_jobs"]
        )
        or any(
            indexed[job]["item_id"] not in {recovery.POLICY_ITEM, POLICY_ITEM} for job in quarantine
        )
    ):
        raise ValueError("Policy quarantine changed")
    reviewed = value["reviewed_exceptions"]
    for item in reviewed:
        bind_request(root, plan, item["request"], item["model"])
    second = reviewed[1]["request"]
    if (root / "requests" / (second["attempt_id"] + ".response.json")).exists():
        raise ValueError("Original connection failure had no provider response")
    for item in (reviewed[0], reviewed[2]):
        response = driver.read(
            root / "requests" / (item["request"]["attempt_id"] + ".response.json")
        )
        raw = response.get("response", {})
        error = raw.get("error", {})
        if (
            response.get("http_status") != 400
            or error.get("type") != "invalid_request_error"
            or error.get("code") != "invalid_prompt"
            or raw.get("usage") is not None
        ):
            raise ValueError("Exact policy-rejection evidence changed")
    if (
        driver.digest(driver.read(root / "requests" / (FAILED_JOB + ".result.json")))
        != value["derived_observation_sha256"]
    ):
        raise ValueError("Derived third observed-error record changed")
    budgets = accounting(root, plan, value)
    for model, ledger in budgets.items():
        expected = []
        for item in reviewed:
            if item["model"] != model:
                continue
            bound = dict(item)
            if bound["decision_sha256"] == "this_manifest":
                bound["decision_sha256"] = driver.digest(value)
            expected.append(bound)
        review_events(ledger, expected)
    return value


def freeze(root: Path) -> dict[str, Any]:
    if (root / "flat-continuation.json").exists():
        raise ValueError("Flat continuation already frozen")
    plan = driver.load_plan(root)
    values, inherited_evidence, inherited_sources = legacy(root, plan)
    policy, connection = (values[name] for name in HISTORICAL[1:])
    budgets = driver.ledgers(root, plan)
    states = {model: ledger.status() for model, ledger in budgets.items()}
    if any(state["pending_requests"] for state in states.values()):
        raise BudgetError("Wait for every in-flight request")
    uncertain = [
        (model, row)
        for model, state in states.items()
        for row in state["requests"]
        if row["state"] == "uncertain"
    ]
    if {row["id"] for _, row in uncertain} != {
        policy["approved_request"]["id"],
        connection["approved_request"]["id"],
        FAILED_REQUEST,
    }:
        raise BudgetError("Exactly the three approved uncertain requests are required")
    old_reviews = original_reviews(values)
    for item in old_reviews:
        actual = next(
            budgets[item["model"]]._review_snapshot(row)
            for model, row in uncertain
            if row["id"] == item["request"]["id"]
        )
        if actual != item["request"]:
            raise ValueError("Historical uncertain snapshot changed")
        review_events(budgets[item["model"]], [item])
    model, row = next((model, row) for model, row in uncertain if row["id"] == FAILED_REQUEST)
    if (
        row["attempt_id"] != FAILED_JOB
        or row["reason"] != "ValueError"
        or Decimal(row["reserved_usd"]) != RESERVATION
    ):
        raise BudgetError("New error differs from the exact approved third exception")
    job = bind_request(root, plan, row, model)
    if job["item_id"] != POLICY_ITEM:
        raise ValueError("Rejected source item differs")
    response_path = root / "requests" / (FAILED_JOB + ".response.json")
    response = driver.read(response_path)
    raw, error = response.get("response", {}), response.get("response", {}).get("error", {})
    if (
        response.get("http_status") != 400
        or error.get("type") != "invalid_request_error"
        or error.get("code") != "invalid_prompt"
        or raw.get("usage") is not None
    ):
        raise ValueError("New response is not the approved no-usage policy rejection")
    admitted = {r["attempt_id"] for state in states.values() for r in state["requests"]}
    untouched = [
        j
        for j in plan["jobs"]
        if j["job_id"] in connection["allowed_jobs"] and j["job_id"] not in admitted
    ]
    newly_quarantined = [j["job_id"] for j in untouched if j["item_id"] == POLICY_ITEM]
    allowed = [j["job_id"] for j in untouched if j["item_id"] != POLICY_ITEM]
    result = {
        "observation": {
            "job_id": FAILED_JOB,
            "response": None,
            "error": "Recorded provider policy rejection; full unknown reservation retained. Never retried.",
            "cost_usd": None,
            "reservation_usd": float(RESERVATION),
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
        },
        "request_id": FAILED_REQUEST,
        "returned_model": None,
        "cost_basis": "Unknown billing remains charged at the complete frozen reservation.",
        "derived_from_retained_policy_response": True,
    }
    result_path = root / "requests" / (FAILED_JOB + ".result.json")
    if result_path.exists():
        if driver.read(result_path) != result:
            raise ValueError("Existing derived error differs")
    else:
        private_json(result_path, result)
    additional = {p.relative_to(root).as_posix() for p in (root / "requests").glob("*.result.json")}
    additional.update(
        "requests/" + FAILED_JOB + suffix for suffix in (".request.json", ".response.json")
    )
    sources = {
        name: hashlib.sha256((driver.ROOT / name).read_bytes()).hexdigest() for name in SOURCES
    }
    all_sources = dict(inherited_sources)
    merge_hashes(all_sources, sources)
    evidence = verify_files(
        root,
        inherited_evidence,
        all_sources,
        additional=additional,
        semantic_json_hashes=derived_hashes(values),
    )
    with budgets[model]._connection() as db:
        events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY sequence")]
    new_review = {
        "model": model,
        "kind": "policy_rejection",
        "request": budgets[model]._review_snapshot(row),
        "decision_sha256": "this_manifest",
        "evidence_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
    }
    value = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "base_plan_sha256": driver.digest(plan),
        "historical_manifest_sha256": {name: driver.digest(values[name]) for name in HISTORICAL},
        "decision": "Retain all three unknown reservations and original attempts; quarantine both rejected source items; dispatch only the frozen untouched unrelated subset. Every new uncertainty stops new dispatch.",
        "analysis_deviation": "Original64-question human and40-task execution cohorts remain unchanged; quarantined missing cells suppress complete-cohort inference. No failure is retried, rewritten or routed to another model.",
        "billing_complete": False,
        "reviewed_exceptions": [*old_reviews, new_review],
        "allowed_jobs": allowed,
        "quarantined_unattempted_jobs": [
            *policy["quarantined_unattempted_jobs"],
            *newly_quarantined,
        ],
        "new_policy_item": POLICY_ITEM,
        "newly_quarantined_jobs": newly_quarantined,
        "retained_request_count": sum(len(state["requests"]) for state in states.values()),
        "cost_before_continuation": driver.snapshot(root, plan),
        "event_prefix": {"count": len(events), "sha256": budgets[model]._events_digest(events)},
        "original_contexts": {
            model: {
                "plan_sha256": driver.digest(plan),
                "amendment_sha256": driver.digest(policy),
                "allowed_attempts": sorted(policy["allowed_attempts"][model]),
            }
            for model in driver.PROFILES
        },
        "source_sha256": sources,
        "evidence_sha256": evidence,
        "derived_observation_sha256": driver.digest(result),
        "verification": "Historical manifest links are checked once; evidence hashes are merged and every retained file is verified in one pass. Frozen prompts, scoring and historical events are unchanged.",
    }
    private_json(root / "flat-continuation.json", value)
    private_json(
        root / "flat-continuation-freeze.json",
        {"sha256": driver.digest(value), "created_at": value["created_at"]},
    )
    revised = accounting(root, plan, value)[model]
    revised.record_bounded_review(
        FAILED_REQUEST,
        request_hash=row["request_hash"],
        reserved_usd=RESERVATION,
        decision_sha256=driver.digest(value),
        evidence_sha256=new_review["evidence_sha256"],
        event_prefix_count=len(events),
        event_prefix_sha256=budgets[model]._events_digest(events),
    )
    return value


def run(root: Path, partition: str) -> None:
    if (root / "STOP").exists():
        raise BudgetError("Shared study stop is active")
    plan = driver.load_plan(root)
    continuation = load(root, plan)
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
    state["bounded_reviewed_unknown_requests"] = 3
    state["billing_complete"] = False
    state["flat_continuation_sha256"] = driver.digest(continuation)
    private_json(root / ("budget-status-" + str(time.time_ns()) + ".json"), state)
    print(json.dumps(state, indent=2), flush=True)
    if failures:
        private_json(root / ("stopped-" + str(time.time_ns()) + ".json"), failures)
        raise RuntimeError("Study stopped; retained accounting requires review")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=("freeze", "run"))
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
            ),
            flush=True,
        )
    else:
        run(args.root, "holdout")


if __name__ == "__main__":
    main()
