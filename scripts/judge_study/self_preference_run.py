"""Bounded, single-dispatch transport for a separately reviewed preference audit.

Preparation never authorizes calls. The runner requires an exact dispatch manifest
and an explicit local authorization record after independent protocol review.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from cvals.judge_calibration import normalize_response
from cvals.review_queues import Rubric
from scripts.benchmarks.budget import BudgetConfig, BudgetError, BudgetLedger
from scripts.judge_study import self_preference_plan as planner
from scripts.judge_study.responses import judge_response

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SOURCES = (
    "scripts/judge_study/self_preference_run.py",
    "scripts/judge_study/analyze_self_preference.py",
    "src/cvals/judge_statistics.py",
    "scripts/judge_study/self_preference_plan.py",
    "scripts/judge_study/responses.py",
    "scripts/benchmarks/budget.py",
    "src/cvals/judge_calibration.py",
    "src/cvals/judge_calibration_models.py",
    "src/cvals/review_queues.py",
    "src/cvals/models.py",
)
RUBRIC = Rubric.model_validate(
    {
        "name": "Patch correctness",
        "version": "1",
        "instructions": planner.INSTRUCTIONS,
        "dimensions": [
            {
                "id": "correctness",
                "name": "Correctness",
                "guidance": "Fix the reported issue without regressions.",
            }
        ],
    }
)


def strict_json(value: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate provider JSON key")
            result[key] = item
        return result

    def constant(_: str) -> Any:
        raise ValueError("Non-finite provider JSON constant")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def read(path: Path) -> Any:
    return strict_json(path.read_bytes())


def prepared(root: Path) -> dict[str, Any]:
    value = read(root / "prepared.json")
    selection = read(root / "selection.json")
    if planner.digest(value) != read(root / "prepared-freeze.json")["sha256"]:
        raise ValueError("Prepared request manifest changed")
    if (
        planner.digest(selection) != read(root / "selection-freeze.json")["sha256"]
        or value["selection_sha256"] != planner.digest(selection)
        or value["source_sha256"] != hashlib.sha256(Path(planner.__file__).read_bytes()).hexdigest()
    ):
        raise ValueError("Prospective selection or planner changed")
    if value["profiles"] != planner.PROFILES or Decimal(value["budget_cap_usd"]) != Decimal("7.95"):
        raise BudgetError("Prepared prices or total cap differ from the authorized allocation")
    if sum(
        (Decimal(profile["cap"]) for profile in planner.PROFILES.values()), Decimal(0)
    ) > Decimal("7.95"):
        raise BudgetError("Model caps exceed the total follow-up allocation")
    if len(value["jobs"]) != 360 or len({job["id"] for job in value["jobs"]}) != 360:
        raise ValueError("Prepared audit must retain its complete 360-job grid")
    for job in value["jobs"]:
        model = job["judge"]
        if model not in planner.MODELS:
            raise ValueError("Unknown judge model")
        if job["status"] != "ready":
            if job["body"] is not None or job["reservation_usd"] is not None:
                raise ValueError("Unavailable pair cannot contain a dispatchable payload")
            continue
        body = job["body"]
        if (
            planner.digest(body) != job["body_sha256"]
            or body["model"] != model
            or body["store"] is not False
            or body["service_tier"] != "default"
            or body["max_output_tokens"] != planner.PROFILES[model]["ceiling"]
            or job["output_ceiling"] != planner.PROFILES[model]["ceiling"]
            or job["input_ceiling"] != len(planner.canonical(body)) + planner.INPUT_FRAMING_BOUND
            or len(planner.canonical(body)) > planner.MAX_REQUEST_BYTES
        ):
            raise ValueError("Prepared request or token ceiling changed")
        expected = (
            Decimal(job["input_ceiling"]) * Decimal(planner.PROFILES[model]["input"])
            + Decimal(job["output_ceiling"]) * Decimal(planner.PROFILES[model]["output"])
        ) / Decimal(1000000)
        if Decimal(job["reservation_usd"]) != expected:
            raise BudgetError("Prepared reservation differs from the bounded request")
    return value


def freeze(root: Path) -> dict[str, Any]:
    root = planner.private_output(root)
    plan = prepared(root)
    if any((root / name).exists() for name in ("requests", "accounting")):
        raise ValueError("Dispatch must freeze before any follow-up attempt")
    value = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "prepared_sha256": planner.digest(plan),
        "selection_sha256": plan["selection_sha256"],
        "profiles": planner.PROFILES,
        "budget_cap_usd": "7.95",
        "max_inflight": 1,
        "job_order": [job["id"] for job in plan["jobs"]],
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES
        },
        "response_policy": "Strict single final phase; selected invalid judgments retained. HTTP policy/refusal quarantines the task globally. Any uncertain accounting halts all new calls. Never retry an admitted request.",
        "accounting": "Input at 2.5 times published ordinary rates; output at ordinary rates. Full unknown reservation retained; this is a conservative configured-rate total, not an invoice.",
        "analysis_role": "Prospective descriptive same-returned-identifier preference audit; weights are not attested and the comparison is not causal self-bias.",
    }
    planner.write_private(root / "dispatch.json", value)
    planner.write_private(root / "dispatch-freeze.json", {"sha256": planner.digest(value)})
    return value


def load(root: Path, *, authorize: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    plan, dispatch = prepared(root), read(root / "dispatch.json")
    if (
        planner.digest(dispatch) != read(root / "dispatch-freeze.json")["sha256"]
        or dispatch["prepared_sha256"] != planner.digest(plan)
        or dispatch["profiles"] != planner.PROFILES
        or dispatch["budget_cap_usd"] != "7.95"
        or dispatch["max_inflight"] != 1
        or dispatch["job_order"] != [job["id"] for job in plan["jobs"]]
    ):
        raise ValueError("Frozen dispatch protocol changed")
    for name, expected in dispatch["source_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen follow-up source changed: " + name)
    if set(dispatch["source_sha256"]) != set(SOURCES):
        raise ValueError("Frozen follow-up source list changed")
    if authorize:
        path = root / "dispatch-authorization.json"
        if not path.exists():
            raise ValueError("Exact follow-up protocol has not been authorized for dispatch")
        approval = read(path)
        if (
            approval.get("authorized") is not True
            or approval.get("dispatch_sha256") != planner.digest(dispatch)
            or approval.get("budget_cap_usd") != "7.95"
        ):
            raise ValueError("Authorization does not bind this exact follow-up protocol")
    return plan, dispatch


def ledgers(root: Path, dispatch: dict[str, Any]) -> dict[str, BudgetLedger]:
    result = {}
    for model, profile in planner.PROFILES.items():
        cap = Decimal(profile["cap"])
        config = BudgetConfig(
            campaign_id="self-preference-" + model.replace(".", "-"),
            campaign_config_hash=planner.digest(dispatch),
            pricing_version="openai-upperbound-2026-10-27",
            user_ceiling_usd=cap,
            admission_ceiling_usd=cap,
            attempt_cap_usd=cap,
            input_rate_per_million=Decimal(profile["input"]),
            output_rate_per_million=Decimal(profile["output"]),
        )
        result[model] = BudgetLedger(root / "accounting" / (model + ".sqlite3"), config)
    return result


def snapshot(budgets: dict[str, BudgetLedger]) -> dict[str, Any]:
    states = {model: ledger.status() for model, ledger in budgets.items()}
    committed = sum(
        (Decimal(value["committed_spend_usd"]) for value in states.values()), Decimal(0)
    )
    return {
        "committed_upper_bound_usd": str(committed),
        "settled_upper_bound_usd": str(
            sum((Decimal(value["known_spend_usd"]) for value in states.values()), Decimal(0))
        ),
        "requests": sum(len(value["requests"]) for value in states.values()),
        "unknown_requests": sum(
            value["pending_requests"] + value["uncertain_requests"] for value in states.values()
        ),
        "models": states,
    }


def quarantine(root: Path, plan: dict[str, Any], job: dict[str, Any], reason: str) -> None:
    directory = Path(plan["provider_quarantine_directory"])
    path = directory / (hashlib.sha256(job["instance_id"].encode()).hexdigest() + ".json")
    if not path.exists():
        planner.write_private(
            path,
            {
                "instance_id": job["instance_id"],
                "job_id": job["id"],
                "policy_quarantine": True,
                "source": "same-identifier-judge",
                "reason": reason,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    stop(root, job, reason)


def stop(root: Path, job: dict[str, Any], reason: str) -> None:
    path = root / "STOP.json"
    if not path.exists():
        planner.write_private(
            path,
            {
                "job_id": job["id"],
                "instance_id": job["instance_id"],
                "reason": reason,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )


def run(root: Path) -> dict[str, Any]:
    root = planner.private_output(root)
    plan, dispatch = load(root, authorize=True)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is required")
    pause = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: pause.set())
    try:
        with (root / "dispatch.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            budgets = ledgers(root, dispatch)
            if any(value.status()["admissions_blocked"] for value in budgets.values()):
                raise BudgetError("Unknown usage or cap blocks every new follow-up call")
            if (root / "STOP.json").exists():
                raise BudgetError("Shared follow-up stop is active")
            with httpx.Client(
                timeout=httpx.Timeout(180, connect=20), trust_env=False, follow_redirects=False
            ) as client:
                for job in plan["jobs"]:
                    if pause.is_set() or (root / "STOP.json").exists():
                        break
                    if job["status"] != "ready":
                        continue
                    output = root / "requests" / job["id"]
                    if (output / "result.json").exists():
                        continue
                    qpath = Path(plan["provider_quarantine_directory"]) / (
                        hashlib.sha256(job["instance_id"].encode()).hexdigest() + ".json"
                    )
                    if qpath.exists():
                        stop(root, job, "Selected task is globally quarantined")
                        break
                    ledger = budgets[job["judge"]]
                    request_id = None
                    settled = False
                    started = time.monotonic()
                    try:
                        ledger.register_attempt(
                            job["id"],
                            planner.digest(
                                {"job_id": job["id"], "body_sha256": job["body_sha256"]}
                            ),
                        )
                        request_id = ledger.reserve(
                            job["id"],
                            job["body_sha256"],
                            job["input_ceiling"],
                            job["output_ceiling"],
                        )
                        planner.write_private(
                            output / "request.json",
                            {
                                "request_id": request_id,
                                "body": job["body"],
                                "body_sha256": job["body_sha256"],
                                "reservation_usd": job["reservation_usd"],
                                "sent_at": datetime.now(UTC).isoformat(),
                            },
                        )
                        with client.stream(
                            "POST",
                            ENDPOINT,
                            headers={
                                "Authorization": "Bearer " + key,
                                "Content-Type": "application/json",
                            },
                            content=planner.canonical(job["body"]),
                        ) as response:
                            payload = bytearray()
                            for chunk in response.iter_bytes():
                                payload.extend(chunk)
                                if len(payload) > MAX_RESPONSE_BYTES:
                                    raise ValueError(
                                        "Provider response exceeds bounded evidence size"
                                    )
                            status = response.status_code
                        redacted = payload.decode().replace(key, "[REDACTED]")
                        planner.write_private(
                            output / "transport.json",
                            {"http_status": status, "body_utf8": redacted},
                        )
                        raw = strict_json(redacted)
                        envelope = {"http_status": status, "response": raw}
                        handling = judge_response(envelope, RUBRIC)
                        planner.write_private(
                            output / "response.json",
                            {
                                **envelope,
                                "response_handling": handling.metadata(),
                            },
                        )
                        if handling.classification in {
                            "provider_policy_rejection",
                            "provider_refusal",
                        }:
                            quarantine(root, plan, job, handling.classification)
                        usage = raw.get("usage") if isinstance(raw, dict) else None
                        if (
                            not isinstance(usage, dict)
                            or type(usage.get("input_tokens")) is not int
                            or type(usage.get("output_tokens")) is not int
                            or raw.get("model") != job["judge"]
                            or raw.get("service_tier", "default") not in (None, "default")
                        ):
                            raise ValueError("Unpriced model, tier or missing usage")
                        cost = ledger.settle(
                            request_id, usage["input_tokens"], usage["output_tokens"]
                        )
                        settled = True
                        normalized = normalize_response(handling.text, ["A", "B"], RUBRIC)
                        if handling.error is not None:
                            normalized = {
                                "status": "error",
                                "preference": None,
                                "rationale": None,
                                "scores": None,
                            }
                        planner.write_private(
                            output / "result.json",
                            {
                                "job_id": job["id"],
                                "instance_id": job["instance_id"],
                                "judge": job["judge"],
                                "returned_judge_model": raw["model"],
                                "provider_response_id": raw.get("id"),
                                "request_id": request_id,
                                "normalized": normalized,
                                "response_handling": handling.metadata(),
                                "provider_usage": usage,
                                "conservative_cost_usd": str(cost),
                                "reservation_usd": job["reservation_usd"],
                                "duration_seconds": time.monotonic() - started,
                            },
                        )
                        if status != 200:
                            stop(root, job, "Observed provider HTTP error")
                        print(
                            json.dumps(
                                {"completed_job": job["id"], "cost_upper_bound_usd": str(cost)}
                            ),
                            flush=True,
                        )
                    except BaseException as error:
                        if request_id is not None and not settled:
                            ledger.uncertain(request_id, type(error).__name__)
                        stop(root, job, type(error).__name__)
                        planner.write_private(
                            root / ("stopped-" + str(time.time_ns()) + ".json"),
                            {
                                "job_id": job["id"],
                                "request_id": request_id,
                                "exception_class": type(error).__name__,
                                "settled": settled,
                            },
                        )
                        raise
            state = snapshot(budgets)
            planner.write_private(root / ("budget-status-" + str(time.time_ns()) + ".json"), state)
            return state
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=("freeze", "run", "status"))
    args = parser.parse_args()
    if args.command == "freeze":
        value = freeze(args.root)
        print(json.dumps({"dispatch_sha256": planner.digest(value), "authorized": False}))
    elif args.command == "run":
        value = run(args.root)
        print(
            json.dumps(
                {
                    key: value[key]
                    for key in ("requests", "committed_upper_bound_usd", "unknown_requests")
                }
            )
        )
    else:
        _, dispatch = load(args.root)
        print(json.dumps(snapshot(ledgers(args.root, dispatch)), indent=2))


if __name__ == "__main__":
    main()
