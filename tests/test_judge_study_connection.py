"""A second reviewed failure narrows authority without changing prior evidence."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from test_judge_study_recovery import _stopped_study, driver


def test_second_continuation_retains_both_errors_and_only_runs_untouched_jobs(
    tmp_path, monkeypatch
):
    from scripts.benchmarks.model import private_json
    from scripts.judge_study import connection_recovery as second
    from scripts.judge_study import recovery

    root, _, plan, first_id, first_bound, calls = _stopped_study(tmp_path, monkeypatch)
    prior = recovery.freeze(root)
    ledger = recovery.accounting(root, plan, prior)["gpt-6-luna"]
    with ledger._connection() as db:
        original_review = dict(
            db.execute("SELECT * FROM events WHERE kind='bounded_unknown_reviewed'").fetchone()
        )
    failed = next(
        j
        for j in plan["jobs"]
        if j["job_id"] in prior["allowed_jobs"] and j["body"]["model"] == "gpt-6-luna"
    )
    ledger.register_attempt(
        failed["job_id"],
        driver.digest({"job": failed["job_id"], "request": failed["request_sha256"]}),
    )
    identifier = ledger.reserve(
        failed["job_id"],
        failed["request_sha256"],
        failed["input_ceiling"],
        driver.PROFILES["gpt-6-luna"]["output_tokens"],
    )
    bound = ledger.config.cost(
        failed["input_ceiling"], driver.PROFILES["gpt-6-luna"]["output_tokens"]
    )
    ledger.uncertain(identifier, "ConnectError")
    private_json(
        root / "requests" / (failed["job_id"] + ".request.json"),
        {
            "body": failed["body"],
            "request_id": identifier,
            "request_sha256": failed["request_sha256"],
            "reserved_upper_bound_usd": str(bound),
        },
    )
    private_json(
        root / "stopped-connection.json",
        [{"model": "gpt-6-luna", "job_id": failed["job_id"], "error_class": "ConnectError"}],
    )
    monkeypatch.setattr(second, "FAILED_JOB", failed["job_id"])
    monkeypatch.setattr(second, "FAILED_REQUEST", identifier)
    monkeypatch.setattr(second, "CONNECTION_RESERVATION", bound)
    before = len(calls)
    continued = second.freeze(root)
    assert len(continued["allowed_jobs"]) == 11
    assert continued["quarantined_unattempted_jobs"] == prior["quarantined_unattempted_jobs"]
    second.run(root, "holdout")
    second.run(root, "holdout")
    assert len(calls) - before == 11
    current = second.accounting(root, plan, second.load(root))["gpt-6-luna"]
    status = current.status()
    assert status["reviewed_uncertain_requests"] == 2
    assert status["unreviewed_uncertain_requests"] == 0
    assert Decimal(status["bounded_reviewed_reservation_usd"]) == first_bound + bound
    assert {r["id"] for r in status["requests"] if r["state"] == "uncertain"} == {
        first_id,
        identifier,
    }
    with current._connection() as db:
        retained = dict(
            db.execute(
                "SELECT * FROM events WHERE sequence=?", (original_review["sequence"],)
            ).fetchone()
        )
    assert retained == original_review
    with current._connection(write=True) as db:
        event = db.execute(
            "SELECT sequence,data FROM events WHERE kind='bounded_unknown_reviewed' AND request_id=?",
            (identifier,),
        ).fetchone()
        data = json.loads(event["data"])
        data["decision_sha256"] = "a" * 64
        db.execute(
            "UPDATE events SET data=? WHERE sequence=?", (json.dumps(data), event["sequence"])
        )
    with pytest.raises(ValueError, match="decision/evidence differs"):
        second.load(root)
