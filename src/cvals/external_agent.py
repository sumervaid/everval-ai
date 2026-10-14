"""Bounded live agent adapters using the same pricing and destination controls."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from cvals.agent_sdk import AgentLimits, AgentRequest, AgentResult, PublicTask
from cvals.audit import _redact
from cvals.models import Scenario, System, canonical_json, new_id
from cvals.providers import (
    MAX_PROMPT_BYTES,
    MAX_RESPONSE_BYTES,
    Submission,
    _pin_destination,
    _usage,
)


def reservation(system: System) -> float:
    return (
        system.adapter_input_token_limit * (system.input_cost_per_million or 0)
        + system.adapter_output_token_limit * (system.output_cost_per_million or 0)
    ) / 1_000_000


def submit_external(scenario: Scenario, system: System) -> Submission:
    request = AgentRequest(
        request_id=new_id(),
        revision=system.adapter_revision,
        model=system.model,
        task=PublicTask.model_validate(scenario.agent_view()),
        limits=AgentLimits(
            input_tokens=system.adapter_input_token_limit,
            output_tokens=system.adapter_output_token_limit,
            timeout_seconds=system.timeout_seconds,
        ),
    )
    payload = request.model_dump(mode="json")
    if len(canonical_json(payload).encode()) > MAX_PROMPT_BYTES:
        raise ValueError("Public adapter input exceeds 128 KiB")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    secret = os.getenv(system.api_key_env, "") if system.api_key_env else ""
    if secret:
        headers["Authorization"] = "Bearer " + secret
    result = Submission(
        None,
        metadata={
            "harness": "external-agent",
            "adapter_revision": system.adapter_revision,
            "request_id": request.request_id,
            "pricing_version": system.pricing_version,
            "cost_basis": "adapter-reported aggregate token usage × configured rates",
            "usage_source": "adapter reported; not independently verified",
        },
    )
    started = time.perf_counter()
    try:
        with (
            httpx.Client(
                timeout=httpx.Timeout(
                    system.timeout_seconds, connect=min(10, system.timeout_seconds)
                ),
                follow_redirects=False,
                trust_env=False,
                event_hooks={"request": [_pin_destination]},
            ) as client,
            client.stream(
                "POST",
                system.base_url.rstrip("/") + "/everval/v1/tasks",
                json=payload,
                headers=headers,
            ) as response,
        ):
            if response.status_code >= 300:
                result.error = f"Agent adapter returned HTTP {response.status_code}; body omitted"
                return result
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if time.perf_counter() - started > system.timeout_seconds:
                    result.error = "Agent adapter exceeded its response deadline"
                    return result
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    result.error = "Agent adapter response exceeds 2 MiB"
                    return result
                raw.extend(chunk)
        data = AgentResult.model_validate_json(raw)
        incoming, outgoing, cost = _usage(data.usage.model_dump() if data.usage else None, system)
        result.input_tokens, result.output_tokens, result.cost_usd = incoming, outgoing, cost

        # Redact strings before JSON serialization, including tokens containing
        # quote/backslash characters and tokens accidentally used as dictionary keys.
        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(secret, "[redacted]") if secret else value
            if isinstance(value, dict):
                return {scrub(key): scrub(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value

        result.metadata["trace"] = scrub(_redact(data.trace))
        if secret and scrub(data.output) != data.output:
            result.error = (
                "Agent adapter output contained its configured credential; output discarded"
            )
            return result
        if data.revision != system.adapter_revision:
            result.error = "Agent adapter revision differs from the frozen system"
        elif incoming is None or outgoing is None or cost is None:
            result.error = (
                "Agent adapter usage is unavailable; additional paid requests are blocked"
            )
            result.metadata["budget_uncertain"] = True
        elif (
            incoming > system.adapter_input_token_limit
            or outgoing > system.adapter_output_token_limit
        ):
            result.error = "Agent adapter exceeded the reserved token ceilings"
            result.metadata["budget_uncertain"] = True
        elif data.error:
            result.error = data.error.replace(secret, "[redacted]") if secret else data.error
        elif data.output is None:
            result.error = "Agent adapter did not return an output"
        else:
            result.output = data.output
        return result
    except (httpx.HTTPError, OSError, ValueError, RecursionError):
        result.error = "Agent adapter transport or response failed; billing may be unknown"
        return result
    finally:
        result.metadata["timing"] = {
            "provider_ms": round((time.perf_counter() - started) * 1000, 3)
        }
        if result.cost_usd is None:
            result.metadata["budget_uncertain"] = True
