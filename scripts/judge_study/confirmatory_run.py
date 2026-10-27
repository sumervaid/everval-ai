"""Frozen, bounded Responses execution for the prospective judge confirmation.

Run only a reviewed frozen plan. This runner never retries an admitted request.
Per-model allocations sum to the cumulative headroom; unknown usage retains the
full reservation and blocks every model. Raw evidence remains in the study root.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

import httpx

from cvals.config import Settings
from cvals.judge_calibration import JudgeCalibration, normalize_response, request_for
from cvals.judge_calibration_models import CalibrationProtocol
from cvals.models import content_hash
from scripts.benchmarks.budget import BudgetConfig, BudgetError
from scripts.judge_study.concurrent_budget import ConcurrentLedger
from scripts.judge_study.responses import judge_response, preserve_policy_stop

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.openai.com/v1/responses"
OPENING_SPEND = Decimal("15.14292900")
USER_CEILING = Decimal("50")
MAX_INFLIGHT = 4
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FRAMING_ALLOWANCE = 8192
CACHE_RESERVE_MULTIPLIER = Decimal("2.5")
SOURCES = [
    "scripts/judge_study/confirmatory_run.py",
    "scripts/judge_study/confirmatory_prepare.py",
    "scripts/judge_study/confirmatory_analysis.py",
    "scripts/judge_study/concurrent_budget.py",
    "scripts/judge_study/responses.py",
    "scripts/benchmarks/budget.py",
    "src/cvals/judge_calibration.py",
    "src/cvals/judge_calibration_models.py",
    "src/cvals/models.py",
    "src/cvals/providers.py",
    "src/cvals/review_queues.py",
]


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def strict_json(value: str | bytes) -> Any:
    def reject(value: str) -> None:
        raise ValueError("Non-finite JSON value: " + value)

    return json.loads(value, object_pairs_hook=unique_object, parse_constant=reject)


def read(path: Path) -> dict[str, Any]:
    value = strict_json(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def save_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = canonical(value) + b"\n"
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("Refusing to replace retained evidence: " + path.name)
        return
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def within(root: Path, name: str) -> Path:
    if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Evidence paths must be relative and remain within their root")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Evidence file is missing or outside its root")
    return path


def price(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("Prices and allocations must be positive finite decimals")
    return result


def validate_configuration(config: dict[str, Any], protocol: CalibrationProtocol) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported configuration schema")
    budget = config["budget"]
    if (
        Decimal(str(budget["opening_spend_usd"])) != OPENING_SPEND
        or price(budget["ceiling_usd"]) > USER_CEILING
        or price(budget["ceiling_usd"]) <= OPENING_SPEND
    ):
        raise BudgetError("Configuration must preserve the prior spend and $50 cumulative ceiling")
    profiles = config["profiles"]
    if not isinstance(profiles, dict) or set(profiles) != {j.model for j in protocol.judges}:
        raise ValueError("Declare exactly one price profile for every scheduled model")
    allocations = Decimal("0")
    for model, profile in profiles.items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", model):
            raise ValueError("Unsafe model identifier")
        if model == "gpt-6.1-sol" and profile["reasoning"] == "none":
            raise ValueError("GPT-6.1 Sol requires a supported reasoning effort of low or higher")
        allocations += price(profile["allocation_usd"])
        price(profile["input_rate_per_million"])
        price(profile["output_rate_per_million"])
        tokens = profile["max_output_tokens"]
        if type(tokens) is not int or not 16 <= tokens <= 4096:
            raise ValueError("Output ceiling must be an integer from 16 to 4096")
        if profile["reasoning"] not in {"none", "low", "medium", "high"}:
            raise ValueError("Unsupported frozen reasoning effort")
    if allocations + OPENING_SPEND > price(budget["ceiling_usd"]):
        raise BudgetError("Model allocations exceed the remaining cumulative budget")
    if not isinstance(config.get("seed"), str) or not config["seed"].strip():
        raise ValueError("Declare a nonempty randomization seed")
    families = {item.family_id for item in protocol.items}
    if len(families) != len(protocol.items) or protocol.repetitions != 1:
        raise ValueError("Confirmation requires one pair per family and one repetition")
    if config.get("order_design", "full") not in {"full", "sampled_mirrors"}:
        raise ValueError("Unsupported frozen order design")
    mirrors = config.get("mirror_families", len(families))
    if type(mirrors) is not int or not 0 <= mirrors <= len(families):
        raise ValueError("Invalid mirror-family count")
    if any(item.partition != "holdout" for item in protocol.items):
        raise ValueError("Use a fresh holdout-only confirmation protocol")


def request_body(payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": payload["model"],
        "input": payload["messages"],
        "max_output_tokens": profile["max_output_tokens"],
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
        "prompt_cache_options": {"mode": "explicit"},
        "service_tier": "default",
        "store": False,
    }


def planned_jobs(protocol: CalibrationProtocol, config: dict[str, Any]) -> list[dict[str, Any]]:
    validate_configuration(config, protocol)
    protocol_hash = content_hash(protocol.model_dump(mode="json"))
    seed = config["seed"]
    families = sorted(
        (item.family_id for item in protocol.items),
        key=lambda family: digest([seed, "mirror", family]),
    )
    mirrors = set(families[: config.get("mirror_families", len(families))])
    jobs = []
    for item in protocol.items:
        primary = ["A", "B"] if int(digest([seed, "order", item.family_id]), 16) % 2 else ["B", "A"]
        for judge in protocol.judges:
            orders = [("primary", primary)]
            if config.get("order_design", "full") == "full":
                orders.append(("primary", list(reversed(primary))))
            elif item.family_id in mirrors:
                orders.append(("mirror", list(reversed(primary))))
            for kind, order in orders:
                identity = {
                    "protocol_hash": protocol_hash,
                    "item_id": item.item_id,
                    "judge_id": judge.id,
                    "repetition": 0,
                    "order": order,
                }
                job = {"id": content_hash(identity), **identity}
                body = request_body(request_for(protocol, job), config["profiles"][judge.model])
                raw_ceiling = len(canonical(body)) + FRAMING_ALLOWANCE
                if raw_ceiling > 128 * 1024:
                    raise ValueError("Public request exceeds the frozen input-byte bound")
                jobs.append(
                    {
                        "job_id": job["id"],
                        "item_id": item.item_id,
                        "family_id": item.family_id,
                        "judge_id": judge.id,
                        "kind": kind,
                        "repetition": 0,
                        "order": order,
                        "partition": item.partition,
                        "model": judge.model,
                        "body": body,
                        "request_sha256": digest(body),
                        "raw_input_token_ceiling": raw_ceiling,
                        "input_ceiling": int(
                            (Decimal(raw_ceiling) * CACHE_RESERVE_MULTIPLIER).to_integral_value(
                                rounding=ROUND_CEILING
                            )
                        ),
                    }
                )
    return sorted(jobs, key=lambda job: digest([seed, "dispatch", job["job_id"]]))


def budget_config(plan: dict[str, Any], model: str) -> BudgetConfig:
    profile = plan["configuration"]["profiles"][model]
    allocation = price(profile["allocation_usd"])
    return BudgetConfig(
        campaign_id="confirm-" + model,
        campaign_config_hash=digest(plan),
        pricing_version=plan["configuration"]["pricing_version"],
        opening_spend_usd=Decimal("0"),
        user_ceiling_usd=allocation,
        admission_ceiling_usd=allocation,
        attempt_cap_usd=min(Decimal("5"), allocation),
        input_rate_per_million=price(profile["input_rate_per_million"]),
        output_rate_per_million=price(profile["output_rate_per_million"]),
    )


def ledgers(
    root: Path, plan: dict[str, Any], *, create: bool = False
) -> dict[str, ConcurrentLedger]:
    return {
        model: ConcurrentLedger(
            root / "accounting" / (model + ".sqlite3"), budget_config(plan, model), create=create
        )
        for model in plan["configuration"]["profiles"]
    }


def freeze(root: Path) -> dict[str, Any]:
    if (root / "plan.json").exists() or (root / "freeze.json").exists():
        raise ValueError("Confirmation is already frozen")
    if (root / "requests").exists() and any((root / "requests").iterdir()):
        raise ValueError("A new confirmation cannot contain previous request evidence")
    if (root / "accounting").exists() and any((root / "accounting").iterdir()):
        raise ValueError("A new confirmation requires empty accounting")
    config = read(root / "configuration.json")
    protocol_path = within(root, config["protocol_file"])
    protocol = CalibrationProtocol.model_validate(read(protocol_path))
    validate_configuration(config, protocol)
    opening_path = within(root, config["budget"]["opening_evidence_file"])
    opening = read(opening_path)
    if Decimal(str(opening["conservative_accounted_total_usd"])) != OPENING_SPEND:
        raise BudgetError("Opening-spend evidence does not match retained cumulative accounting")
    save_once(root / "approved-protocol.json", protocol.model_dump(mode="json"))
    evidence = {
        config["protocol_file"]: hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        config["budget"]["opening_evidence_file"]: hashlib.sha256(
            opening_path.read_bytes()
        ).hexdigest(),
    }
    for name in config.get("evidence_files", []):
        evidence[name] = hashlib.sha256(within(root, name).read_bytes()).hexdigest()
    sources = {
        name: hashlib.sha256(within(ROOT, name).read_bytes()).hexdigest()
        for name in sorted(set(SOURCES + config.get("source_files", [])))
    }
    plan = {
        "schema_version": 1,
        "configuration": config,
        "configuration_sha256": hashlib.sha256(
            (root / "configuration.json").read_bytes()
        ).hexdigest(),
        "protocol_sha256": hashlib.sha256(
            (root / "approved-protocol.json").read_bytes()
        ).hexdigest(),
        "source_sha256": sources,
        "evidence_sha256": evidence,
        "endpoint": ENDPOINT,
        "automatic_retries": 0,
        "max_inflight": MAX_INFLIGHT,
        "cache_reservation_multiplier": str(CACHE_RESERVE_MULTIPLIER),
        "jobs": planned_jobs(protocol, config),
    }
    service = JudgeCalibration(Settings(root / "workspace"))
    campaign = service.create(protocol)
    if campaign["provider_calls_started"] or campaign["imported_judgments"]:
        raise ValueError("Confirmation must be frozen before any campaign observations")
    service.release_holdout(
        campaign["id"],
        [judge.id for judge in protocol.judges],
        "All configurations and order assignments were fixed before any confirmation calls.",
    )
    plan["campaign_id"] = campaign["id"]
    plan["product_full_grid_calls"] = campaign["planned_judgments"]
    save_once(root / "plan.json", plan)
    ledgers(root, plan, create=True)
    save_once(
        root / "freeze.json",
        {
            "plan_sha256": digest(plan),
            "frozen_at": datetime.now(UTC).isoformat(),
            "provider_calls_before_freeze": 0,
        },
    )
    return plan


def load_plan(root: Path) -> dict[str, Any]:
    plan = read(root / "plan.json")
    if read(root / "freeze.json")["plan_sha256"] != digest(plan):
        raise ValueError("Frozen plan changed")
    checks = {
        root / "configuration.json": plan["configuration_sha256"],
        root / "approved-protocol.json": plan["protocol_sha256"],
        **{within(ROOT, name): sha for name, sha in plan["source_sha256"].items()},
        **{within(root, name): sha for name, sha in plan["evidence_sha256"].items()},
    }
    for path, expected in checks.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen source or evidence changed: " + path.name)
    protocol = CalibrationProtocol.model_validate(read(root / "approved-protocol.json"))
    validate_configuration(plan["configuration"], protocol)
    if plan["jobs"] != planned_jobs(protocol, plan["configuration"]):
        raise ValueError("Frozen jobs do not match the product payloads and declared assignment")
    return plan


@contextlib.contextmanager
def process_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root / "dispatch.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another confirmation dispatcher owns this root") from None
        yield descriptor
    finally:
        os.close(descriptor)


def verify_lock(root: Path, descriptor: int) -> None:
    opened = os.fstat(descriptor)
    current = (root / "dispatch.lock").stat(follow_symlinks=False)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise RuntimeError("Dispatcher lock was replaced")


def summary(accounting: dict[str, ConcurrentLedger]) -> dict[str, Any]:
    models = {}
    for model, ledger in accounting.items():
        with ledger._connection() as db:
            totals = ledger._totals(list(db.execute("SELECT * FROM requests")))
            requests = db.execute("SELECT count(*) FROM requests").fetchone()[0]
        models[model] = {
            **{
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in totals.items()
            },
            "requests": requests,
            "allocation_usd": str(ledger.config.admission_ceiling_usd),
        }
    new = sum(
        (Decimal(value["committed_request_cost_usd"]) for value in models.values()), Decimal(0)
    )
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "opening_spend_usd": str(OPENING_SPEND),
        "new_committed_usd": str(new),
        "cumulative_committed_usd": str(OPENING_SPEND + new),
        "ceiling_usd": str(USER_CEILING),
        "provider_requests": sum(value["requests"] for value in models.values()),
        "models": models,
    }


def gate(accounting: dict[str, ConcurrentLedger], *, allow_owned: bool) -> None:
    for ledger in accounting.values():
        with ledger._connection() as db:
            rows = list(db.execute("SELECT id,state FROM requests WHERE state != 'settled'"))
        for row in rows:
            if row["state"] == "uncertain" or not allow_owned or row["id"] not in ledger._owned:
                raise BudgetError("Unknown or orphaned usage blocks all model admissions")


def verified_usage(raw: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    if raw.get("model") != job["model"] or raw.get("service_tier", "default") not in {
        None,
        "default",
    }:
        raise ValueError("Unpriced returned model or service tier")
    usage = raw.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Missing or invalid provider usage")
    details = usage.get("input_tokens_details", {})
    if not isinstance(details, dict) or any(
        type(details.get(name)) is not int or details[name] != 0
        for name in ("cached_tokens", "cache_write_tokens")
    ):
        raise ValueError("Unexpected cache accounting requires explicit review")
    if usage["input_tokens"] > job["raw_input_token_ceiling"]:
        raise ValueError("Input usage exceeds the original byte-based bound")
    return usage


def verify_prefix(
    root: Path,
    plan: dict[str, Any],
    accounting: dict[str, ConcurrentLedger],
    *,
    require_settled: bool = True,
) -> None:
    """Recompute settled records from retained provider bytes and ledger usage.

    Analysis may retain incomplete accounting with require_settled=False.
    Dispatch always requires every previously admitted request to be settled.
    """
    expected = {job["job_id"]: job for job in plan["jobs"]}
    protocol = CalibrationProtocol.model_validate(read(root / "approved-protocol.json"))
    seen = set()
    for model, ledger in accounting.items():
        with ledger._connection() as db:
            rows = list(db.execute("SELECT * FROM requests"))
        for row in rows:
            job = expected.get(row["attempt_id"])
            if job is None or job["model"] != model or row["request_hash"] != job["request_sha256"]:
                raise ValueError("Ledger request is outside the frozen plan")
            seen.add(job["job_id"])
            prefix = root / "requests" / job["job_id"]
            result_path = prefix.with_suffix(".result.json")
            if row["state"] != "settled":
                if require_settled:
                    raise BudgetError(
                        "An interrupted request requires review; it cannot be retried"
                    )
                if result_path.exists():
                    result = read(result_path)
                    if (
                        result["request_id"] != row["id"]
                        or result["request_sha256"] != row["request_hash"]
                        or result["settled"]
                        or result["cost_usd"] is not None
                        or Decimal(result["reservation_usd"]) != Decimal(row["reserved_usd"])
                        or result["normalized"] != {"status": "error", "preference": None}
                    ):
                        raise ValueError(
                            "Unsettled evidence must remain an error with unknown cost"
                        )
                continue
            if not result_path.exists():
                raise BudgetError("Settled accounting is missing retained result evidence")
            result = read(result_path)
            request = read(prefix.with_suffix(".request.json"))
            envelope = read(prefix.with_suffix(".response.json"))
            raw = strict_json(prefix.with_suffix(".raw").read_bytes())
            if (
                request["body"] != job["body"]
                or request["request_id"] != row["id"]
                or request["request_sha256"] != row["request_hash"]
                or Decimal(request["reservation_usd"]) != Decimal(row["reserved_usd"])
                or envelope["response"] != raw
            ):
                raise ValueError("Retained request or raw response evidence changed")
            usage = verified_usage(raw, job)
            handling = judge_response(envelope, protocol.rubric)
            normalized = normalize_response(handling.text, job["order"], protocol.rubric)
            if handling.error is not None:
                normalized = {"status": "error", "preference": None}
            cost = Decimal(row["known_cost_usd"])
            observation = result["observation"]
            if (
                result["request_id"] != row["id"]
                or result["request_sha256"] != row["request_hash"]
                or not result["settled"]
                or Decimal(result["cost_usd"]) != cost
                or Decimal(result["reservation_usd"]) != Decimal(row["reserved_usd"])
                or result["returned_model"] != model
                or result["provider_usage"] != usage
                or result["response_handling"] != handling.metadata()
                or result["normalized"] != normalized
                or usage["input_tokens"] != row["input_tokens"]
                or usage["output_tokens"] != row["output_tokens"]
                or ledger.config.cost(usage["input_tokens"], usage["output_tokens"]) != cost
                or observation["response"] != handling.text
                or observation["error"] != handling.error
                or observation["cost_usd"] != float(cost)
                or observation["reservation_usd"] != float(Decimal(row["reserved_usd"]))
            ):
                raise ValueError("Retained result differs from its provider response or accounting")
    for path in (root / "requests").glob("*.result.json"):
        if path.name.removesuffix(".result.json") not in seen:
            raise ValueError("A result lacks its admitted accounting record")


def stop_once(root: Path, reason: str, job: dict[str, Any] | None = None) -> None:
    marker = root / "STOP"
    if not marker.exists():
        with contextlib.suppress(FileExistsError):
            save_once(
                marker,
                {
                    "reason": reason,
                    "job_id": job["job_id"] if job else None,
                    "item_id": job["item_id"] if job else None,
                    "policy": "No automatic retry, rewriting, or routing of an admitted request.",
                },
            )


def run(root: Path, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY must come from the private environment")
    with process_lock(root) as descriptor:
        if (root / "STOP").exists() or (root / "PAUSE").exists():
            raise RuntimeError("A retained stop or pause blocks dispatch")
        plan = load_plan(root)
        accounting = ledgers(root, plan)
        gate(accounting, allow_owned=False)
        verify_prefix(root, plan, accounting)
        protocol = CalibrationProtocol.model_validate(read(root / "approved-protocol.json"))
        output = root / "requests"
        output.mkdir(exist_ok=True, mode=0o700)
        stop = threading.Event()
        admission = threading.Lock()
        failures = []

        def execute(job: dict[str, Any]) -> None:
            model = job["model"]
            ledger = accounting[model]
            request_id = None
            settled = False
            reserved = Decimal("0")
            start = time.monotonic()
            handling = None
            usage = None
            raw = None
            response_bytes = bytearray()
            try:
                with admission:
                    verify_lock(root, descriptor)
                    if stop.is_set() or (root / "STOP").exists() or (root / "PAUSE").exists():
                        stop.set()
                        return
                    gate(accounting, allow_owned=True)
                    if digest(job["body"]) != job["request_sha256"]:
                        raise ValueError("Frozen request changed")
                    ledger.register_attempt(
                        job["job_id"], digest([job["job_id"], job["request_sha256"]])
                    )
                    ceiling = job["body"]["max_output_tokens"]
                    request_id = ledger.reserve(
                        job["job_id"], job["request_sha256"], job["input_ceiling"], ceiling
                    )
                    reserved = ledger.config.cost(job["input_ceiling"], ceiling)
                    save_once(
                        output / (job["job_id"] + ".request.json"),
                        {
                            "request_id": request_id,
                            "request_sha256": job["request_sha256"],
                            "body": job["body"],
                            "reservation_usd": str(reserved),
                            "sent_at": datetime.now(UTC).isoformat(),
                        },
                    )
                start = time.monotonic()
                with (
                    httpx.Client(
                        timeout=httpx.Timeout(180, connect=20),
                        follow_redirects=False,
                        trust_env=False,
                        transport=transport,
                    ) as client,
                    client.stream(
                        "POST",
                        ENDPOINT,
                        headers={
                            "Authorization": "Bearer " + key,
                            "Content-Type": "application/json",
                        },
                        content=canonical(job["body"]),
                    ) as response,
                ):
                    response_bytes = bytearray()
                    for chunk in response.iter_bytes():
                        response_bytes.extend(chunk)
                        if len(response_bytes) > MAX_RESPONSE_BYTES:
                            raise ValueError(
                                "Provider response exceeds the retained evidence limit"
                            )
                    http_status = response.status_code
                    provider_request_id = response.headers.get("x-request-id")
                retained = bytes(response_bytes).replace(key.encode(), b"[REDACTED]")
                raw_path = output / (job["job_id"] + ".raw")
                with raw_path.open("xb") as stream:
                    raw_path.chmod(0o600)
                    stream.write(retained)
                    stream.flush()
                    os.fsync(stream.fileno())
                raw = strict_json(retained)
                if not isinstance(raw, dict):
                    raise ValueError("Provider response must be an object")
                handling = judge_response(
                    {"http_status": http_status, "response": raw}, protocol.rubric
                )
                with admission:
                    if preserve_policy_stop(root, handling, job):
                        stop.set()
                save_once(
                    output / (job["job_id"] + ".response.json"),
                    {
                        "http_status": http_status,
                        "provider_request_id": provider_request_id,
                        "response": raw,
                        "response_handling": handling.metadata(),
                    },
                )
                usage = verified_usage(raw, job)
                cost = ledger.settle(request_id, usage["input_tokens"], usage["output_tokens"])
                settled = True
                normalized = normalize_response(handling.text, job["order"], protocol.rubric)
                if handling.error is not None:
                    normalized = {"status": "error", "preference": None}
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
                save_once(
                    output / (job["job_id"] + ".result.json"),
                    {
                        "job_id": job["job_id"],
                        "request_id": request_id,
                        "request_sha256": job["request_sha256"],
                        "settled": True,
                        "cost_usd": str(cost),
                        "reservation_usd": str(reserved),
                        "returned_model": raw["model"],
                        "provider_usage": usage,
                        "response_handling": handling.metadata(),
                        "normalized": normalized,
                        "observation": observation,
                    },
                )
                if http_status != 200:
                    with admission:
                        stop.set()
                        stop_once(root, "provider_http_error", job)
            except BaseException as error:
                partial = output / (job["job_id"] + ".raw.partial")
                if response_bytes and not (output / (job["job_id"] + ".raw")).exists():
                    with partial.open("xb") as stream:
                        partial.chmod(0o600)
                        stream.write(
                            bytes(response_bytes[:MAX_RESPONSE_BYTES]).replace(
                                key.encode(), b"[REDACTED]"
                            )
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                with admission:
                    stop.set()
                    if request_id is not None and not settled:
                        ledger.uncertain(request_id, type(error).__name__)
                    stop_once(root, type(error).__name__, job)
                    failures.append({"job_id": job["job_id"], "error_class": type(error).__name__})
                if request_id is not None and not settled:
                    save_once(
                        output / (job["job_id"] + ".result.json"),
                        {
                            "job_id": job["job_id"],
                            "request_id": request_id,
                            "request_sha256": job["request_sha256"],
                            "settled": False,
                            "cost_usd": None,
                            "reservation_usd": str(reserved),
                            "returned_model": raw.get("model") if isinstance(raw, dict) else None,
                            "provider_usage": usage,
                            "response_handling": handling.metadata() if handling else None,
                            "normalized": {"status": "error", "preference": None},
                            "observation": {
                                "job_id": job["job_id"],
                                "response": None,
                                "error": "Retained dispatch failure: " + type(error).__name__,
                                "cost_usd": None,
                                "reservation_usd": float(reserved),
                                "duration_ms": (time.monotonic() - start) * 1000,
                            },
                        },
                    )

        old_handlers = {}

        def pause(signum: int, frame: Any) -> None:
            stop.set()
            if not (root / "PAUSE").exists():
                save_once(
                    root / "PAUSE",
                    {"signal": signum, "policy": "Drain admitted requests; admit no next job."},
                )

        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                old_handlers[sig] = signal.signal(sig, pause)
        completed = {
            path.name.removesuffix(".result.json") for path in output.glob("*.result.json")
        }
        jobs = iter(job for job in plan["jobs"] if job["job_id"] not in completed)
        try:
            with ThreadPoolExecutor(max_workers=MAX_INFLIGHT) as pool:
                active = set()
                for _ in range(MAX_INFLIGHT):
                    job = next(jobs, None)
                    if job is not None:
                        active.add(pool.submit(execute, job))
                while active:
                    done, active = wait(active, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                        if not stop.is_set():
                            job = next(jobs, None)
                            if job is not None:
                                active.add(pool.submit(execute, job))
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        status = summary(accounting)
        status["stopped"] = stop.is_set()
        status["failures"] = failures
        status["planned_calls"] = len(plan["jobs"])
        status["retained_results"] = len(list(output.glob("*.result.json")))
        save_once(root / ("status-" + str(time.time_ns()) + ".json"), status)
        if failures:
            raise RuntimeError("Confirmation stopped; evidence and reservations are retained")
        return status


def collect(root: Path) -> dict[str, Any]:
    """Import retained study judgments into the actual product campaign."""
    plan = load_plan(root)
    protocol = CalibrationProtocol.model_validate(read(root / "approved-protocol.json"))
    service = JudgeCalibration(Settings(root / "workspace"))
    campaign = service.create(protocol)
    service.release_holdout(
        campaign["id"],
        [judge.id for judge in protocol.judges],
        "All configurations and order assignments were fixed before any confirmation calls.",
    )
    rows = []
    for job in plan["jobs"]:
        path = root / "requests" / (job["job_id"] + ".result.json")
        if path.exists():
            rows.append(read(path)["observation"])
    if rows:
        service.import_observations(campaign["id"], rows)
    return {
        "campaign_id": campaign["id"],
        "study_planned_calls": len(plan["jobs"]),
        "product_full_grid_calls": campaign["planned_judgments"],
        "deliberately_unscheduled_mirror_jobs": campaign["planned_judgments"] - len(plan["jobs"]),
        "imported_study_results": len(rows),
        "scope": "Inspect the product campaign; use the confirmatory analyzer for the frozen family-level decision.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("command", choices=["freeze", "run", "status", "collect"])
    args = parser.parse_args()
    if args.command == "freeze":
        plan = freeze(args.root)
        result = {"plan_sha256": digest(plan), "planned_calls": len(plan["jobs"])}
    elif args.command == "run":
        result = run(args.root)
    elif args.command == "collect":
        result = collect(args.root)
    else:
        result = summary(ledgers(args.root, load_plan(args.root)))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
