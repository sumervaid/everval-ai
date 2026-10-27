"""Phase handling is a transport decision, independent of judgment plausibility."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import httpx
import pytest
from test_judge_study import mock_client, study

from cvals.review_queues import Rubric
from scripts.judge_study import connection_recovery, driver, flat_recovery, parallel, recovery
from scripts.judge_study.responses import MAX_TEXT_BYTES, judge_response, select_final_text

RUBRIC = Rubric.model_validate(
    {
        "name": "Correctness",
        "version": "1",
        "instructions": "Compare correctness.",
        "dimensions": [{"id": "correct", "name": "Correctness", "guidance": "Satisfy the task."}],
    }
)
VALID = json.dumps({"preference": "A", "rationale": "It satisfies the task."})


def message(text=VALID, phase="final_answer"):
    value = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }
    if phase is not None:
        value["phase"] = phase
    return value


def envelope(*messages):
    return {"http_status": 200, "response": {"status": "completed", "output": list(messages)}}


def test_selects_only_explicit_final_after_commentary():
    value = judge_response(
        envelope(message("I will compare them.", "commentary"), message()), RUBRIC
    )
    assert value.text == VALID
    assert value.phase_selection == "explicit_final_answer"
    assert value.classification == "valid_selected_final"
    assert value.judgment_status == "completed"
    assert value.error is None
    assert "It satisfies" not in json.dumps(value.metadata())


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        VALID + VALID,
        '{"preference":"C","rationale":"Unknown label."}',
        '{"preference":"A","rationale":""}',
        '{"preference":"A","rationale":"x","unexpected":true}',
        '{"preference":"A","preference":"B","rationale":"Duplicate."}',
        '{"preference":"A","rationale":"x","scores":{"A":{"other":1},"B":{"other":0}}}',
    ],
)
def test_selected_invalid_judgment_is_preserved_for_normal_grading(text):
    result = judge_response(envelope(message(text)), RUBRIC)
    assert result.text == text
    assert result.classification == "invalid_selected_final"
    assert result.judgment_status == "invalid"
    assert result.error is None


def test_valid_abstention_is_distinct_from_transport_failure():
    result = judge_response(
        envelope(
            message(
                json.dumps(
                    {
                        "preference": "abstain",
                        "rationale": "Insufficient evidence.",
                    }
                )
            )
        ),
        RUBRIC,
    )
    assert result.classification == "valid_selected_final"
    assert result.judgment_status == "abstained"


@pytest.mark.parametrize(
    "messages",
    [
        [message(), message()],
        [message("Commentary", None), message()],
        [message(), message("Later commentary", "commentary")],
        [message(VALID, None), message(VALID, None)],
        [message(VALID, "unknown")],
        [message(VALID, [])],
        [message("malformed", "final_answer"), message(VALID, "final_answer")],
    ],
)
def test_ambiguous_phases_never_choose_parseable_or_last_json(messages):
    result = select_final_text(envelope(*messages))
    assert result.classification == "transport_phase_ambiguity"
    assert result.text is None
    assert result.error


@pytest.mark.parametrize("messages", [[], [message("Still thinking.", "commentary")]])
def test_missing_final_is_reported(messages):
    assert select_final_text(envelope(*messages)).classification == "missing_final_answer"


def test_one_unphased_assistant_message_is_supported():
    result = judge_response(envelope(message(VALID, None)), RUBRIC)
    assert result.phase_selection == "legacy_single_message"
    assert result.text == VALID


@pytest.mark.parametrize(
    "status, classification",
    [
        ("incomplete", "incomplete_response"),
        ("failed", "incomplete_response"),
        (None, "incomplete_response"),
    ],
)
def test_incomplete_status_is_not_salvaged(status, classification):
    value = envelope(message())
    value["response"]["status"] = status
    assert select_final_text(value).classification == classification


def test_missing_status_is_separate():
    value = envelope(message())
    del value["response"]["status"]
    assert select_final_text(value).classification == "missing_response_status"


@pytest.mark.parametrize("phase", ["commentary", "final_answer"])
def test_refusal_is_not_ignored_even_with_other_final_text(phase):
    refused = message("", phase)
    refused["content"] = [{"type": "refusal", "refusal": "Cannot comply."}]
    value = envelope(refused, message()) if phase == "commentary" else envelope(refused)
    result = select_final_text(value)
    assert result.classification == "provider_refusal"
    assert result.text is None


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda raw: raw.update(output=None), "malformed_response"),
        (lambda raw: raw.update(output=[[]]), "malformed_response"),
        (lambda raw: raw.update(output=[{"type": []}]), "transport_phase_ambiguity"),
        (lambda raw: raw["output"][0].update(role="user"), "malformed_response"),
        (lambda raw: raw["output"][0].update(status=[]), "incomplete_response"),
        (
            lambda raw: raw["output"][0].update(content=[{"type": "output_text", "text": []}]),
            "malformed_response",
        ),
        (
            lambda raw: raw["output"][0]["content"].append({"type": "output_text", "text": VALID}),
            "malformed_response",
        ),
        (lambda raw: raw["output"][0]["content"][0].update(text=" " * 5), "missing_final_answer"),
        (
            lambda raw: raw["output"][0]["content"][0].update(text="x" * (MAX_TEXT_BYTES + 1)),
            "malformed_response",
        ),
        (lambda raw: raw["output"][0]["content"][0].update(text=chr(0xD800)), "malformed_response"),
    ],
)
def test_malformed_untrusted_content_is_classified(change, expected):
    value = envelope(message())
    change(value["response"])
    assert select_final_text(value).classification == expected


@pytest.mark.parametrize(
    "code, classification",
    [
        ("invalid_prompt", "provider_policy_rejection"),
        ("server_error", "provider_http_error"),
    ],
)
def test_http_errors_are_distinguished_without_exposing_provider_text(code, classification):
    result = select_final_text(
        {
            "http_status": 400,
            "response": {
                "error": {"code": code, "message": "Private content"},
            },
        }
    )
    assert result.classification == classification
    assert "Private content" not in json.dumps(result.metadata())


def test_live_driver_mock_preserves_phase_provenance_without_changing_accounting(
    tmp_path, monkeypatch
):
    root = study(tmp_path)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        response = envelope(message("I will compare carefully.", "commentary"), message())[
            "response"
        ]
        response.update(
            model=body["model"],
            service_tier="default",
            usage={"input_tokens": 20, "output_tokens": 10},
        )
        return httpx.Response(200, json=response)

    mock_client(monkeypatch, handler)
    driver.run(root, partition="dev", smoke=True)
    results = list((root / "requests").glob("*.result.json"))
    assert len(calls) == len(results) == 6
    for path in results:
        result = driver.read(path)
        assert result["observation"]["response"] == VALID
        assert result["observation"]["error"] is None
        assert result["response_handling"]["classification"] == "valid_selected_final"
        raw = driver.read(path.with_name(path.name.replace(".result.", ".response.")))
        assert raw["response_handling"] == result["response_handling"]
        assert len(raw["response"]["output"]) == 2
    state = driver.snapshot(root, driver.load_plan(root))
    assert state["unknown_requests"] == 0
    assert float(state["conservative_committed_usd"]) < 0.01


def test_new_freezes_bind_phase_helper_source(tmp_path):
    root = study(tmp_path)
    path = "scripts/judge_study/responses.py"
    plan = driver.load_plan(root)
    assert (
        plan["source_sha256"][path] == hashlib.sha256((driver.ROOT / path).read_bytes()).hexdigest()
    )
    for paths in (
        parallel.AMENDED_SOURCES,
        recovery.RECOVERY_SOURCES,
        connection_recovery.SOURCES,
        flat_recovery.SOURCES,
    ):
        assert path in paths


def test_phase_selection_does_not_depend_on_judgment_content():
    first = envelope(message("Correct JSON here: " + VALID, "commentary"), message("not JSON"))
    second = deepcopy(first)
    second["response"]["output"][-1]["content"][0]["text"] = VALID
    assert select_final_text(first).phase_selection == select_final_text(second).phase_selection
    assert judge_response(first, RUBRIC).classification == "invalid_selected_final"


@pytest.mark.parametrize("variant", ["incomplete", "http_error", "incomplete_refusal"])
def test_policy_signal_precedes_generic_incomplete_status(variant):
    value = envelope(message())
    if variant == "incomplete":
        value["response"].update(
            status="incomplete", incomplete_details={"reason": "content_filter"}
        )
    elif variant == "http_error":
        value.update(http_status=400)
        value["response"]["error"] = {"code": "content_filter"}
    else:
        value["response"]["status"] = "incomplete"
        value["response"]["output"][0]["content"] = [{"type": "refusal", "refusal": "Declined"}]
    result = select_final_text(value)
    assert result.classification == (
        "provider_refusal" if variant == "incomplete_refusal" else "provider_policy_rejection"
    )
    assert result.text is None


def test_legacy_research_runner_persists_policy_stop_before_resuming(tmp_path, monkeypatch):
    root = study(tmp_path)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        raw = envelope(message())["response"]
        raw.update(
            model=body["model"],
            status="incomplete",
            incomplete_details={"reason": "content_filter"},
            usage={"input_tokens": 20, "output_tokens": 10},
        )
        return httpx.Response(200, json=raw)

    mock_client(monkeypatch, handler)
    driver.run(root, partition="dev", smoke=True)
    assert 1 <= len(calls) <= 3
    assert (root / "STOP").exists()
    before = len(calls)
    with pytest.raises(driver.BudgetError, match="Shared study stop"):
        driver.run(root, partition="dev", smoke=True)
    assert len(calls) == before
