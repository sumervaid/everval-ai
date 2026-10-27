"""No-network safety checks for the separately authorized preference transport."""

from __future__ import annotations

import fcntl
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from test_self_preference_plan import fixture_run

from scripts.judge_study import self_preference_plan as planner
from scripts.judge_study import self_preference_run as runner


def campaign(tmp_path, *, approve=True):
    fresh, root, _ = fixture_run(tmp_path)
    planner.prepare(fresh, root)
    dispatch = runner.freeze(root)
    if approve:
        planner.write_private(
            root / "dispatch-authorization.json",
            {
                "authorized": True,
                "dispatch_sha256": planner.digest(dispatch),
                "budget_cap_usd": "7.95",
            },
        )
    return root


def mock_client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        runner.httpx, "Client", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def response(request, *, refusal=False):
    body = json.loads(request.content)
    content = (
        [{"type": "refusal", "refusal": "Cannot answer."}]
        if refusal
        else [
            {
                "type": "output_text",
                "text": '{"preference":"A","rationale":"Meets the issue."}',
            }
        ]
    )
    return {
        "id": "response-test",
        "model": body["model"],
        "service_tier": "default",
        "status": "completed",
        "usage": {"input_tokens": 20, "output_tokens": 10},
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "I will compare the patches."}],
            },
            {"type": "message", "role": "assistant", "phase": "final_answer", "content": content},
        ],
    }


def test_full_grid_uses_final_phase_and_never_reissues(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=response(request))

    mock_client(monkeypatch, handle)
    state = runner.run(root)
    assert len(calls) == state["requests"] == 360
    assert state["unknown_requests"] == 0
    assert Decimal(state["committed_upper_bound_usd"]) < Decimal(".1")
    result = planner.read(next((root / "requests").glob("*/result.json")))
    assert result["normalized"]["status"] == "completed"
    assert result["response_handling"]["phase_selection"] == "explicit_final_answer"
    runner.run(root)
    assert len(calls) == 360


def test_preparation_is_not_dispatch_authorization(tmp_path, monkeypatch):
    root = campaign(tmp_path, approve=False)
    mock_client(monkeypatch, lambda _: pytest.fail("No call is authorized"))
    with pytest.raises(ValueError, match="not been authorized"):
        runner.run(root)
    assert not (root / "accounting").exists()


def test_unknown_usage_stops_every_model_and_keeps_full_reservation(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(502, json={"error": {"code": "server_error"}})

    mock_client(monkeypatch, handle)
    with pytest.raises(ValueError, match="missing usage"):
        runner.run(root)
    plan, dispatch = runner.load(root)
    state = runner.snapshot(runner.ledgers(root, dispatch))
    assert len(calls) == state["requests"] == state["unknown_requests"] == 1
    assert Decimal(state["committed_upper_bound_usd"]) == Decimal(
        plan["jobs"][0]["reservation_usd"]
    )
    with pytest.raises(runner.BudgetError, match="Unknown usage"):
        runner.run(root)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["policy", "refusal", "filtered", "filter_code"])
def test_policy_or_refusal_quarantines_source_before_any_other_judge(tmp_path, monkeypatch, kind):
    root = campaign(tmp_path)
    calls = []

    def handle(request):
        calls.append(request)
        raw = response(request, refusal=kind == "refusal")
        if kind in ("policy", "filter_code"):
            raw = {
                **raw,
                "error": {"code": "invalid_prompt" if kind == "policy" else "content_filter"},
            }
        if kind == "filtered":
            raw.update(status="incomplete", incomplete_details={"reason": "content_filter"})
        return httpx.Response(400 if kind in ("policy", "filter_code") else 200, json=raw)

    mock_client(monkeypatch, handle)
    state = runner.run(root)
    plan, _ = runner.load(root)
    task_id = plan["jobs"][0]["instance_id"]
    path = root / "STOP.json"
    assert path.exists()
    qpath = Path(plan["provider_quarantine_directory"]) / (
        hashlib.sha256(task_id.encode()).hexdigest() + ".json"
    )
    assert planner.read(qpath)["instance_id"] == task_id
    assert len(calls) == state["requests"] == 1
    assert state["unknown_requests"] == 0
    result = planner.read(next((root / "requests").glob("*/result.json")))
    assert result["normalized"]["status"] == "error"


def test_duplicate_provider_usage_keys_are_retained_and_not_settled(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    mock_client(monkeypatch, lambda _: httpx.Response(200, content=b'{"usage":{},"usage":{}}'))
    with pytest.raises(ValueError, match="Duplicate provider"):
        runner.run(root)
    _, dispatch = runner.load(root)
    state = runner.snapshot(runner.ledgers(root, dispatch))
    assert state["unknown_requests"] == 1
    evidence = planner.read(next((root / "requests").glob("*/transport.json")))
    assert evidence["body_utf8"] == '{"usage":{},"usage":{}}'


def test_frozen_request_ceiling_cannot_be_rebased(tmp_path):
    root = campaign(tmp_path)
    plan = planner.read(root / "prepared.json")
    plan["jobs"][0]["input_ceiling"] -= 1
    (root / "prepared.json").write_bytes(planner.canonical(plan))
    (root / "prepared-freeze.json").write_bytes(planner.canonical({"sha256": planner.digest(plan)}))
    with pytest.raises(ValueError, match="token ceiling"):
        runner.load(root)


def test_source_freeze_cannot_omit_response_helper(tmp_path):
    root = campaign(tmp_path)
    value = planner.read(root / "dispatch.json")
    del value["source_sha256"]["scripts/judge_study/responses.py"]
    (root / "dispatch.json").write_bytes(planner.canonical(value))
    (root / "dispatch-freeze.json").write_bytes(
        planner.canonical({"sha256": planner.digest(value)})
    )
    with pytest.raises(ValueError, match="source list"):
        runner.load(root)


def test_existing_global_quarantine_prevents_any_dispatch(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    plan, _ = runner.load(root)
    task_id = plan["jobs"][0]["instance_id"]
    path = Path(plan["provider_quarantine_directory"]) / (
        hashlib.sha256(task_id.encode()).hexdigest() + ".json"
    )
    planner.write_private(path, {"instance_id": task_id, "policy_quarantine": True})
    mock_client(monkeypatch, lambda _: pytest.fail("Quarantined content must not be sent"))
    state = runner.run(root)
    assert state["requests"] == 0
    assert (root / "STOP.json").exists()


def test_single_dispatch_owner_blocks_second_runner(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    mock_client(monkeypatch, lambda _: pytest.fail("Second dispatcher must not call"))
    with (root / "dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            runner.run(root)


def test_cap_admission_refuses_before_network(tmp_path, monkeypatch):
    profiles = {model: {**profile, "cap": ".000001"} for model, profile in planner.PROFILES.items()}
    monkeypatch.setattr(planner, "PROFILES", profiles)
    root = campaign(tmp_path)
    mock_client(monkeypatch, lambda _: pytest.fail("Insufficient budget must block dispatch"))
    with pytest.raises(runner.BudgetError, match="admission ceiling"):
        runner.run(root)
    _, dispatch = runner.load(root)
    state = runner.snapshot(runner.ledgers(root, dispatch))
    assert state["requests"] == 0


def test_total_model_caps_cannot_exceed_allocation(tmp_path, monkeypatch):
    fresh, root, _ = fixture_run(tmp_path)
    planner.prepare(fresh, root)
    monkeypatch.setattr(
        planner, "PROFILES", {model: {**p, "cap": "10"} for model, p in planner.PROFILES.items()}
    )
    with pytest.raises(runner.BudgetError, match="prices or total cap"):
        runner.freeze(root)
