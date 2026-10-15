"""Provider transport checks use an in-memory HTTP endpoint and no live credentials."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmarks.budget import BudgetConfig, BudgetError, BudgetLedger  # noqa: E402
from scripts.benchmarks.model import (  # noqa: E402
    MAX_PROMPT_BYTES,
    MeteredOpenAI,
    ProviderFailure,
    UnknownUsage,
)

MESSAGES = [{"role": "user", "content": "Investigate a public fixture."}]


def ledger_at(path: Path) -> BudgetLedger:
    return BudgetLedger(
        path,
        BudgetConfig(
            campaign_id="transport-fixture",
            campaign_config_hash="a" * 64,
            pricing_version="fixture-price-version",
        ),
    )


def response_body(**updates: object) -> dict:
    return {
        "model": "gpt-6.1-sol",
        "service_tier": "default",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "choices": [{"message": {"role": "assistant", "content": "text"}, "finish_reason": "stop"}],
        **updates,
    }


def make_model(tmp_path: Path, handler) -> tuple[MeteredOpenAI, BudgetLedger]:
    ledger = ledger_at(tmp_path / "ledger.sqlite3")
    return (
        MeteredOpenAI(
            ledger=ledger,
            attempt_id="fixture-attempt",
            attempt_hash="b" * 64,
            output_dir=tmp_path / "private",
            api_key="fixture-secret-never-sent-to-network",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        ledger,
    )


def test_usage_is_settled_before_model_content_is_returned(tmp_path: Path) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        assert request.url.host == "api.openai.com"
        assert payload["store"] is False
        assert payload["service_tier"] == "default"
        assert "tools" not in payload
        assert "extra" not in payload["messages"][0]
        return httpx.Response(200, json=response_body())

    model, ledger = make_model(tmp_path, handler)
    result = model.query([{"role": "user", "content": "fixture", "extra": {"private": "omit"}}])
    assert result["extra"]["cost"] == float(Decimal("0.00045"))
    assert model.calls == len(seen) == 1
    assert model.cost == result["extra"]["cost"]
    evidence = list((tmp_path / "private").glob("*.json"))
    assert len(evidence) == 2
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in evidence)
    assert "fixture-secret" not in json.dumps(model.serialize())
    assert all("fixture-secret" not in p.read_text() for p in evidence)
    # A known settlement allows a distinct next request.
    model.query([{"role": "user", "content": "another fixture"}])
    assert model.calls == 2


def test_missing_usage_stops_all_subsequent_requests(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=response_body(usage=None))

    model, ledger = make_model(tmp_path, handler)
    with pytest.raises(UnknownUsage):
        model.query(MESSAGES)
    with pytest.raises(BudgetError):
        model.query([{"role": "user", "content": "try a different request"}])
    assert len(calls) == 1
    # Reopening the same durable ledger cannot turn the unknown charge into zero.
    reopened = ledger_at(tmp_path / "ledger.sqlite3")
    with pytest.raises(BudgetError):
        reopened.reserve("fixture-attempt", "c" * 64, 100, 100)


def test_transport_failure_is_not_retried(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("fixture timeout")

    model, ledger = make_model(tmp_path, handler)
    with pytest.raises(UnknownUsage):
        model.query(MESSAGES)
    assert len(calls) == 1
    with pytest.raises(BudgetError):
        ledger.reserve("fixture-attempt", "d" * 64, 100, 100)


def test_prompt_bound_rejects_before_any_request(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("An oversized prompt reached HTTP transport")

    model, ledger = make_model(tmp_path, handler)
    with pytest.raises(ValueError, match="128 KiB"):
        model.query([{"role": "user", "content": "x" * MAX_PROMPT_BYTES}])
    # No unresolved reservation was created.
    ledger.reserve("fixture-attempt", "e" * 64, 100, 100)


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": -1},
        {"prompt_tokens": 1},
    ],
)
def test_invalid_usage_remains_unsettled(tmp_path: Path, usage: dict) -> None:
    model, ledger = make_model(
        tmp_path, lambda request: httpx.Response(200, json=response_body(usage=usage))
    )
    with pytest.raises(UnknownUsage):
        model.query(MESSAGES)
    with pytest.raises(BudgetError):
        ledger.reserve("fixture-attempt", "f" * 64, 100, 100)


def test_response_over_reservation_preserves_known_cost_and_stops(tmp_path: Path) -> None:
    model, ledger = make_model(
        tmp_path,
        lambda request: httpx.Response(
            200, json=response_body(usage={"prompt_tokens": 200_000, "completion_tokens": 20})
        ),
    )
    with pytest.raises(UnknownUsage):
        model.query(MESSAGES)
    with pytest.raises(BudgetError):
        ledger.reserve("fixture-attempt", "f" * 64, 100, 100)
    # The overrun is a recorded, chargeable observation, not discarded evidence.
    raw = next((tmp_path / "private").glob("*.response.json"))
    assert json.loads(raw.read_text())["response"]["usage"]["prompt_tokens"] == 200_000


def test_priced_usage_is_retained_when_provider_output_is_invalid(tmp_path: Path) -> None:
    model, ledger = make_model(
        tmp_path, lambda request: httpx.Response(200, json=response_body(choices=[]))
    )
    with pytest.raises(ProviderFailure, match="choice count"):
        model.query(MESSAGES)
    assert model.cost == float(Decimal("0.00045"))
    # Accounting is settled despite a failed task attempt.
    ledger.reserve("fixture-attempt", "f" * 64, 100, 100)


def test_unexpected_response_model_retains_known_usage_and_blocks_more_calls(
    tmp_path: Path,
) -> None:
    model, ledger = make_model(
        tmp_path,
        lambda request: httpx.Response(200, json=response_body(model="unpriced-model-fixture")),
    )
    with pytest.raises(UnknownUsage):
        model.query(MESSAGES)
    status = ledger.status()
    assert status["unsettled_known_lower_bound_usd"] == "0.00045"
    assert status["uncertain_requests"] == 1
    assert status["cost_complete"] is False
    with pytest.raises(BudgetError):
        model.query([{"role": "user", "content": "No further paid admission"}])
    assert len(ledger.status()["requests"]) == 1


def test_partial_invalid_usage_keeps_the_valid_input_cost_lower_bound(tmp_path: Path) -> None:
    model, ledger = make_model(
        tmp_path,
        lambda request: httpx.Response(
            200, json=response_body(usage={"prompt_tokens": 100, "completion_tokens": -1})
        ),
    )
    with pytest.raises(UnknownUsage):
        model.query(MESSAGES)
    status = ledger.status()
    assert status["unsettled_known_lower_bound_usd"] == "0.00025"
    assert status["requests"][0]["input_tokens"] == 100
    assert status["requests"][0]["output_tokens"] is None
    assert status["uncertain_requests"] == 1
    assert status["admissions_blocked"] is True
