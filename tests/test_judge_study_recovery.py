"""A rejected study item stays quarantined under explicit bounded continuation."""

from __future__ import annotations

import json

import httpx
import pytest
from test_judge_study import driver, mock_client, study


def _stopped_study(tmp_path, monkeypatch):
    from scripts.benchmarks.model import private_json
    from scripts.judge_study import parallel, recovery

    root = study(tmp_path)
    (root / "requests").mkdir()
    parallel.amendment(root, create=True)
    calls = []

    def good(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "service_tier": "default",
                "status": "completed",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"preference":"A","rationale":"Correct."}',
                            }
                        ],
                    }
                ],
            },
        )

    mock_client(monkeypatch, good)
    driver.run(root, partition="dev")
    driver.release(root)
    plan = driver.load_plan(root)
    failed = next(
        j
        for j in plan["jobs"]
        if j["partition"] == "holdout"
        and j["cohort"] == "human"
        and j["body"]["model"] == "gpt-6-luna"
    )
    ledger = driver.ledgers(root, plan)["gpt-6-luna"]
    attempt_hash = driver.digest({"job": failed["job_id"], "request": failed["request_sha256"]})
    ledger.register_attempt(failed["job_id"], attempt_hash)
    request = ledger.reserve(
        failed["job_id"],
        failed["request_sha256"],
        failed["input_ceiling"],
        driver.PROFILES["gpt-6-luna"]["output_tokens"],
    )
    reserved = ledger.config.cost(
        failed["input_ceiling"], driver.PROFILES["gpt-6-luna"]["output_tokens"]
    )
    ledger.uncertain(request, "ValueError")
    private_json(
        root / "requests" / (failed["job_id"] + ".request.json"),
        {
            "body": failed["body"],
            "request_id": request,
            "request_sha256": failed["request_sha256"],
            "reserved_upper_bound_usd": str(reserved),
        },
    )
    private_json(
        root / "requests" / (failed["job_id"] + ".response.json"),
        {
            "http_status": 400,
            "response": {
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_prompt",
                    "message": "Policy rejection.",
                }
            },
        },
    )
    monkeypatch.setattr(recovery, "REJECTED_JOB", failed["job_id"])
    monkeypatch.setattr(recovery, "REJECTED_REQUEST", request)
    monkeypatch.setattr(recovery, "POLICY_ITEM", failed["item_id"])
    monkeypatch.setattr(recovery, "RESERVATION", reserved)
    return root, failed, plan, request, reserved, calls


def test_policy_continuation_only_dispatches_untouched_unrelated_jobs(tmp_path, monkeypatch):
    from scripts.judge_study import recovery

    root, failed, plan, request, reserved, calls = _stopped_study(tmp_path, monkeypatch)
    before = len(calls)
    frozen = recovery.freeze(root)
    assert len(frozen["quarantined_unattempted_jobs"]) == 11
    assert len(frozen["allowed_jobs"]) == 12
    recovery.run(root, "holdout")
    recovery.run(root, "holdout")
    assert len(calls) - before == 12
    for job in plan["jobs"]:
        if job["item_id"] == failed["item_id"] and job["job_id"] != failed["job_id"]:
            assert not (root / "requests" / (job["job_id"] + ".request.json")).exists()
    state = recovery.accounting(root, plan, recovery.load(root))["gpt-6-luna"].status()
    assert state["reviewed_uncertain_requests"] == 1
    assert state["pending_requests"] == 0
    assert state["bounded_reviewed_reservation_usd"] == str(reserved)
    assert next(r for r in state["requests"] if r["id"] == request)["state"] == "uncertain"
    rejected = driver.read(root / "requests" / (failed["job_id"] + ".result.json"))["observation"]
    assert rejected["cost_usd"] is None and rejected["input_tokens"] is None
    changed = driver.read(root / "policy-continuation.json")
    changed["allowed_jobs"].append(failed["job_id"])
    (root / "policy-continuation.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="continuation changed"):
        recovery.load(root)


@pytest.mark.parametrize(
    "field", ["request_id", "request_sha256", "body", "reserved_upper_bound_usd"]
)
def test_policy_review_rejects_dispatched_request_mismatch(tmp_path, monkeypatch, field):
    from scripts.judge_study import recovery

    root, failed, *_ = _stopped_study(tmp_path, monkeypatch)
    path = root / "requests" / (failed["job_id"] + ".request.json")
    data = driver.read(path)
    data[field] = {"model": "changed"} if field == "body" else "1"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="dispatched request differs"):
        recovery.freeze(root)
    assert not (root / "policy-continuation.json").exists()
