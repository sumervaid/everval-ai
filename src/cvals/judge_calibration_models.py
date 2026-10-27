"""Typed frozen inputs and explicit provenance for judge measurement."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from cvals.models import Manifest, System, canonical_json, content_hash
from cvals.review_queues import Rubric

Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.:-]+$")]
Label = Literal["A", "B", "tie", "abstain"]
Partition = Literal["dev", "holdout"]
MAX_JOBS = 30_000


class PublicTask(Manifest):
    name: str = Field(default="Anonymous task", min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=100000)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ReviewerLabel(Manifest):
    reviewer_id: Identifier
    label: Label | None = None


class Reference(Manifest):
    source: Literal["human_collected", "scripted", "unverified", "execution"] = "unverified"
    label: Label | None = None
    reviewer_labels: list[ReviewerLabel] = Field(default_factory=list, max_length=100)
    provenance: str = Field(
        default="Unverified caller-supplied reference.", min_length=1, max_length=16000
    )
    source_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def distinct_reviewers(self) -> Reference:
        ids = [row.reviewer_id for row in self.reviewer_labels]
        if len(set(ids)) != len(ids):
            raise ValueError("Retain at most one frozen label per reviewer")
        if self.source == "human_collected" and not self.reviewer_labels:
            raise ValueError("Human-collected references require retained reviewer labels")
        return self


class CalibrationItem(Manifest):
    item_id: Identifier
    task_id: Identifier
    family_id: Identifier
    partition: Partition
    task: PublicTask
    responses: dict[str, Any]
    generator_models: dict[str, str] = Field(default_factory=dict)
    reference_context: Any = None
    reference: Reference = Field(default_factory=Reference)

    @model_validator(mode="after")
    def paired_responses(self) -> CalibrationItem:
        if set(self.responses) != {"A", "B"}:
            raise ValueError("Every item requires exactly two canonical responses: A and B")
        if self.generator_models and set(self.generator_models) != {"A", "B"}:
            raise ValueError("Generator identities require both canonical response labels")
        return self


class Judge(Manifest):
    id: Identifier
    model: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    prompt_variant: Literal["standard", "reference_context"] = "standard"
    system: System | None = None

    @model_validator(mode="after")
    def provider_snapshot(self) -> Judge:
        if self.system is not None and (
            self.system.kind != "http"
            or self.system.harness != "single-turn"
            or self.system.model != self.model
        ):
            raise ValueError("A judge system must be a matching single-turn HTTP model")
        return self


class PrimaryContrast(Manifest):
    left: Identifier
    right: Identifier


class CalibrationProtocol(Manifest):
    schema_version: Literal[1] = 1
    purpose: Literal["judge_calibration"] = "judge_calibration"
    name: str = Field(min_length=1, max_length=200)
    prompt_revision: Literal["anonymous-pairwise/1"] = "anonymous-pairwise/1"
    measurement_claim: str = Field(
        default="Agreement with the declared reference on frozen items.",
        min_length=1,
        max_length=8000,
    )
    rubric: Rubric
    judges: list[Judge] = Field(min_length=1, max_length=20)
    items: list[CalibrationItem] = Field(min_length=1, max_length=5000)
    repetitions: int = Field(default=1, ge=1, le=20, strict=True)
    budget_usd: float = Field(default=0, ge=0, le=100000, strict=True)
    primary_endpoint: Literal["reference", "human_votes"] = "reference"
    primary_contrasts: list[PrimaryContrast] = Field(default_factory=list, max_length=100)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    planned_comparisons: int = Field(default=1, ge=1, le=10000, strict=True)
    resamples: int = Field(default=5000, ge=100, le=10000, strict=True)
    seed: int = Field(default=17, ge=0, le=2**32 - 1, strict=True)
    min_families: int = Field(default=20, ge=2, le=1000, strict=True)

    @model_validator(mode="after")
    def frozen_design(self) -> CalibrationProtocol:
        for name, ids in (
            ("item", [x.item_id for x in self.items]),
            ("judge", [x.id for x in self.judges]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"Duplicate {name} identity")
        known = {judge.id for judge in self.judges}
        pairs = [(row.left, row.right) for row in self.primary_contrasts]
        if len(set(pairs)) != len(pairs) or any(
            a == b or a not in known or b not in known for a, b in pairs
        ):
            raise ValueError(
                "Primary contrasts must name distinct, declared judges without duplicates"
            )
        if self.planned_comparisons < (len(pairs) or len(self.judges)):
            raise ValueError("Allocate the primary comparison count before evaluation")
        for attribute in ("task_id", "family_id"):
            splits: dict[str, str] = {}
            for item in self.items:
                identity = getattr(item, attribute)
                if identity in splits and splits[identity] != item.partition:
                    raise ValueError(f"{attribute} crosses development and holdout partitions")
                splits[identity] = item.partition
        task_families: dict[str, str] = {}
        fingerprints: dict[str, str] = {}
        for item in self.items:
            previous = task_families.setdefault(item.task_id, item.family_id)
            if previous != item.family_id:
                raise ValueError("A task must belong to one declared family")
            fingerprint = content_hash(
                {"instruction": item.task.instruction, "inputs": item.task.inputs}
            )
            split = fingerprints.setdefault(fingerprint, item.partition)
            if split != item.partition:
                raise ValueError("Identical public tasks cross development and holdout partitions")
        if len(self.items) * len(self.judges) * self.repetitions * 2 > MAX_JOBS:
            raise ValueError(f"A protocol supports at most {MAX_JOBS} planned judgments")
        if any(j.prompt_variant == "reference_context" for j in self.judges) and any(
            item.reference_context is None
            or isinstance(item.reference_context, str)
            and not item.reference_context.strip()
            or isinstance(item.reference_context, (dict, list))
            and not item.reference_context
            for item in self.items
        ):
            raise ValueError("Reference-context judges need separately declared public evidence")
        return self


class JudgeResponse(Manifest):
    preference: Label
    rationale: str = Field(min_length=1, max_length=16000)
    scores: dict[str, dict[str, float]] | None = None

    @field_validator("rationale")
    @classmethod
    def meaningful_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonempty judgment rationale is required")
        return value.strip()

    @field_validator("scores", mode="before")
    @classmethod
    def finite_scores(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"A", "B"}:
            raise ValueError("Scores require both displayed responses")
        for row in value.values():
            if not isinstance(row, dict) or any(
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0 <= score <= 1
                for score in row.values()
            ):
                raise ValueError("Scores must be finite numbers between zero and one")
        return value


class ImportedObservation(Manifest):
    job_id: Identifier
    response: dict[str, Any] | str | None = None
    error: str | None = Field(default=None, max_length=2000)
    cost_usd: float | None = Field(default=None, ge=0, le=1000000, strict=True)
    reservation_usd: float = Field(default=0, ge=0, le=1000000, strict=True)
    input_tokens: int | None = Field(default=None, ge=0, le=1000000000, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, le=1000000000, strict=True)
    duration_ms: float | None = Field(default=None, ge=0, le=604800000, strict=True)

    @model_validator(mode="after")
    def bounded_response(self) -> ImportedObservation:
        if len(canonical_json(self.response).encode()) > 256 * 1024:
            raise ValueError("Judge response exceeds the 256 KiB limit")
        if self.error is not None and not self.error.strip():
            raise ValueError("Errors must be nonempty")
        return self
