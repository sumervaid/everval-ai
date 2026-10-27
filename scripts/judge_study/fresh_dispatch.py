"""Prospective safeguards for the frozen fresh-repair experiment.

The adapter preserves controls, prompts, token limits and prices. It proves
prefix completeness and validates each response before admitting the next job.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.judge_study import fresh_identity
from scripts.judge_study import fresh_repositories as study

_ORIGINAL_LOADS = json.loads
RAW_LIMIT = 2 * 1024 * 1024


class DuplicateKey(ValueError):
    """A JSON object contains an ambiguous repeated key."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON constant")


def strict_loads(value: str | bytes | bytearray, **kwargs: Any) -> Any:
    if kwargs.get("object_pairs_hook") is not None:
        raise ValueError("Custom JSON object hooks are unsupported")
    kwargs["object_pairs_hook"] = _unique_pairs
    kwargs["parse_constant"] = _reject_constant
    return _ORIGINAL_LOADS(value, **kwargs)


def adapter_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def save_once(path: Path, value: Any) -> None:
    if path.exists():
        if study.read(path) != value:
            raise ValueError("Immutable adapter evidence changed")
    else:
        study.private_json(path, value)


def prefix_proof(root: Path) -> dict[str, Any]:
    plan = study.load(root)
    prefix = []
    selected = []
    for entry in plan["tasks"]:
        path = root / "controls" / entry["instance_id"] / "controls.json"
        if not path.exists():
            raise ValueError("Incomplete ordered prefix before the fiftieth admission")
        control = study.read(path)
        if (
            control["instance_id"] != entry["instance_id"]
            or control["task_sha256"] != entry["task_sha256"]
            or control["selection_sha256"] != study.digest(plan)
            or control["runner_sha256"] != plan["source_sha256"]
        ):
            raise ValueError("Prefix control is not bound to the frozen selection")
        prefix.append(
            {"instance_id": entry["instance_id"], "control_sha256": study.digest(control)}
        )
        if control["valid"]:
            selected.append(entry["instance_id"])
        if len(selected) == 50:
            break
    if len(selected) != 50:
        raise ValueError("Fifty strict admissions are required")
    return {
        "selection_sha256": study.digest(plan),
        "prefix": prefix,
        "selected_task_ids": selected,
        "no_earlier_missing_controls": True,
    }


def freeze(root: Path) -> None:
    proof = prefix_proof(root)
    save_once(root / "admission-prefix.json", proof)
    if not (root / "inference.json").exists():
        with fresh_identity.policy(root):
            study.freeze_inference(root)
    inference = study.inference_plan(root)
    actual = [entry["instance_id"] for entry in inference["tasks"]]
    if actual != proof["selected_task_ids"]:
        raise ValueError("Inference selection differs from the complete prefix")
    plan = study.load(root)
    quarantine = plan["amendment"].get("provider_policy_quarantine", [])
    excluded = sorted(row["instance_id"] for row in quarantine)
    if set(actual) & set(excluded):
        raise ValueError("A quarantined source entered the cohort")
    manifest = {
        "schema_version": 1,
        "adapter_sha256": adapter_sha256(),
        "control_runner_sha256": inference["source_sha256"],
        "identity_policy": plan["amendment"].get("identity_policy"),
        "inference_sha256": study.digest(inference),
        "prefix_sha256": study.digest(proof),
        "job_ids": [job["id"] for job in inference["jobs"]],
        "initial_quarantined_task_ids": excluded,
        "global_quarantine_directory": str(root.parent.parent / "provider-quarantine"),
        "dispatch_concurrency": 1,
        "api_ceiling_usd": "12",
        "modal_ceiling_usd": "0",
        "response_policy": (
            "One job per process with a process-level lock; validate every response "
            "before the next admission. Reject duplicate provider and patch JSON keys. "
            "Retain bounded redacted transport bytes. Unknown accounting or provider "
            "errors stop admissions; policy/refusal also quarantines the source globally."
        ),
    }
    save_once(root / "dispatch-freeze.json", manifest)
    save_once(root / "dispatch-freeze-sha256.json", {"sha256": study.digest(manifest)})


def verified_plan(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = study.read(root / "dispatch-freeze.json")
    if (
        study.digest(manifest) != study.read(root / "dispatch-freeze-sha256.json")["sha256"]
        or manifest["adapter_sha256"] != adapter_sha256()
    ):
        raise ValueError("Dispatch adapter or manifest changed")
    inference = study.inference_plan(root)
    selection = study.load(root)
    if inference["selection_sha256"] != study.digest(selection) or (
        selection.get("amendment") or {}
    ).get("identity_policy") != manifest.get("identity_policy"):
        raise ValueError("Frozen selection or identity policy changed")
    if study.digest(inference) != manifest["inference_sha256"]:
        raise ValueError("Frozen request manifest changed")
    proof = study.read(root / "admission-prefix.json")
    if study.digest(proof) != manifest["prefix_sha256"]:
        raise ValueError("Admission prefix proof changed")
    if [entry["instance_id"] for entry in inference["tasks"]] != proof["selected_task_ids"]:
        raise ValueError("Frozen task selection differs from prefix proof")
    return inference, manifest


def quarantine_path(directory: Path, task_id: str) -> Path:
    return directory / (hashlib.sha256(task_id.encode()).hexdigest() + ".json")


def stop(root: Path, reason: str, job: dict[str, Any], *, policy: bool = False) -> None:
    record = {
        "reason": reason,
        "job_id": job["id"],
        "instance_id": job["instance_id"],
        "policy_quarantine": policy,
        "created_at": datetime.now(UTC).isoformat(),
        "adapter_sha256": adapter_sha256(),
    }
    if policy:
        target = quarantine_path(root.parent.parent / "provider-quarantine", job["instance_id"])
        if not target.exists():
            study.private_json(target, record)
    if not (root / "dispatch-STOP.json").exists():
        study.private_json(root / "dispatch-STOP.json", record)


def gate(
    root: Path, inference: dict[str, Any], manifest: dict[str, Any], job: dict[str, Any]
) -> None:
    from scripts.benchmarks.budget import BudgetLedger

    if (root / "dispatch-STOP.json").exists() or (root / "pause-requested").exists():
        raise ValueError("Shared dispatch stop or pause is active")
    if (
        job["instance_id"] in manifest["initial_quarantined_task_ids"]
        or quarantine_path(
            Path(manifest["global_quarantine_directory"]), job["instance_id"]
        ).exists()
    ):
        raise ValueError("Source is quarantined from all provider calls")
    for model in study.PROFILES:
        state = BudgetLedger(
            root / "accounting" / (model + ".sqlite3"),
            study.budget_config(inference, model),
            create=False,
        ).status()
        if state["admissions_blocked"]:
            raise ValueError("A model ledger blocks global dispatch")


def response_problem(status: int, response: Any, model: str) -> tuple[str | None, bool]:
    if not isinstance(response, dict):
        return "Malformed provider envelope", False
    error = response.get("error")
    policy = bool(
        isinstance(error, dict)
        and any(
            token in str(error.get(field, "")).lower()
            for field in ("code", "type", "message")
            for token in (
                "content_policy",
                "content policy",
                "content_filter",
                "invalid_prompt",
                "safety",
            )
        )
    )
    output = response.get("output")
    if isinstance(output, list):
        for message in output:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(item, dict) and item.get("type") == "refusal" for item in content
            ):
                policy = True
    details = response.get("incomplete_details")
    if isinstance(details, dict) and details.get("reason") == "content_filter":
        policy = True
    if policy:
        return "Provider policy rejection or refusal", True
    if status != 200 or error:
        return "Provider error", False
    usage = response.get("usage")
    if (
        not isinstance(usage, dict)
        or type(usage.get("input_tokens")) is not int
        or type(usage.get("output_tokens")) is not int
        or usage["input_tokens"] < 0
        or usage["output_tokens"] < 0
    ):
        return "Unknown provider accounting", False
    if response.get("model") != model or response.get("service_tier", "default") not in {
        None,
        "default",
    }:
        return "Unknown model identity or pricing tier", False
    if response.get("status") not in {"completed", "incomplete"}:
        return "Unknown provider completion state", False
    return None, False


@contextlib.contextmanager
def process_lock(root: Path):
    descriptor = os.open(
        root / "dispatch.lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    finally:
        os.close(descriptor)


def verify_lease(root: Path, descriptor: int) -> None:
    actual = os.fstat(descriptor)
    expected = (root / "dispatch.lock").stat(follow_symlinks=False)
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("Child did not inherit the dispatch lock")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextlib.contextmanager
def recorded_transport(output: Path, key: str):
    """Retain bounded redacted bytes even when the provider JSON is ambiguous."""
    import httpx

    original = httpx.Client.stream

    @contextlib.contextmanager
    def stream(client, method, url, *args, **kwargs):
        captured = bytearray()
        status = None
        received = 0
        try:
            with original(client, method, url, *args, **kwargs) as response:
                status = response.status_code

                class Observed:
                    status_code = status

                    def iter_bytes(self):
                        nonlocal received
                        for chunk in response.iter_bytes():
                            received += len(chunk)
                            captured.extend(chunk[: max(0, RAW_LIMIT - len(captured))])
                            yield chunk

                yield Observed()
        finally:
            redacted = bytes(captured).replace(key.encode(), b"[REDACTED]")
            path = output / "transport-response.bin"
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as handle:
                path.chmod(0o600)
                handle.write(redacted)
                handle.flush()
                os.fsync(handle.fileno())
            study.private_json(
                output / "transport-response.json",
                {
                    "http_status": status,
                    "received_bytes": received,
                    "truncated": received > RAW_LIMIT,
                    "redacted_bytes_sha256": hashlib.sha256(redacted).hexdigest(),
                    "adapter_sha256": adapter_sha256(),
                },
            )

    httpx.Client.stream = stream
    try:
        yield
    finally:
        httpx.Client.stream = original


def retained_response(output: Path) -> tuple[int, Any]:
    metadata = strict_loads((output / "transport-response.json").read_bytes())
    raw = (output / "transport-response.bin").read_bytes()
    if (
        metadata["truncated"]
        or metadata["redacted_bytes_sha256"] != hashlib.sha256(raw).hexdigest()
        or type(metadata["http_status"]) is not int
    ):
        raise ValueError("Incomplete or altered transport evidence")
    return metadata["http_status"], strict_loads(raw)


def validate_result(root: Path, job: dict[str, Any]) -> None:
    output = root / "requests" / job["id"]
    status, response = retained_response(output)
    reason, policy = response_problem(status, response, job["model"])
    if reason is not None:
        stop(root, reason, job, policy=policy)
        raise ValueError(reason)
    request = strict_loads((output / "request.json").read_bytes())
    result = strict_loads((output / "result.json").read_bytes())
    envelope = strict_loads((output / "response.json").read_bytes())
    if (
        study.digest(request["body"]) != job["body_sha256"]
        or envelope != {"http_status": status, "response": response}
        or result["instance_id"] != job["instance_id"]
        or result["model"] != job["model"]
        or result["returned_generator_model"] != response["model"]
        or result["provider_usage"] != response["usage"]
    ):
        raise ValueError("Request, response, identity or accounting binding changed")
    expected_patch = None
    inner_status = "no_valid_completed_patch"
    text = study.select_final_text(response)
    if response["status"] == "completed" and text is not None:
        try:
            parsed = strict_loads(text)
            if (
                isinstance(parsed, dict)
                and set(parsed) == {"patch"}
                and isinstance(parsed["patch"], str)
            ):
                expected_patch = parsed["patch"]
                inner_status = "valid_patch_json"
        except DuplicateKey:
            inner_status = "duplicate_patch_json_keys"
        except (TypeError, ValueError):
            inner_status = "invalid_patch_json"
    if result["patch"] != expected_patch:
        raise ValueError("Stored patch differs from strict final-message interpretation")
    save_once(
        output / "dispatch-validation.json",
        {
            "job_id": job["id"],
            "body_sha256": job["body_sha256"],
            "result_sha256": study.digest(result),
            "transport_sha256": hashlib.sha256(
                (output / "transport-response.bin").read_bytes()
            ).hexdigest(),
            "inner_status": inner_status,
            "adapter_sha256": adapter_sha256(),
        },
    )


@contextlib.contextmanager
def graceful_interrupt(root: Path):
    def pause(signum, frame):
        (root / "pause-requested").write_text("Finish the current request; admit no next job.\n")

    previous = signal.signal(signal.SIGINT, pause)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def one(root: Path, identifier: str, descriptor: int) -> None:
    verify_lease(root, descriptor)
    inference, manifest = verified_plan(root)
    matches = [job for job in inference["jobs"] if job["id"] == identifier]
    if len(matches) != 1:
        raise ValueError("Child must select exactly one frozen job")
    job = matches[0]
    gate(root, inference, manifest, job)
    output = root / "requests" / identifier
    if (output / "request.json").exists():
        raise ValueError("A recorded request cannot be dispatched again")
    original_plan = study.inference_plan
    original_budget_config = study.budget_config

    def full_campaign_config(filtered_plan, model):
        # Filtering dispatch must never alter the full-manifest financial identity.
        return original_budget_config(inference, model)

    def only_job(path):
        checked = original_plan(path)
        if study.digest(checked) != manifest["inference_sha256"]:
            raise ValueError("Child frozen plan changed")
        return {**checked, "jobs": [job]}

    study.inference_plan = only_job
    study.budget_config = full_campaign_config
    json.loads = strict_loads
    try:
        with graceful_interrupt(root), recorded_transport(output, os.environ["OPENAI_API_KEY"]):
            study.run_inference(root, job["model"])
        validate_result(root, job)
    except BaseException as error:
        reason, policy = type(error).__name__, False
        try:
            status, response = retained_response(output)
            found, policy = response_problem(status, response, job["model"])
            reason = found or reason
        except (OSError, TypeError, ValueError, KeyError):
            pass
        stop(root, reason, job, policy=policy)
        raise RuntimeError("Fresh dispatch stopped; evidence and reservations retained") from None
    finally:
        study.inference_plan = original_plan
        study.budget_config = original_budget_config
        json.loads = _ORIGINAL_LOADS


def dispatch(root: Path) -> None:
    with graceful_interrupt(root), process_lock(root) as descriptor:
        inference, manifest = verified_plan(root)
        for job in inference["jobs"]:
            if (root / "pause-requested").exists():
                return
            gate(root, inference, manifest, job)
            output = root / "requests" / job["id"]
            if (output / "result.json").exists():
                validate_result(root, job)
                continue
            if (output / "request.json").exists():
                stop(root, "Interrupted request requires explicit accounting review", job)
                raise ValueError("Request already registered; automatic retry prohibited")
            log = root / "dispatch-logs" / (job["id"] + ".log")
            log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with log.open("xb") as handle:
                    log.chmod(0o600)
                    child = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "scripts.judge_study.fresh_dispatch",
                            "one",
                            "--root",
                            str(root),
                            "--job",
                            job["id"],
                            "--lease-fd",
                            str(descriptor),
                        ],
                        cwd=study.ROOT,
                        pass_fds=(descriptor,),
                        stdin=subprocess.DEVNULL,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        timeout=240,
                        check=False,
                    )
                if child.returncode != 0:
                    raise RuntimeError("Child request stopped")
                validate_result(root, job)
                gate(root, inference, manifest, job)
            except BaseException as error:
                stop(root, type(error).__name__, job)
                raise RuntimeError(
                    "Global fresh dispatch stopped; no further jobs admitted"
                ) from None
            print(
                json.dumps({"job_id": job["id"], "model": job["model"], "validated": True}),
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "dispatch", "one"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job")
    parser.add_argument("--lease-fd", type=int)
    args = parser.parse_args()
    root = study.private_root(args.root)
    if args.command == "freeze":
        freeze(root)
    elif args.command == "dispatch":
        dispatch(root)
    else:
        if args.job is None or args.lease_fd is None:
            parser.error("one requires --job and --lease-fd")
        one(root, args.job, args.lease_fd)


if __name__ == "__main__":
    main()
