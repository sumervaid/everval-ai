"""Live adapter contract, public/private boundaries, and SDK authorization."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from cvals.agent_sdk import AgentRequest, AgentResult, AgentUsage, create_agent_app
from cvals.config import Settings
from cvals.models import Check, Scenario, System
from cvals.providers import reserve_cost, submit


@pytest.fixture
def task():
    return Scenario(
        id="public",
        name="Public task",
        domain="data",
        instruction="Return total.",
        inputs={"rows": [2, 3]},
        expected={"secret": "PRIVATE_EXPECTED"},
        provenance={"private": "PRIVATE_PROVENANCE"},
        reference_output={"total": 5, "secret": "PRIVATE_FIX"},
        checks=[Check(name="Total", kind="equals", path="total", expected=5)],
    )


@pytest.fixture
def system():
    return System(
        name="Team agent",
        kind="http",
        model="internal-agent",
        base_url="http://127.0.0.1:9191",
        harness="external-agent",
        adapter_revision="reviewed-v1",
        input_cost_per_million=1,
        output_cost_per_million=2,
        adapter_input_token_limit=1000,
        adapter_output_token_limit=200,
    )


def mock_transport(monkeypatch, callback):
    original = httpx.Client
    monkeypatch.setattr(
        "cvals.external_agent.httpx.Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(callback),
            **kwargs,
        ),
    )


def test_adapter_receives_only_public_contract_and_known_usage(task, system, tmp_path, monkeypatch):
    def respond(request):
        body = json.loads(request.content)
        encoded = json.dumps(body)
        assert "PRIVATE_" not in encoded
        assert body["revision"] == "reviewed-v1"
        assert body["limits"]["input_tokens"] == 1000
        parsed = AgentRequest.model_validate(body)
        assert parsed.task.inputs["rows"] == [2, 3]
        return httpx.Response(
            200,
            json={
                "revision": "reviewed-v1",
                "output": {"total": 5},
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "trace": [{"tool": "calculator", "duration_ms": 2}],
            },
        )

    mock_transport(monkeypatch, respond)
    result = submit(task, system, Settings(data_dir=tmp_path), 1)
    assert result.output == {"total": 5} and result.error is None
    assert result.cost_usd == 0.00002
    assert result.metadata["timing"]["provider_ms"] >= 0
    assert reserve_cost(task, system) == 0.0014


@pytest.mark.parametrize(
    "change,error",
    [
        ({"revision": "different"}, "revision"),
        ({"usage": None}, "usage"),
        ({"usage": {"prompt_tokens": 1001, "completion_tokens": 1}}, "ceilings"),
    ],
)
def test_adapter_blocks_invalid_execution_evidence(
    task, system, tmp_path, monkeypatch, change, error
):
    raw = {
        "revision": "reviewed-v1",
        "output": {"total": 5},
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        **change,
    }
    mock_transport(monkeypatch, lambda _: httpx.Response(200, json=raw))
    result = submit(task, system, Settings(data_dir=tmp_path), 1)
    assert result.output is None
    assert error in result.error
    if error != "revision":
        assert result.metadata["budget_uncertain"]


def test_sdk_checks_revision_auth_and_rejects_private_fields(task, system, monkeypatch):
    monkeypatch.setenv("TEST_ADAPTER_TOKEN", "local-secret")
    calls = []

    def handle(request):
        calls.append(request.request_id)
        return AgentResult(
            revision="reviewed-v1",
            output={"total": 5},
            usage=AgentUsage(prompt_tokens=1, completion_tokens=1),
        )

    app = create_agent_app(handle, revision="reviewed-v1", token_env="TEST_ADAPTER_TOKEN")
    body = {
        "request_id": "test",
        "revision": "reviewed-v1",
        "model": "internal-agent",
        "task": task.agent_view(),
        "limits": {"input_tokens": 1000, "output_tokens": 200, "timeout_seconds": 10},
    }
    with TestClient(app) as client:
        assert client.post("/everval/v1/tasks", json=body).status_code == 401
        headers = {"Authorization": "Bearer local-secret"}
        assert (
            client.post(
                "/everval/v1/tasks", json={**body, "revision": "wrong"}, headers=headers
            ).status_code
            == 409
        )
        private = {**body, "task": {**task.agent_view(), "checks": []}}
        assert client.post("/everval/v1/tasks", json=private, headers=headers).status_code == 422
        result = client.post("/everval/v1/tasks", json=body, headers=headers)
        assert result.status_code == 200
        assert result.json()["output"] == {"total": 5}
    assert calls == ["test"]


@pytest.mark.parametrize("tokens", [True, False, "10", 10.0, 1.5])
def test_adapter_rejects_coerced_usage_as_unknown(task, system, tmp_path, monkeypatch, tokens):
    raw = {
        "revision": "reviewed-v1",
        "output": {"total": 5},
        "usage": {"prompt_tokens": tokens, "completion_tokens": 1},
    }
    mock_transport(monkeypatch, lambda _: httpx.Response(200, json=raw))
    result = submit(task, system, Settings(data_dir=tmp_path), 1)
    assert result.error and result.output is None
    assert result.cost_usd is None and result.metadata["budget_uncertain"]


@pytest.mark.parametrize("secret", ["ordinary-secret", 'secret"with\\escaping'])
def test_adapter_discards_output_that_echoes_credential_and_redacts_trace(
    task, system, tmp_path, monkeypatch, secret
):
    monkeypatch.setenv("TEST_AGENT_KEY", secret)
    system = system.model_copy(update={"api_key_env": "TEST_AGENT_KEY"})
    raw = {
        "revision": "reviewed-v1",
        "output": {"nested": [{"credential_copy": "prefix " + secret}]},
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "trace": [{"message": "echo: " + secret, secret: "value"}],
    }
    mock_transport(monkeypatch, lambda _: httpx.Response(200, json=raw))
    result = submit(task, system, Settings(data_dir=tmp_path), 1)
    assert result.output is None and "credential" in result.error
    assert result.cost_usd == 0.00002
    assert secret not in str(result.metadata)
    assert result.metadata["trace"] == [{"message": "echo: [redacted]", "[redacted]": "value"}]
