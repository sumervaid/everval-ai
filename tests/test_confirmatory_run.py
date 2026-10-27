"""No-network integration checks for prospective study dispatch and accounting."""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from cvals.judge_calibration_models import CalibrationProtocol
from scripts.benchmarks.budget import BudgetError
from scripts.judge_study import confirmatory_run as study


def protocol(count: int = 2) -> dict[str, Any]:
    return {
        "name": "Confirmatory transport controls",
        "rubric": {
            "name": "Correctness",
            "version": "1",
            "instructions": "Choose the answer satisfying the task.",
            "dimensions": [
                {"id": "correct", "name": "Correctness", "guidance": "Check the answer."}
            ],
        },
        "judges": [
            {"id": "terra-standard", "model": "gpt-6.1-terra", "revision": "gpt-6.1-terra"},
            {
                "id": "terra-reference_context",
                "model": "gpt-6.1-terra",
                "revision": "gpt-6.1-terra",
                "prompt_variant": "reference_context",
            },
            {"id": "sol-standard", "model": "gpt-6.1-sol", "revision": "gpt-6.1-sol"},
        ],
        "items": [
            {
                "item_id": f"item-{index}",
                "task_id": f"task-{index}",
                "family_id": f"family-{index}",
                "partition": "holdout",
                "task": {"instruction": f"Compute {index} + 1.", "inputs": {}},
                "responses": {"A": str(index + 1), "B": str(index + 2)},
                "generator_models": {"A": "hidden-generator-one", "B": "hidden-generator-two"},
                "reference_context": {"checklist": ["Check the arithmetic."]},
                "reference": {
                    "source": "human_collected",
                    "label": "A",
                    "reviewer_labels": [{"reviewer_id": "private-reviewer", "label": "A"}],
                    "provenance": "Private evaluator-only reference.",
                },
            }
            for index in range(count)
        ],
        "repetitions": 1,
        "budget_usd": 2.0,
        "primary_endpoint": "human_votes",
        "primary_contrasts": [
            {"left": "terra-standard", "right": "terra-reference_context"},
            {"left": "terra-standard", "right": "sol-standard"},
        ],
        "planned_comparisons": 2,
        "min_families": 2,
    }


def configuration() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol_file": "protocol.json",
        "pricing_version": "no-network-test-prices",
        "budget": {
            "opening_spend_usd": "15.14292900",
            "ceiling_usd": "50",
            "opening_evidence_file": "prior-budget.json",
        },
        "profiles": {
            "gpt-6.1-terra": {
                "allocation_usd": "1",
                "input_rate_per_million": "1",
                "output_rate_per_million": "5",
                "max_output_tokens": 256,
                "reasoning": "none",
            },
            "gpt-6.1-sol": {
                "allocation_usd": "1",
                "input_rate_per_million": "2",
                "output_rate_per_million": "10",
                "max_output_tokens": 1536,
                "reasoning": "low",
            },
        },
        "order_design": "full",
        "seed": "fixed-assignment-test",
    }


def prepare(root: Path, count: int = 2) -> dict[str, Any]:
    study.save_once(root / "protocol.json", protocol(count))
    study.save_once(root / "configuration.json", configuration())
    study.save_once(root / "prior-budget.json", {"conservative_accounted_total_usd": "15.14292900"})
    return study.freeze(root)


def response(model: str, *, preference: str = "A") -> dict[str, Any]:
    return {
        "id": "response-test",
        "model": model,
        "service_tier": "default",
        "status": "completed",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "This is not the judgment."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {"preference": preference, "rationale": "Checked the task."}
                        ),
                    }
                ],
            },
        ],
    }


def test_full_grid_is_deterministic_blinded_and_cumulative_budget_is_bounded(tmp_path):
    plan = prepare(tmp_path)
    jobs = plan["jobs"]
    assert len(jobs) == 12
    assert {job["kind"] for job in jobs} == {"primary"}
    assert plan["product_full_grid_calls"] == len(jobs)
    assert study.load_plan(tmp_path) == plan
    for job in jobs:
        payload = json.dumps(job["body"])
        assert "private-reviewer" not in payload
        assert "hidden-generator" not in payload
        assert "Private evaluator-only" not in payload
        assert job["body"]["prompt_cache_options"] == {"mode": "explicit"}
        assert job["body"]["store"] is False
        assert job["body"]["service_tier"] == "default"
        assert job["input_ceiling"] >= Decimal(job["raw_input_token_ceiling"]) * Decimal("2.5")
        assert job["request_sha256"] == study.digest(job["body"])
    for item in ("item-0", "item-1"):
        for arm in ("terra-standard", "terra-reference_context", "sol-standard"):
            assert {
                tuple(j["order"]) for j in jobs if j["item_id"] == item and j["judge_id"] == arm
            } == {
                ("A", "B"),
                ("B", "A"),
            }
    config = configuration()
    config["profiles"]["gpt-6.1-terra"]["allocation_usd"] = "20"
    config["profiles"]["gpt-6.1-sol"]["allocation_usd"] = "20"
    with pytest.raises(BudgetError, match="remaining"):
        study.validate_configuration(config, CalibrationProtocol.model_validate(protocol()))
    config = configuration()
    config["budget"]["opening_spend_usd"] = "0"
    with pytest.raises(BudgetError, match="prior spend"):
        study.validate_configuration(config, CalibrationProtocol.model_validate(protocol()))


def test_frozen_configuration_and_external_source_changes_fail_closed(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "frozen.py"
    source.write_text("original source")
    monkeypatch.setattr(study, "ROOT", source_root)
    monkeypatch.setattr(study, "SOURCES", ["frozen.py"])
    root = tmp_path / "study"
    prepare(root)
    source.write_text("changed source")
    with pytest.raises(ValueError, match="Frozen source"):
        study.load_plan(root)
    source.write_text("original source")
    config = study.read(root / "configuration.json")
    config["profiles"]["gpt-6.1-terra"]["max_output_tokens"] = 512
    (root / "configuration.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Frozen source"):
        study.load_plan(root)


def test_process_lock_and_orphaned_requests_prevent_duplicate_dispatch(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    with (
        study.process_lock(tmp_path),
        pytest.raises(RuntimeError, match="Another"),
        study.process_lock(tmp_path),
    ):
        pass
    ledger = study.ledgers(tmp_path, plan)["gpt-6.1-terra"]
    job = next(j for j in plan["jobs"] if j["model"] == "gpt-6.1-terra")
    ledger.register_attempt(job["job_id"], study.digest(["registered"]))
    ledger.reserve(job["job_id"], job["request_sha256"], job["input_ceiling"], 256)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    with pytest.raises(BudgetError, match="orphaned"):
        study.run(tmp_path, transport=httpx.MockTransport(lambda request: pytest.fail("No retry")))
    assert study.summary(study.ledgers(tmp_path, plan))["provider_requests"] == 1


def test_real_ledgers_settle_per_model_import_into_product_and_resume_without_calls(
    tmp_path, monkeypatch
):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    lock = threading.Lock()
    active = 0
    peak = 0
    calls = []

    def handle(request):
        nonlocal active, peak
        body = json.loads(request.content)
        with lock:
            calls.append(study.digest(body))
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return httpx.Response(200, json=response(body["model"]))

    state = study.run(tmp_path, transport=httpx.MockTransport(handle))
    assert state["provider_requests"] == 12
    assert Decimal(state["new_committed_usd"]) == Decimal("0.0032")
    assert Decimal(state["cumulative_committed_usd"]) == Decimal("15.14612900")
    assert 1 <= peak <= 4
    assert len(calls) == len(set(calls)) == 12
    for job in plan["jobs"]:
        result = study.read(tmp_path / "requests" / (job["job_id"] + ".result.json"))
        assert result["normalized"]["preference"] == job["order"][0]
        assert result["normalized"]["status"] == "completed"
        assert result["response_handling"]["phase_selection"] == "explicit_final_answer"
        assert result["provider_usage"]["output_tokens"] == 20
    imported = study.collect(tmp_path)
    assert imported["imported_study_results"] == 12
    assert imported["deliberately_unscheduled_mirror_jobs"] == 0
    second = study.run(
        tmp_path, transport=httpx.MockTransport(lambda request: pytest.fail("No rerun"))
    )
    assert second["provider_requests"] == 12


@pytest.mark.parametrize("field", ["cached_tokens", "cache_write_tokens"])
def test_unexpected_cache_usage_retains_full_reservation_and_blocks_every_model(
    tmp_path, monkeypatch, field
):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(study.digest(body))
        value = response(body["model"])
        value["usage"]["input_tokens_details"][field] = 25
        return httpx.Response(200, json=value)

    with pytest.raises(RuntimeError, match="stopped"):
        study.run(tmp_path, transport=httpx.MockTransport(handle))
    assert 1 <= len(calls) <= 4
    state = study.summary(study.ledgers(tmp_path, plan))
    assert Decimal(state["new_committed_usd"]) > 0
    assert sum(v["uncertain_requests"] for v in state["models"].values()) == len(calls)
    assert (tmp_path / "STOP").exists()
    with pytest.raises(RuntimeError, match="retained stop"):
        study.run(tmp_path, transport=httpx.MockTransport(lambda request: pytest.fail("No retry")))


def test_policy_rejection_is_preserved_and_never_reworded_or_routed(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    def handle(request):
        return httpx.Response(
            400, json={"error": {"code": "invalid_prompt", "message": "Policy rejection."}}
        )

    with pytest.raises(RuntimeError, match="stopped"):
        study.run(tmp_path, transport=httpx.MockTransport(handle))
    marker = study.read(tmp_path / "STOP")
    assert marker["reason"] == "provider_policy_rejection"
    assert marker["item_id"] in {j["item_id"] for j in plan["jobs"]}
    assert list((tmp_path / "requests").glob("*.response.json"))
    assert all(
        study.read(p)["normalized"]["status"] == "error"
        for p in (tmp_path / "requests").glob("*.result.json")
    )


def test_invalid_selected_final_is_an_observed_failure_with_known_cost(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    def handle(request):
        value = response(json.loads(request.content)["model"])
        value["output"][-1]["content"][0]["text"] = (
            '{"preference":"A","preference":"B","rationale":"x"}'
        )
        return httpx.Response(200, json=value)

    state = study.run(tmp_path, transport=httpx.MockTransport(handle))
    assert state["provider_requests"] == len(plan["jobs"])
    assert not state["stopped"]
    for path in (tmp_path / "requests").glob("*.result.json"):
        result = study.read(path)
        assert result["settled"]
        assert result["normalized"]["status"] == "invalid"
        assert result["response_handling"]["classification"] == "invalid_selected_final"


@pytest.mark.parametrize(
    "kind", ["connection", "duplicate-envelope", "model", "tier", "missing-usage"]
)
def test_transport_or_unpriced_response_stops_without_automatic_retry(tmp_path, monkeypatch, kind):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(study.digest(body))
        if kind == "connection":
            raise httpx.ConnectError("No connection", request=request)
        if kind == "duplicate-envelope":
            return httpx.Response(200, content=b'{"model":"first","model":"second"}')
        value = response(body["model"])
        if kind == "model":
            value["model"] = "unpriced-snapshot"
        elif kind == "tier":
            value["service_tier"] = "priority"
        elif kind == "missing-usage":
            value.pop("usage")
        return httpx.Response(200, json=value)

    with pytest.raises(RuntimeError, match="stopped"):
        study.run(tmp_path, transport=httpx.MockTransport(handle))
    assert len(calls) == len(set(calls))
    assert 1 <= len(calls) <= 4
    state = study.summary(study.ledgers(tmp_path, plan))
    assert sum(v["uncertain_requests"] for v in state["models"].values()) == len(calls)


def test_no_key_admits_nothing(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="private environment"):
        study.run(tmp_path)
    assert study.summary(study.ledgers(tmp_path, plan))["provider_requests"] == 0


def test_unsupported_sol_effort_and_existing_accounting_are_rejected(tmp_path):
    config = configuration()
    config["profiles"]["gpt-6.1-sol"]["reasoning"] = "none"
    with pytest.raises(ValueError, match="Sol requires"):
        study.validate_configuration(config, CalibrationProtocol.model_validate(protocol()))
    (tmp_path / "accounting").mkdir()
    (tmp_path / "accounting" / "retained.sqlite3").write_bytes(b"do not overwrite")
    with pytest.raises(ValueError, match="empty accounting"):
        study.freeze(tmp_path)


def test_allocation_failure_admits_no_charge_and_retains_the_stop(tmp_path, monkeypatch):
    config = configuration()
    for profile in config["profiles"].values():
        profile["allocation_usd"] = "0.000001"
    study.save_once(tmp_path / "configuration.json", config)
    study.save_once(tmp_path / "protocol.json", protocol())
    study.save_once(
        tmp_path / "prior-budget.json", {"conservative_accounted_total_usd": "15.14292900"}
    )
    plan = study.freeze(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    with pytest.raises(RuntimeError, match="stopped"):
        study.run(
            tmp_path, transport=httpx.MockTransport(lambda request: pytest.fail("No admission"))
        )
    state = study.summary(study.ledgers(tmp_path, plan))
    assert state["provider_requests"] == 0
    assert Decimal(state["cumulative_committed_usd"]) == study.OPENING_SPEND


def test_oversized_response_preserves_bounded_partial_evidence(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(study, "MAX_RESPONSE_BYTES", 128)
    with pytest.raises(RuntimeError, match="stopped"):
        study.run(
            tmp_path,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 512)),
        )
    partials = list((tmp_path / "requests").glob("*.raw.partial"))
    assert partials and all(path.stat().st_size == 128 for path in partials)
    state = study.summary(study.ledgers(tmp_path, plan))
    assert sum(v["uncertain_requests"] for v in state["models"].values()) == len(partials)


@pytest.mark.parametrize("artifact", ["normalized", "request", "raw"])
def test_resume_recomputes_records_and_rejects_tampered_evidence(tmp_path, monkeypatch, artifact):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    study.run(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response(json.loads(request.content)["model"]))
        ),
    )
    job = plan["jobs"][0]
    prefix = tmp_path / "requests" / job["job_id"]
    if artifact == "normalized":
        path = prefix.with_suffix(".result.json")
        value = study.read(path)
        value["normalized"]["preference"] = "tie"
        path.write_text(json.dumps(value))
    elif artifact == "request":
        path = prefix.with_suffix(".request.json")
        value = study.read(path)
        value["body"]["max_output_tokens"] = 4096
        path.write_text(json.dumps(value))
    else:
        path = prefix.with_suffix(".raw")
        value = study.read(path)
        value["usage"]["input_tokens"] += 1
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Retained"):
        study.run(
            tmp_path,
            transport=httpx.MockTransport(
                lambda request: pytest.fail("Tampered evidence must block")
            ),
        )


def test_analysis_can_audit_retained_unknown_reservations_without_resuming(tmp_path, monkeypatch):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    def disconnected(request):
        raise httpx.ConnectError("Unavailable", request=request)

    with pytest.raises(RuntimeError, match="stopped"):
        study.run(tmp_path, transport=httpx.MockTransport(disconnected))
    study.verify_prefix(tmp_path, plan, study.ledgers(tmp_path, plan), require_settled=False)


@pytest.mark.parametrize("field", ["cached_tokens", "cache_write_tokens"])
def test_missing_cache_metadata_keeps_pricing_unknown(tmp_path, monkeypatch, field):
    plan = prepare(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    def handle(request):
        value = response(json.loads(request.content)["model"])
        value["usage"]["input_tokens_details"].pop(field)
        return httpx.Response(200, json=value)

    with pytest.raises(RuntimeError, match="stopped"):
        study.run(tmp_path, transport=httpx.MockTransport(handle))
    state = study.summary(study.ledgers(tmp_path, plan))
    assert sum(v["uncertain_requests"] for v in state["models"].values()) >= 1
