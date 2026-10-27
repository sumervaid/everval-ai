"""Post-hoc attribution of retained transport and final-judgment failures.

No calls or score changes. An invalid concatenation is attributed to phase
handling only when the original payload exactly matches that concatenation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from cvals.judge_calibration import normalize_response
from cvals.judge_calibration_models import CalibrationProtocol
from scripts.judge_study.phase_audit import digest, final_text


def classify(
    record: dict[str, Any] | None,
    envelope: dict[str, Any] | None,
    order: list[str],
    rubric: Any,
) -> dict[str, str | bool]:
    if record is None:
        return {
            "attribution": "unattempted",
            "original_status": "missing",
            "selected_status": "missing",
        }
    if record.get("error") is not None:
        return {
            "attribution": "provider_error",
            "original_status": "error",
            "selected_status": "error",
        }
    original = normalize_response(record.get("response"), order, rubric)
    result: dict[str, str | bool] = {"original_status": original["status"]}
    if envelope is None:
        return {
            **result,
            "attribution": "missing_provider_evidence",
            "selected_status": "unavailable",
        }
    text, reason = final_text(envelope)
    result["phase_selection"] = reason
    if text is None:
        return {
            **result,
            "attribution": "unresolved_transport_or_refusal",
            "selected_status": "unavailable",
        }
    selected = normalize_response(text, order, rubric)
    result["selected_status"] = selected["status"]
    if selected["status"] == "invalid":
        return {**result, "attribution": "selected_final_judgment_invalid"}
    if original["status"] != selected["status"]:
        messages = [x for x in envelope["response"]["output"] if x.get("type") == "message"]
        content_valid = all(
            isinstance(message.get("content"), list)
            and all(isinstance(c, dict) for c in message["content"])
            for message in messages
        )
        texts = (
            [
                c["text"]
                for message in messages
                for c in message["content"]
                if c.get("type") == "output_text" and isinstance(c.get("text"), str)
            ]
            if content_valid
            else []
        )
        matches = content_valid and record.get("response") == "\n".join(texts)
        commentary = any(message.get("phase") == "commentary" for message in messages)
        recovered = (
            original["status"] == "invalid"
            and selected["status"] in {"completed", "abstained"}
            and reason == "explicit_final_answer"
            and commentary
            and matches
        )
        return {
            **result,
            "attribution": "transport_phase_concatenation_recovered"
            if recovered
            else "offline_status_change_unattributed",
            "original_matches_frozen_concatenation": matches,
        }
    if original["status"] == "completed" and original["preference"] != selected["preference"]:
        return {**result, "attribution": "valid_decision_changed"}
    return {**result, "attribution": "unchanged_" + original["status"]}


def load(path: Path) -> tuple[dict[str, Any], str]:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Audit input exceeds 32 MiB")
    payload = path.read_bytes()
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def run(root: Path) -> dict[str, Any]:
    plan, _ = load(root / "plan.json")
    marker, _ = load(root / "freeze.json")
    if digest(plan) != marker["plan_sha256"]:
        raise ValueError("Frozen plan changed")
    protocols = {}
    for cohort, expected in plan["protocol_sha256"].items():
        value, fingerprint = load(root / "approved" / (cohort + ".json"))
        if fingerprint != expected:
            raise ValueError("Frozen protocol changed")
        protocols[cohort] = CalibrationProtocol.model_validate(value)
    groups, sources = {}, []
    for job in plan["jobs"]:
        key = (job["cohort"], job["partition"], job["judge_id"])
        group = groups.setdefault(
            key,
            {
                "cohort": key[0],
                "partition": key[1],
                "judge_id": key[2],
                "scheduled": 0,
                "attributions": Counter(),
                "selected_statuses": Counter(),
            },
        )
        record, envelope = None, None
        for suffix in ("result", "response"):
            path = root / "requests" / (job["job_id"] + "." + suffix + ".json")
            if path.exists():
                value, fingerprint = load(path)
                sources.append([path.name, fingerprint])
                if suffix == "result":
                    record = value["observation"]
                else:
                    envelope = value
        classified = classify(record, envelope, job["order"], protocols[job["cohort"]].rubric)
        group["scheduled"] += 1
        group["attributions"][classified["attribution"]] += 1
        group["selected_statuses"][classified["selected_status"]] += 1
    return {
        "schema_version": "everval.posthoc-phase-attribution/v1",
        "analysis_role": "post_hoc_descriptive_transport_attribution",
        "plan_sha256": digest(plan),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "selector_sha256": hashlib.sha256(
            Path(final_text.__code__.co_filename).read_bytes()
        ).hexdigest(),
        "normalizer_sha256": hashlib.sha256(
            Path(normalize_response.__code__.co_filename).read_bytes()
        ).hexdigest(),
        "retained_source_set_sha256": digest(sorted(sources)),
        "provider_calls": 0,
        "frozen_scores_modified": False,
        "groups": [groups[key] for key in sorted(groups)],
        "interpretation": "Recovered concatenations are transport phase-selection failures. Invalid selected final judgments fail the frozen judgment contract, not necessarily the provider JSON schema. Ambiguous phases, refusals and missing provider evidence are not attributed to model schema noncompliance. All unattempted slots remain missing; original scores and endpoints are unchanged.",
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
    print(json.dumps({"aggregate": str(args.output), "provider_calls": 0}))


if __name__ == "__main__":
    main()
