"""Run a frozen, local public-repository pilot with cumulative paid-call accounting.

Preparation and controls are free of model calls. This entry point admits only
tasks with retained passing controls, verifies frozen inputs, and never retries
an attempt that has started. Use --execute to admit paid evaluation explicitly.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .budget import BudgetConfig, BudgetError, BudgetLedger
from .model import MeteredOpenAI, canonical, private_json

ARMS = ("baseline", "everval")
REQUIRED_SOURCES = {
    "scripts/benchmarks/budget.py",
    "scripts/benchmarks/model.py",
    "scripts/benchmarks/executor.py",
    "scripts/benchmarks/campaign.py",
}


def validate_controls_evidence(item: dict, task: dict, control: dict) -> None:
    """Bind admission to complete negative and positive controls for this task."""
    image = item["image_id"]
    identity = item["instance_id"]
    if control.get("valid") is not True:
        raise ValueError("Admitted task has no passing base/gold controls")
    if control.get("instance_id") != identity or control.get("image_id") != image:
        raise ValueError("Control identity or image does not match the task")
    ftp, ptp = task["FAIL_TO_PASS"], task["PASS_TO_PASS"]
    if not isinstance(ftp, list) or not isinstance(ptp, list) or not ftp or not ptp:
        raise ValueError("Controls need explicit nonempty official FTP and PTP tests")
    for group, tests in (("fail_to_pass_count", ftp), ("pass_to_pass_count", ptp)):
        if control.get(group) != len(tests):
            raise ValueError("Control test counts do not match the task")
    for name, patch in (("base", ""), ("gold", task["patch"])):
        cycle = control.get(name, {})
        if (
            cycle.get("instance_id") != identity
            or cycle.get("image_id") != image
            or cycle.get("status") != "graded"
            or cycle.get("all_required_tests_observed") is not True
            or cycle.get("resolved") is not (name == "gold")
            or cycle.get("patch_sha256") != hashlib.sha256(patch.encode()).hexdigest()
        ):
            raise ValueError("Incomplete or mismatched control cycle")
        if cycle.get("fail_to_pass_count") != len(ftp) or cycle.get("pass_to_pass_count") != len(
            ptp
        ):
            raise ValueError("Control cycle test counts do not match the task")
        statuses = cycle.get("test_statuses", {})
        for test in ftp:
            if statuses.get(test) != ("FAILED" if name == "base" else "PASSED"):
                raise ValueError("Official FAIL_TO_PASS control contract did not hold")
        if any(statuses.get(test) != "PASSED" for test in ptp):
            raise ValueError("Official PASS_TO_PASS control contract did not hold")


def validate_result(result: dict, item: dict, arm: str, attempt_hash: str) -> None:
    expected = {
        "instance_id": item["instance_id"],
        "split": item["split"],
        "arm": arm,
        "attempt_hash": attempt_hash,
    }
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("Retained attempt identity does not match the frozen campaign")
    if (
        not isinstance(result.get("status"), str)
        or not result["status"]
        or type(result.get("submitted")) is not bool
        or type(result.get("resolved")) is not bool
    ):
        raise ValueError("Retained attempt is not a terminal result")
    if result["resolved"] and (
        not result["submitted"]
        or not isinstance(result.get("grade"), dict)
        or result["grade"].get("resolved") is not True
    ):
        raise ValueError("A passing attempt requires a submitted, graded repair")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_plan(path: Path) -> tuple[dict[str, Any], str]:
    return verify_plan_data(read_json(path))


def verify_plan_data(plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported campaign schema")
    if not set(plan["model"]) <= {"model_name", "reasoning_effort", "max_completion_tokens"}:
        raise ValueError("Only public frozen model settings belong in a campaign plan")
    root = Path(__file__).resolve().parents[2]
    if not plan["source_sha256"].keys() >= REQUIRED_SOURCES:
        raise ValueError("Frozen campaign omits required execution sources")
    for relative, expected in plan["source_sha256"].items():
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or sha256(source) != expected:
            raise ValueError("Campaign source differs from the frozen configuration: " + relative)
    selection_path = Path(plan["selection_path"])
    if sha256(selection_path) != plan["selection_sha256"]:
        raise ValueError("Frozen selection bytes changed")
    selection = read_json(selection_path)
    expected_tasks = {(item["instance_id"], item["split"]) for item in selection["tasks"]}
    selected_tasks = {(item["instance_id"], item["split"]) for item in plan["tasks"]}
    if not expected_tasks or selected_tasks != expected_tasks:
        raise ValueError("Campaign task set differs from the frozen selection")
    seen = set()
    for item in plan["tasks"]:
        if (
            not isinstance(item["instance_id"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", item["instance_id"]) is None
        ):
            raise ValueError("Task identity must be a safe bounded identifier")
        if type(item["admitted"]) is not bool:
            raise ValueError("Task admission must be explicit")
        if item["instance_id"] in seen:
            raise ValueError("Duplicate task identity")
        seen.add(item["instance_id"])
        if item["split"] not in {"development", "test"}:
            raise ValueError("Unsupported cohort partition")
        task_path = Path(item["task_path"])
        if sha256(task_path) != item["task_sha256"]:
            raise ValueError("Frozen task bytes changed")
        task = read_json(task_path)
        if task["instance_id"] != item["instance_id"]:
            raise ValueError("Task identity differs from the frozen manifest")
        control = read_json(Path(item["control_path"]))
        if sha256(Path(item["control_path"])) != item["control_sha256"]:
            raise ValueError("Frozen control evidence changed")
        if item["admitted"]:
            validate_controls_evidence(item, task, control)
            if re.fullmatch(r"sha256:[0-9a-f]{64}", str(item["image_id"])) is None:
                raise ValueError("Execution requires an immutable local image ID")
    return plan, hashlib.sha256(canonical(plan)).hexdigest()


def budget_config(plan: dict[str, Any], plan_hash: str) -> BudgetConfig:
    config = plan["budget"]
    return BudgetConfig(
        campaign_id=plan["campaign_id"],
        campaign_config_hash=plan_hash,
        pricing_version=config["pricing_version"],
        **{
            key: Decimal(config[key])
            for key in (
                "opening_spend_usd",
                "user_ceiling_usd",
                "admission_ceiling_usd",
                "attempt_cap_usd",
                "input_rate_per_million",
                "output_rate_per_million",
            )
        },
    )


def paired_order(instance_id: str) -> tuple[str, str]:
    """Counterbalance order without looking at an outcome or changing the task set."""
    return ARMS if hashlib.sha256(instance_id.encode()).digest()[0] % 2 == 0 else ARMS[::-1]


def attempt_identity(plan_hash: str, item: dict, arm: str) -> tuple[str, str]:
    attempt_id = item["instance_id"] + "." + arm
    attempt_hash = hashlib.sha256(
        canonical({"campaign": plan_hash, "task": item["instance_id"], "arm": arm, "repetition": 1})
    ).hexdigest()
    return attempt_id, attempt_hash


def cleanup_blocked(result: dict) -> bool:
    grade = result.get("grade")
    return (
        result.get("cleanup_confirmed") is False
        or result.get("status") == "cleanup_error"
        or result.get("error_type") == "SandboxCleanupFailure"
        or (
            isinstance(grade, dict)
            and (
                grade.get("cleanup_confirmed") is False
                or grade.get("status") == "cleanup_error"
                or grade.get("error_type") == "SandboxCleanupFailure"
            )
        )
    )


@contextmanager
def campaign_lock(root: Path) -> Iterator[None]:
    """Serialize admission and reporting against the same local evidence snapshot."""
    descriptor = os.open(
        root / "campaign.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BudgetError("Campaign lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetError(
                "Campaign is active; retry accounting review when it is idle"
            ) from None
        yield
    finally:
        os.close(descriptor)


def _evidence_json(path: Path) -> dict[str, Any]:
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > 16 * 1024 * 1024:
        raise BudgetError("Accounting evidence must be a bounded regular file")
    return read_json(path)


def _expected_attempts(plan: dict, plan_hash: str) -> dict[str, tuple[dict, str, str]]:
    return {
        attempt_identity(plan_hash, item, arm)[0]: (
            item,
            arm,
            attempt_identity(plan_hash, item, arm)[1],
        )
        for item in plan["tasks"]
        if item["admitted"]
        for arm in ARMS
    }


def _attempt_directories(root: Path, expected: dict) -> dict[str, Path]:
    directory = root / "attempts"
    if not directory.exists():
        if directory.is_symlink():
            raise BudgetError("Attempt evidence directory must not be a symlink")
        return {}
    if directory.is_symlink() or not directory.is_dir():
        raise BudgetError("Attempt evidence must be a directory")
    result = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_dir() or path.name not in expected:
            raise BudgetError("Unrecognized attempt evidence requires accounting recovery")
        result[path.name] = path
    return result


def _validate_start(marker: dict, attempt_id: str, attempt_hash: str, plan_hash: str) -> None:
    if (
        marker.get("attempt_id") != attempt_id
        or marker.get("attempt_hash") != attempt_hash
        or marker.get("plan_sha256") != plan_hash
    ):
        raise ValueError("Attempt start marker belongs to a different frozen run")


def _retain_interrupted(root: Path, plan: dict, plan_hash: str) -> None:
    expected = _expected_attempts(plan, plan_hash)
    for attempt_id, directory in _attempt_directories(root, expected).items():
        marker, result_path = directory / "started.json", directory / "result.json"
        item, arm, attempt_hash = expected[attempt_id]
        if marker.exists() or marker.is_symlink():
            _validate_start(_evidence_json(marker), attempt_id, attempt_hash, plan_hash)
            if not result_path.exists():
                # This can precede model construction/ledger registration. Retain the
                # interruption without fabricating a registration or zero-cost account.
                private_json(
                    result_path,
                    {
                        "instance_id": item["instance_id"],
                        "arm": arm,
                        "split": item["split"],
                        "status": "interrupted",
                        "cleanup_confirmed": False,
                        "accounting_recovery_required": True,
                        "submitted": False,
                        "resolved": False,
                        "attempt_hash": attempt_hash,
                        "grade": None,
                    },
                )
                raise RuntimeError(
                    "Interrupted attempt requires accounting recovery and confirmed sandbox cleanup"
                )
        if result_path.exists():
            result = _evidence_json(result_path)
            validate_result(result, item, arm, attempt_hash)
            if cleanup_blocked(result):
                raise RuntimeError("Unconfirmed sandbox cleanup blocks campaign resume")


def open_campaign_ledger(root: Path, plan: dict, plan_hash: str, *, create: bool) -> BudgetLedger:
    path = root / "budget.sqlite3"
    directories = _attempt_directories(root, _expected_attempts(plan, plan_hash))
    if not path.exists() and directories:
        raise BudgetError(
            "Accounting ledger is missing beside retained attempts; recovery is required"
        )
    return BudgetLedger(path, budget_config(plan, plan_hash), create=create)


def _valid_usage(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def _reconcile_request(row: dict, provider: Path, plan: dict, ledger: BudgetLedger) -> None:
    request_path = provider / (row["id"] + ".request.json")
    response_path = provider / (row["id"] + ".response.json")
    request = _evidence_json(request_path) if request_path.exists() else None
    response = _evidence_json(response_path) if response_path.exists() else None
    if row["state"] == "settled" and (request is None or response is None):
        raise BudgetError("Settled accounting lacks retained provider evidence")
    if response is not None and request is None:
        raise BudgetError("Provider response lacks its retained request")
    if request is not None:
        payload = request.get("payload")
        if (
            not isinstance(payload, dict)
            or request.get("request_id") != row["id"]
            or request.get("request_sha256") != row["request_hash"]
            or hashlib.sha256(canonical(payload)).hexdigest() != row["request_hash"]
        ):
            raise BudgetError("Provider request evidence differs from durable accounting")
        model = plan["model"]
        expected_settings = {
            "model": model["model_name"],
            "reasoning_effort": model["reasoning_effort"],
            "max_completion_tokens": model["max_completion_tokens"],
            "service_tier": "default",
            "stream": False,
            "store": False,
            "n": 1,
        }
        if (
            set(payload) != {*expected_settings, "messages"}
            or any(payload.get(key) != value for key, value in expected_settings.items())
            or row["input_ceiling"] != len(canonical(payload["messages"])) + 8192
            or row["output_ceiling"] != model["max_completion_tokens"]
        ):
            raise BudgetError("Provider request differs from the frozen model or reservation")
    reserved = ledger.config.cost(row["input_ceiling"], row["output_ceiling"])
    if Decimal(row["reserved_usd"]) != reserved:
        raise BudgetError("Provider reservation differs from frozen token pricing")
    if response is None:
        return
    body = response.get("response")
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    known = Decimal(row["known_cost_usd"])
    for field, value in zip(("input_tokens", "output_tokens"), tokens, strict=True):
        if row[field] is not None and (not _valid_usage(value) or row[field] != value):
            raise BudgetError("Provider usage evidence differs from durable accounting")
    evidence_cost = ledger.config.cost(
        tokens[0] if _valid_usage(tokens[0]) else 0,
        tokens[1] if _valid_usage(tokens[1]) else 0,
    )
    if evidence_cost > known:
        raise BudgetError("Provider evidence contains charges missing from durable accounting")
    if row["state"] == "settled" and (
        not all(_valid_usage(value) for value in tokens)
        or any(
            row[field] != value
            for field, value in zip(("input_tokens", "output_tokens"), tokens, strict=True)
        )
        or evidence_cost != known
        or body.get("model") != plan["model"]["model_name"]
        or body.get("service_tier", "default") not in {"default", None}
        or tokens[0] > row["input_ceiling"]
        or tokens[1] > row["output_ceiling"]
    ):
        raise BudgetError("Settled provider evidence is not fully reconciled")


def reconcile_accounting(root: Path, plan: dict, plan_hash: str, ledger: BudgetLedger) -> dict:
    """Verify retained evidence without replaying requests or reconstructing missing charges.

    Call while holding campaign_lock. A coherent rollback of every local copy cannot
    be detected here; this is reconciliation, not an externally anchored audit log.
    """
    status = ledger.status()
    expected = _expected_attempts(plan, plan_hash)
    directories = _attempt_directories(root, expected)
    attempts = {row["attempt_id"]: row for row in status["attempts"]}
    for attempt_id, record in attempts.items():
        if attempt_id not in expected or record["attempt_hash"] != expected[attempt_id][2]:
            raise BudgetError("Registered attempt differs from the frozen campaign")
    requests = {}
    for row in status["requests"]:
        if row["attempt_id"] not in attempts or re.fullmatch(r"[0-9a-f]{32}", row["id"]) is None:
            raise BudgetError("Unregistered provider accounting requires recovery")
        requests[row["id"]] = row
    for attempt_id, directory in directories.items():
        item, arm, attempt_hash = expected[attempt_id]
        if attempt_id not in attempts:
            raise BudgetError("Retained attempt has no matching accounting registration")
        marker = directory / "started.json"
        if marker.exists() or marker.is_symlink():
            _validate_start(_evidence_json(marker), attempt_id, attempt_hash, plan_hash)
        result_path = directory / "result.json"
        result = _evidence_json(result_path) if result_path.exists() else None
        if result is not None:
            validate_result(result, item, arm, attempt_hash)
        if (marker.exists() and result is None) or (
            result is not None
            and (
                result.get("accounting_recovery_required") is True
                or result.get("status") == "interrupted"
            )
        ):
            attempts[attempt_id]["recovery_required"] = True
            status.update(
                cost_complete=False,
                admissions_blocked=True,
                blocked_reason="interrupted_attempt_accounting",
            )
        provider = directory / "provider"
        if provider.is_symlink() or (provider.exists() and not provider.is_dir()):
            raise BudgetError("Provider evidence must be a private directory")
        if provider.exists():
            for path in provider.iterdir():
                match = re.fullmatch(r"([0-9a-f]{32})\.(request|response)\.json", path.name)
                if (
                    match is None
                    or path.is_symlink()
                    or not path.is_file()
                    or match[1] not in requests
                    or requests[match[1]]["attempt_id"] != attempt_id
                ):
                    raise BudgetError(
                        "Retained provider evidence is absent from durable accounting"
                    )
        own = [row for row in requests.values() if row["attempt_id"] == attempt_id]
        if result is not None:
            recorded = result.get("provider")
            if not isinstance(recorded, dict):
                # Only our synthetic recovery record can omit serialization. Ordinary
                # zero-call/error results still receive MeteredOpenAI.serialize().
                interrupted = (
                    "provider" not in result
                    and result.get("status") == "interrupted"
                    and result.get("accounting_recovery_required") is True
                    and result.get("cleanup_confirmed") is False
                    and result["submitted"] is False
                    and result["resolved"] is False
                )
                if not interrupted:
                    raise BudgetError("Retained attempt lacks a valid provider summary")
                continue
            settled = [row for row in own if row["state"] == "settled"]
            if (
                not _valid_usage(recorded.get("calls"))
                or recorded["calls"] != len(settled)
                or recorded.get("attempt_id") != attempt_id
            ):
                raise BudgetError("Retained provider call count differs from durable accounting")
            try:
                amount = Decimal(str(recorded["recorded_cost_usd"]))
            except (KeyError, ValueError, InvalidOperation):
                raise BudgetError("Retained provider cost is invalid") from None
            # Model serialization uses a float; exact token pricing is checked below.
            settled_cost = sum((Decimal(row["known_cost_usd"]) for row in settled), Decimal(0))
            if not amount.is_finite() or amount < 0 or abs(amount - settled_cost) > Decimal("1e-9"):
                raise BudgetError("Retained provider cost differs from durable accounting")
    for attempt_id, record in attempts.items():
        if not (root / "attempts" / attempt_id / "result.json").exists():
            # A registration itself proves work began. Missing start/result files do
            # not permit resetting that attempt or treating it as a free non-run.
            record["recovery_required"] = True
            status.update(
                cost_complete=False,
                admissions_blocked=True,
                blocked_reason="interrupted_attempt_accounting",
            )
    for row in requests.values():
        _reconcile_request(row, root / "attempts" / row["attempt_id"] / "provider", plan, ledger)
    status["evidence_reconciled"] = True
    return status


def run_phase(plan_path: Path, phase: str, *, execute: bool) -> dict[str, Any]:
    plan, plan_hash = verify_plan(plan_path)
    output = plan_path.parent
    with campaign_lock(output):
        if execute:
            _retain_interrupted(output, plan, plan_hash)
        return _run_phase_locked(output, plan, plan_hash, phase, execute=execute)


def _run_phase_locked(
    output: Path, plan: dict, plan_hash: str, phase: str, *, execute: bool
) -> dict:
    ledger = open_campaign_ledger(output, plan, plan_hash, create=True)
    status = reconcile_accounting(output, plan, plan_hash, ledger)
    selected = [item for item in plan["tasks"] if item["split"] == phase]
    if not execute:
        return {
            "mode": "plan",
            "phase": phase,
            "selected_tasks": len(selected),
            "admitted_tasks": sum(item["admitted"] for item in selected),
            "paid_requests": 0,
            "budget": status,
        }
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required before paid admissions")
    # Import only for explicit execution. Upstream tools must not load a global dotenv.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["MSWEA_SILENT_STARTUP"] = "1"
    from .executor import grade_patch, run_attempt

    if phase == "test":
        for item in plan["tasks"]:
            if item["split"] == "development" and item["admitted"]:
                for arm in ARMS:
                    attempt_id, attempt_hash = attempt_identity(plan_hash, item, arm)
                    prior = output / "attempts" / attempt_id / "result.json"
                    if not prior.exists():
                        raise ValueError(
                            "Finish the frozen development phase before test admission"
                        )
                    validate_result(read_json(prior), item, arm, attempt_hash)
    for item in selected:
        if not item["admitted"]:
            continue
        task = read_json(Path(item["task_path"]))
        for arm in paired_order(item["instance_id"]):
            if reconcile_accounting(output, plan, plan_hash, ledger)["admissions_blocked"]:
                raise RuntimeError("Campaign accounting or admission ceiling blocks further calls")
            attempt_id, attempt_hash = attempt_identity(plan_hash, item, arm)
            attempt_dir = output / "attempts" / attempt_id
            attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            result_path = attempt_dir / "result.json"
            if result_path.exists():
                validate_result(read_json(result_path), item, arm, attempt_hash)
                continue
            marker = attempt_dir / "started.json"
            private_json(
                marker,
                {
                    "attempt_id": attempt_id,
                    "attempt_hash": attempt_hash,
                    "plan_sha256": plan_hash,
                    "started_at": datetime.now(UTC).isoformat(),
                },
            )
            model = MeteredOpenAI(
                ledger=ledger,
                attempt_id=attempt_id,
                attempt_hash=attempt_hash,
                output_dir=attempt_dir / "provider",
                **plan["model"],
            )
            started = time.monotonic()
            try:
                result = run_attempt(
                    task=task,
                    image=item["image_id"],
                    model=model,
                    output_dir=attempt_dir / "solver",
                    arm=arm,
                    **plan["execution"],
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                result = {
                    "status": "executor_error",
                    "error_type": type(exc).__name__,
                    "submitted": False,
                    "patch": "",
                }
            finally:
                model.close()
            result.update(
                {
                    "instance_id": item["instance_id"],
                    "arm": arm,
                    "split": phase,
                    "attempt_hash": attempt_hash,
                    "duration_seconds": time.monotonic() - started,
                    "provider": model.serialize(),
                }
            )
            patch = result.get("patch", "")
            grade = None
            if result.get("submitted") is True and isinstance(patch, str) and patch:
                try:
                    grade = grade_patch(
                        task=task,
                        image=item["image_id"],
                        patch=patch,
                        output_dir=attempt_dir / "grade",
                        timeout=plan["grading_timeout_seconds"],
                    )
                except Exception as exc:
                    grade = {
                        "resolved": False,
                        "status": "grader_error",
                        "error_type": type(exc).__name__,
                    }
            result["grade"] = grade
            result["resolved"] = bool(grade and grade.get("resolved") is True)
            validate_result(result, item, arm, attempt_hash)
            private_json(result_path, result)
            status = reconcile_accounting(output, plan, plan_hash, ledger)
            print(
                json.dumps(
                    {
                        "event": "attempt_finished",
                        "instance_id": item["instance_id"],
                        "arm": arm,
                        "resolved": result["resolved"],
                        "status": result.get("status"),
                        "known_spend_usd": status["known_spend_usd"],
                        "pending_requests": status["pending_requests"],
                        "uncertain_requests": status["uncertain_requests"],
                    }
                ),
                flush=True,
            )
            if cleanup_blocked(result):
                raise RuntimeError("Unconfirmed sandbox cleanup stops further paid admissions")
            if status["admissions_blocked"]:
                raise RuntimeError("Unresolved accounting stops the campaign")
    return reconcile_accounting(output, plan, plan_hash, ledger)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "test"), default="development")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    status = run_phase(args.plan.expanduser().resolve(), args.phase, execute=args.execute)
    print(json.dumps(status, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
