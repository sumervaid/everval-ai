"""Inspect evidence required to qualify an evaluation for researcher review."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

EVIDENCE_MAX = 10_000
Text = Annotated[str, Field(min_length=1, max_length=8000)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False, strict=True)]


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QualificationControl(EvidenceModel):
    id: Identifier
    kind: Literal["positive", "negative", "mutation"]
    expected_pass: StrictBool
    observed_pass: StrictBool


class RepeatedGrades(EvidenceModel):
    item_id: Identifier
    grades: list[StrictBool] = Field(min_length=2, max_length=100)


class HumanJudgment(EvidenceModel):
    item_id: Identifier
    grader_pass: StrictBool
    human_pass: StrictBool
    blinded: StrictBool
    adjudicated: StrictBool = False


class QualificationEvidence(EvidenceModel):
    measurement_claim: Text
    owner: Identifier
    population: Text
    limitations: Text
    task_version: Identifier
    grader_version: Identifier
    semantic_rubric: str = Field(default="", max_length=16000)
    adjudication_limitations: str = Field(default="", max_length=8000)
    controls: list[QualificationControl] = Field(default_factory=list, max_length=EVIDENCE_MAX)
    repeated_grades: list[RepeatedGrades] = Field(default_factory=list, max_length=EVIDENCE_MAX)
    human_judgments: list[HumanJudgment] = Field(default_factory=list, max_length=EVIDENCE_MAX)
    saturation_scores: list[Score] = Field(default_factory=list, max_length=EVIDENCE_MAX)
    min_examples_per_class: int = Field(default=5, ge=2, le=1000, strict=True)
    min_repeat_items: int = Field(default=5, ge=2, le=1000, strict=True)
    minimum_agreement: float = Field(default=0.9, ge=0.5, le=1, allow_inf_nan=False)
    saturation_boundary: float = Field(default=0.95, gt=0.5, lt=1, allow_inf_nan=False)


def qualify_evaluation(evidence: dict[str, Any] | QualificationEvidence) -> dict[str, Any]:
    """Return gaps and descriptive diagnostics; never certify a grader as truthful.

    Human reference judgments must be gathered blind to checkpoint/model identity.
    All evidence is caller-supplied: this checklist cannot verify provenance,
    reviewer independence, blinding, or whether a rubric measures the claimed skill.
    """
    value = QualificationEvidence.model_validate(evidence)
    gaps: list[str] = []
    controls = value.controls
    judgments = value.human_judgments
    repeated = value.repeated_grades
    for label, ids in (
        ("controls", [row.id for row in controls]),
        ("human judgments", [row.item_id for row in judgments]),
        ("repeated grading", [row.item_id for row in repeated]),
    ):
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate item identity in {label}")
    if not value.semantic_rubric:
        gaps.append(
            "A semantic rubric is required; structural probes alone do not calibrate a grader."
        )
    positive = [row for row in controls if row.kind == "positive" and row.expected_pass]
    negative = [row for row in controls if row.kind == "negative" and not row.expected_pass]
    mutations = [row for row in controls if row.kind == "mutation" and not row.expected_pass]
    if not positive:
        gaps.append("No positive control with expected pass.")
    if not negative:
        gaps.append("No negative control with expected failure.")
    if not mutations:
        gaps.append("No behavior-breaking mutation with expected failure.")
    failed_controls = [row.id for row in controls if row.observed_pass != row.expected_pass]
    if failed_controls:
        gaps.append(f"{len(failed_controls)} controls disagreed with their expected outcome.")
    inconsistent = sum(len(set(row.grades)) > 1 for row in repeated)
    repeat_agreement = 1 - inconsistent / len(repeated) if repeated else None
    if len(repeated) < value.min_repeat_items:
        gaps.append("Too few distinct items with repeated grader measurements.")
    if repeat_agreement is not None and repeat_agreement < value.minimum_agreement:
        gaps.append("Repeated grader agreement is below the declared threshold.")
    positives = [row for row in judgments if row.human_pass]
    negatives = [row for row in judgments if not row.human_pass]
    false_negative = sum(not row.grader_pass for row in positives)
    false_positive = sum(row.grader_pass for row in negatives)
    sensitivity = 1 - false_negative / len(positives) if positives else None
    specificity = 1 - false_positive / len(negatives) if negatives else None
    agreement = (
        sum(row.grader_pass == row.human_pass for row in judgments) / len(judgments)
        if judgments
        else None
    )
    for label, rows, class_agreement in (
        ("positive", positives, sensitivity),
        ("negative", negatives, specificity),
    ):
        if len(rows) < value.min_examples_per_class:
            gaps.append(f"Too few distinct blinded human {label} examples.")
        if class_agreement is not None and class_agreement < value.minimum_agreement:
            gaps.append(f"Human/grader {label}-class agreement is below the declared threshold.")
    if any(not row.blinded for row in judgments):
        gaps.append("Human calibration includes unblinded judgments.")
    unadjudicated = sum(
        row.grader_pass != row.human_pass and not row.adjudicated for row in judgments
    )
    if unadjudicated:
        gaps.append(f"{unadjudicated} human/grader disagreements lack adjudication.")
    if not value.adjudication_limitations:
        gaps.append("Human-reference and adjudication limitations have not been documented.")
    saturation_mean = (
        sum(value.saturation_scores) / len(value.saturation_scores)
        if value.saturation_scores
        else None
    )
    saturation = saturation_mean is not None and (
        saturation_mean >= value.saturation_boundary
        or saturation_mean <= 1 - value.saturation_boundary
    )
    if saturation_mean is None:
        gaps.append("No declared-population measurements to inspect floor/ceiling saturation.")
    elif saturation:
        gaps.append(
            "The evaluation is near its declared floor or ceiling; monitoring sensitivity needs review."
        )
    return {
        "status": "gaps_found" if gaps else "ready_for_review",
        "gaps": gaps,
        "automatic_trust": False,
        "controls": {
            "count": len(controls),
            "failed_ids": failed_controls,
            "positive": len(positive),
            "negative": len(negative),
            "mutation": len(mutations),
        },
        "repeatability": {
            "items": len(repeated),
            "inconsistent_items": inconsistent,
            "all_repetitions_agree_fraction": repeat_agreement,
        },
        "calibration": {
            "items": len(judgments),
            "positive_items": len(positives),
            "negative_items": len(negatives),
            "agreement": agreement,
            "sensitivity": sensitivity,
            "specificity": specificity,
            "false_positives": false_positive,
            "false_negatives": false_negative,
            "unadjudicated_disagreements": unadjudicated,
            "unblinded_items": sum(not row.blinded for row in judgments),
        },
        "saturation": {
            "items": len(value.saturation_scores),
            "mean": saturation_mean,
            "flagged": saturation,
        },
        "limitations": [
            "All rates are descriptive and do not establish population-level grader accuracy.",
            "Reference judgments, blinding, versions and provenance are supplied by the evaluation owner.",
            "Adjudication does not erase observed disagreements or make the human reference infallible.",
            "Passing this checklist requires researcher review; it never grants automatic production trust.",
        ],
    }
