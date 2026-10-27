"""A post-hoc regrade follows phase metadata, never a convenient JSON fragment."""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
final_text = importlib.import_module("scripts.judge_study.phase_audit").final_text


def message(phase, text='{"preference":"A","rationale":"Justified."}'):
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": text}],
    }


def response(*messages):
    return {"http_status": 200, "response": {"status": "completed", "output": list(messages)}}


def test_explicit_final_is_separate_from_intermediate_commentary():
    final = message("final_answer")
    value, reason = final_text(response(message("commentary", "Intermediate prose"), final))
    assert value == final["content"][0]["text"] and reason == "explicit_final_answer"


def test_only_one_phaseless_legacy_message_is_unambiguous():
    assert final_text(response(message(None)))[1] == "legacy_single_message"
    assert final_text(response(message(None), message(None)))[0] is None


@pytest.mark.parametrize(
    "messages",
    [
        [message("final_answer"), message("final_answer")],
        [message("commentary")],
        [message(None), message("final_answer")],
        [message("final_answer"), message("commentary")],
        [message("unknown")],
    ],
)
def test_ambiguous_or_nonfinal_messages_are_never_salvaged(messages):
    assert final_text(response(*messages))[0] is None


def test_selected_final_text_still_needs_strict_judgment_validation():
    # Selection is based on phase, not whether another message contains valid JSON.
    value, reason = final_text(response(message("commentary"), message("final_answer", "not JSON")))
    assert value == "not JSON" and reason == "explicit_final_answer"


def test_refusals_multiple_final_parts_and_incomplete_provider_results_stay_unavailable():
    final = message("final_answer")
    final["content"].append({"type": "output_text", "text": "{}"})
    assert final_text(response(final))[0] is None
    final["content"] = [{"type": "refusal", "refusal": "Declined"}]
    assert final_text(response(final))[1] == "final_refusal_or_nontext"
    envelope = response(message("final_answer"))
    envelope["response"]["status"] = "incomplete"
    assert final_text(envelope)[0] is None
