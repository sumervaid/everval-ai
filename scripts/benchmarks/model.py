"""Metered OpenAI text-action transport for the public-repository pilot.

Credentials and trajectories stay in the local campaign directory. Every paid
request needs a durable reservation; missing usage stops subsequent admissions.
No request is retried automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from cvals.providers import _pin_destination

from .budget import BudgetLedger

ENDPOINT = "https://api.openai.com/v1/chat/completions"
MAX_PROMPT_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FRAMING_TOKEN_ALLOWANCE = 8192


class ProviderFailure(RuntimeError):
    """The attempt failed, with any reported usage retained."""


class UnknownUsage(ProviderFailure):
    """The campaign cannot admit another paid request."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def private_json(path: Path, value: Any) -> None:
    """Create evidence exactly once without overwriting a prior request."""
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


class MeteredOpenAI:
    """A small model transport; upstream action parsing belongs to the executor."""

    def __init__(
        self,
        *,
        ledger: BudgetLedger,
        attempt_id: str,
        attempt_hash: str,
        output_dir: Path,
        model_name: str = "gpt-6.1-sol",
        reasoning_effort: str = "medium",
        max_completion_tokens: int = 8192,
        client: httpx.Client | None = None,
        api_key: str | None = None,
    ) -> None:
        if model_name != "gpt-6.1-sol":
            raise ValueError("This frozen pricing profile supports gpt-6.1-sol only")
        if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Unsupported reasoning effort")
        if type(max_completion_tokens) is not int or not 1 <= max_completion_tokens <= 8192:
            raise ValueError("Completion ceiling must be an integer between 1 and 8192")
        if ledger.config.input_rate_per_million != Decimal(
            "2.50"
        ) or ledger.config.output_rate_per_million != Decimal("10"):
            raise ValueError("Ledger rates differ from the frozen model price profile")
        self.ledger = ledger
        self.attempt_id = attempt_id
        self.model_name = model_name
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output_dir.chmod(0o700)
        self._key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        if not self._key:
            raise ValueError("OPENAI_API_KEY is required for an explicitly admitted live run")
        self._owned_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(240, connect=20),
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": [_pin_destination]},
        )
        self.ledger.register_attempt(attempt_id, attempt_hash)
        self.calls = 0
        self.cost = 0.0

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def get_template_vars(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model": self.model_name,
            "cost": self.cost,
            "n_calls": self.calls,
        }

    def serialize(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "reasoning_effort": self.reasoning_effort,
            "max_completion_tokens": self.max_completion_tokens,
            "calls": self.calls,
            "recorded_cost_usd": self.cost,
            "attempt_id": self.attempt_id,
            "automatic_retries": 0,
        }

    @staticmethod
    def format_message(*, role: str, content: str, extra: dict | None = None) -> dict:
        return {"role": role, "content": content, "extra": extra or {}}

    def query(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        wire_messages = []
        if not 1 <= len(messages) <= 512:
            raise ValueError("Message count exceeds this campaign's bounds")
        for message in messages:
            if message.get("role") not in {"system", "user", "assistant"}:
                raise ValueError("Only text-action conversation roles are supported")
            if not isinstance(message.get("content"), str):
                raise ValueError("Only plain-text messages are supported")
            wire_messages.append({"role": message["role"], "content": message["content"]})
        prompt_bytes = canonical(wire_messages)
        if len(prompt_bytes) > MAX_PROMPT_BYTES:
            raise ValueError("Conversation exceeds the frozen 128 KiB prompt limit")
        payload = {
            "model": self.model_name,
            "messages": wire_messages,
            "reasoning_effort": self.reasoning_effort,
            "max_completion_tokens": self.max_completion_tokens,
            "service_tier": "default",
            "stream": False,
            "store": False,
            "n": 1,
        }
        body = canonical(payload)
        request_hash = hashlib.sha256(body).hexdigest()
        request_id = self.ledger.reserve(
            self.attempt_id,
            request_hash,
            input_token_ceiling=len(prompt_bytes) + FRAMING_TOKEN_ALLOWANCE,
            output_token_ceiling=self.max_completion_tokens,
        )
        prefix = self.output_dir / str(request_id)
        started = time.monotonic()
        request_evidence = {
            "request_id": request_id,
            "request_sha256": request_hash,
            "started_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        raw = None
        try:
            private_json(prefix.with_suffix(".request.json"), request_evidence)
            with self._client.stream(
                "POST",
                ENDPOINT,
                headers={
                    "Authorization": "Bearer " + self._key,
                    "Content-Type": "application/json",
                },
                content=body,
            ) as response:
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise UnknownUsage("Provider response exceeds the evidence size limit")
                status = response.status_code
                try:
                    raw = json.loads(chunks)
                except (ValueError, UnicodeError) as exc:
                    raise UnknownUsage("Provider response is not valid JSON") from exc
                if not isinstance(raw, dict):
                    raise UnknownUsage("Provider response is not an object")
            private_json(
                prefix.with_suffix(".response.json"),
                {
                    "http_status": status,
                    "duration_seconds": time.monotonic() - started,
                    "response": raw,
                },
            )
            usage = raw.get("usage")
            if not isinstance(usage, dict):
                raise UnknownUsage("Provider did not report usage")
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            if (
                type(input_tokens) is not int
                or type(output_tokens) is not int
                or input_tokens < 0
                or output_tokens < 0
            ):
                raise UnknownUsage("Provider usage is invalid")
            if raw.get("service_tier", "default") not in {None, "default"}:
                self.ledger.uncertain(
                    request_id,
                    "unpriced_service_tier",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                raise UnknownUsage("Provider returned an unpriced service tier")
            if raw.get("model") != self.model_name:
                self.ledger.uncertain(
                    request_id,
                    "unpriced_model",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                raise UnknownUsage("Provider returned a model outside the frozen price profile")
            cost = self.ledger.settle(request_id, input_tokens, output_tokens)
        except BaseException as exc:
            # Even interruption after sending a request must leave admissions closed.
            # The ledger treats settled requests idempotently; a known charge is retained.
            partial = raw.get("usage", {}) if isinstance(raw, dict) else {}
            if not isinstance(partial, dict):
                partial = {}
            known = {}
            for field, source in (
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "completion_tokens"),
            ):
                value = partial.get(source)
                if type(value) is int and 0 <= value < 2**63:
                    known[field] = value
            with suppress(Exception):
                self.ledger.uncertain(request_id, type(exc).__name__, **known)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise UnknownUsage("Paid request has unresolved accounting; campaign stopped") from exc
        self.calls += 1
        self.cost += float(cost)
        if status != 200:
            raise ProviderFailure(f"Provider HTTP status {status}; reported usage was retained")
        choices = raw.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderFailure("Provider returned an invalid choice count; usage was retained")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderFailure("Provider returned no text action; usage was retained")
        return {
            "role": "assistant",
            "content": message["content"],
            "extra": {
                "cost": float(cost),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "request_id": request_id,
                "response_model": raw.get("model"),
                "finish_reason": choice.get("finish_reason"),
                "duration_seconds": time.monotonic() - started,
            },
        }
