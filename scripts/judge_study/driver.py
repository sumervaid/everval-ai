"""Freeze and execute a bounded OpenAI judge study with retained request accounting.

All requests are public-input payloads from a frozen calibration protocol.
Per-model caps sum to $30, leaving $20 of the user ceiling unused by this runner.
No automatic retries or implicit release of interrupted financial reservations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from cvals.config import Settings
from cvals.judge_calibration import JudgeCalibration, request_for
from cvals.judge_calibration_models import CalibrationProtocol
from cvals.review_queues import Rubric
from scripts.benchmarks.budget import BudgetConfig, BudgetError, BudgetLedger
from scripts.benchmarks.model import private_json
from scripts.judge_study.responses import judge_response, preserve_policy_stop

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.openai.com/v1/responses"
PROFILES = {
    "gpt-6-luna": {
        "cap": "2",
        "input": "0.25",
        "output": "0.5",
        "output_tokens": 768,
        "reasoning": "none",
    },
    "gpt-6.1-terra": {
        "cap": "8",
        "input": "2.5",
        "output": "5",
        "output_tokens": 768,
        "reasoning": "none",
    },
    "gpt-6.1-sol": {
        "cap": "20",
        "input": "5",
        "output": "10",
        "output_tokens": 1536,
        "reasoning": "low",
    },
}
SOURCES = [
    "src/cvals/judge_calibration.py",
    "src/cvals/judge_calibration_models.py",
    "src/cvals/models.py",
    "src/cvals/review_queues.py",
    "src/cvals/judge_statistics.py",
    "scripts/judge_study/responses.py",
    "scripts/judge_study/driver.py",
    "scripts/judge_study/prepare.py",
    "scripts/benchmarks/budget.py",
]
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FRAMING_ALLOWANCE = 8192


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def request_body(job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    model = payload["model"]
    profile = PROFILES[model]
    return {
        "model": model,
        "input": payload["messages"],
        "max_output_tokens": profile["output_tokens"],
        "reasoning": {"effort": profile["reasoning"]},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "everval_judgment",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "preference": {"type": "string", "enum": ["A", "B", "tie", "abstain"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["preference", "rationale"],
                    "additionalProperties": False,
                },
            }
        },
        "service_tier": "default",
        "store": False,
    }


def configuration(plan: dict[str, Any], model: str) -> BudgetConfig:
    if plan["profiles"] != PROFILES or sum(Decimal(p["cap"]) for p in PROFILES.values()) > 30:
        raise BudgetError("Frozen pricing/caps differ from the admitted $30 inference allocation")
    p = PROFILES[model]
    return BudgetConfig(
        campaign_id="judge-" + model.replace(".", "-"),
        campaign_config_hash=digest(plan),
        pricing_version="openai-upperbound-2026-10-27",
        user_ceiling_usd=Decimal(p["cap"]),
        admission_ceiling_usd=Decimal(p["cap"]),
        attempt_cap_usd=Decimal("1"),
        input_rate_per_million=Decimal(p["input"]),
        output_rate_per_million=Decimal(p["output"]),
    )


def ledgers(root: Path, plan: dict[str, Any], *, create: bool = False) -> dict[str, BudgetLedger]:
    return {
        m: BudgetLedger(
            root / "accounting" / (m + ".sqlite3"), configuration(plan, m), create=create
        )
        for m in PROFILES
    }


def load_plan(root: Path) -> dict[str, Any]:
    plan = read(root / "plan.json")
    if read(root / "freeze.json")["plan_sha256"] != digest(plan):
        raise ValueError("Frozen study plan changed")
    for path, expected in plan["source_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise ValueError("Study source changed after freezing: " + path)
    for cohort, expected in plan["protocol_sha256"].items():
        if (
            hashlib.sha256((root / "approved" / (cohort + ".json")).read_bytes()).hexdigest()
            != expected
        ):
            raise ValueError("Frozen study protocol changed")
    return plan


def freeze(root: Path) -> None:
    if (root / "plan.json").exists():
        raise ValueError("Study is already frozen")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "approved").mkdir(exist_ok=True)
    service = JudgeCalibration(Settings(root / "workspace"))
    jobs, campaigns, hashes, metadata = [], {}, {}, {}
    for cohort in ["human", "execution"]:
        protocol = read(root / (cohort + "-protocol.json"))
        protocol["budget_usd"] = 30
        value = CalibrationProtocol.model_validate(protocol)
        created = service.create(value)
        campaigns[cohort] = created["id"]
        path = root / "approved" / (cohort + ".json")
        private_json(path, value.model_dump(mode="json"))
        hashes[cohort] = hashlib.sha256(path.read_bytes()).hexdigest()
        with service.store._connect() as db:
            rows = service._jobs(db, created["id"])
        for row in rows:
            exported = {
                "job_id": row["id"],
                "item_id": row["item_id"],
                "judge_id": row["judge_id"],
                "repetition": row["repetition"],
                "order": row["order"],
                "payload": request_for(value, row),
            }
            body = request_body(exported)
            input_ceiling = len(canonical(body)) + FRAMING_ALLOWANCE
            if input_ceiling > 128 * 1024:
                raise ValueError("Prompt exceeds the frozen input bound")
            jobs.append(
                {
                    "cohort": cohort,
                    "campaign_id": created["id"],
                    "partition": row["partition"],
                    **{k: v for k, v in exported.items() if k != "payload"},
                    "body": body,
                    "request_sha256": digest(body),
                    "input_ceiling": input_ceiling,
                }
            )
        metadata[cohort] = {
            "items": len(value.items),
            "development": sum(x.partition == "dev" for x in value.items),
            "held_out": sum(x.partition == "holdout" for x in value.items),
            "families": len({x.family_id for x in value.items}),
            "jobs": len(rows),
        }
    plan = {
        "schema_version": 1,
        "name": "Everval judge study 2026-10-27",
        "user_total_cap_usd": "50",
        "inference_allocation_usd": "30",
        "unused_allocation_usd": "20",
        "cost_basis": "Conservative upper bound: all input at 2.5x standard uncached input price, all output at standard price. This is not an invoice.",
        "pricing_source": "https://developers.openai.com/api/docs/pricing",
        "profiles": PROFILES,
        "endpoint": ENDPOINT,
        "automatic_retries": 0,
        "campaigns": campaigns,
        "protocol_sha256": hashes,
        "cohorts": metadata,
        "primary_endpoint": "Human-vote agreement, equal question and equal retained-reviewer weighting.",
        "primary_contrasts": [
            "luna:rubric-guided-minus-standard",
            "terra:rubric-guided-minus-standard",
            "sol:rubric-guided-minus-standard",
        ],
        "primary_familywise_alpha": 0.05,
        "primary_contrast_alpha": 0.05 / 3,
        "selection": "All six judge configurations are predeclared for holdout; development is compatibility/estimation, not winner selection.",
        "power": {
            "human_primary": "Fractional paired question-level difference; normal-approximation sensitivity over predeclared paired SDs 0.10, 0.25, 0.50 at n=64 and alpha=.05/3. Exact McNemar does not apply to this endpoint.",
            "execution_secondary": "40 held-out tasks; binary task-level comparisons require one prespecified paired decision; repeated observations do not increase independent sample size.",
        },
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES
        },
        "jobs": jobs,
    }
    private_json(root / "plan.json", plan)
    private_json(
        root / "freeze.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "plan_sha256": digest(plan),
            "provider_calls_before_freeze": 0,
        },
    )
    ledgers(root, plan, create=True)
    print(
        json.dumps(
            {
                "plan_sha256": digest(plan),
                "cohorts": metadata,
                "jobs": len(jobs),
                "inference_cap_usd": 30,
            },
            indent=2,
        )
    )


def snapshot(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    states = {m: ledger.status() for m, ledger in ledgers(root, plan).items()}
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "cost_basis": plan["cost_basis"],
        "inference_cap_usd": 30,
        "total_user_cap_usd": 50,
        "conservative_committed_usd": str(
            sum(Decimal(s["committed_spend_usd"]) for s in states.values())
        ),
        "conservative_settled_usd": str(
            sum(Decimal(s["known_spend_usd"]) for s in states.values())
        ),
        "requests": sum(len(s["requests"]) for s in states.values()),
        "unknown_requests": sum(
            int(s["uncertain_requests"]) + int(s["pending_requests"]) for s in states.values()
        ),
        "models": {
            m: {
                k: s[k]
                for k in [
                    "committed_spend_usd",
                    "known_spend_usd",
                    "admissions_blocked",
                    "blocked_reason",
                ]
            }
            for m, s in states.items()
        },
    }


def collect(root: Path, plan: dict[str, Any], partition: str) -> None:
    service = JudgeCalibration(Settings(root / "workspace"))
    for cohort, identifier in plan["campaigns"].items():
        rows = []
        for job in plan["jobs"]:
            if job["cohort"] != cohort or job["partition"] != partition:
                continue
            path = root / "requests" / (job["job_id"] + ".result.json")
            if path.exists():
                rows.append(read(path)["observation"])
        if rows:
            service.import_observations(identifier, rows)


def run(root: Path, *, partition: str, smoke: bool = False) -> None:
    if (root / "STOP").exists():
        raise BudgetError("Shared study stop is active")
    plan = load_plan(root)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY must be supplied through the private environment")
    service = JudgeCalibration(Settings(root / "workspace"))
    rubrics = {
        cohort: Rubric.model_validate(read(root / "approved" / (cohort + ".json"))["rubric"])
        for cohort in plan["campaigns"]
    }
    if partition == "holdout":
        for identifier in plan["campaigns"].values():
            campaign = service.get(identifier)
            if not campaign["state"]["holdout_released"]:
                raise ValueError("Holdout selection must be explicitly frozen after development")
    accounting = ledgers(root, plan)
    for ledger in accounting.values():
        if ledger.status()["admissions_blocked"]:
            raise BudgetError("Unsettled usage or cap blocks further calls")
    jobs = [j for j in plan["jobs"] if j["partition"] == partition]
    if smoke:
        seen = set()
        jobs = [j for j in jobs if j["judge_id"] not in seen and not seen.add(j["judge_id"])]
    output = root / "requests"
    output.mkdir(exist_ok=True, mode=0o700)
    stop = threading.Event()
    failures = []

    def worker(model: str) -> None:
        ledger = accounting[model]
        completed = 0
        with httpx.Client(
            timeout=httpx.Timeout(180, connect=20), follow_redirects=False, trust_env=False
        ) as client:
            for job in jobs:
                if stop.is_set():
                    break
                if (
                    job["body"]["model"] != model
                    or (output / (job["job_id"] + ".result.json")).exists()
                ):
                    continue
                request_id = None
                settled = False
                start = time.monotonic()
                try:
                    request_hash = digest(job["body"])
                    if request_hash != job["request_sha256"]:
                        raise ValueError("Frozen request hash changed")
                    ledger.register_attempt(
                        job["job_id"], digest({"job": job["job_id"], "request": request_hash})
                    )
                    ceiling = int(PROFILES[model]["output_tokens"])
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
                    with client.stream(
                        "POST",
                        ENDPOINT,
                        headers={
                            "Authorization": "Bearer " + key,
                            "Content-Type": "application/json",
                        },
                        content=canonical(job["body"]),
                    ) as response:
                        raw_bytes = bytearray()
                        for chunk in response.iter_bytes():
                            raw_bytes.extend(chunk)
                            if len(raw_bytes) > MAX_RESPONSE_BYTES:
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
                    completed += 1
                    if completed % 20 == 0 or smoke:
                        print(
                            model,
                            "completed",
                            completed,
                            "last_cost_upper_bound_usd",
                            str(cost),
                            flush=True,
                        )
                except BaseException as error:
                    stop.set()
                    if request_id is not None and not settled:
                        ledger.uncertain(request_id, type(error).__name__)
                    failures.append(
                        {
                            "model": model,
                            "job_id": job["job_id"],
                            "error_class": type(error).__name__,
                        }
                    )
                    print(model, "STOPPED", type(error).__name__, flush=True)
                    break

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, list(PROFILES)))
    collect(root, plan, partition)
    state = snapshot(root, plan)
    (root / "budget-status.json").write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state, indent=2), flush=True)
    if failures:
        private_json(root / ("stopped-" + str(time.time_ns()) + ".json"), failures)
        raise RuntimeError("Study stopped; retained accounting requires review")


def release(root: Path) -> None:
    plan = load_plan(root)
    service = JudgeCalibration(Settings(root / "workspace"))
    collect(root, plan, "dev")
    for identifier in plan["campaigns"].values():
        value = service.get(identifier)
        ids = [j["id"] for j in value["protocol"]["judges"]]
        service.release_holdout(
            identifier,
            ids,
            "All six configurations were predeclared before any calls. No development winner selection or prompt modification.",
        )
    print("All predeclared configurations released to the frozen held-out cohorts")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("command", choices=["freeze", "status", "run", "collect", "release"])
    parser.add_argument("--phase", choices=["dev", "holdout"], default="dev")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.root)
    elif args.command == "status":
        print(json.dumps(snapshot(args.root, load_plan(args.root)), indent=2))
    elif args.command == "run":
        run(args.root, partition=args.phase, smoke=args.smoke)
    elif args.command == "release":
        release(args.root)
    else:
        collect(args.root, load_plan(args.root), args.phase)


if __name__ == "__main__":
    main()
