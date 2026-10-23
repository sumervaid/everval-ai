"""Bounded immutable declarations for checkpoint evaluation."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from cvals.models import Manifest, canonical_json

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Label = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[^\x00-\x1f\x7f]+$")]


class TrainingDomain(Manifest):
    name: Label
    collection_id: Identifier
    task_families: dict[Identifier, Identifier] = Field(min_length=1, max_length=1000)
    practical_threshold: float = Field(default=0.05, ge=0, le=1, strict=True)


class TrainingProtocol(Manifest):
    inference_scope: Literal["exploratory", "predeclared"] = "exploratory"
    independence_statement: str = Field(default="", max_length=8000)
    domains: list[TrainingDomain] = Field(min_length=1, max_length=20)
    baseline_step: int = Field(ge=0, le=10**12, strict=True)
    comparison_steps: list[Annotated[int, Field(ge=0, le=10**12, strict=True)]] = Field(
        min_length=1, max_length=100
    )
    alpha: float = Field(default=0.05, gt=0, le=0.2, strict=True)
    max_looks: int = Field(default=10, ge=1, le=100, strict=True)
    min_clusters: int = Field(default=5, ge=2, le=1000, strict=True)
    repetitions: int = Field(default=1, ge=1, le=10, strict=True)
    budget_per_domain_usd: float = Field(default=0, ge=0, le=100000, strict=True)
    lifetime_budget_usd: float = Field(default=0, ge=0, le=1000000, strict=True)

    @model_validator(mode="after")
    def unique_grid(self) -> Self:
        if self.inference_scope == "predeclared" and not self.independence_statement.strip():
            raise ValueError(
                "Predeclared inference requires an independence statement about checkpoint selection and task reuse"
            )
        if len(set(self.comparison_steps)) != len(self.comparison_steps):
            raise ValueError("Comparison steps must be unique")
        if any(step <= self.baseline_step for step in self.comparison_steps):
            raise ValueError("Comparison steps must follow the baseline")
        if self.comparison_steps != sorted(self.comparison_steps):
            raise ValueError("Comparison steps must be sorted")
        if len(self.comparison_steps) > self.max_looks:
            raise ValueError("The comparison grid exceeds the declared maximum looks")
        for values in ([d.name for d in self.domains], [d.collection_id for d in self.domains]):
            if len(values) != len(set(values)):
                raise ValueError("Domain names and collection IDs must be unique")
        return self


class TrainingRunCreate(Manifest):
    name: Label
    training_run_id: Identifier
    provenance_kind: Literal["declared_training", "synthetic_control"]
    provenance: str = Field(min_length=1, max_length=8000)
    training_config_sha256: Digest
    protocol: TrainingProtocol


class CheckpointCreate(Manifest):
    step: int = Field(ge=0, le=10**12, strict=True)
    weight_sha256: Digest
    system_id: Identifier
    expected_system_hash: Digest
    revision: str = Field(min_length=1, max_length=200)
    provenance: str = Field(min_length=1, max_length=8000)


class InvestigationCreate(Manifest):
    comparison_id: Identifier
    observations: str = Field(min_length=1, max_length=10000)
    hypotheses: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=20
    )
    recommendation: str = Field(min_length=1, max_length=10000)
    next_checks: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=20
    )
    regrade_record_ids: list[Identifier] = Field(default_factory=list, max_length=50)


class QualificationCreate(Manifest):
    domain: Label
    evidence: dict[str, Any] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(canonical_json(self.evidence).encode()) > 1024 * 1024:
            raise ValueError("Qualification evidence exceeds 1 MiB")
        return self


class MixManifestRow(Manifest):
    checkpoint_id: Identifier
    task_family: Identifier
    eval_id: Identifier
    domain: Label


class MixCreate(Manifest):
    name: Label
    development: list[dict[str, Any]] = Field(min_length=1, max_length=20000)
    validation_manifest: list[MixManifestRow] = Field(min_length=1, max_length=20000)
    config: dict[str, Any] = Field(min_length=1, max_length=20)


class MixValidation(Manifest):
    validation: list[dict[str, Any]] = Field(min_length=1, max_length=20000)
