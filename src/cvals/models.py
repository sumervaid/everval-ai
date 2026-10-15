"""Versioned, bounded scenario manifests and experiment admission contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal, Self
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Domain = Literal["coding", "documents", "data", "workflow"]
CheckKind = Literal[
    "equals",
    "contains",
    "numeric",
    "json_schema",
    "citation",
    "forbidden",
    "python_tests",
    "repository_tests",
]
Identifier = str
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return uuid4().hex


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    @model_validator(mode="after")
    def bounded_json(self) -> Self:
        try:
            size = len(canonical_json(self.model_dump(mode="python")).encode())
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("Manifest must contain finite, non-recursive JSON values") from exc
        if size > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest exceeds the 64 MiB limit")
        return self


class Check(Manifest):
    id: str = Field(
        default_factory=new_id, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    name: str = Field(min_length=1, max_length=200)
    kind: CheckKind
    path: str = Field(default="", max_length=1000)
    expected: Any = None
    weight: float = Field(default=1, gt=0, le=1000)
    required: bool = True
    tolerance: float = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def simple_path(cls, value: str) -> str:
        if value and any(not part or len(part) > 200 for part in value.split(".")):
            raise ValueError("Check paths use nonempty dotted keys")
        return value

    @model_validator(mode="after")
    def valid_definition(self) -> Self:
        if self.kind == "json_schema":
            from jsonschema import Draft202012Validator
            from jsonschema.exceptions import SchemaError

            try:
                Draft202012Validator.check_schema(self.expected)
            except SchemaError as exc:
                raise ValueError("Invalid JSON Schema") from exc

            # Evaluators are offline. External references may leak data or hang a worker.
            def inspect(value: Any) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {"$id", "$anchor", "$dynamicAnchor", "$recursiveRef"}:
                            raise ValueError(
                                "JSON Schema identifiers and dynamic resolution are unsupported offline"
                            )
                        if key in {"pattern", "patternProperties"}:
                            raise ValueError(
                                "Regular expressions are not supported in offline schemas; use explicit enum or property checks"
                            )
                        if key in {"$ref", "$dynamicRef"} and (
                            not isinstance(item, str) or not item.startswith("#/$defs/")
                        ):
                            raise ValueError("JSON Schema references must point into local $defs")
                        inspect(item)
                elif isinstance(value, list):
                    for item in value:
                        inspect(item)

            inspect(self.expected)
        if self.kind == "numeric" and (
            isinstance(self.expected, bool) or not isinstance(self.expected, (int, float))
        ):
            raise ValueError("Numeric checks require a numeric expected value")
        if self.kind == "repository_tests":
            node = self.expected.get("node") if isinstance(self.expected, dict) else None
            if (
                not isinstance(node, dict)
                or node.get("runner") not in {"node-test", "jest", "vitest"}
                or not isinstance(node.get("selectors"), list)
                or not node["selectors"]
                or not all(isinstance(s, str) and s.strip() for s in node["selectors"])
            ):
                raise ValueError("Repository checks require a Node runner and nonempty selectors")
        if self.kind == "python_tests":
            if not isinstance(self.expected, dict):
                raise ValueError("Python checks require a verifier object")
            if "cases" in self.expected:
                if (
                    not isinstance(self.expected["cases"], list)
                    or not self.expected["cases"]
                    or not isinstance(self.expected.get("entrypoint"), str)
                ):
                    raise ValueError("Python case checks require nonempty cases and an entrypoint")
            elif "pytest" in self.expected:
                profile = self.expected["pytest"]
                if not isinstance(profile, dict) or not profile.get("selectors"):
                    raise ValueError("Repository checks require nonempty pytest selectors")
            elif not isinstance(self.expected.get("tests"), str):
                raise ValueError("Python checks require expected.tests or cases with an entrypoint")
        return self


class Scenario(Manifest):
    id: str = Field(
        default_factory=new_id, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    name: str = Field(min_length=1, max_length=200)
    domain: Domain
    description: str = Field(default="", max_length=20000)
    instruction: str = Field(min_length=1, max_length=100000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    checks: list[Check] = Field(min_length=1, max_length=100)
    reference_output: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list, max_length=100)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_checks(self) -> Self:
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("Check IDs must be unique within each scenario")
        return self

    def agent_view(self) -> dict[str, Any]:
        """Construct a whitelist; never serialize evaluator-only data to a provider."""
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "instruction": self.instruction,
            "inputs": self.inputs,
        }


class Collection(Manifest):
    schema_version: Literal[1] = 1
    id: str = Field(
        default_factory=new_id, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    domain: Literal["coding", "documents", "data", "workflow", "mixed"] = "mixed"
    version: str = Field(default="1.0.0", min_length=1, max_length=64)
    created_at: str = Field(default_factory=utc_now)
    benchmark_release_id: str | None = Field(default=None, max_length=128)
    benchmark_partition: Literal["dev", "holdout", "all"] | None = None
    scenarios: list[Scenario] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_scenarios(self) -> Self:
        if len({s.id for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("Scenario IDs must be unique within each collection")
        if self.domain != "mixed" and any(s.domain != self.domain for s in self.scenarios):
            raise ValueError(
                "Collection domain must match its scenarios; use mixed for multiple domains"
            )
        return self


class ReplaySubmission(Manifest):
    """Evidence supplied by an external harness; unavailable metrics stay null."""

    output: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=2000)
    cost_usd: float | None = Field(default=None, ge=0, le=1000000)
    input_tokens: int | None = Field(default=None, ge=0, le=1000000000)
    output_tokens: int | None = Field(default=None, ge=0, le=1000000000)
    duration_ms: float | None = Field(default=None, ge=0, le=604800000)
    trace: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    provenance: dict[str, Any] = Field(default_factory=dict)


class System(Manifest):
    id: str = Field(
        default_factory=new_id, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["reference", "incomplete", "http", "replay"]
    model: str = Field(default="", max_length=200)
    base_url: str = Field(default="", max_length=2000)
    api_key_env: str = Field(default="", max_length=128)
    temperature: float | None = Field(default=None, ge=0, le=2)
    additional_instructions: str = Field(default="", max_length=8000)
    harness: Literal["single-turn", "tools", "external-agent"] = "single-turn"
    adapter_revision: str = Field(default="", max_length=200)
    adapter_input_token_limit: int = Field(default=131072, ge=1, le=100000000)
    adapter_output_token_limit: int = Field(default=32768, ge=1, le=100000000)
    max_steps: int = Field(default=8, ge=1, le=30)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    input_cost_per_million: float | None = Field(default=None, ge=0, le=100000)
    output_cost_per_million: float | None = Field(default=None, ge=0, le=100000)
    pricing_version: str = Field(default="user-configured", max_length=200)
    timeout_seconds: float = Field(default=60, ge=1, le=120)
    token_limit_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"

    collection_hash: str = Field(default="", max_length=64)
    submissions: dict[str, ReplaySubmission] = Field(default_factory=dict, max_length=1000)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def provider_config(self) -> Self:
        if self.additional_instructions and (
            self.kind != "http" or self.harness == "external-agent"
        ):
            raise ValueError(
                "Additional instructions require a live single-turn or tool harness; external adapter changes need a new revision"
            )
        if self.kind == "replay":
            if not re.fullmatch(r"[a-f0-9]{64}", self.collection_hash):
                raise ValueError("Replay systems require a SHA-256 collection fingerprint")
        elif self.submissions or self.collection_hash:
            raise ValueError("Recorded submissions are only valid for replay systems")

        if self.api_key_env and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", self.api_key_env):
            raise ValueError("api_key_env must be an environment variable name, not a key")
        if self.harness == "external-agent" and (
            self.kind != "http" or not self.adapter_revision.strip()
        ):
            raise ValueError("External agents require an HTTP system and a pinned adapter revision")
        if self.kind == "http":
            url = urlsplit(self.base_url)
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError("Provider port must be between 1 and 65535")
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Provider URL must be an HTTP(S) base URL without credentials, query, or fragment"
                )
            if not self.model.strip():
                raise ValueError("HTTP systems require a model identifier")
        return self


class ExperimentCreate(Manifest):
    name: str = Field(min_length=1, max_length=200)
    collection_id: str = Field(min_length=1, max_length=128)
    system_ids: list[str] = Field(min_length=1, max_length=20)
    repetitions: int = Field(default=1, ge=1, le=30)
    budget_usd: float = Field(default=0, ge=0, le=100000)

    @field_validator("system_ids")
    @classmethod
    def unique_systems(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("System IDs must be unique")
        return value
