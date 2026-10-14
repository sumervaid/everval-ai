"""Behavioral coverage for isolation, accounting, recovery, and uncertainty."""

from __future__ import annotations

import json
import sqlite3
import time

import httpx
import pytest
from pydantic import ValidationError

from cvals.config import Settings
from cvals.engine import Engine
from cvals.evaluators import evaluate
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System, content_hash
from cvals.providers import Submission, payload_for, reserve_cost, submit
from cvals.reports import build_report, export_report, wilson
from cvals.store import LeaseLost, Store


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "workspace")


@pytest.fixture
def scenario():
    return Scenario(
        id="invoice",
        name="Invoice total",
        domain="data",
        instruction="Sum rows. Return {total:number}.",
        inputs={"rows": [2, 3]},
        expected={"secret": "CANARY_EXPECTED"},
        provenance={"hidden": "CANARY_PROVENANCE"},
        checks=[Check(id="total", name="Total", kind="numeric", path="total", expected=5)],
        reference_output={"total": 5, "hidden_reference": "CANARY_REFERENCE"},
    )


@pytest.fixture
def store(settings, scenario):
    result = Store(settings)
    result.init()
    result.save_collection(
        Collection(id="collection", name="Invoices", domain="data", scenarios=[scenario])
    )
    result.ensure_fixture_systems()
    return result


def create(store, **overrides):
    return store.create_experiment(
        ExperimentCreate(
            **{
                "name": "Comparison",
                "collection_id": "collection",
                "system_ids": ["fixture-reference", "fixture-incomplete"],
                **overrides,
            }
        )
    )


def test_provider_public_payload_cannot_include_grading_secrets(scenario):
    system = System(name="Local", kind="http", model="test", base_url="http://127.0.0.1:9000/v1")
    payload = json.dumps(payload_for(scenario, system))
    assert "CANARY" not in payload
    assert (
        "checks" not in payload and "expected" not in payload and "reference_output" not in payload
    )
    assert '"rows"' in json.loads(payload)["messages"][1]["content"]


def test_manifest_bounds_and_duplicates(scenario):
    with pytest.raises(ValidationError):
        Collection(name="Bad", scenarios=[scenario, scenario])
    with pytest.raises(ValidationError):
        Check(name="Bad", kind="numeric", expected=float("nan"))
    with pytest.raises(ValidationError):
        Check(name="Bad", kind="json_schema", expected={"$ref": "https://example.com/schema"})
    with pytest.raises(ValidationError):
        System(name="Bad", kind="http", model="model", base_url="https://secret@example.com/v1")
    with pytest.raises(ValidationError):
        System(
            name="Bad",
            kind="http",
            model="model",
            base_url="https://example.com",
            api_key_env="sk-secret",
        )


def test_end_to_end_fixture_controls_are_explicit(store, settings):
    experiment = create(store, repetitions=3)
    result = Engine(store, settings).run_experiment(experiment["id"])
    assert result["status"] == "completed"
    assert result["total_trials"] == result["completed_trials"] == 6
    reference, empty = result["report"]["systems"]
    assert reference["passed"] == 3 and empty["passed"] == 0
    assert reference["fixture_control"] and empty["fixture_control"]
    assert reference["cost_usd"] == 0 and reference["cost_per_pass"] == 0
    assert empty["cost_per_pass"] is None
    assert len(result["trials"][0]["attempts"]) == 1
    assert result["report"]["comparisons"][0]["paired_tasks"] == 1
    assert result["report"]["comparisons"][0]["conclusion"] == "insufficient evidence"
    assert store.overview()["pass_rate"] is None  # Fixtures never pollute model metrics.


def test_collection_updates_do_not_change_snapshot_or_rerun(store, settings, scenario):
    original = create(store)
    changed = scenario.model_copy(update={"reference_output": {"total": 999}})
    with pytest.raises(ValueError, match="new version"):
        store.save_collection(
            Collection(id="collection", name="Invoices", domain="data", scenarios=[changed])
        )
    store.save_collection(
        Collection(
            id="collection", name="Changed", domain="data", version="2.0", scenarios=[changed]
        )
    )
    store.delete_system("fixture-reference")
    store.delete_collection("collection")
    rerun = store.rerun_experiment(original["id"])
    assert rerun["collection_hash"] == original["collection_hash"]
    result = Engine(store, settings).run_experiment(rerun["id"])
    assert result["report"]["systems"][0]["passed"] == 1
    assert result["parent_experiment_id"] == original["id"]


def test_snapshot_tampering_fails_closed(store, settings):
    experiment = create(store)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE experiments SET snapshot='{}' WHERE id=?", (experiment["id"],))
    with pytest.raises(ValueError, match="integrity"):
        store.get_experiment(experiment["id"])


def test_global_lease_prevents_competing_workers(store, settings):
    a, b = create(store), create(store)
    assert store.claim("one", a["id"]) == a["id"]
    assert store.claim("two", b["id"]) is None
    assert Engine(store, settings).recover() == 0
    with pytest.raises(LeaseLost):
        store.start_trial(a["id"], "two", a["trials"][0]["id"], 0)


def test_crash_recovery_preserves_interrupted_attempt(store, settings):
    experiment = create(store)
    id = experiment["id"]
    assert store.claim("crashed", id) == id
    trial_id = experiment["trials"][0]["id"]
    store.start_trial(id, "crashed", trial_id, 0)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE worker_lease SET expires_at=?", (time.time() - 1,))
    assert Engine(store, settings).recover() == 1
    result = Engine(store, settings).run_experiment(id)
    assert result["status"] == "failed"
    assert result["trials"][0]["status"] == "interrupted"
    assert result["trials"][0]["attempts"][0]["status"] == "interrupted"
    assert len(result["trials"][0]["attempts"]) == 1
    assert result["completed_trials"] == 2
    assert result["report"]["completed_trials"] == 1


def test_finalization_is_idempotency_guarded(store):
    experiment = create(store)
    id, trial_id = experiment["id"], experiment["trials"][0]["id"]
    store.claim("worker", id)
    store.start_trial(id, "worker", trial_id, 0)
    store.finish_trial(id, "worker", trial_id, {"status": "error", "error": "Example"})
    with pytest.raises(ValueError, match="active trial"):
        store.finish_trial(id, "worker", trial_id, {"status": "completed"})


def test_cancelled_scheduled_trials_remain_in_denominator(store, settings):
    experiment = create(store)
    result = store.cancel_experiment(experiment["id"])
    assert result["status"] == "cancelled"
    assert result["completed_trials"] == 2
    assert all(t["status"] == "cancelled" for t in result["trials"])
    assert result["report"]["incomplete_trials"] == 2
    assert result["report"]["systems"][0]["pass_rate"] == 0
    assert not Engine(store, settings).run_pending_once()


def test_cancel_during_attempt_records_known_cost(store, settings, monkeypatch):
    experiment = create(store)

    def cancel(*args):
        store.cancel_experiment(experiment["id"])
        return Submission({"total": 5}, cost_usd=0)

    monkeypatch.setattr("cvals.engine.submit", cancel)
    result = Engine(store, settings).run_experiment(experiment["id"])
    assert result["status"] == "cancelled"
    assert [t["status"] for t in result["trials"]] == ["cancelled", "cancelled"]
    assert result["trials"][0]["attempts"][0]["cost_usd"] == 0


def test_remote_providers_need_explicit_admission(store):
    store.save_system(
        System(
            id="remote",
            name="Remote",
            kind="http",
            model="example",
            base_url="https://example.com/v1",
            input_cost_per_million=1,
            output_cost_per_million=2,
        )
    )
    with pytest.raises(ValueError, match="disabled"):
        create(store, system_ids=["remote"], budget_usd=5)


def test_budget_insufficient_never_calls_provider(store, settings, monkeypatch):
    store.save_system(
        System(
            id="local",
            name="Local priced",
            kind="http",
            model="example",
            base_url="http://127.0.0.1:9000/v1",
            input_cost_per_million=10,
            output_cost_per_million=10,
        )
    )
    experiment = create(store, system_ids=["local"], budget_usd=0.000001)

    def forbidden(*args):
        pytest.fail("No request should be issued without enough budget")

    monkeypatch.setattr("cvals.engine.submit", forbidden)
    result = Engine(store, settings).run_experiment(experiment["id"])
    assert result["trials"][0]["status"] == "budget_exceeded"
    assert result["trials"][0]["attempts"] == []
    assert result["report"]["systems"][0]["cost_usd"] == 0


def test_unknown_billing_stops_future_requests(store, settings, monkeypatch):
    store.save_system(
        System(
            id="local",
            name="Local priced",
            kind="http",
            model="example",
            base_url="http://127.0.0.1:9000/v1",
            input_cost_per_million=1,
            output_cost_per_million=1,
        )
    )
    experiment = create(store, system_ids=["local"], repetitions=2, budget_usd=1)
    calls = []

    def unknown(*args):
        calls.append(True)
        return Submission(None, error="Timeout")

    monkeypatch.setattr("cvals.engine.submit", unknown)
    result = Engine(store, settings).run_experiment(experiment["id"])
    assert len(calls) == 1
    assert result["budget_uncertain"]
    assert result["trials"][1]["status"] == "budget_exceeded"
    assert result["report"]["systems"][0]["cost_usd"] is None
    assert not result["report"]["systems"][0]["cost_complete"]


def test_numeric_missing_and_boolean_are_not_successes(scenario, settings):
    assert not evaluate(scenario, {}, settings)[0]["passed"]
    assert not evaluate(scenario, {"total": True}, settings)[0]["passed"]
    assert evaluate(scenario, {"total": 5}, settings)[0]["passed"]


def test_citations_require_real_source_and_verbatim_quote(settings):
    scenario = Scenario(
        name="Cite",
        domain="documents",
        instruction="Cite evidence",
        inputs={"sources": [{"id": "policy", "text": "Payment is due in 30 days."}]},
        checks=[
            Check(
                name="Citations",
                kind="citation",
                path="citations",
                expected={"source_ids": ["policy"], "require_quote": True},
            )
        ],
    )
    assert evaluate(
        scenario, {"citations": [{"source_id": "policy", "quote": "30 days"}]}, settings
    )[0]["passed"]
    assert not evaluate(
        scenario, {"citations": [{"source_id": "policy", "quote": "60 days"}]}, settings
    )[0]["passed"]
    assert not evaluate(
        scenario, {"citations": [{"source_id": "invented", "quote": "30 days"}]}, settings
    )[0]["passed"]


def test_verifier_failure_is_not_scored_as_model_failure(store, settings, monkeypatch):
    from cvals.evaluators import EvaluationError

    def unavailable(*args):
        raise EvaluationError("No isolation available")

    monkeypatch.setattr("cvals.engine.evaluate", unavailable)
    result = Engine(store, settings).run_experiment(create(store)["id"])
    assert result["status"] == "failed"
    assert result["trials"][0]["score"] is None and result["trials"][0]["passed"] is None
    assert result["report"]["systems"][0]["failed"] == 0
    assert result["report"]["systems"][0]["errors"] == 1


def test_wilson_small_sample_does_not_claim_certainty():
    assert wilson(0, 0) == (None, None)
    low, high = wilson(1, 1)
    assert 0.20 < low < 0.21 and high == pytest.approx(1)
    low, high = wilson(0, 1)
    assert low == pytest.approx(0) and 0.79 < high < 0.80


def test_report_reproduction_and_safe_exports(store, settings):
    result = Engine(store, settings).run_experiment(create(store, name="=HYPERLINK()")["id"])
    assert content_hash(build_report(result)) == content_hash(
        store.get_experiment(result["id"])["report"]
    )
    result["trials"][0]["scenario_name"] = "=HYPERLINK()"
    assert "'=HYPERLINK()" in export_report(result, "csv")
    result["name"] = "<script>alert(1)</script>"
    assert "<script>" not in export_report(result, "html")
    assert json.loads(export_report(result, "json"))["report"] == build_report(result)


def test_http_adapter_keeps_usage_on_invalid_json(scenario, settings, monkeypatch):
    real_client = httpx.Client
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "example",
                "choices": [{"message": {"content": "bad json"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(
        "cvals.providers.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    system = System(
        name="Local",
        kind="http",
        model="example",
        base_url="http://127.0.0.1:9000/v1",
        input_cost_per_million=2,
        output_cost_per_million=4,
    )
    result = submit(scenario, system, settings, 1)
    assert result.output is None and result.error
    assert result.cost_usd == pytest.approx(0.00004)
    assert "CANARY" not in json.dumps(captured)
    assert reserve_cost(scenario, system) > result.cost_usd


def test_http_adapter_does_not_follow_redirect_or_echo_errors(scenario, settings, monkeypatch):
    real_client = httpx.Client
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": "https://attacker.example"}, text="SECRET_ERROR"
        )

    monkeypatch.setattr(
        "cvals.providers.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    system = System(
        name="Local",
        kind="http",
        model="example",
        base_url="http://127.0.0.1:9000/v1",
        input_cost_per_million=0,
        output_cost_per_million=0,
    )
    result = submit(scenario, system, settings, 0)
    assert len(calls) == 1 and result.error
    assert "SECRET_ERROR" not in result.error


def test_provider_dns_pinning_preserves_tls_identity(monkeypatch):
    from cvals.providers import _pin_destination

    monkeypatch.setattr(
        "cvals.providers.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    _pin_destination(request)
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "provider.example"
    assert request.extensions["sni_hostname"] == "provider.example"


@pytest.mark.parametrize(
    "address", ["169.254.169.254", "10.0.0.1", "192.168.1.1", "127.0.0.1", "::1"]
)
def test_remote_dns_cannot_reach_private_or_metadata_addresses(monkeypatch, address):
    from cvals.providers import _pin_destination

    monkeypatch.setattr(
        "cvals.providers.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", (address, 443))],
    )
    with pytest.raises(ValueError, match="nonpublic"):
        _pin_destination(httpx.Request("POST", "https://provider.example/v1/chat/completions"))


def test_nested_nan_and_unsafe_schema_patterns_are_rejected(scenario):
    with pytest.raises(ValidationError):
        Scenario(**{**scenario.model_dump(), "inputs": {"nested": [float("nan")]}})
    with pytest.raises(ValidationError, match="Regular expressions"):
        Check(name="Unsafe", kind="json_schema", expected={"type": "string", "pattern": "(a+)+$"})


def test_provider_missing_message_preserves_known_usage(scenario, settings, monkeypatch):
    real_client = httpx.Client

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": None}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(
        "cvals.providers.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    system = System(
        name="Local",
        kind="http",
        model="example",
        base_url="http://127.0.0.1:9000/v1",
        input_cost_per_million=2,
        output_cost_per_million=4,
    )
    result = submit(scenario, system, settings, 1)
    assert result.error and result.cost_usd == pytest.approx(0.00004)


def test_external_submissions_are_durable_and_preserve_metrics(store, settings):
    fingerprint = content_hash(store.get_collection("collection"))
    experiment = store.import_submissions(
        collection_id="collection",
        name="External harness",
        collection_hash=fingerprint,
        submissions={
            "invoice": {
                "output": {"total": 5},
                "cost_usd": 0.25,
                "input_tokens": 100,
                "output_tokens": 25,
                "duration_ms": 1500,
                "trace": [{"tool": "calculator", "result": 5}],
            }
        },
        provenance={"harness": "example/1", "run_id": "source-run-42"},
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    trial = result["trials"][0]
    assert trial["passed"] and trial["cost_usd"] == 0.25
    assert trial["duration_ms"] == 1500 and trial["evaluation_duration_ms"] >= 0
    assert trial["input_tokens"] == 100 and trial["output_tokens"] == 25
    assert trial["provider_metadata"]["source_trace"][0]["tool"] == "calculator"
    assert result["budget_committed_usd"] == 0 and not result["budget_uncertain"]
    assert not result["report"]["systems"][0]["fixture_control"]
    assert result["report"]["systems"][0]["submission_replay"]


def test_external_submissions_missing_usage_stays_unknown(store, settings):
    experiment = store.import_submissions(
        "collection",
        "Unknown usage",
        content_hash(store.get_collection("collection")),
        {"invoice": {"output": {"total": 5}}},
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    trial = result["trials"][0]
    assert trial["passed"]
    assert trial["cost_usd"] is None and trial["duration_ms"] is None
    assert trial["input_tokens"] is None and trial["output_tokens"] is None
    assert result["report"]["systems"][0]["cost_usd"] is None
    assert result["report"]["systems"][0]["median_duration_ms"] is None


def test_external_submission_fingerprint_mismatch_is_atomic(store):
    original_systems = len(store.list_systems())
    original_experiments = len(store.list_experiments())
    with pytest.raises(ValueError, match="fingerprint"):
        store.import_submissions(
            "collection", "Mismatch", "0" * 64, {"invoice": {"output": {"total": 5}}}
        )
    assert len(store.list_systems()) == original_systems
    assert len(store.list_experiments()) == original_experiments


def test_external_submission_unknown_scenario_is_rejected(store):
    with pytest.raises(ValueError, match="unknown scenario"):
        store.import_submissions(
            "collection",
            "Typo",
            content_hash(store.get_collection("collection")),
            {"invented": {"output": {"total": 5}}},
        )


def test_missing_external_submission_remains_visible(store, settings):
    experiment = store.import_submissions(
        "collection", "Missing output", content_hash(store.get_collection("collection")), {}
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    assert result["status"] == "failed"
    assert result["trials"][0]["status"] == "error"
    assert result["report"]["total_trials"] == 1
    assert result["report"]["incomplete_trials"] == 1
    assert "No external submission" in result["trials"][0]["error"]


def test_external_submission_replay_does_not_create_independent_repeats(store, settings):
    experiment = store.import_submissions(
        "collection",
        "Captured run",
        content_hash(store.get_collection("collection")),
        {"invoice": {"output": {"total": 5}}},
    )
    with pytest.raises(ValueError, match="independent trials"):
        create(store, system_ids=experiment["system_ids"], repetitions=2)
    rerun = store.rerun_experiment(experiment["id"])
    result = Engine(store, settings).run_experiment(rerun["id"])
    assert result["total_trials"] == 1
    assert result["parent_experiment_id"] == experiment["id"]
    assert any("no new independent" in warning for warning in result["report"]["warnings"])


def test_external_errors_preserve_reported_cost(store, settings):
    experiment = store.import_submissions(
        "collection",
        "Source failure",
        content_hash(store.get_collection("collection")),
        {"invoice": {"error": "Source harness timed out", "cost_usd": 0.125, "duration_ms": 30000}},
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    assert result["trials"][0]["status"] == "error"
    assert result["trials"][0]["cost_usd"] == 0.125
    assert result["trials"][0]["duration_ms"] == 30000
    assert result["report"]["systems"][0]["cost_per_pass"] is None
