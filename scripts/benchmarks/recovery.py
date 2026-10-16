"""Explicit, evidence-bound continuation with permanently reserved unknown usage.

This module never reconstructs missing tokens, retries a model request, or changes
a frozen solver configuration. Each reviewed uncertainty still consumes its full
reservation and remains incomplete accounting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from .budget import BudgetError
from .model import canonical

KIND = "bounded-unknown-usage-v1"
REVIEW_KIND = "bounded-output-limit-review-v1"
OVERRIDES = frozenset(
    {
        "scripts/benchmarks/budget.py",
        "scripts/benchmarks/campaign.py",
        "scripts/benchmarks/report.py",
        "scripts/benchmarks/recovery.py",
    }
)
OUTPUT_LIMIT_MESSAGE = (
    "Could not finish the message because max_tokens or model output limit was reached. "
    "Please try again with higher max_tokens."
)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _file(path: Path, *, maximum: int = 64 * 1024 * 1024) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise BudgetError("Recovery evidence must be a bounded regular file")
    return path.read_bytes()


def _json(path: Path) -> dict:
    data = json.loads(_file(path, maximum=16 * 1024 * 1024))
    if not isinstance(data, dict):
        raise BudgetError("Recovery evidence must be a JSON object")
    return data


def _sha(path: Path) -> str:
    return hashlib.sha256(_file(path)).hexdigest()


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or len(relative) > 2048:
        raise BudgetError("Invalid recovery evidence path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise BudgetError("Recovery evidence must remain inside its campaign")
    current = root
    for part in parts.parts:
        current = current / part
        if current.is_symlink():
            raise BudgetError("Recovery evidence cannot traverse symlinks")
    return current


def _write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _ledger_identity(root: Path) -> dict:
    path = root / "budget.sqlite3"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise BudgetError("Recovery requires the original regular ledger")
    return {"canonical_path": str(path), "device": info.st_dev, "inode": info.st_ino}


def _database(root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    path = root / "budget.sqlite3"
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BudgetError("An existing regular ledger is required")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        events = [dict(r) for r in connection.execute("SELECT * FROM events ORDER BY sequence")]
        requests = [dict(r) for r in connection.execute("SELECT * FROM requests ORDER BY id")]
        attempts = [dict(r) for r in connection.execute("SELECT * FROM attempts ORDER BY id")]
    if [r["sequence"] for r in events] != list(range(1, len(events) + 1)):
        raise BudgetError("Recovery requires a complete ordered event history")
    return events, requests, attempts


def _check_prefix(events: list[dict], prefix: dict) -> None:
    count = prefix.get("count")
    if type(count) is not int or count < 1 or count > len(events):
        raise BudgetError("Invalid recovery event prefix")
    if _digest(events[:count]) != prefix.get("sha256"):
        raise BudgetError("Retained ledger event prefix changed")


def _check_files(root: Path, evidence: dict) -> None:
    if not isinstance(evidence, dict) or not evidence:
        raise BudgetError("Recovery requires retained artifact bindings")
    for relative, expected in evidence.items():
        if _sha(_inside(root, relative)) != expected:
            raise BudgetError("Retained recovery artifact changed: " + relative)


def _expected(plan: dict, plan_hash: str) -> dict[str, str]:
    from .campaign import _expected_attempts

    return {identity: record[2] for identity, record in _expected_attempts(plan, plan_hash).items()}


def _manifest(
    amendment: Path, plan_path: Path, plan: dict
) -> tuple[dict, str, Path, list[dict], list[dict]]:
    root = plan_path.parent.resolve()
    amendment = _inside(
        root, (amendment.parent.resolve() / amendment.name).relative_to(root).as_posix()
    )
    manifest = _json(amendment)
    plan_hash = _digest(plan)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != KIND
        or manifest.get("original_plan_sha256") != _sha(plan_path)
        or manifest.get("original_plan_canonical_sha256") != plan_hash
        or _json(plan_path) != plan
        or manifest.get("campaign_root") != str(root)
        or manifest.get("ledger_identity") != _ledger_identity(root)
    ):
        raise BudgetError("Recovery amendment does not bind the original frozen plan")
    overrides = manifest.get("source_overrides")
    if not isinstance(overrides, dict) or set(overrides) != OVERRIDES:
        raise BudgetError("Recovery may change only the declared accounting and reporting sources")
    checkout = Path(__file__).resolve().parents[2]
    for relative, binding in overrides.items():
        before = plan["source_sha256"].get(relative)
        if (
            not isinstance(binding, dict)
            or set(binding) != {"before", "after"}
            or binding["before"] != before
            or (before is None) != (relative == "scripts/benchmarks/recovery.py")
            or _sha(_inside(checkout, relative)) != binding["after"]
        ):
            raise BudgetError("Recovery source binding differs from the reviewed amendment")
    evidence = manifest.get("retained_evidence")
    _check_files(root, evidence)
    retained = manifest.get("retained_attempts")
    if not isinstance(retained, dict) or not retained:
        raise BudgetError("Recovery must preserve all previously started attempts")
    expected = _expected(plan, plan_hash)
    for identity, attempt_hash in retained.items():
        if expected.get(identity) != attempt_hash:
            raise BudgetError("Retained recovery attempt differs from the original plan")
        for name in ("started.json", "result.json"):
            if f"attempts/{identity}/{name}" not in evidence:
                raise BudgetError("Retained attempts need immutable start and result evidence")
    allowed = manifest.get("allowed_attempts")
    expected_allowed = [
        {"attempt_id": identity, "attempt_hash": value}
        for identity, value in sorted(expected.items())
        if identity not in retained
    ]
    if allowed != expected_allowed or not allowed:
        raise BudgetError(
            "Recovery allowlist must contain exactly the originally unstarted attempts"
        )
    bound_paths = set(evidence)
    actual_paths = set()
    for identity in retained:
        directory = _inside(root, "attempts/" + identity)
        for file in directory.rglob("*"):
            if file.is_symlink() or (not file.is_dir() and not file.is_file()):
                raise BudgetError("Retained attempt artifact type changed")
            if file.is_file():
                actual_paths.add(file.relative_to(root).as_posix())
    if actual_paths != bound_paths:
        raise BudgetError("Retained attempt artifact set changed")
    events, requests, _ = _database(root)
    _check_prefix(events, manifest.get("original_event_prefix", {}))
    old_count = manifest["original_event_prefix"]["count"]
    registered = {
        row["attempt_id"] for row in events[:old_count] if row["kind"] == "attempt_registered"
    }
    if registered != set(retained):
        raise BudgetError("Recovery omitted a previously registered attempt")
    amendment_hash = _sha(amendment)
    for allowed_attempt in allowed:
        directory = root / "attempts" / allowed_attempt["attempt_id"]
        for name in ("started.json", "result.json"):
            path = directory / name
            if path.exists() or path.is_symlink():
                retained_record = _json(_inside(root, path.relative_to(root).as_posix()))
                if retained_record.get("accounting_amendment_sha256") != amendment_hash:
                    raise BudgetError("Continuation attempt omitted its recovery provenance")
    return manifest, amendment_hash, root, events, requests


def _review_proof(root: Path, plan: dict, row: dict, events: list[dict]) -> dict:
    from .budget import BudgetLedger
    from .campaign import _expected_attempts, _reconcile_request, budget_config, validate_result

    if (
        row["state"] != "uncertain"
        or row["reason"] != "UnknownUsage"
        or row["input_tokens"] is not None
        or row["output_tokens"] is not None
        or Decimal(row["known_cost_usd"]) != 0
    ):
        raise BudgetError("This recovery supports only retained output-limit errors with no usage")
    identity = row["attempt_id"]
    plan_hash = _digest(plan)
    expected = _expected_attempts(plan, plan_hash)
    if identity not in expected:
        raise BudgetError("Unknown reviewed attempt")
    item, arm, attempt_hash = expected[identity]
    base = f"attempts/{identity}"
    paths = [
        base + "/started.json",
        base + "/result.json",
        base + "/provider/" + row["id"] + ".request.json",
        base + "/provider/" + row["id"] + ".response.json",
        base + "/solver/sandbox/container-removal.json",
        base + "/solver/sandbox/container-ownership.json",
    ]
    evidence = {name: _sha(_inside(root, name)) for name in paths}
    result = _json(_inside(root, paths[1]))
    validate_result(result, item, arm, attempt_hash)
    if (
        result.get("status") != "UnknownUsage"
        or result.get("cleanup_confirmed") is not True
        or result["submitted"] is not False
        or result["resolved"] is not False
        or result.get("grade") is not None
        or result.get("patch", "") != ""
    ):
        raise BudgetError(
            "Only terminal unsubmitted attempts with confirmed cleanup can be reviewed"
        )
    marker = _json(_inside(root, paths[0]))
    if (
        marker.get("attempt_id") != identity
        or marker.get("attempt_hash") != attempt_hash
        or marker.get("plan_sha256") != plan_hash
    ):
        raise BudgetError("Reviewed attempt start differs from the frozen plan")
    removal = _json(_inside(root, paths[4]))
    ownership = _json(_inside(root, paths[5]))
    if (
        removal.get("confirmed") is not True
        or type(removal.get("returncode")) is not int
        or removal.get("returncode") != 0
        or ownership.get("image_id") != item["image_id"]
        or re.fullmatch(r"everval-benchmark-[0-9a-f]{32}", str(ownership.get("container_name", "")))
        is None
        or str(removal.get("output", "")).strip() != ownership.get("container_name")
    ):
        raise BudgetError("Reviewed solver removal is not confirmed")
    response = _json(_inside(root, paths[3]))
    body = response.get("response")
    error = body.get("error") if isinstance(body, dict) else None
    if (
        response.get("http_status") != 400
        or not isinstance(body, dict)
        or "usage" in body
        or not isinstance(error, dict)
        or error.get("type") != "invalid_request_error"
        or error.get("message") != OUTPUT_LIMIT_MESSAGE
    ):
        raise BudgetError("Only the inspected HTTP 400 output-limit error is eligible")
    ledger = BudgetLedger(root / "budget.sqlite3", budget_config(plan, plan_hash), create=False)
    _reconcile_request(row, root / base / "provider", plan, ledger)
    return {
        "request": {
            k: row[k]
            for k in (
                "id",
                "attempt_id",
                "request_hash",
                "state",
                "input_ceiling",
                "output_ceiling",
                "reserved_usd",
                "known_cost_usd",
                "input_tokens",
                "output_tokens",
                "reason",
                "created_at",
                "updated_at",
            )
        },
        "attempt_hash": attempt_hash,
        "evidence": evidence,
        "event_prefix": {"count": len(events), "sha256": _digest(events)},
    }


def _validate_reviews(
    amendment: Path,
    plan: dict,
    manifest_hash: str,
    root: Path,
    events: list[dict],
    requests: list[dict],
) -> None:
    rows = {row["id"]: row for row in requests}
    seen = set()
    for event in events:
        if event["kind"] != "bounded_unknown_reviewed":
            continue
        request_id = event["request_id"]
        if request_id in seen or request_id not in rows:
            raise BudgetError("Duplicate or unbound recovery review event")
        seen.add(request_id)
        path = _inside(
            root,
            (amendment.parent / "decisions" / (request_id + ".json")).relative_to(root).as_posix(),
        )
        decision = _json(path)
        data = json.loads(event["data"])
        proof = decision.get("proof", {})
        if (
            decision.get("schema_version") != 1
            or decision.get("kind") != REVIEW_KIND
            or decision.get("amendment_sha256") != manifest_hash
            or decision.get("request_id") != request_id
            or data.get("decision_sha256") != _sha(path)
            or data.get("evidence_sha256") != _digest(proof)
            or data.get("amendment_sha256") != manifest_hash
        ):
            raise BudgetError("Recovery review event does not match its immutable decision")
        prefix = proof.get("event_prefix", {})
        if prefix.get("count") != event["sequence"] - 1:
            raise BudgetError("Recovery review is not anchored to its preceding ledger history")
        _check_prefix(events, prefix)
        fresh = _review_proof(root, plan, rows[request_id], events[: prefix["count"]])
        if fresh != proof:
            raise BudgetError("Reviewed uncertainty evidence changed")


def load_context(amendment: Path, plan_path: Path, plan: dict):
    from .budget import RecoveryContext

    amendment = amendment.parent.resolve() / amendment.name
    manifest, digest, root, events, requests = _manifest(amendment, plan_path, plan)
    _validate_reviews(amendment, plan, digest, root, events, requests)
    return RecoveryContext(
        plan_sha256=_digest(plan),
        amendment_sha256=digest,
        allowed_attempts=tuple(
            (row["attempt_id"], row["attempt_hash"]) for row in manifest["allowed_attempts"]
        ),
    )


def public_recovery_metadata(amendment: Path, plan_path: Path) -> dict:
    plan = _json(plan_path)
    manifest, digest, _, _, _ = _manifest(amendment, plan_path, plan)
    return {
        "schema_version": 1,
        "kind": KIND,
        "amendment_sha256": digest,
        "original_plan_sha256": manifest["original_plan_sha256"],
        "original_plan_canonical_sha256": manifest["original_plan_canonical_sha256"],
        "source_overrides": manifest["source_overrides"],
        "allowed_attempts": manifest["allowed_attempts"],
        "original_event_prefix": manifest["original_event_prefix"],
        "retained_attempt_count": len(manifest["retained_attempts"]),
    }


def create_amendment(plan_path: Path, amendment: Path) -> dict:
    from .budget import BudgetLedger
    from .campaign import (
        _attempt_directories,
        _expected_attempts,
        budget_config,
        campaign_lock,
        reconcile_accounting,
        verify_plan_data,
    )

    plan_path = plan_path.resolve()
    root = plan_path.parent
    amendment = _inside(
        root, (amendment.parent.resolve() / amendment.name).relative_to(root).as_posix()
    )
    plan = _json(plan_path)
    plan_hash = _digest(plan)
    checkout = Path(__file__).resolve().parents[2]
    overrides = {
        name: {"before": plan["source_sha256"].get(name), "after": _sha(checkout / name)}
        for name in sorted(OVERRIDES)
    }
    with campaign_lock(root):
        expected = _expected_attempts(plan, plan_hash)
        directories = _attempt_directories(root, expected)
        ledger = BudgetLedger(root / "budget.sqlite3", budget_config(plan, plan_hash), create=False)
        status = reconcile_accounting(root, plan, plan_hash, ledger)
        if status["pending_requests"] or not status["uncertain_requests"]:
            raise BudgetError("An idle campaign with retained uncertain usage is required")
        events, _, attempts = _database(root)
        if any(row["kind"] == "bounded_unknown_reviewed" for row in events):
            raise BudgetError("An existing recovery amendment must be reused")
        if set(directories) != {row["id"] for row in attempts}:
            raise BudgetError("Every registered attempt needs retained evidence")
        evidence = {}
        retained = {}
        for identity, directory in sorted(directories.items()):
            result = _json(directory / "result.json")
            if result.get("cleanup_confirmed") is not True:
                raise BudgetError("All prior solver cleanup must be confirmed before amendment")
            grade = result.get("grade")
            if grade is not None and grade.get("cleanup_confirmed") is not True:
                raise BudgetError("All prior grader cleanup must be confirmed before amendment")
            retained[identity] = expected[identity][2]
            for file in sorted(directory.rglob("*")):
                if file.is_symlink():
                    raise BudgetError("Retained attempt evidence cannot include symlinks")
                if file.is_file():
                    relative = file.relative_to(root).as_posix()
                    evidence[relative] = _sha(file)
        manifest = {
            "schema_version": 1,
            "kind": KIND,
            "created_at": datetime.now(UTC).isoformat(),
            "reason": "Continue only unstarted attempts after explicit review; unknown usage remains reserved.",
            "original_plan_sha256": _sha(plan_path),
            "original_plan_canonical_sha256": plan_hash,
            "source_overrides": overrides,
            "campaign_root": str(root),
            "ledger_identity": _ledger_identity(root),
            "retained_evidence": evidence,
            "retained_attempts": retained,
            "allowed_attempts": [
                {"attempt_id": identity, "attempt_hash": value}
                for identity, value in sorted(_expected(plan, plan_hash).items())
                if identity not in retained
            ],
            "original_event_prefix": {"count": len(events), "sha256": _digest(events)},
        }
        _write_new(amendment, manifest)
        # No ledger changes have occurred. An invalid amendment cannot admit calls.
        verify_plan_data(plan, amendment=amendment, campaign_root=root, plan_path=plan_path)
        load_context(amendment, plan_path, plan)
    return public_recovery_metadata(amendment, plan_path)


def review_request(plan_path: Path, amendment: Path, request_id: str) -> dict:
    from .campaign import (
        campaign_lock,
        open_campaign_ledger,
        reconcile_accounting,
        verify_plan,
    )

    plan_path = plan_path.resolve()
    root = plan_path.parent
    amendment = _inside(
        root, (amendment.parent.resolve() / amendment.name).relative_to(root).as_posix()
    )
    with campaign_lock(root):
        plan, plan_hash = verify_plan(plan_path, amendment=amendment)
        context = load_context(amendment, plan_path, plan)
        ledger = open_campaign_ledger(root, plan, plan_hash, create=False, recovery_context=context)
        status = reconcile_accounting(root, plan, plan_hash, ledger)
        if status["pending_requests"]:
            raise BudgetError("Pending requests must not be reviewed as terminal usage")
        events, rows, _ = _database(root)
        matches = [row for row in rows if row["id"] == request_id]
        if len(matches) != 1:
            raise BudgetError("Unknown request identity")
        row = matches[0]
        path = _inside(
            root,
            (amendment.parent / "decisions" / (request_id + ".json")).relative_to(root).as_posix(),
        )
        existing = [
            r
            for r in events
            if r["kind"] == "bounded_unknown_reviewed" and r["request_id"] == request_id
        ]
        if existing:
            # load_context already verified this immutable decision and event.
            return {"reviewed": True, "already_recorded": True, "request_id": request_id}
        proof = _review_proof(root, plan, row, events)
        decision = {
            "schema_version": 1,
            "kind": REVIEW_KIND,
            "recorded_at": datetime.now(UTC).isoformat(),
            "amendment_sha256": context.amendment_sha256,
            "request_id": request_id,
            "decision": "Retain full reservation and quarantine the interrupted attempt.",
            "proof": proof,
        }
        if path.exists():
            retained = _json(path)
            if any(retained.get(k) != v for k, v in decision.items() if k != "recorded_at"):
                raise BudgetError(
                    "Existing uncommitted review decision differs from current evidence"
                )
        else:
            _write_new(path, decision)
        sequence = ledger.record_bounded_review(
            request_id,
            request_hash=row["request_hash"],
            reserved_usd=Decimal(row["reserved_usd"]),
            decision_sha256=_sha(path),
            evidence_sha256=_digest(proof),
            event_prefix_count=proof["event_prefix"]["count"],
            event_prefix_sha256=proof["event_prefix"]["sha256"],
        )
        load_context(amendment, plan_path, plan)
        status = reconcile_accounting(root, plan, plan_hash, ledger)
        return {
            "reviewed": True,
            "request_id": request_id,
            "event_sequence": sequence,
            "known_spend_usd": status["known_spend_usd"],
            "committed_spend_usd": status["committed_spend_usd"],
            "cost_complete": status["cost_complete"],
            "admissions_blocked": status["admissions_blocked"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--amendment", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create")
    review = commands.add_parser("review")
    review.add_argument("--request-id", required=True)
    commands.add_parser("inspect")
    args = parser.parse_args()
    os.umask(0o077)
    plan_path, amendment = args.plan.expanduser().resolve(), args.amendment.expanduser().absolute()
    if args.command == "create":
        result = create_amendment(plan_path, amendment)
    elif args.command == "review":
        result = review_request(plan_path, amendment, args.request_id)
    else:
        from .campaign import campaign_lock

        with campaign_lock(plan_path.parent):
            load_context(amendment, plan_path, _json(plan_path))
            result = public_recovery_metadata(amendment, plan_path)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
