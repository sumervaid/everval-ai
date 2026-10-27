"""Only matched retained concatenation proves a phase-handling failure."""

import importlib
import json

import pytest
from test_judge_calibration import protocol
from test_judge_phase_audit import message, response

from cvals.judge_calibration_models import CalibrationProtocol

attribution = importlib.import_module("scripts.judge_study.phase_attribution")
RUBRIC = CalibrationProtocol.model_validate(protocol()).rubric


def classify(raw, envelope):
    return attribution.classify({"response": raw, "error": None}, envelope, ["A", "B"], RUBRIC)


def test_only_matching_commentary_concatenation_is_attributed_to_transport():
    final = message("final_answer")
    text = final["content"][0]["text"]
    envelope = response(message("commentary", "Intermediate text"), final)
    result = classify("Intermediate text\n" + text, envelope)
    assert result["attribution"] == "transport_phase_concatenation_recovered"
    assert result["original_status"] == "invalid" and result["selected_status"] == "completed"
    assert (
        classify("different corrupted payload", envelope)["attribution"]
        == "offline_status_change_unattributed"
    )


def test_invalid_final_is_not_rescued_by_valid_commentary_json():
    commentary = message("commentary")
    envelope = response(commentary, message("final_answer", "not JSON"))
    result = classify(commentary["content"][0]["text"] + "\nnot JSON", envelope)
    assert result["attribution"] == "selected_final_judgment_invalid"
    assert result["selected_status"] == "invalid"


@pytest.mark.parametrize(
    "envelope",
    [
        response(message(None), message(None)),
        response(message("commentary")),
        response(message("final_answer"), message("final_answer")),
    ],
)
def test_ambiguous_phases_are_not_claimed_as_model_contract_failures(envelope):
    assert classify("bad", envelope)["attribution"] == "unresolved_transport_or_refusal"


def test_refusal_is_not_json_noncompliance():
    final = message("final_answer")
    final["content"] = [{"type": "refusal", "refusal": "Declined"}]
    assert classify("bad", response(final))["attribution"] == "unresolved_transport_or_refusal"


def test_errors_unattempted_and_missing_evidence_stay_distinct():
    assert attribution.classify(None, None, ["A", "B"], RUBRIC)["attribution"] == "unattempted"
    assert (
        attribution.classify(
            {"error": "policy"}, response(message("final_answer")), ["A", "B"], RUBRIC
        )["attribution"]
        == "provider_error"
    )
    assert classify("bad", None)["attribution"] == "missing_provider_evidence"


def test_recovered_abstention_is_not_counted_as_completed_agreement():
    text = json.dumps({"preference": "abstain", "rationale": "Evidence insufficient."})
    result = classify(
        "Intermediate\n" + text,
        response(message("commentary", "Intermediate"), message("final_answer", text)),
    )
    assert result["attribution"] == "transport_phase_concatenation_recovered"
    assert result["selected_status"] == "abstained"


def test_malformed_commentary_cannot_prove_the_original_concatenation():
    commentary = message("commentary")
    commentary["content"] = None
    result = classify("bad", response(commentary, message("final_answer")))
    assert result["attribution"] == "offline_status_change_unattributed"
