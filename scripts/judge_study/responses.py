"""Strict phase selection for future Responses-based research runs.

The original October 27 study is reproducible at commit
94e2960258a7ad658d082d628fbc258ea983461b. This helper never rewrites its evidence.
Phase semantics: https://developers.openai.com/api/docs/guides/reasoning#phase-parameter
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cvals.judge_calibration import normalize_response
from cvals.review_queues import Rubric

MAX_TEXT_BYTES = 256 * 1024


@dataclass(frozen=True)
class SelectedResponse:
    text: str | None
    classification: str
    reason: str
    phase_selection: str | None = None
    judgment_status: str | None = None

    @property
    def error(self) -> str | None:
        # A selected but invalid judgment must reach the ordinary invalid-response grader.
        return (
            None
            if self.text is not None
            else (f"Response handling: {self.classification} ({self.reason})")
        )

    def metadata(self) -> dict[str, str | None]:
        return {
            "classification": self.classification,
            "reason": self.reason,
            "phase_selection": self.phase_selection,
            "judgment_status": self.judgment_status,
        }


def select_final_text(envelope: Any) -> SelectedResponse:
    """Select by transport phase alone, never by whether candidate text parses."""

    def fail(kind: str, reason: str) -> SelectedResponse:
        return SelectedResponse(None, kind, reason)

    if not isinstance(envelope, dict) or not isinstance(envelope.get("response"), dict):
        return fail("malformed_response", "Response envelope must contain an object")
    raw = envelope["response"]
    error = raw.get("error")
    details = raw.get("incomplete_details")
    if (
        isinstance(error, dict)
        and error.get("code") in ("invalid_prompt", "content_filter", "content_policy_violation")
    ) or (isinstance(details, dict) and details.get("reason") == "content_filter"):
        return fail("provider_policy_rejection", "Provider explicitly reported content filtering")
    output = raw.get("output")
    if isinstance(output, list) and len(output) <= 100:
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "refusal" for part in content
            ):
                return fail("provider_refusal", "Provider supplied refusal content")
    if envelope.get("http_status") != 200:
        return fail("provider_http_error", "Provider returned a non-success HTTP status")
    if "status" not in raw:
        return fail("missing_response_status", "Completed response status was not supplied")
    if raw["status"] != "completed":
        return fail("incomplete_response", "Provider did not mark the response completed")
    output = raw.get("output")
    if (
        not isinstance(output, list)
        or len(output) > 100
        or any(not isinstance(item, dict) for item in output)
    ):
        return fail("malformed_response", "Output must be a bounded object list")
    if any(item.get("type") not in ("message", "reasoning") for item in output):
        return fail("transport_phase_ambiguity", "Unexpected output item type")
    messages = [item for item in output if item.get("type") == "message"]
    if not messages:
        return fail("missing_final_answer", "No assistant message was supplied")
    for message in messages:
        if message.get("role") != "assistant":
            return fail("malformed_response", "Output message is not an assistant message")
        if message.get("status") not in (None, "completed"):
            return fail("incomplete_response", "An assistant message is incomplete")
        if message.get("phase") not in (None, "commentary", "final_answer"):
            return fail("transport_phase_ambiguity", "Unknown assistant phase")
        content = message.get("content")
        if not isinstance(content, list) or any(not isinstance(c, dict) for c in content):
            return fail("malformed_response", "Assistant content must be an object list")
        if any(c.get("type") == "refusal" for c in content):
            return fail("provider_refusal", "Provider supplied refusal content")
    final = [message for message in messages if message.get("phase") == "final_answer"]
    if (
        len(final) == 1
        and final[0] is messages[-1]
        and all(message is final[0] or message.get("phase") == "commentary" for message in messages)
    ):
        selected, phase = final[0], "explicit_final_answer"
    elif not final and len(messages) == 1 and messages[0].get("phase") is None:
        selected, phase = messages[0], "legacy_single_message"
    elif not final and all(message.get("phase") == "commentary" for message in messages):
        return fail("missing_final_answer", "Only commentary messages were supplied")
    else:
        return fail("transport_phase_ambiguity", "No unique unambiguous final assistant message")
    content = selected["content"]
    if (
        len(content) != 1
        or content[0].get("type") != "output_text"
        or not isinstance(content[0].get("text"), str)
    ):
        return fail("malformed_response", "Final message must contain exactly one text block")
    text = content[0]["text"]
    if not text.strip():
        return fail("missing_final_answer", "Selected final text is empty")
    try:
        text_size = len(text.encode())
    except UnicodeError:
        return fail("malformed_response", "Selected final text has invalid Unicode")
    if text_size > MAX_TEXT_BYTES:
        return fail("malformed_response", "Selected final text exceeds the evidence bound")
    return SelectedResponse(text, "selected_final", "Unique final message selected", phase)


def judge_response(envelope: Any, rubric: Rubric) -> SelectedResponse:
    """Distinguish selected-final judgment validity from transport-phase failures."""
    selected = select_final_text(envelope)
    if selected.text is None:
        return selected
    normalized = normalize_response(selected.text, ["A", "B"], rubric)
    status = normalized["status"]
    return replace(
        selected,
        classification="invalid_selected_final" if status == "invalid" else "valid_selected_final",
        reason="Selected final evaluated against the frozen judgment contract",
        judgment_status=status,
    )


def preserve_policy_stop(root: Path, selected: SelectedResponse, job: dict[str, Any]) -> bool:
    """Persist a shared stop before any subsequent dispatch after a policy signal."""
    if selected.classification not in ("provider_policy_rejection", "provider_refusal"):
        return False
    marker = root / "STOP"
    value = {
        "reason": selected.classification,
        "job_id": job["job_id"],
        "item_id": job["item_id"],
        "policy": "No retry, rewriting or routing of this task to another model.",
    }
    try:
        with marker.open("xb") as stream:
            marker.chmod(0o600)
            stream.write(json.dumps(value, sort_keys=True).encode() + b"\n")
    except FileExistsError:
        pass
    return True
