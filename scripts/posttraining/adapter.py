"""Immutable loopback adapter for the prospective local checkpoint study.

This is trusted research code. It serves one verified checkpoint and performs no
repository execution, network downloads, model switching, or answer repair.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from cvals.agent_sdk import AgentRequest, AgentResult, AgentUsage, create_agent_app
from cvals.models import content_hash
from cvals.training_ingestion import hash_checkpoint_artifact

SYSTEM_PROMPT = "Follow the task instructions. Return only the requested JSON object."
COST_SCOPE = (
    "Zero local provider cash fee. Hardware, electricity, training, operator time, "
    "and development-service costs are excluded; elapsed time is measured separately."
)
PARSER_VERSION = "strict-json-object-v1"


class Runtime(Protocol):
    def prompt_token_count(self, messages: list[dict[str, str]]) -> int: ...

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 32,
        *,
        max_prompt_tokens: int = 512,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]: ...


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    return digest.hexdigest()


def checkpoint_identity(base: Path, adapters: Path | None) -> dict[str, Any]:
    """A reproducible digest of actual finalized artifacts, without seed salting."""
    manifest = {
        "format": "everval-mlx-checkpoint-v1",
        "base_sha256": hash_checkpoint_artifact(base),
        "adapter_sha256": hash_checkpoint_artifact(adapters) if adapters else None,
    }
    return {**manifest, "weight_sha256": content_hash(manifest)}


def adapter_revision(
    identity: Mapping[str, Any],
    *,
    model_id: str,
    max_tokens: int,
    max_prompt_tokens: int,
    requirements_lock: Path,
    runtime_source: Path,
) -> str:
    return content_hash(
        {
            "format": "everval-local-study-adapter-v1",
            "checkpoint": dict(identity),
            "model_id": model_id,
            "max_tokens": max_tokens,
            "max_prompt_tokens": max_prompt_tokens,
            "temperature": 0,
            "system_prompt": SYSTEM_PROMPT,
            "parser_version": PARSER_VERSION,
            "adapter_source_sha256": file_sha256(Path(__file__)),
            "runtime_source_sha256": file_sha256(runtime_source),
            "requirements_lock_sha256": file_sha256(requirements_lock),
        }
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"Nonstandard JSON constant: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Nonfinite JSON number")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON member")
        value[key] = item
    return value


def parse_model_output(text: str) -> tuple[dict[str, Any], str | None]:
    """Retain parse failures as empty, unsuccessful submissions; never repair text."""
    try:
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_unique_object,
        )
        if not isinstance(value, dict):
            return {}, "The generated JSON value is not an object."
        return value, None
    except (ValueError, RecursionError):
        return {}, "The generated text is not a strict, unique-key JSON object."


def render_messages(request: AgentRequest) -> list[dict[str, str]]:
    """The complete user prompt is frozen in the scenario instruction."""
    if request.task.inputs:
        raise ValueError("Study tasks must put the complete frozen prompt in instruction")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": request.task.instruction},
    ]


class CheckpointAdapter:
    """Serialize inference and return known resource accounting even for bad answers."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        model_id: str,
        revision: str,
        weight_sha256: str,
        max_tokens: int,
        max_prompt_tokens: int,
    ):
        if not 1 <= max_tokens <= 32768 or not 1 <= max_prompt_tokens <= 100000:
            raise ValueError("Token limits must be positive and bounded")
        self.runtime = runtime
        self.model_id = model_id
        self.revision = revision
        self.weight_sha256 = weight_sha256
        self.max_tokens = max_tokens
        self.max_prompt_tokens = max_prompt_tokens
        self._lock = threading.Lock()

    def _rejected(self, reason: str) -> AgentResult:
        return AgentResult(
            revision=self.revision,
            usage=AgentUsage(prompt_tokens=0, completion_tokens=0),
            error=reason,
            trace=[{"kind": "request_rejected", "inference_started": False}],
        )

    def __call__(self, request: AgentRequest) -> AgentResult:
        if request.revision != self.revision or request.model != self.model_id:
            return self._rejected("Requested model or revision differs from this checkpoint")
        if not self._lock.acquire(blocking=False):
            return self._rejected("This single-checkpoint inference service is busy")
        started = time.perf_counter()
        try:
            try:
                messages = render_messages(request)
            except ValueError as error:
                return self._rejected(str(error))
            input_limit = min(request.limits.input_tokens, self.max_prompt_tokens)
            output_limit = min(request.limits.output_tokens, self.max_tokens)
            count = self.runtime.prompt_token_count(messages)
            if count > input_limit:
                return self._rejected("Tokenized public prompt exceeds its frozen input ceiling")
            generated = self.runtime.generate(
                messages,
                max_tokens=output_limit,
                max_prompt_tokens=input_limit,
                timeout_seconds=request.limits.timeout_seconds,
            )
            text = generated["text"]
            incoming, outgoing = generated["prompt_tokens"], generated["completion_tokens"]
            elapsed_ms = generated["elapsed_ms"]
            if (
                not isinstance(text, str)
                or type(incoming) is not int
                or type(outgoing) is not int
                or incoming != count
                or not 0 <= incoming <= input_limit
                or not 0 <= outgoing <= output_limit
                or not isinstance(elapsed_ms, (int, float))
                or not math.isfinite(elapsed_ms)
                or elapsed_ms < 0
            ):
                raise ValueError("Runtime returned invalid or out-of-ceiling accounting")
            finish = generated["finish_reason"]
            if finish not in {"stop", "length", "timeout"}:
                raise ValueError("Runtime returned an unknown finish reason")
            output, parse_error = parse_model_output(text)
            trace = [
                {
                    "kind": "local_checkpoint_generation",
                    "weight_sha256": self.weight_sha256,
                    "raw_text": text,
                    "parse_valid": parse_error is None,
                    "parse_error": parse_error,
                    "parser_version": PARSER_VERSION,
                    "finish_reason": finish,
                    "inference_ms": elapsed_ms,
                    "handler_ms": round((time.perf_counter() - started) * 1000, 3),
                    "cost_scope": COST_SCOPE,
                    "deadline_limit": "Deadline checked between token kernels; kernels are not preemptible.",
                }
            ]
            return AgentResult(
                revision=self.revision,
                output=output if finish != "timeout" else None,
                usage=AgentUsage(prompt_tokens=incoming, completion_tokens=outgoing),
                trace=trace,
                error="Local inference exceeded its token-step deadline"
                if finish == "timeout"
                else None,
            )
        finally:
            self._lock.release()

    def app(self):
        return create_agent_app(self, revision=self.revision)
