import contextlib
import json
import signal
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.judge_study import fresh_dispatch as dispatch  # noqa: E402
from scripts.judge_study import fresh_repositories as study  # noqa: E402


def test_duplicate_provider_and_patch_keys_are_rejected() -> None:
    for value in (
        '{"model":"one","model":"two"}',
        '{"usage":{"input_tokens":1,"input_tokens":2}}',
        '{"patch":"first","patch":"second"}',
    ):
        with pytest.raises(dispatch.DuplicateKey):
            dispatch.strict_loads(value)
    assert dispatch.strict_loads('{"patch":"one"}') == {"patch": "one"}


def make_prefix(root: Path, monkeypatch, missing: int | None = None):
    entries = [{"instance_id": f"task-{i}", "task_sha256": f"hash-{i}"} for i in range(52)]
    plan = {"tasks": entries, "source_sha256": "frozen-source"}
    monkeypatch.setattr(study, "load", lambda path: plan)
    for index, entry in enumerate(entries[:51]):
        if index == missing:
            continue
        study.private_json(
            root / "controls" / entry["instance_id"] / "controls.json",
            {
                "instance_id": entry["instance_id"],
                "task_sha256": entry["task_sha256"],
                "selection_sha256": study.digest(plan),
                "runner_sha256": plan["source_sha256"],
                "valid": index != 0,
            },
        )
    return plan


def test_prefix_rejects_earlier_gap_despite_fifty_later_admissions(
    tmp_path: Path, monkeypatch
) -> None:
    make_prefix(tmp_path, monkeypatch, missing=0)
    with pytest.raises(ValueError, match="Incomplete ordered prefix"):
        dispatch.prefix_proof(tmp_path)


def test_prefix_proof_includes_exclusions_and_stops_at_fiftieth(
    tmp_path: Path, monkeypatch
) -> None:
    make_prefix(tmp_path, monkeypatch)
    proof = dispatch.prefix_proof(tmp_path)
    assert len(proof["prefix"]) == 51
    assert proof["selected_task_ids"] == [f"task-{i}" for i in range(1, 51)]
    assert proof["no_earlier_missing_controls"] is True


def test_process_lock_prevents_second_scheduler(tmp_path: Path) -> None:
    with dispatch.process_lock(tmp_path) as descriptor:
        dispatch.verify_lease(tmp_path, descriptor)
        with pytest.raises(BlockingIOError), dispatch.process_lock(tmp_path):
            pytest.fail("Second scheduler acquired the first scheduler's lease")


def valid_response():
    return {
        "model": "gpt-6-luna",
        "service_tier": "default",
        "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 3},
        "output": [],
    }


@pytest.mark.parametrize(
    "change,policy",
    [
        ({"usage": None}, False),
        ({"model": "unpriced-model"}, False),
        ({"status": "failed", "error": {"code": "server_error"}}, False),
        ({"error": {"code": "content_policy_violation"}}, True),
        (
            {
                "output": [
                    {"type": "message", "content": [{"type": "refusal", "refusal": "declined"}]}
                ]
            },
            True,
        ),
        ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}, True),
    ],
)
def test_unknowns_and_policy_failures_remain_distinct(change, policy) -> None:
    reason, actual_policy = dispatch.response_problem(
        200, {**valid_response(), **change}, "gpt-6-luna"
    )
    assert reason is not None
    assert actual_policy is policy
    assert dispatch.response_problem(200, valid_response(), "gpt-6-luna") == (None, False)


def test_transport_preserves_ambiguous_bytes_and_redacts_key(tmp_path: Path) -> None:
    key = "private-test-key"
    payload = ('{"model":"one","model":"two","echo":"' + key + '"}').encode()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    with (
        httpx.Client(transport=transport) as client,
        dispatch.recorded_transport(tmp_path, key),
        client.stream("POST", "https://api.openai.com/v1/responses") as response,
    ):
        assert b"".join(response.iter_bytes()) == payload
    assert (tmp_path / "transport-response.bin").read_bytes() == payload.replace(
        key.encode(), b"[REDACTED]"
    )
    assert (tmp_path / "transport-response.bin").stat().st_mode & 0o777 == 0o600
    with pytest.raises(dispatch.DuplicateKey):
        dispatch.retained_response(tmp_path)


def test_transport_evidence_is_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dispatch, "RAW_LIMIT", 16)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 100))
    with (
        httpx.Client(transport=transport) as client,
        dispatch.recorded_transport(tmp_path, "key"),
        client.stream("POST", "https://api.openai.com/v1/responses") as response,
    ):
        assert len(b"".join(response.iter_bytes())) == 100
    assert (tmp_path / "transport-response.bin").stat().st_size == 16
    with pytest.raises(ValueError, match="Incomplete"):
        dispatch.retained_response(tmp_path)


def test_gate_checks_other_models_unknown_accounting(tmp_path: Path, monkeypatch) -> None:
    from scripts.benchmarks import budget

    seen = []

    class Ledger:
        def __init__(self, path, config, create):
            self.name = path.stem
            seen.append(self.name)

        def status(self):
            return {"admissions_blocked": self.name == "gpt-6.1-terra"}

    monkeypatch.setattr(budget, "BudgetLedger", Ledger)
    monkeypatch.setattr(study, "budget_config", lambda plan, model: model)
    manifest = {
        "initial_quarantined_task_ids": [],
        "global_quarantine_directory": str(tmp_path / "quarantine"),
    }
    with pytest.raises(ValueError, match="global dispatch"):
        dispatch.gate(tmp_path, {}, manifest, {"instance_id": "task", "model": "gpt-6-luna"})
    assert "gpt-6-luna" in seen and "gpt-6.1-terra" in seen


def test_child_executes_exactly_one_frozen_job(tmp_path: Path, monkeypatch) -> None:
    jobs = [{"id": "a", "model": "gpt-6-luna"}, {"id": "b", "model": "gpt-6-luna"}]
    plan = {"jobs": jobs}
    manifest = {"inference_sha256": study.digest(plan)}
    monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
    monkeypatch.setattr(dispatch, "verify_lease", lambda root, descriptor: None)
    monkeypatch.setattr(dispatch, "verified_plan", lambda root: (plan, manifest))
    monkeypatch.setattr(dispatch, "gate", lambda *args: None)
    monkeypatch.setattr(dispatch, "recorded_transport", lambda *args: contextlib.nullcontext())
    monkeypatch.setattr(dispatch, "validate_result", lambda *args: None)
    monkeypatch.setattr(study, "inference_plan", lambda root: plan)
    called = []

    def run(root, model):
        called.extend(study.inference_plan(root)["jobs"])

    monkeypatch.setattr(study, "run_inference", run)
    dispatch.one(tmp_path, "b", 12)
    assert called == [jobs[1]]


def test_policy_stop_prevents_any_subsequent_child(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "fresh" / "amendment-4"
    root.mkdir(parents=True)
    jobs = [
        {"id": "a", "instance_id": "task-a", "model": "gpt-6-luna"},
        {"id": "b", "instance_id": "task-b", "model": "gpt-6-luna"},
    ]
    monkeypatch.setattr(dispatch, "verified_plan", lambda path: ({"jobs": jobs}, {}))
    monkeypatch.setattr(dispatch, "gate", lambda *args: None)
    called = []

    def run(command, **kwargs):
        called.append(command)
        dispatch.stop(root, "Provider policy rejection", jobs[0], policy=True)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, "run", run)
    previous_handler = signal.getsignal(signal.SIGINT)
    with pytest.raises(RuntimeError, match="no further jobs admitted"):
        dispatch.dispatch(root)
    assert signal.getsignal(signal.SIGINT) is previous_handler
    assert len(called) == 1
    assert study.read(root / "dispatch-STOP.json")["policy_quarantine"] is True
    assert dispatch.quarantine_path(tmp_path / "provider-quarantine", "task-a").exists()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_provider_json_is_rejected(constant: str) -> None:
    with pytest.raises(ValueError, match="Non-finite"):
        dispatch.strict_loads('{"value":' + constant + "}")


def test_invalid_prompt_quarantines_source() -> None:
    reason, policy = dispatch.response_problem(
        400,
        {"error": {"code": "invalid_prompt", "type": "invalid_request_error"}},
        "gpt-6-luna",
    )
    assert reason == "Provider policy rejection or refusal"
    assert policy is True


@pytest.mark.parametrize("mode", ["valid", "duplicate_patch", "duplicate_outer"])
def test_real_ledger_and_mock_transport_preserve_full_campaign(
    tmp_path: Path, monkeypatch, mode
) -> None:
    from scripts.benchmarks.budget import BudgetLedger

    jobs = []
    for identifier in ("first", "later"):
        body = {"model": "gpt-6-luna", "input": identifier}
        jobs.append(
            {
                "id": identifier,
                "instance_id": "task-" + identifier,
                "model": "gpt-6-luna",
                "body": body,
                "body_sha256": study.digest(body),
                "input_ceiling": 100,
            }
        )
    plan = {"jobs": jobs}
    manifest = {
        "inference_sha256": study.digest(plan),
        "initial_quarantined_task_ids": [],
        "global_quarantine_directory": str(tmp_path / "quarantine"),
    }
    for model in study.PROFILES:
        BudgetLedger(
            tmp_path / "accounting" / (model + ".sqlite3"), study.budget_config(plan, model)
        )
    monkeypatch.setattr(dispatch, "verified_plan", lambda root: (plan, manifest))
    monkeypatch.setattr(study, "inference_plan", lambda root: plan)
    monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
    final = '{"patch":""}' if mode != "duplicate_patch" else '{"patch":"ignored","patch":""}'
    response = {
        **valid_response(),
        "id": "mock-response",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": final}],
            }
        ],
    }
    payload = json.dumps(response).encode()
    if mode == "duplicate_outer":
        payload = payload[:-1] + b', "model": "gpt-6-luna"}'
    calls = []

    def handler(request):
        calls.append(request.content)
        return httpx.Response(200, content=payload)

    original_init = httpx.Client.__init__

    def mock_init(client, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(client, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", mock_init)
    with dispatch.process_lock(tmp_path) as descriptor:
        if mode == "duplicate_outer":
            with pytest.raises(RuntimeError, match="reservations retained"):
                dispatch.one(tmp_path, "first", descriptor)
        else:
            dispatch.one(tmp_path, "first", descriptor)
    assert calls == [study.canonical(jobs[0]["body"])]
    assert not (tmp_path / "requests/later").exists()
    ledger = BudgetLedger(
        tmp_path / "accounting/gpt-6-luna.sqlite3",
        study.budget_config(plan, "gpt-6-luna"),
        create=False,
    )
    state = ledger.status()
    assert len(state["requests"]) == 1
    if mode == "duplicate_outer":
        assert state["uncertain_requests"] == 1
        assert (tmp_path / "dispatch-STOP.json").exists()
        assert (tmp_path / "requests/first/transport-response.bin").read_bytes() == payload
    else:
        assert state["uncertain_requests"] == 0
        result = study.read(tmp_path / "requests/first/result.json")
        assert result["patch"] == (None if mode == "duplicate_patch" else "")
        validation = study.read(tmp_path / "requests/first/dispatch-validation.json")
        assert validation["inner_status"] == (
            "duplicate_patch_json_keys" if mode == "duplicate_patch" else "valid_patch_json"
        )
