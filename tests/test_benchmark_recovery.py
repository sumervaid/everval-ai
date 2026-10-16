"""Recovery exercises original fixtures, real SQLite accounting, and no HTTP or Docker."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from decimal import Decimal

import pytest
from test_benchmark_campaign import isolated_execution as isolated_execution
from test_benchmark_campaign import plan_file as plan_file
from test_benchmark_campaign import saved_result, write_json

from scripts.benchmarks import campaign, recovery, report
from scripts.benchmarks.budget import BudgetError
from scripts.benchmarks.model import canonical


def _hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _provider_files(root, identity, request_id, payload, response):
    base = root / "attempts" / identity / "provider"
    write_json(
        base / (request_id + ".request.json"),
        {
            "request_id": request_id,
            "request_sha256": _hash(payload),
            "payload": payload,
        },
    )
    write_json(
        base / (request_id + ".response.json"),
        {
            "http_status": 400 if "error" in response else 200,
            "response": response,
            "duration_seconds": 1.0,
        },
    )


def _payload(plan):
    return {
        "model": plan["model"]["model_name"],
        "messages": [{"role": "user", "content": "Original recovery fixture."}],
        "reasoning_effort": plan["model"]["reasoning_effort"],
        "max_completion_tokens": plan["model"]["max_completion_tokens"],
        "service_tier": "default",
        "stream": False,
        "store": False,
        "n": 1,
    }


@pytest.fixture
def stopped(plan_file):
    plan = json.loads(plan_file.read_text())
    for relative in ("budget.py", "campaign.py", "report.py"):
        plan["source_sha256"]["scripts/benchmarks/" + relative] = "f" * 64
    path = plan_file.parent / "campaign.json"
    write_json(path, plan)
    root = path.parent
    plan_hash = _hash(plan)
    ledger = campaign.BudgetLedger(root / "budget.sqlite3", campaign.budget_config(plan, plan_hash))
    target = plan["tasks"][1]
    uncertain = None
    for item in plan["tasks"]:
        arms = campaign.ARMS if item["split"] == "development" else ("baseline",)
        for arm in arms:
            identity, attempt_hash = campaign.attempt_identity(plan_hash, item, arm)
            ledger.register_attempt(identity, attempt_hash)
            base = root / "attempts" / identity
            write_json(
                base / "started.json",
                {
                    "attempt_id": identity,
                    "attempt_hash": attempt_hash,
                    "plan_sha256": plan_hash,
                },
            )
            result = saved_result(plan_hash, item, arm)
            result.update(
                cleanup_confirmed=True,
                provider={
                    "attempt_id": identity,
                    "calls": 0,
                    "recorded_cost_usd": 0.0,
                },
            )
            if item == target:
                payload = _payload(plan)
                request_id = ledger.reserve(
                    identity, _hash(payload), len(canonical(payload["messages"])) + 8192, 8192
                )
                _provider_files(
                    root,
                    identity,
                    request_id,
                    payload,
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": recovery.OUTPUT_LIMIT_MESSAGE,
                        },
                    },
                )
                ledger.uncertain(request_id, "UnknownUsage")
                result.update(status="UnknownUsage", patch="")
                name = "everval-benchmark-" + "a" * 32
                write_json(
                    base / "solver/sandbox/container-removal.json",
                    {
                        "confirmed": True,
                        "returncode": 0,
                        "output": name + "\n",
                    },
                )
                write_json(
                    base / "solver/sandbox/container-ownership.json",
                    {
                        "container_name": name,
                        "image_id": item["image_id"],
                    },
                )
                uncertain = (identity, request_id)
            write_json(base / "result.json", result)
    amendment = root / "recovery/amendment.json"
    return path, plan, plan_hash, ledger, amendment, uncertain


def _create(stopped):
    path, plan, plan_hash, ledger, amendment, uncertain = stopped
    recovery.create_amendment(path, amendment)
    return path, plan, plan_hash, ledger, amendment, uncertain


def _review(stopped):
    values = _create(stopped)
    recovery.review_request(values[0], values[4], values[5][1])
    return values


def test_recovery_finishes_only_unstarted_work_without_fabricating_usage(
    stopped, isolated_execution
):
    path, plan, plan_hash, old_ledger, amendment, (identity, request_id) = _create(stopped)
    original_plan = path.read_bytes()
    original_result = (path.parent / "attempts" / identity / "result.json").read_bytes()
    original_requests = old_ledger.status()["requests"]
    assert old_ledger.status()["admissions_blocked"] is True
    with pytest.raises(ValueError, match="source differs"):
        campaign.verify_plan(path)
    assert campaign.verify_plan(path, amendment=amendment)[1] == plan_hash
    inspected = campaign.run_phase(path, "test", execute=False, amendment=amendment)
    assert inspected["budget"]["admissions_blocked"] is True
    reviewed = recovery.review_request(path, amendment, request_id)
    assert reviewed["admissions_blocked"] is False
    assert reviewed["cost_complete"] is False
    calls, executor = isolated_execution

    def solve(**kwargs):
        model = kwargs["model"]
        payload = _payload(plan)
        rid = model.ledger.reserve(
            model.attempt_id, _hash(payload), len(canonical(payload["messages"])) + 8192, 8192
        )
        _provider_files(
            path.parent,
            model.attempt_id,
            rid,
            payload,
            {
                "model": plan["model"]["model_name"],
                "service_tier": "default",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
        model.cost = float(model.ledger.settle(rid, 10, 5))
        model.calls = 1
        calls["runs"].append((kwargs["task"]["instance_id"], kwargs["arm"]))
        return {
            "status": "Submitted",
            "submitted": True,
            "patch": "original fixture repair",
            "cleanup_confirmed": True,
        }

    executor.run_attempt = solve
    executor.grade_patch = lambda **kwargs: {
        "status": "graded",
        "resolved": True,
        "all_required_tests_observed": True,
        "cleanup_confirmed": True,
    }
    final = campaign.run_phase(path, "test", execute=True, amendment=amendment)
    assert calls["runs"] == [(plan["tasks"][1]["instance_id"], "everval")]
    assert final["cost_complete"] is False
    assert final["uncertain_requests"] == final["reviewed_uncertain_requests"] == 1
    assert final["admissions_blocked"] is False
    assert path.read_bytes() == original_plan
    assert (path.parent / "attempts" / identity / "result.json").read_bytes() == original_result
    assert next(r for r in final["requests"] if r["id"] == request_id) == original_requests[0]
    delta = Decimal(final["committed_spend_usd"]) - Decimal(final["known_spend_usd"])
    assert delta == Decimal(original_requests[0]["reserved_usd"])
    projected = report.build_report(path, amendment=amendment)
    assert projected["schema_version"] == 2
    assert projected["plan_sha256"] == plan_hash
    assert projected["source_sha256"] == plan["source_sha256"]
    assert projected["test"]["complete"] is True
    pairs = projected["test"]["paired"]
    assert pairs["completed_pairs"] == 1 and pairs["pending_pairs"] == 0
    assert pairs["quality_comparable_pairs"] == 0 and pairs["unevaluable_pairs"] == 1
    assert pairs["everval_only_resolved"] == 0
    assert projected["accounting"]["cost_complete"] is False
    campaign.run_phase(path, "test", execute=True, amendment=amendment)
    assert calls["runs"] == [(plan["tasks"][1]["instance_id"], "everval")]
    assert recovery.review_request(path, amendment, request_id)["already_recorded"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        "model_override",
        "allow_old",
        "drop_allowed",
        "artifact_changed",
        "artifact_added",
        "root",
        "inode",
        "event_prefix",
    ],
)
def test_amendment_rejects_changed_sources_scope_evidence_and_location(stopped, mutation):
    path, plan, _, _, amendment, (identity, _) = _create(stopped)
    manifest = json.loads(amendment.read_text())
    if mutation == "model_override":
        manifest["source_overrides"]["scripts/benchmarks/model.py"] = {
            "before": "a" * 64,
            "after": "a" * 64,
        }
    elif mutation == "allow_old":
        manifest["allowed_attempts"].append({"attempt_id": identity, "attempt_hash": "a" * 64})
    elif mutation == "drop_allowed":
        manifest["allowed_attempts"] = []
    elif mutation == "artifact_changed":
        (path.parent / "attempts" / identity / "result.json").write_text("{}")
    elif mutation == "artifact_added":
        (path.parent / "attempts" / identity / "extra.txt").write_text("unexpected")
    elif mutation == "root":
        manifest["campaign_root"] += "-copy"
    elif mutation == "inode":
        manifest["ledger_identity"]["inode"] += 1
    elif mutation == "event_prefix":
        manifest["original_event_prefix"]["sha256"] = "a" * 64
    write_json(amendment, manifest)
    with pytest.raises((BudgetError, ValueError)):
        recovery.load_context(amendment, path, plan)


@pytest.mark.parametrize(
    "mutation",
    ["other_error", "usage_present", "removal_wrong_owner", "false_cleanup", "wrong_price_request"],
)
def test_only_inspected_terminal_output_limit_failures_qualify(stopped, mutation):
    path, plan, _, _, amendment, (identity, rid) = stopped
    base = path.parent / "attempts" / identity
    if mutation in {"other_error", "usage_present"}:
        response = base / "provider" / (rid + ".response.json")
        data = json.loads(response.read_text())
        if mutation == "other_error":
            data["response"]["error"]["message"] = "Different provider failure"
        else:
            data["response"]["usage"] = {}
        write_json(response, data)
    elif mutation == "removal_wrong_owner":
        removal = base / "solver/sandbox/container-removal.json"
        data = json.loads(removal.read_text())
        data["output"] = "everval-benchmark-" + "b" * 32 + "\n"
        write_json(removal, data)
    elif mutation == "false_cleanup":
        result = base / "result.json"
        data = json.loads(result.read_text())
        data["cleanup_confirmed"] = False
        write_json(result, data)
    else:
        req = base / "provider" / (rid + ".request.json")
        data = json.loads(req.read_text())
        data["payload"]["service_tier"] = "priority"
        write_json(req, data)
    with pytest.raises((BudgetError, ValueError)):
        recovery.create_amendment(path, amendment)
        recovery.review_request(path, amendment, rid)


def test_decision_evidence_cannot_be_changed_or_replaced_by_symlink(stopped, tmp_path):
    path, plan, _, _, amendment, (_, rid) = _review(stopped)
    decision = amendment.parent / "decisions" / (rid + ".json")
    saved = decision.read_bytes()
    decision.write_text("{}")
    with pytest.raises(BudgetError):
        recovery.load_context(amendment, path, plan)
    decision.unlink()
    other = tmp_path / "external.json"
    other.write_bytes(saved)
    decision.symlink_to(other)
    with pytest.raises(BudgetError):
        recovery.load_context(amendment, path, plan)


def test_original_event_history_and_review_prefix_are_immutable(stopped):
    path, plan, _, _, amendment, _ = _review(stopped)
    with sqlite3.connect(path.parent / "budget.sqlite3") as connection:
        connection.execute("UPDATE events SET recorded_at='changed' WHERE sequence=1")
    with pytest.raises(BudgetError, match="prefix"):
        recovery.load_context(amendment, path, plan)


def test_copied_campaign_cannot_reuse_recovery_lock_and_budget(stopped, tmp_path):
    path, plan, _, _, amendment, _ = _review(stopped)
    destination = tmp_path / "copy"
    shutil.copytree(path.parent, destination)
    with pytest.raises(BudgetError, match="original frozen plan"):
        recovery.load_context(
            destination / "recovery/amendment.json", destination / "campaign.json", plan
        )


def test_review_requires_idle_campaign_lock(stopped):
    path, _, _, _, amendment, (_, rid) = _create(stopped)
    with campaign.campaign_lock(path.parent), pytest.raises(BudgetError, match="active"):
        recovery.review_request(path, amendment, rid)


def test_recovery_metadata_omits_local_evidence_and_preserves_original_source_map(stopped):
    path, plan, _, _, amendment, _ = _create(stopped)
    result = recovery.public_recovery_metadata(amendment, path)
    serialized = json.dumps(result)
    assert str(path.parent) not in serialized
    assert "retained_evidence" not in result and "ledger_identity" not in result
    assert result["retained_attempt_count"] == 3
    assert len(result["allowed_attempts"]) == 1
    for relative, binding in result["source_overrides"].items():
        assert binding["before"] == plan["source_sha256"].get(relative)


def test_amended_interruption_retains_provenance_and_remains_reportable(stopped):
    path, plan, plan_hash, _, amendment, _ = _review(stopped)
    context = recovery.load_context(amendment, path, plan)
    ledger = campaign.open_campaign_ledger(
        path.parent, plan, plan_hash, create=False, recovery_context=context
    )
    identity, attempt_hash = context.allowed_attempts[0]
    ledger.register_attempt(identity, attempt_hash)
    write_json(
        path.parent / "attempts" / identity / "started.json",
        {
            "attempt_id": identity,
            "attempt_hash": attempt_hash,
            "plan_sha256": plan_hash,
            "accounting_amendment_sha256": context.amendment_sha256,
        },
    )
    with pytest.raises(RuntimeError, match="Interrupted attempt"):
        campaign.run_phase(path, "test", execute=True, amendment=amendment)
    retained = json.loads((path.parent / "attempts" / identity / "result.json").read_text())
    assert retained["accounting_amendment_sha256"] == context.amendment_sha256
    assert retained["status"] == "interrupted" and retained["cleanup_confirmed"] is False
    projected = report.build_report(path, amendment=amendment)
    assert projected["test"]["paired"]["quality_comparable_pairs"] == 0
    assert projected["accounting"]["cost_complete"] is False
