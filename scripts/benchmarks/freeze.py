"""Freeze local task, runtime, control and spending evidence before a live pilot.

This command makes no model calls. Every selected task needs a terminal
preparation result and retained controls or an explicit exclusion.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .budget import _decimal, _exact, _tokens
from .campaign import budget_config, read_json, sha256, verify_plan_data
from .model import canonical, private_json

PRICING_VERSION = "openai-standard-short-context-2026-10-15-input-cache-write-upper-bound"


def validate_prior_accounting(path: Path, opening_spend_usd: Decimal) -> str:
    """Bind opening spend to complete, exact token reconciliation evidence."""
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Prior accounting evidence exceeds its size bound")

    def reject_constant(value: str) -> None:
        raise ValueError("Nonfinite numbers are not valid accounting evidence")

    evidence = json.loads(raw, parse_float=Decimal, parse_constant=reject_constant)
    if (
        not isinstance(evidence, dict)
        or evidence.get("status") != "passed"
        or evidence.get("all_usage_known") is not True
    ):
        raise ValueError("Prior accounting must be passed and have complete known usage")

    def amount(value: object, name: str, *, zero: bool = True) -> Decimal:
        if type(value) is int:
            value = Decimal(value)
        return _decimal(value, name, zero=zero, fractional_places=24)

    models = evidence.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= 256:
        raise ValueError("Prior accounting needs a bounded, nonempty model reconciliation")
    seen = set()
    calls = 0
    with _exact():
        recomputed = Decimal("0")
        for model in models:
            if not isinstance(model, dict):
                raise ValueError("Every prior model needs explicit accounting")
            identity = model.get("model")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError("Prior model identities must be present and unique")
            seen.add(identity)
            if not isinstance(model.get("pricing_version"), str) or not model["pricing_version"]:
                raise ValueError("Prior model pricing evidence is missing")
            model_calls = _tokens(model.get("calls"), "calls")
            inputs = _tokens(model.get("input_tokens"), "input_tokens")
            outputs = _tokens(model.get("output_tokens"), "output_tokens")
            if model_calls == 0 and (inputs or outputs):
                raise ValueError("Prior token usage has no corresponding provider calls")
            input_rate = amount(model.get("input_cost_per_million"), "input_rate", zero=False)
            output_rate = amount(model.get("output_cost_per_million"), "output_rate", zero=False)
            calculated = (inputs * input_rate + outputs * output_rate) / Decimal("1000000")
            if calculated != amount(model.get("recomputed_cost_usd"), "model_cost"):
                raise ValueError("Prior model cost does not match its recorded tokens and rates")
            recomputed += calculated
            calls += model_calls
        admissions = _tokens(evidence.get("provider_admissions"), "provider_admissions")
        recorded = amount(evidence.get("recomputed_cost_usd"), "recomputed_cost_usd")
        known = amount(evidence.get("known_provider_cost_usd"), "known_provider_cost_usd")
        tolerance = amount(evidence.get("tolerance_usd"), "tolerance_usd", zero=False)
        difference = amount(evidence.get("absolute_difference_usd"), "absolute_difference_usd")
        if (
            calls != admissions
            or recomputed != recorded
            or recorded != opening_spend_usd
            or known > opening_spend_usd
            or tolerance > Decimal("0.000000001")
            or abs(recomputed - known) > tolerance
            or difference != abs(recomputed - known)
        ):
            raise ValueError("Opening spend does not match the reconciled prior accounting")
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def preparation_lock(root: Path) -> Iterator[None]:
    """Share preparation's local lock through evidence reads and plan creation."""
    try:
        descriptor = os.open(
            root / ".preparation.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EISDIR, errno.ENXIO}:
            raise ValueError("Preparation lock must be a regular file, not a symlink") from error
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Preparation lock must be a regular file, not a symlink")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Another preparation or freezing process owns this directory"
            ) from error
        yield
    finally:
        os.close(descriptor)


def freeze(
    output_dir: Path,
    *,
    opening_spend_usd: Decimal,
    prior_accounting: Path | None = None,
) -> Path:
    root = output_dir.expanduser().resolve()
    checkout = Path(__file__).resolve().parents[2]
    if root.is_relative_to(checkout):
        raise ValueError("Campaign artifacts must be outside the Git checkout")
    with preparation_lock(root):
        return _freeze_locked(
            root,
            checkout,
            opening_spend_usd=opening_spend_usd,
            prior_accounting=prior_accounting,
        )


def _freeze_locked(
    root: Path,
    checkout: Path,
    *,
    opening_spend_usd: Decimal,
    prior_accounting: Path | None,
) -> Path:
    _decimal(opening_spend_usd, "opening_spend_usd", zero=True)
    if opening_spend_usd and prior_accounting is None:
        raise ValueError("Nonzero opening spend requires its retained accounting evidence")
    prior_digest = (
        validate_prior_accounting(prior_accounting, opening_spend_usd)
        if prior_accounting is not None
        else None
    )
    if (root / "preparation-blocked.json").exists():
        raise ValueError("Unconfirmed Docker preparation cleanup blocks campaign freezing")
    selection_path = root / "selection/selection-public.json"
    selection = read_json(selection_path)
    index = read_json(root / "selection/selection-private-index.json")
    if index["selection_sha256"] != sha256(selection_path):
        raise ValueError("Private task index does not match the frozen public selection")
    images = read_json(root / "runtime-images.json")
    private_tasks = {item["instance_id"]: item for item in index["tasks"]}
    if len(private_tasks) != len(index["tasks"]):
        raise ValueError("Duplicate task identities in private index")
    selected = selection["tasks"]
    if set(private_tasks) != {item["instance_id"] for item in selected}:
        raise ValueError("Private task index differs from the declared selection")
    tasks = []
    for item in selected:
        identity = item["instance_id"]
        private = private_tasks[identity]
        task_path = Path(private["task_path"]).resolve()
        if not task_path.is_relative_to(root):
            raise ValueError("Private task artifacts must stay in the campaign directory")
        digest = sha256(task_path)
        if digest != private["task_sha256"] or digest != item["task_sha256"]:
            raise ValueError("Task bytes differ from the frozen selection")
        if private["split"] != item["split"]:
            raise ValueError("Task partition differs from the frozen selection")
        prepared = images.get(identity, {})
        if prepared.get("cleanup_complete") is False:
            raise ValueError("Unconfirmed Docker preparation cleanup blocks campaign freezing")
        if prepared.get("preparation_status") not in {"ready", "failed"}:
            raise ValueError("Every selected task needs a terminal preparation result")
        control_path = root / "controls" / identity / "controls.json"
        control = read_json(control_path)
        if control.get("instance_id") != identity or type(control.get("valid")) is not bool:
            raise ValueError("Every selected task needs explicit, identified control evidence")
        admitted = prepared["preparation_status"] == "ready" and control["valid"]
        if control["valid"] and prepared["preparation_status"] != "ready":
            raise ValueError("Passing controls cannot refer to failed preparation")
        exclusion = None
        if not admitted:
            exclusion = (
                "preparation_failed"
                if prepared["preparation_status"] == "failed"
                else "control_contract_failed"
            )
        tasks.append(
            {
                "instance_id": identity,
                "split": item["split"],
                "task_path": str(task_path),
                "task_sha256": digest,
                "image_id": prepared.get("image_id"),
                "control_path": str(control_path),
                "control_sha256": sha256(control_path),
                "admitted": admitted,
                "exclusion_reason": exclusion,
            }
        )
    sources = {
        str(path.relative_to(checkout)): sha256(path)
        for path in sorted((checkout / "scripts/benchmarks").glob("*.py"))
    }
    for relative in ("src/cvals/providers.py", "src/cvals/models.py"):
        sources[relative] = sha256(checkout / relative)
    plan = {
        "schema_version": 1,
        "campaign_id": selection["campaign"],
        "frozen_at": datetime.now(UTC).isoformat(),
        "selection_path": str(selection_path),
        "selection_sha256": sha256(selection_path),
        "runtime_manifest_sha256": sha256(root / "runtime-images.json"),
        "prior_accounting_sha256": prior_digest,
        "tasks": tasks,
        "source_sha256": sources,
        "model": {
            "model_name": "gpt-6.1-sol",
            "reasoning_effort": "medium",
            "max_completion_tokens": 8192,
        },
        "execution": {
            "step_limit": 250,
            "cost_limit": 6.0,
            "wall_time_limit_seconds": 1800,
            "command_timeout": 60,
        },
        "grading_timeout_seconds": 600,
        "budget": {
            "pricing_version": PRICING_VERSION,
            "opening_spend_usd": str(opening_spend_usd),
            "user_ceiling_usd": "200",
            "admission_ceiling_usd": "190",
            "attempt_cap_usd": "6",
            "input_rate_per_million": "2.50",
            "output_rate_per_million": "10",
        },
    }
    budget_config(plan, hashlib.sha256(canonical(plan)).hexdigest())
    path = root / "campaign.json"
    # Never replace an existing plan or ledger: changed methods require a new run.
    if path.exists() or (root / "budget.sqlite3").exists():
        raise ValueError("Campaign is already frozen; never reset its spending ledger")
    verify_plan_data(plan)
    private_json(path, plan)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--opening-spend-usd", type=Decimal, default=Decimal("0"))
    parser.add_argument("--prior-accounting", type=Path)
    args = parser.parse_args()
    path = freeze(
        args.output_dir,
        opening_spend_usd=args.opening_spend_usd,
        prior_accounting=args.prior_accounting,
    )
    plan = read_json(path)
    print(
        json.dumps(
            {
                "plan": str(path),
                "tasks": len(plan["tasks"]),
                "admitted_tasks": sum(item["admitted"] for item in plan["tasks"]),
                "paid_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
