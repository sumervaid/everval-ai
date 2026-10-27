"""Post-hoc offline phase-aware regrade; frozen scores and ledgers stay untouched.

Official semantics: https://developers.openai.com/api/docs/guides/reasoning#phase-parameter
Commentary is intermediate; final_answer marks the completed answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from cvals.judge_calibration import normalize_response
from cvals.judge_calibration_models import CalibrationProtocol
from scripts.judge_study.identification_bounds import identification_bounds


def final_text(envelope: dict[str, Any]) -> tuple[str | None, str]:
    """Select explicit phase metadata, never the last parseable JSON value."""
    raw = envelope.get("response")
    if (
        envelope.get("http_status") != 200
        or not isinstance(raw, dict)
        or raw.get("status") != "completed"
    ):
        return None, "provider_not_completed"
    output = raw.get("output")
    if (
        not isinstance(output, list)
        or len(output) > 100
        or any(not isinstance(item, dict) for item in output)
    ):
        return None, "invalid_output_structure"
    if any(item.get("type") not in {"message", "reasoning"} for item in output):
        return None, "unexpected_output_type"
    messages = [item for item in output if item.get("type") == "message"]
    if any(
        item.get("role") != "assistant"
        or item.get("status") not in {None, "completed"}
        or item.get("phase") not in {None, "commentary", "final_answer"}
        for item in messages
    ):
        return None, "invalid_message_metadata"
    finals = [item for item in messages if item.get("phase") == "final_answer"]
    if len(finals) == 1:
        selected = finals[0]
        if messages[-1] is not selected or any(
            item is not selected and item.get("phase") != "commentary" for item in messages
        ):
            return None, "ambiguous_message_phases"
        reason = "explicit_final_answer"
    elif not finals and len(messages) == 1 and messages[0].get("phase") is None:
        selected = messages[0]
        reason = "legacy_single_message"
    else:
        return None, "missing_or_multiple_final_answers"
    content = selected.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return None, "ambiguous_final_content"
    if content[0].get("type") != "output_text" or not isinstance(content[0].get("text"), str):
        return None, "final_refusal_or_nontext"
    text = content[0]["text"]
    if not text.strip() or len(text.encode()) > 256 * 1024:
        return None, "empty_or_oversized_final"
    return text, reason


def read(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Audit input exceeds its byte bound")
    return json.loads(path.read_bytes())


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def run(root: Path) -> dict[str, Any]:
    plan = read(root / "plan.json")
    if digest(plan) != read(root / "freeze.json")["plan_sha256"]:
        raise ValueError("Frozen plan fingerprint changed")
    protocols = {
        name: CalibrationProtocol.model_validate(read(root / "approved" / (name + ".json")))
        for name in ("human", "execution")
    }
    for name, expected in plan["protocol_sha256"].items():
        if (
            hashlib.sha256((root / "approved" / (name + ".json")).read_bytes()).hexdigest()
            != expected
        ):
            raise ValueError("Frozen protocol fingerprint changed")
    groups = {}
    observations = defaultdict(list)
    source_digests = []
    for job in plan["jobs"]:
        group_key = (job["cohort"], job["partition"], job["judge_id"])
        if group_key not in groups:
            groups[group_key] = {
                "cohort": job["cohort"],
                "partition": job["partition"],
                "judge_id": job["judge_id"],
                "model_id": job["body"]["model"],
                "scheduled": 0,
                "retained_observations": 0,
                "original_statuses": Counter(),
                "phase_aware_statuses": Counter(),
                "selection_reasons": Counter(),
                "status_transitions": Counter(),
                "valid_decision_changes": 0,
            }
        group = groups[group_key]
        group["scheduled"] += 1
        identity = {key: job[key] for key in ("item_id", "judge_id", "repetition", "order")}
        original = revised = {"status": "missing", "preference": None}
        result_path = root / "requests" / (job["job_id"] + ".result.json")
        response_path = root / "requests" / (job["job_id"] + ".response.json")
        reason = "no_retained_observation"
        if result_path.exists():
            record = read(result_path)["observation"]
            source_digests.append(
                [result_path.name, hashlib.sha256(result_path.read_bytes()).hexdigest()]
            )
            group["retained_observations"] += 1
            original = normalize_response(
                record.get("response"), job["order"], protocols[job["cohort"]].rubric
            )
            if record.get("error") is not None:
                original = {"status": "error", "preference": None}
            revised = original
            if response_path.exists():
                envelope = read(response_path)
                source_digests.append(
                    [response_path.name, hashlib.sha256(response_path.read_bytes()).hexdigest()]
                )
                text, reason = final_text(envelope)
                if record.get("error") is None:
                    revised = (
                        normalize_response(text, job["order"], protocols[job["cohort"]].rubric)
                        if text is not None
                        else {"status": "invalid", "preference": None}
                    )
                else:
                    reason = "retained_provider_error_unchanged"
            else:
                reason = "provider_evidence_missing_no_regrade"
        group["original_statuses"][original["status"]] += 1
        group["phase_aware_statuses"][revised["status"]] += 1
        group["selection_reasons"][reason] += 1
        group["status_transitions"][original["status"] + "->" + revised["status"]] += 1
        group["valid_decision_changes"] += (
            original["status"] == revised["status"] == "completed"
            and original["preference"] != revised["preference"]
        )
        observations[(job["cohort"], job["partition"], "frozen")].append(
            {**identity, "status": original["status"], "preference": original["preference"]}
        )
        observations[(job["cohort"], job["partition"], "phase_aware")].append(
            {**identity, "status": revised["status"], "preference": revised["preference"]}
        )
    bounds = {}
    for name, protocol in protocols.items():
        bounds[name] = {
            variant: identification_bounds(
                protocol.model_dump(mode="json"), observations[(name, "holdout", variant)]
            )
            for variant in ("frozen", "phase_aware")
        }
    return {
        "schema_version": "everval.posthoc-phase-audit/v1",
        "analysis_role": "post_hoc_offline_transport_sensitivity",
        "plan_sha256": digest(plan),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "normalizer_source_sha256": hashlib.sha256(
            Path(normalize_response.__code__.co_filename).read_bytes()
        ).hexdigest(),
        "bounds_source_sha256": hashlib.sha256(
            Path(identification_bounds.__code__.co_filename).read_bytes()
        ).hexdigest(),
        "retained_source_set_sha256": digest(sorted(source_digests)),
        "official_phase_documentation": "https://developers.openai.com/api/docs/guides/reasoning#phase-parameter",
        "provider_calls": 0,
        "frozen_scores_modified": False,
        "groups": [groups[key] for key in sorted(groups)],
        "heldout_identification_bounds": bounds,
        "interpretation": "Strict phase metadata selection regrades retained outputs offline only. Original frozen scores remain authoritative for the declared study. Changes caused by commentary concatenation are transport limitations, not demonstrated final-answer schema failures. Identification bounds preserve all scheduled outcomes and are not confidence intervals or population claims.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(
        os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {"aggregate": str(args.output), "groups": len(result["groups"]), "provider_calls": 0}
        )
    )


if __name__ == "__main__":
    main()
