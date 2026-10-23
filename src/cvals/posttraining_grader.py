"""Descriptive grader drift on the exact same retained outputs."""

from __future__ import annotations

from typing import Annotated, Any, Self

from pydantic import Field, StrictBool, model_validator

from cvals.models import Manifest, content_hash

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class GradedOutput(Manifest):
    item_id: Identifier
    output_sha256: Digest
    passed: StrictBool


class GraderDriftRequest(Manifest):
    old_grader_revision: Identifier
    new_grader_revision: Identifier
    provenance: str = Field(min_length=1, max_length=8000)
    old: list[GradedOutput] = Field(min_length=1, max_length=10000)
    new: list[GradedOutput] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def different_revisions(self) -> Self:
        if self.old_grader_revision == self.new_grader_revision:
            raise ValueError("Grader revisions must differ; use research repeat for repeatability")
        return self


def compare_graders(request: dict[str, Any] | GraderDriftRequest) -> dict[str, Any]:
    value = GraderDriftRequest.model_validate(request)
    old = {row.item_id: row for row in value.old}
    new = {row.item_id: row for row in value.new}
    if len(old) != len(value.old) or len(new) != len(value.new):
        raise ValueError("Each item must have one recorded grade per revision")
    if old.keys() != new.keys():
        raise ValueError("Grader comparison requires the same complete item set")
    changed_outputs = [
        key for key in sorted(old) if old[key].output_sha256 != new[key].output_sha256
    ]
    if changed_outputs:
        raise ValueError(
            "Output fingerprints changed; this would conflate model and grader changes"
        )
    flips = [
        {
            "item_id": key,
            "output_sha256": old[key].output_sha256,
            "old_passed": old[key].passed,
            "new_passed": new[key].passed,
        }
        for key in sorted(old)
        if old[key].passed != new[key].passed
    ]
    old_passes = sum(row.passed for row in old.values())
    new_passes = sum(row.passed for row in new.values())
    return {
        "kind": "fixed_output_grader_drift",
        "evidence_sha256": content_hash(value.model_dump(mode="json")),
        "old_grader_revision": value.old_grader_revision,
        "new_grader_revision": value.new_grader_revision,
        "provenance": value.provenance,
        "items": len(old),
        "old_pass_rate": old_passes / len(old),
        "new_pass_rate": new_passes / len(old),
        "pass_rate_delta": (new_passes - old_passes) / len(old),
        "pass_to_fail": sum(row["old_passed"] for row in flips),
        "fail_to_pass": sum(row["new_passed"] for row in flips),
        "agreement": 1 - len(flips) / len(old),
        "flips": flips,
        "limitations": [
            "Descriptive comparison of caller-supplied grades on fingerprint-matched outputs.",
            "No model or grader was executed by this comparison.",
            "A changed grade does not identify which grader is correct.",
            "Fingerprints and revisions do not independently attest upstream execution or human review.",
            "These sample rates are not population uncertainty estimates or causal training effects.",
        ],
    }
