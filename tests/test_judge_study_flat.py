"""A flat exact review preserves three failures and narrows dispatch safely."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from test_judge_study_recovery import _stopped_study, driver

from scripts.benchmarks.model import private_json
from scripts.judge_study import connection_recovery as second
from scripts.judge_study import flat_recovery as flat
from scripts.judge_study import recovery


def stopped_third(tmp_path, monkeypatch):
    original_freeze = driver.freeze

    def expanded(root, *args, **kwargs):
        for cohort in ("human", "execution"):
            path = root / (cohort + "-protocol.json")
            protocol = json.loads(path.read_text())
            extra = copy.deepcopy(next(i for i in protocol["items"] if i["partition"] == "holdout"))
            for field in ("item_id", "task_id", "family_id"):
                extra[field] += "-extra"
            protocol["items"].append(extra)
            path.write_text(json.dumps(protocol))
        return original_freeze(root, *args, **kwargs)

    monkeypatch.setattr(driver, "freeze", expanded)
    root, _, plan, first_id, first_bound, calls = _stopped_study(tmp_path, monkeypatch)
    prior = recovery.freeze(root)
    ledger = recovery.accounting(root, plan, prior)["gpt-6-luna"]

    def uncertain(job, current, reason):
        current.register_attempt(
            job["job_id"], driver.digest({"job": job["job_id"], "request": job["request_sha256"]})
        )
        identifier = current.reserve(
            job["job_id"],
            job["request_sha256"],
            job["input_ceiling"],
            driver.PROFILES["gpt-6-luna"]["output_tokens"],
        )
        bound = current.config.cost(
            job["input_ceiling"], driver.PROFILES["gpt-6-luna"]["output_tokens"]
        )
        current.uncertain(identifier, reason)
        private_json(
            root / "requests" / (job["job_id"] + ".request.json"),
            {
                "body": job["body"],
                "request_id": identifier,
                "request_sha256": job["request_sha256"],
                "reserved_upper_bound_usd": str(bound),
            },
        )
        return identifier, bound

    connection_job = next(
        j
        for j in plan["jobs"]
        if j["job_id"] in prior["allowed_jobs"] and j["body"]["model"] == "gpt-6-luna"
    )
    connection_id, connection_bound = uncertain(connection_job, ledger, "ConnectError")
    private_json(
        root / "stopped-connection.json",
        [
            {
                "model": "gpt-6-luna",
                "job_id": connection_job["job_id"],
                "error_class": "ConnectError",
            }
        ],
    )
    monkeypatch.setattr(second, "FAILED_JOB", connection_job["job_id"])
    monkeypatch.setattr(second, "FAILED_REQUEST", connection_id)
    monkeypatch.setattr(second, "CONNECTION_RESERVATION", connection_bound)
    monkeypatch.setattr(flat, "CONNECTION_REQUEST", connection_id)
    continued = second.freeze(root)
    current = second.accounting(root, plan, continued)["gpt-6-luna"]
    failed = next(
        j
        for j in plan["jobs"]
        if j["job_id"] in continued["allowed_jobs"]
        and j["body"]["model"] == "gpt-6-luna"
        and j["cohort"] == "execution"
    )
    third_id, third_bound = uncertain(failed, current, "ValueError")
    private_json(
        root / "requests" / (failed["job_id"] + ".response.json"),
        {
            "http_status": 400,
            "response": {"error": {"type": "invalid_request_error", "code": "invalid_prompt"}},
        },
    )
    monkeypatch.setattr(flat, "FAILED_JOB", failed["job_id"])
    monkeypatch.setattr(flat, "FAILED_REQUEST", third_id)
    monkeypatch.setattr(flat, "POLICY_ITEM", failed["item_id"])
    monkeypatch.setattr(flat, "RESERVATION", third_bound)
    with current._connection() as db:
        old_events = [
            dict(x)
            for x in db.execute(
                "SELECT * FROM events WHERE kind='bounded_unknown_reviewed' ORDER BY sequence"
            )
        ]
    return root, plan, failed, old_events, first_bound + connection_bound + third_bound, calls


def test_three_exceptions_preserve_old_reviews_and_dispatch_only_untouched_jobs(
    tmp_path, monkeypatch
):
    root, plan, failed, old_events, total_reserve, calls = stopped_third(tmp_path, monkeypatch)
    before = len(calls)
    value = flat.freeze(root)
    assert value["allowed_jobs"]
    assert all(
        next(j for j in plan["jobs"] if j["job_id"] == identifier)["item_id"] != failed["item_id"]
        for identifier in value["allowed_jobs"]
    )
    flat.run(root, "holdout")
    flat.run(root, "holdout")
    assert len(calls) - before == len(value["allowed_jobs"])
    current = flat.accounting(root, plan, flat.load(root))["gpt-6-luna"]
    status = current.status()
    assert status["reviewed_uncertain_requests"] == 3
    assert status["unreviewed_uncertain_requests"] == 0
    assert Decimal(status["bounded_reviewed_reservation_usd"]) == total_reserve
    with current._connection() as db:
        retained = [
            dict(x)
            for x in db.execute(
                "SELECT * FROM events WHERE kind='bounded_unknown_reviewed' ORDER BY sequence"
            )
        ]
    assert retained[:2] == old_events
    assert len(retained) == 3
    event = retained[-1]
    data = json.loads(event["data"])
    data["decision_sha256"] = "a" * 64
    with current._connection(write=True) as db:
        db.execute(
            "UPDATE events SET data=? WHERE sequence=?", (json.dumps(data), event["sequence"])
        )
    with pytest.raises(ValueError, match="decision/evidence"):
        flat.load(root)


@pytest.mark.parametrize(
    "field", ["request_id", "request_sha256", "body", "reserved_upper_bound_usd"]
)
def test_third_exception_requires_exact_dispatched_request_binding(tmp_path, monkeypatch, field):
    root, _, failed, _, _, _ = stopped_third(tmp_path, monkeypatch)
    path = root / "requests" / (failed["job_id"] + ".request.json")
    value = driver.read(path)
    value[field] = {} if field == "body" else "different"
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, ArithmeticError), match="binding|ConversionSyntax"):
        flat.freeze(root)


def test_old_derived_error_cannot_be_rebaselined_in_flat_manifest(tmp_path, monkeypatch):
    root, _, _, _, _, _ = stopped_third(tmp_path, monkeypatch)
    prior = driver.read(root / "connection-continuation.json")
    path = root / "requests" / (prior["approved_request"]["attempt_id"] + ".result.json")
    value = driver.read(path)
    value["observation"]["error"] = "altered retrospective label"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Derived historical error"):
        flat.freeze(root)


def test_overlapping_prefixes_are_verified_in_one_file_read(tmp_path, monkeypatch):
    value = {"retained": True}
    path = tmp_path / "result.json"
    path.write_text(json.dumps(value))
    import hashlib

    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    original = Path.read_bytes
    reads = []

    def observed(self):
        if self == path:
            reads.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", observed)
    flat.verify_files(
        tmp_path,
        {"result.json": expected},
        {},
        additional={"result.json"},
        semantic_json_hashes={"result.json": driver.digest(value)},
    )
    assert reads == [path]
