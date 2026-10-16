"""Explicit enterprise destinations and TLS trust use mock transports only."""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import certifi
import httpx
import pytest

from cvals.config import Settings, provider_origin
from cvals.models import Check, Scenario, System
from cvals.providers import (
    is_internal_provider,
    is_local_provider,
    provider_http_options,
    submit,
    validate_provider,
)


def internal_system(**changes):
    return System(
        **{
            "name": "Internal inference",
            "kind": "http",
            "model": "on-prem-model",
            "base_url": "https://inference.corp.example:8443/v1",
            "input_cost_per_million": 0,
            "output_cost_per_million": 0,
            **changes,
        }
    )


def internal_settings(tmp_path, **changes):
    return Settings(
        data_dir=tmp_path,
        internal_provider_origins=("https://inference.corp.example:8443",),
        **changes,
    )


def resolve(monkeypatch, *addresses):
    calls = []

    def getaddrinfo(host, port, **kwargs):
        calls.append((host, port))
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, 1, 6, "", (address, port))
            for address in addresses
        ]

    monkeypatch.setattr("cvals.providers.socket.getaddrinfo", getaddrinfo)
    return calls


@pytest.mark.parametrize(
    "origin",
    [
        "http://inference.corp.example",
        "https://*.corp.example",
        "https://user:secret@inference.corp.example",
        "https://inference.corp.example/",
        "https://inference.corp.example/v1",
        "https://inference.corp.example?x=1",
        "https://inference.corp.example#",
        "https://inference.corp.example:",
        "https://inference.corp.example:0",
        "https://inference.corp.example:65536",
        "https://inference.corp.example:bad",
        "https://inference.corp.example.evil/path",
        "https://[fe80::1%eth0]",
        "https://bad_host",
        "https://white space",
        "",
    ],
)
def test_origin_allowlist_is_exact_https_without_ambiguous_components(tmp_path, origin):
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, internal_provider_origins=(origin,))


def test_origin_normalization_and_environment_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "EVERVAL_INTERNAL_PROVIDER_ORIGINS",
        "https://INFERENCE.corp.example:443, https://[fd00::1]:8443",
    )
    monkeypatch.setenv("EVERVAL_PROVIDER_CA_BUNDLE", str(tmp_path / "corporate.pem"))
    monkeypatch.setenv("EVERVAL_ALLOW_REMOTE_PROVIDERS", "false")
    settings = Settings.from_env()
    assert settings.internal_provider_origins == (
        "https://[fd00::1]:8443",
        "https://inference.corp.example",
    )
    assert settings.provider_ca_bundle == tmp_path / "corporate.pem"
    assert settings.allow_remote_providers is False
    assert (
        provider_origin("https://inference.corp.example:443/v1") == "https://inference.corp.example"
    )
    with pytest.raises(ValueError, match="immutable"):
        Settings(data_dir=tmp_path, internal_provider_origins=["https://inference.corp.example"])


def test_internal_policy_does_not_expand_loopback_classification_or_public_opt_in(tmp_path):
    settings = internal_settings(tmp_path)
    system = internal_system()
    assert is_internal_provider(system, settings)
    assert not is_local_provider(system)
    validate_provider(system, settings, 0)
    for base_url in (
        "https://inference.corp.example:9443/v1",
        "https://other.corp.example:8443/v1",
        "https://inference.corp.example.evil:8443/v1",
        "http://inference.corp.example:8443/v1",
    ):
        with pytest.raises(ValueError, match="disabled"):
            validate_provider(system.model_copy(update={"base_url": base_url}), settings, 0)


def test_missing_rates_and_priced_internal_budgets_remain_enforced(tmp_path):
    settings = internal_settings(tmp_path)
    with pytest.raises(ValueError, match="Explicit"):
        validate_provider(internal_system(input_cost_per_million=None), settings, 0)
    priced = internal_system(input_cost_per_million=1)
    with pytest.raises(ValueError, match="positive experiment budget"):
        validate_provider(priced, settings, 0)
    validate_provider(priced, settings, 1)
    public = internal_system(base_url="https://public.example/v1")
    with pytest.raises(ValueError, match="nonzero declared pricing"):
        validate_provider(public, replace(settings, allow_remote_providers=True), 1)


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "fd00::10",
        "fc00::1",
    ],
)
def test_approved_dns_answers_pin_private_address_preserving_origin(tmp_path, monkeypatch, address):
    settings = internal_settings(tmp_path)
    calls = resolve(monkeypatch, address)
    request = httpx.Request("POST", internal_system().base_url + "/chat/completions")
    hook = provider_http_options(internal_system(), settings)["event_hooks"]["request"][0]
    hook(request)
    assert request.url.host == address
    assert request.headers["host"] == "inference.corp.example:8443"
    assert request.extensions["sni_hostname"] == "inference.corp.example"
    assert request.url.path == "/v1/chat/completions"
    assert calls == [("inference.corp.example", 8443)]


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "169.254.1.1",
        "127.0.0.1",
        "::1",
        "fe80::1",
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
        "100.64.0.1",
        "172.32.0.1",
        "192.0.2.1",
        "240.0.0.1",
        "::ffff:10.0.0.1",
        "64:ff9b::a9fe:a9fe",
        "93.184.216.34",
    ],
)
def test_approved_origin_cannot_resolve_to_unapproved_networks(tmp_path, monkeypatch, address):
    resolve(monkeypatch, address)
    request = httpx.Request("POST", internal_system().base_url)
    hook = provider_http_options(internal_system(), internal_settings(tmp_path))["event_hooks"][
        "request"
    ][0]
    with pytest.raises(ValueError, match="nonpublic"):
        hook(request)


def test_mixed_dns_answers_fail_before_request_is_rewritten(tmp_path, monkeypatch):
    resolve(monkeypatch, "10.0.0.1", "169.254.169.254")
    request = httpx.Request("POST", internal_system().base_url)
    with pytest.raises(ValueError, match="nonpublic"):
        provider_http_options(internal_system(), internal_settings(tmp_path))["event_hooks"][
            "request"
        ][0](request)
    assert request.url.host == "inference.corp.example"


def test_new_request_is_checked_again_after_dns_rebinding(tmp_path, monkeypatch):
    calls = []

    def changing(*args, **kwargs):
        calls.append(args)
        address = "10.0.0.1" if len(calls) == 1 else "169.254.169.254"
        return [(2, 1, 6, "", (address, 8443))]

    monkeypatch.setattr("cvals.providers.socket.getaddrinfo", changing)
    hook = provider_http_options(internal_system(), internal_settings(tmp_path))["event_hooks"][
        "request"
    ][0]
    first = httpx.Request("POST", internal_system().base_url)
    hook(first)
    assert first.url.host == "10.0.0.1"
    with pytest.raises(ValueError, match="nonpublic"):
        hook(httpx.Request("POST", internal_system().base_url))
    assert len(calls) == 2


def test_hook_cannot_be_reused_for_another_origin(tmp_path, monkeypatch):
    calls = resolve(monkeypatch, "10.0.0.1")
    hook = provider_http_options(internal_system(), internal_settings(tmp_path))["event_hooks"][
        "request"
    ][0]
    with pytest.raises(ValueError, match="changed"):
        hook(httpx.Request("POST", "https://other.corp.example:8443/v1"))
    assert calls == []


def test_zero_cost_internal_origin_cannot_borrow_public_provider_permissions(tmp_path, monkeypatch):
    resolve(monkeypatch, "93.184.216.34")
    settings = internal_settings(tmp_path, allow_remote_providers=True)
    options = provider_http_options(internal_system(), settings)
    with pytest.raises(ValueError, match="nonpublic"):
        options["event_hooks"]["request"][0](httpx.Request("POST", internal_system().base_url))
    priced = internal_system(input_cost_per_million=1)
    request = httpx.Request("POST", priced.base_url)
    provider_http_options(priced, settings)["event_hooks"]["request"][0](request)
    assert request.url.host == "93.184.216.34"


@pytest.mark.parametrize(
    "url", ["http://localhost:9000/v1", "http://127.0.0.1:9000/v1", "http://[::1]:9000/v1"]
)
def test_loopback_policy_remains_available_without_approval(tmp_path, url):
    system = internal_system(base_url=url)
    settings = Settings(data_dir=tmp_path)
    validate_provider(system, settings, 0)
    request = httpx.Request("POST", url)
    provider_http_options(system, settings)["event_hooks"]["request"][0](request)
    assert request.url.host in {"127.0.0.1", "::1"}


def test_explicit_private_ip_origin_is_supported_and_metadata_literal_is_not(tmp_path):
    settings = Settings(data_dir=tmp_path, internal_provider_origins=("https://10.0.0.2",))
    system = internal_system(base_url="https://10.0.0.2/v1")
    validate_provider(system, settings, 0)
    request = httpx.Request("POST", system.base_url)
    provider_http_options(system, settings)["event_hooks"]["request"][0](request)
    assert request.url.host == "10.0.0.2"
    blocked = internal_system(base_url="https://169.254.169.254/v1")
    settings = replace(settings, internal_provider_origins=("https://169.254.169.254",))
    with pytest.raises(ValueError, match="outside"):
        validate_provider(blocked, settings, 0)


def test_ca_bundle_adds_trust_without_disabling_hostname_or_chain_verification(tmp_path):
    bundle = tmp_path / "corporate.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    settings = internal_settings(tmp_path, provider_ca_bundle=bundle)
    options = provider_http_options(internal_system(), settings)
    context = options["verify"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    assert provider_http_options(internal_system(), internal_settings(tmp_path))["verify"] is True


def test_missing_or_invalid_ca_never_disables_verification(tmp_path):
    settings = internal_settings(tmp_path, provider_ca_bundle=tmp_path / "missing.pem")
    with pytest.raises(ValueError, match="existing certificate"):
        provider_http_options(internal_system(), settings)
    bad = tmp_path / "invalid.pem"
    bad.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        provider_http_options(internal_system(), replace(settings, provider_ca_bundle=bad))


@pytest.mark.parametrize("harness", ["single-turn", "tools", "external-agent"])
def test_every_harness_uses_shared_internal_policy_and_tls(tmp_path, monkeypatch, harness):
    resolve(monkeypatch, "10.12.34.56")
    original = httpx.Client
    clients = []
    requests = []
    bundle = tmp_path / "corporate.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    settings = internal_settings(tmp_path, provider_ca_bundle=bundle)
    system = internal_system(
        harness=harness,
        adapter_revision="reviewed-v1" if harness == "external-agent" else "",
    )
    scenario = Scenario(
        name="Public fixture",
        domain="coding",
        instruction="Keep the public file.",
        inputs={"files": {"solution.py": "value = 1\n"}},
        checks=[Check(name="value", kind="equals", path="value", expected=1)],
    )

    def response(request):
        requests.append(request)
        if harness == "external-agent":
            body = {
                "revision": "reviewed-v1",
                "output": {"value": 1},
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        else:
            content = "Done" if harness == "tools" else json.dumps({"value": 1})
            body = {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        return httpx.Response(200, json=body)

    def client(**kwargs):
        clients.append(kwargs)
        return original(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr("cvals.providers.httpx.Client", client)
    result = submit(scenario, system, settings, 0)
    assert result.error is None
    assert result.cost_usd == 0
    assert len(requests) == 1
    assert requests[0].url.host == "10.12.34.56"
    assert requests[0].headers["host"] == "inference.corp.example:8443"
    assert all(isinstance(item["verify"], ssl.SSLContext) for item in clients)
    assert all(item["verify"].check_hostname for item in clients)


def test_worker_environment_overrides_ambient_destination_policy(tmp_path, monkeypatch):
    from cvals.runtime import ensure_worker

    settings = internal_settings(tmp_path, provider_ca_bundle=tmp_path / "corporate.pem")
    monkeypatch.setenv("EVERVAL_INTERNAL_PROVIDER_ORIGINS", "https://unapproved.example")
    monkeypatch.setenv("EVERVAL_PROVIDER_CA_BUNDLE", "/unapproved.pem")
    states = iter([False, True])
    monkeypatch.setattr("cvals.runtime.worker_running", lambda _: next(states))
    captured = {}

    def spawn(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr("cvals.runtime.subprocess.Popen", spawn)
    assert ensure_worker(settings)["started"] is True
    environment = captured["env"]
    assert environment["EVERVAL_INTERNAL_PROVIDER_ORIGINS"] == "https://inference.corp.example:8443"
    assert environment["EVERVAL_PROVIDER_CA_BUNDLE"] == str(settings.provider_ca_bundle)
    assert environment["EVERVAL_ALLOW_REMOTE_PROVIDERS"] == "false"


def test_tmux_receives_the_same_explicit_policy(tmp_path, monkeypatch):
    from cvals.runtime import tmux_session

    settings = internal_settings(tmp_path, provider_ca_bundle=tmp_path / "corporate.pem")
    monkeypatch.setattr("cvals.runtime.ensure_worker", lambda _: {})
    monkeypatch.setattr("cvals.runtime.shutil.which", lambda _: "/usr/bin/tmux")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1 if "has-session" in command else 0)

    monkeypatch.setattr("cvals.runtime.subprocess.run", run)
    tmux_session(settings, attach=False)
    new = next(command for command in commands if "new-session" in command)
    assert "EVERVAL_INTERNAL_PROVIDER_ORIGINS=https://inference.corp.example:8443" in new
    assert "EVERVAL_PROVIDER_CA_BUNDLE=" + str(settings.provider_ca_bundle) in new
    assert "EVERVAL_ALLOW_REMOTE_PROVIDERS=false" in new


@pytest.mark.parametrize("configured", [False, True])
def test_diagnostics_distinguish_internal_policy_without_exposing_secrets(
    tmp_path, monkeypatch, configured
):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from cvals.api import create_app
    from cvals.cli import app
    from cvals.workbench import Workbench

    origins = ("https://inference.corp.example:8443",) if configured else ()
    bundle = tmp_path / "private-ca-location.pem" if configured else None
    settings = Settings(
        data_dir=tmp_path / "workspace",
        token="local-test-token",
        internal_provider_origins=origins,
        provider_ca_bundle=bundle,
    )
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr("cvals.api.docker_available", lambda _: False)
    # Neither diagnostics nor settings should validate a CA by opening it or
    # contact a provider. This path deliberately does not exist.
    workbench = Workbench(settings)
    terminal = workbench.diagnostics()
    command = CliRunner().invoke(app, ["doctor", "--json"])
    assert command.exit_code == 0, command.output
    doctor = json.loads(command.output)
    with TestClient(create_app(settings, start_worker=False)) as client:
        assert client.get("/api/settings").status_code == 401
        health = client.get("/api/health").json()
        assert "internal_provider_origins" not in health
        assert "provider_ca_configured" not in health
        response = client.get("/api/settings", headers={"Authorization": "Bearer local-test-token"})
        assert response.status_code == 200
        browser = response.json()
    for diagnostics in (terminal, doctor, browser):
        assert diagnostics["remote_providers_enabled"] is False
        assert diagnostics["internal_provider_origins"] == list(origins)
        assert diagnostics["provider_ca_configured"] is configured
        serialized = json.dumps(diagnostics)
        assert "local-test-token" not in serialized
        assert "private-ca-location.pem" not in serialized
