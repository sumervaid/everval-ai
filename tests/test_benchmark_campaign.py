"""Campaign admission checks use original local fixtures and never permit HTTP."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
campaign = importlib.import_module("scripts.benchmarks.campaign")
IMAGE = "sha256:" + "a" * 64
SOURCE_FILES = (
    "scripts/benchmarks/budget.py",
    "scripts/benchmarks/model.py",
    "scripts/benchmarks/executor.py",
    "scripts/benchmarks/campaign.py",
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def original_task(identifier):
    return {
        "instance_id": identifier,
        "repo": "original/fixture",
        "base_commit": "b" * 40,
        "problem_statement": "Count each original fixture item according to its quantity.",
        "patch": "original teaching repair",
        "test_patch": "original private test fixture",
        "FAIL_TO_PASS": ["tests/test_cart.py::test_quantity"],
        "PASS_TO_PASS": ["tests/test_cart.py::test_empty"],
    }


def control_for(task):
    common = {
        "instance_id": task["instance_id"],
        "image_id": IMAGE,
        "status": "graded",
        "all_required_tests_observed": True,
        "fail_to_pass_count": 1,
        "pass_to_pass_count": 1,
        "missing_required_tests": [],
    }
    before = {
        **common,
        "resolved": False,
        "patch_sha256": digest(""),
        "test_statuses": {task["FAIL_TO_PASS"][0]: "FAILED", task["PASS_TO_PASS"][0]: "PASSED"},
        "container_script_returncode": 1,
    }
    after = {
        **common,
        "resolved": True,
        "patch_sha256": digest(task["patch"]),
        "test_statuses": {task["FAIL_TO_PASS"][0]: "PASSED", task["PASS_TO_PASS"][0]: "PASSED"},
        "container_script_returncode": 0,
    }
    return {
        **common,
        "valid": True,
        "base": before,
        "gold": after,
        "base_resolved": False,
        "gold_resolved": True,
        "base_status": "graded",
        "gold_status": "graded",
        "controls_are_stricter_than_official_skip_xfail_semantics": True,
    }


@pytest.fixture
def plan_file(tmp_path):
    plan = {
        "schema_version": 1,
        "campaign_id": "original-admission-fixture",
        "source_sha256": {name: campaign.sha256(ROOT / name) for name in SOURCE_FILES},
        "model": {
            "model_name": "gpt-6.1-sol",
            "reasoning_effort": "medium",
            "max_completion_tokens": 8192,
        },
        "execution": {"step_limit": 3, "cost_limit": 6.0},
        "grading_timeout_seconds": 30,
        "budget": {
            "pricing_version": "fixture-conservative-2026-10-15",
            "opening_spend_usd": "0.482999",
            "user_ceiling_usd": "200",
            "admission_ceiling_usd": "190",
            "attempt_cap_usd": "6",
            "input_rate_per_million": "2.50",
            "output_rate_per_million": "10",
        },
        "tasks": [],
    }
    for identifier, split in (("fixture__cart-1", "development"), ("fixture__cart-2", "test")):
        task = original_task(identifier)
        task_path = tmp_path / "fixtures" / (identifier + ".task.json")
        control_path = tmp_path / "fixtures" / (identifier + ".control.json")
        write_json(task_path, task)
        write_json(control_path, control_for(task))
        plan["tasks"].append(
            {
                "instance_id": identifier,
                "split": split,
                "task_path": str(task_path),
                "task_sha256": campaign.sha256(task_path),
                "control_path": str(control_path),
                "control_sha256": campaign.sha256(control_path),
                "image_id": IMAGE,
                "admitted": True,
            }
        )
    selection_path = tmp_path / "selection.json"
    write_json(
        selection_path,
        {
            "tasks": [
                {"instance_id": item["instance_id"], "split": item["split"]}
                for item in plan["tasks"]
            ]
        },
    )
    plan["selection_path"] = str(selection_path)
    plan["selection_sha256"] = campaign.sha256(selection_path)
    path = tmp_path / "campaign" / "plan.json"
    write_json(path, plan)
    return path


@pytest.fixture(autouse=True)
def isolated_execution(monkeypatch):
    calls = {"models": [], "runs": [], "grades": [], "closed": []}

    def forbid_http(*args, **kwargs):
        pytest.fail("Campaign admission test reached HTTP")

    monkeypatch.setattr(httpx.Client, "send", forbid_http)
    monkeypatch.setenv("OPENAI_API_KEY", "original-test-value-never-sent")

    class Model:
        def __init__(self, *, ledger, attempt_id, attempt_hash, **kwargs):
            self.ledger = ledger
            self.attempt_id = attempt_id
            self.output_dir = Path(kwargs["output_dir"])
            self.ledger.register_attempt(attempt_id, attempt_hash)
            self.calls = 0
            self.cost = 0.0
            calls["models"].append(attempt_id)

        def close(self):
            calls["closed"].append(self.attempt_id)

        def serialize(self):
            return {
                "calls": self.calls,
                "recorded_cost_usd": self.cost,
                "attempt_id": self.attempt_id,
            }

    def run_attempt(**kwargs):
        calls["runs"].append((kwargs["task"]["instance_id"], kwargs["arm"]))
        return {"status": "completed", "submitted": False, "patch": ""}

    def grade_patch(**kwargs):
        calls["grades"].append(kwargs)
        return {"resolved": False, "status": "graded"}

    stub = ModuleType("scripts.benchmarks.executor")
    stub.run_attempt = run_attempt
    stub.grade_patch = grade_patch
    monkeypatch.setitem(sys.modules, "scripts.benchmarks.executor", stub)
    monkeypatch.setattr(campaign, "MeteredOpenAI", Model)
    return calls, stub


def saved_result(plan_hash, item, arm):
    _, attempt_hash = campaign.attempt_identity(plan_hash, item, arm)
    return {
        "instance_id": item["instance_id"],
        "arm": arm,
        "split": item["split"],
        "attempt_hash": attempt_hash,
        "status": "completed",
        "submitted": False,
        "resolved": False,
        "grade": None,
    }


def seed_results(plan_file):
    plan, plan_hash = campaign.verify_plan(plan_file)
    ledger = campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3", campaign.budget_config(plan, plan_hash)
    )
    for item in plan["tasks"]:
        if item["split"] == "development":
            for arm in campaign.ARMS:
                attempt_id, attempt_hash = campaign.attempt_identity(plan_hash, item, arm)
                ledger.register_attempt(attempt_id, attempt_hash)
                write_json(
                    plan_file.parent / "attempts" / attempt_id / "result.json",
                    saved_result(plan_hash, item, arm),
                )
    return plan, plan_hash


def test_plan_only_does_not_construct_model_execute_or_need_a_key(
    plan_file, isolated_execution, monkeypatch
):
    calls, _ = isolated_execution
    monkeypatch.delenv("OPENAI_API_KEY")
    result = campaign.run_phase(plan_file, "development", execute=False)
    assert result["mode"] == "plan"
    assert result["selected_tasks"] == result["admitted_tasks"] == 1
    assert result["paid_requests"] == 0
    assert result["budget"]["known_spend_usd"] == "0.482999"
    assert calls == {"models": [], "runs": [], "grades": [], "closed": []}


def test_order_is_predefined_and_completed_attempts_are_not_retried(plan_file, isolated_execution):
    calls, _ = isolated_execution
    plan, _ = campaign.verify_plan(plan_file)
    first = plan["tasks"][0]
    campaign.run_phase(plan_file, "development", execute=True)
    expected = [(first["instance_id"], arm) for arm in campaign.paired_order(first["instance_id"])]
    assert calls["runs"] == expected
    assert calls["models"] == calls["closed"]
    campaign.run_phase(plan_file, "development", execute=True)
    assert calls["runs"] == expected
    campaign.run_phase(plan_file, "test", execute=True)
    second = plan["tasks"][1]
    assert calls["runs"][2:] == [
        (second["instance_id"], arm) for arm in campaign.paired_order(second["instance_id"])
    ]
    assert set(campaign.paired_order(f"original-{index}") for index in range(30)) == {
        ("baseline", "everval"),
        ("everval", "baseline"),
    }


def test_missing_development_results_stop_test_admission(plan_file, isolated_execution):
    calls, _ = isolated_execution
    with pytest.raises(ValueError, match="development"):
        campaign.run_phase(plan_file, "test", execute=True)
    assert calls["models"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instance_id", "wrong-task"),
        ("arm", "unknown"),
        ("split", "test"),
        ("attempt_hash", "0" * 64),
        ("resolved", 1),
        ("submitted", "yes"),
        ("status", None),
    ],
)
def test_stale_or_malformed_development_result_cannot_open_test_gate(
    plan_file, isolated_execution, field, value
):
    calls, _ = isolated_execution
    plan, plan_hash = seed_results(plan_file)
    item = plan["tasks"][0]
    result = saved_result(plan_hash, item, campaign.ARMS[0])
    result[field] = value
    attempt_id, _ = campaign.attempt_identity(plan_hash, item, campaign.ARMS[0])
    write_json(plan_file.parent / "attempts" / attempt_id / "result.json", result)
    with pytest.raises(ValueError):
        campaign.run_phase(plan_file, "test", execute=True)
    assert calls["models"] == []


def test_started_attempt_blocks_resume_until_cleanup_is_reviewed(plan_file, isolated_execution):
    calls, _ = isolated_execution
    plan, plan_hash = campaign.verify_plan(plan_file)
    item = plan["tasks"][0]
    arm = campaign.paired_order(item["instance_id"])[0]
    attempt_id, attempt_hash = campaign.attempt_identity(plan_hash, item, arm)
    directory = plan_file.parent / "attempts" / attempt_id
    marker = directory / "started.json"
    write_json(
        marker,
        {
            "attempt_id": attempt_id,
            "attempt_hash": attempt_hash,
            "plan_sha256": plan_hash,
            "started_at": "2026-10-15T00:00:00+00:00",
        },
    )
    original = marker.read_bytes()
    with pytest.raises(RuntimeError, match="cleanup"):
        campaign.run_phase(plan_file, "development", execute=True)
    result_path = directory / "result.json"
    retained = result_path.read_bytes()
    result = campaign.read_json(result_path)
    assert result["status"] == "interrupted"
    assert result["resolved"] is False
    assert result["cleanup_confirmed"] is False
    assert result["accounting_recovery_required"] is True
    assert not (plan_file.parent / "budget.sqlite3").exists()
    assert result["attempt_hash"] == attempt_hash
    assert marker.read_bytes() == original
    assert calls["models"] == calls["runs"] == []
    with pytest.raises(RuntimeError, match="cleanup"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert result_path.read_bytes() == retained
    assert calls["models"] == calls["runs"] == []


def test_started_marker_from_another_plan_is_not_relabelled(plan_file, isolated_execution):
    calls, _ = isolated_execution
    plan, plan_hash = campaign.verify_plan(plan_file)
    item = plan["tasks"][0]
    arm = campaign.paired_order(item["instance_id"])[0]
    attempt_id, attempt_hash = campaign.attempt_identity(plan_hash, item, arm)
    directory = plan_file.parent / "attempts" / attempt_id
    write_json(
        directory / "started.json",
        {"attempt_id": attempt_id, "attempt_hash": attempt_hash, "plan_sha256": "0" * 64},
    )
    with pytest.raises(ValueError):
        campaign.run_phase(plan_file, "development", execute=True)
    assert not (directory / "result.json").exists()
    assert calls["models"] == []


def test_pending_request_blocks_resumed_campaign_before_another_model(
    plan_file, isolated_execution
):
    calls, _ = isolated_execution
    plan, plan_hash = campaign.verify_plan(plan_file)
    ledger = campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3", campaign.budget_config(plan, plan_hash)
    )
    attempt_id, attempt_hash = campaign.attempt_identity(plan_hash, plan["tasks"][0], "baseline")
    ledger.register_attempt(attempt_id, attempt_hash)
    ledger.reserve(attempt_id, digest("interrupted-http-request"), 100, 100)
    with pytest.raises(RuntimeError, match="accounting"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []
    assert ledger.status()["pending_requests"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "not_valid",
        "wrong_instance",
        "wrong_image",
        "base_wrong_image",
        "gold_wrong_patch",
        "base_wrong_patch",
        "base_did_not_fail",
        "base_regression",
        "gold_failure",
        "unobserved",
        "wrong_count",
    ],
)
def test_inconsistent_control_evidence_fails_before_paid_admission(
    plan_file, isolated_execution, mutation
):
    calls, _ = isolated_execution
    plan = campaign.read_json(plan_file)
    item = plan["tasks"][0]
    task = campaign.read_json(Path(item["task_path"]))
    control = campaign.read_json(Path(item["control_path"]))
    if mutation == "not_valid":
        control["valid"] = False
    elif mutation == "wrong_instance":
        control["instance_id"] = "other"
    elif mutation == "wrong_image":
        control["image_id"] = "sha256:" + "c" * 64
    elif mutation == "base_wrong_image":
        control["base"]["image_id"] = "sha256:" + "c" * 64
    elif mutation == "gold_wrong_patch":
        control["gold"]["patch_sha256"] = digest("different repair")
    elif mutation == "base_wrong_patch":
        control["base"]["patch_sha256"] = digest("unexpected starting edit")
    elif mutation == "base_did_not_fail":
        control["base"]["test_statuses"][task["FAIL_TO_PASS"][0]] = "PASSED"
    elif mutation == "base_regression":
        control["base"]["test_statuses"][task["PASS_TO_PASS"][0]] = "FAILED"
    elif mutation == "gold_failure":
        control["gold"]["test_statuses"][task["FAIL_TO_PASS"][0]] = "FAILED"
    elif mutation == "unobserved":
        control["gold"]["all_required_tests_observed"] = False
    else:
        control["base"]["fail_to_pass_count"] = 2
    write_json(Path(item["control_path"]), control)
    item["control_sha256"] = campaign.sha256(Path(item["control_path"]))
    write_json(plan_file, plan)
    with pytest.raises(ValueError):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []


@pytest.mark.parametrize(
    "mutation", ["task_hash", "control_hash", "source_hash", "missing_source", "outside_source"]
)
def test_frozen_hashes_and_source_scope_fail_before_model(
    plan_file, isolated_execution, mutation, tmp_path
):
    calls, _ = isolated_execution
    plan = campaign.read_json(plan_file)
    if mutation == "task_hash":
        plan["tasks"][0]["task_sha256"] = "0" * 64
    elif mutation == "control_hash":
        plan["tasks"][0]["control_sha256"] = "0" * 64
    elif mutation == "source_hash":
        plan["source_sha256"][SOURCE_FILES[0]] = "0" * 64
    elif mutation == "missing_source":
        del plan["source_sha256"][SOURCE_FILES[0]]
    else:
        outside = tmp_path / "outside-source.txt"
        outside.write_text("original local fixture, not executable code\n")
        plan["source_sha256"][os.path.relpath(outside, ROOT)] = campaign.sha256(outside)
    write_json(plan_file, plan)
    with pytest.raises(ValueError):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []


def test_known_cost_survives_executor_error_and_is_not_charged_again(plan_file, isolated_execution):
    calls, stub = isolated_execution
    ordinary = stub.run_attempt

    def fail_after_synthetic_known_usage(**kwargs):
        if not calls["runs"]:
            model = kwargs["model"]
            request = retain_provider_request(plan_file, model.ledger, model.attempt_id)
            model.cost = float(model.ledger.settle(request, 100, 20))
            model.calls = 1
            calls["runs"].append((kwargs["task"]["instance_id"], kwargs["arm"]))
            raise RuntimeError("Original local executor fixture")
        return ordinary(**kwargs)

    stub.run_attempt = fail_after_synthetic_known_usage
    result = campaign.run_phase(plan_file, "development", execute=True)
    assert result["known_spend_usd"] == "0.483449"
    assert result["settled_cost_usd"] == "0.00045"
    assert result["cost_complete"] is True
    saved = [
        campaign.read_json(path) for path in (plan_file.parent / "attempts").glob("*/result.json")
    ]
    assert sum(row["status"] == "executor_error" for row in saved) == 1
    assert len(calls["runs"]) == 2
    again = campaign.run_phase(plan_file, "development", execute=True)
    assert again["known_spend_usd"] == result["known_spend_usd"]
    assert len(calls["runs"]) == 2


@pytest.mark.parametrize("mutation", ["selection_hash", "omitted_task", "changed_partition"])
def test_frozen_selection_prevents_silent_task_or_partition_changes(
    plan_file, isolated_execution, mutation
):
    calls, _ = isolated_execution
    plan = campaign.read_json(plan_file)
    if mutation == "selection_hash":
        plan["selection_sha256"] = "0" * 64
    elif mutation == "omitted_task":
        plan["tasks"] = plan["tasks"][:1]
    else:
        plan["tasks"][1]["split"] = "development"
    write_json(plan_file, plan)
    with pytest.raises(ValueError, match="selection"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []


def test_unsafe_task_identity_is_rejected_before_creating_attempt_paths(
    plan_file, isolated_execution
):
    calls, _ = isolated_execution
    plan = campaign.read_json(plan_file)
    selection = campaign.read_json(Path(plan["selection_path"]))
    plan["tasks"][0]["instance_id"] = "../outside"
    selection["tasks"][0]["instance_id"] = "../outside"
    write_json(Path(plan["selection_path"]), selection)
    plan["selection_sha256"] = campaign.sha256(Path(plan["selection_path"]))
    write_json(plan_file, plan)
    with pytest.raises(ValueError, match="safe bounded identifier"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []
    assert not (plan_file.parent / "attempts").exists()


def test_image_requires_a_full_immutable_digest(plan_file, isolated_execution):
    calls, _ = isolated_execution
    plan = campaign.read_json(plan_file)
    item = plan["tasks"][0]
    control = campaign.read_json(Path(item["control_path"]))
    item["image_id"] = "sha256:short"
    for record in (control, control["base"], control["gold"]):
        record["image_id"] = item["image_id"]
    write_json(Path(item["control_path"]), control)
    item["control_sha256"] = campaign.sha256(Path(item["control_path"]))
    write_json(plan_file, plan)
    with pytest.raises(ValueError, match="immutable local image"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []


@pytest.mark.parametrize("stage", ["solver", "grader"])
@pytest.mark.parametrize("failure", ["unconfirmed", "status", "exception"])
def test_cleanup_failure_stops_second_admission_and_stays_blocked_on_resume(
    plan_file, isolated_execution, stage, failure
):
    calls, stub = isolated_execution

    class SandboxCleanupFailure(RuntimeError):
        pass

    def failure_result():
        if failure == "exception":
            raise SandboxCleanupFailure("Original cleanup failure fixture")
        return (
            {"cleanup_confirmed": False}
            if failure == "unconfirmed"
            else {"status": "cleanup_error"}
        )

    def solver(**kwargs):
        calls["runs"].append((kwargs["task"]["instance_id"], kwargs["arm"]))
        return {
            "status": "completed",
            "submitted": stage == "grader",
            "patch": "original patch fixture" if stage == "grader" else "",
            **(failure_result() if stage == "solver" else {}),
        }

    def grader(**kwargs):
        calls["grades"].append(kwargs)
        return {"resolved": False, "status": "graded", **failure_result()}

    stub.run_attempt = solver
    stub.grade_patch = grader
    with pytest.raises(RuntimeError, match="cleanup"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert len(calls["models"]) == 1
    assert calls["closed"] == calls["models"]
    assert len(calls["grades"]) == (1 if stage == "grader" else 0)
    retained = list((plan_file.parent / "attempts").glob("*/result.json"))
    assert len(retained) == 1
    original = retained[0].read_bytes()
    with pytest.raises(RuntimeError, match="cleanup"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert retained[0].read_bytes() == original
    assert len(calls["models"]) == 1


@pytest.mark.parametrize("setting", ["api_key", "client", "output_dir", "endpoint"])
def test_private_or_unfrozen_model_settings_are_rejected_before_admission(
    plan_file, isolated_execution, setting
):
    calls, _ = isolated_execution
    plan = campaign.read_json(plan_file)
    plan["model"][setting] = "original-local-test-value"
    write_json(plan_file, plan)
    with pytest.raises(ValueError, match="public frozen model settings"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert calls["models"] == []
    assert not (plan_file.parent / "budget.sqlite3").exists()


def retain_provider_request(plan_file, ledger, attempt_id, *, input_tokens=100, output_tokens=20):
    plan, _ = campaign.verify_plan(plan_file)
    settings = plan["model"]
    messages = [{"role": "user", "content": "private-target-evidence-do-not-export"}]
    payload = {
        "model": settings["model_name"],
        "messages": messages,
        "reasoning_effort": settings["reasoning_effort"],
        "max_completion_tokens": settings["max_completion_tokens"],
        "service_tier": "default",
        "stream": False,
        "store": False,
        "n": 1,
    }
    request_hash = hashlib.sha256(campaign.canonical(payload)).hexdigest()
    request = ledger.reserve(
        attempt_id,
        request_hash,
        len(campaign.canonical(messages)) + 8192,
        settings["max_completion_tokens"],
    )
    directory = plan_file.parent / "attempts" / attempt_id / "provider"
    write_json(
        directory / (request + ".request.json"),
        {
            "request_id": request,
            "request_sha256": request_hash,
            "payload": payload,
        },
    )
    write_json(
        directory / (request + ".response.json"),
        {
            "http_status": 200,
            "duration_seconds": 0.25,
            "response": {
                "model": settings["model_name"],
                "service_tier": "default",
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
                "choices": [{"message": {"content": "private-response-do-not-export"}}],
            },
        },
    )
    return request


def accounting_reader(plan_file, operation):
    if operation == "export":
        from scripts.benchmarks.report import build_report

        return build_report(plan_file)
    return campaign.run_phase(plan_file, "test", execute=operation == "resume")


@pytest.fixture
def charged_campaign(plan_file, isolated_execution):
    campaign.run_phase(plan_file, "development", execute=True)
    plan, plan_hash = campaign.verify_plan(plan_file)
    ledger = campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3",
        campaign.budget_config(plan, plan_hash),
        create=False,
    )
    attempt_id, _ = campaign.attempt_identity(plan_hash, plan["tasks"][0], "baseline")
    before = plan_file.parent / "before-charge.sqlite3"
    shutil.copyfile(ledger.path, before)
    request = retain_provider_request(plan_file, ledger, attempt_id)
    cost = ledger.settle(request, 100, 20)
    result_path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(result_path)
    result["provider"] = {"calls": 1, "recorded_cost_usd": float(cost), "attempt_id": attempt_id}
    write_json(result_path, result)
    return ledger, request, attempt_id, before


@pytest.mark.parametrize("operation", ["export", "resume", "plan"])
def test_missing_ledger_beside_completed_evidence_never_resets_spend(
    charged_campaign, plan_file, isolated_execution, operation
):
    ledger, _, _, _ = charged_campaign
    ledger.path.unlink()
    calls, _ = isolated_execution
    before = len(calls["models"])
    with pytest.raises(campaign.BudgetError, match="missing beside retained"):
        accounting_reader(plan_file, operation)
    assert not ledger.path.exists()
    assert len(calls["models"]) == before


@pytest.mark.parametrize("operation", ["export", "resume"])
def test_stale_ledger_restore_cannot_hide_retained_provider_call(
    charged_campaign, plan_file, isolated_execution, operation
):
    ledger, _, _, backup = charged_campaign
    shutil.copyfile(backup, ledger.path)
    calls, _ = isolated_execution
    before = len(calls["models"])
    with pytest.raises(campaign.BudgetError, match="absent from durable accounting"):
        accounting_reader(plan_file, operation)
    assert len(calls["models"]) == before
    assert (
        campaign.BudgetLedger(ledger.path, ledger.config, create=False).status()["requests"] == []
    )


def test_report_refuses_to_create_even_a_fresh_ledger(plan_file):
    from scripts.benchmarks.report import build_report

    with pytest.raises(campaign.BudgetError, match="Existing accounting"):
        build_report(plan_file)
    assert not (plan_file.parent / "budget.sqlite3").exists()


def test_registered_attempt_hash_must_match_frozen_identity(
    charged_campaign, plan_file, isolated_execution
):
    ledger, _, attempt_id, _ = charged_campaign
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("UPDATE attempts SET attempt_hash=? WHERE id=?", ("f" * 64, attempt_id))
    with pytest.raises(campaign.BudgetError, match="Registered attempt"):
        accounting_reader(plan_file, "resume")
    assert len(isolated_execution[0]["models"]) == 2


@pytest.mark.parametrize("operation", ["resume", "export"])
def test_result_without_ledger_registration_is_never_zero_cost(plan_file, operation):
    plan, plan_hash = campaign.verify_plan(plan_file)
    campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3", campaign.budget_config(plan, plan_hash)
    )
    item = plan["tasks"][0]
    attempt_id, _ = campaign.attempt_identity(plan_hash, item, "baseline")
    write_json(
        plan_file.parent / "attempts" / attempt_id / "result.json",
        saved_result(plan_hash, item, "baseline"),
    )
    with pytest.raises(campaign.BudgetError, match="no matching accounting registration"):
        accounting_reader(plan_file, operation)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_request",
        "missing_response",
        "request_hash",
        "request_payload",
        "request_id",
        "response_usage",
        "response_model",
        "ledger_cost",
        "ledger_reservation",
        "result_calls",
        "result_cost",
    ],
)
def test_retained_request_response_and_result_must_reconcile(
    charged_campaign, plan_file, isolated_execution, mutation
):
    ledger, request, attempt_id, _ = charged_campaign
    provider = plan_file.parent / "attempts" / attempt_id / "provider"
    request_path = provider / (request + ".request.json")
    response_path = provider / (request + ".response.json")
    result_path = provider.parent / "result.json"
    if mutation == "missing_request":
        request_path.unlink()
    elif mutation == "missing_response":
        response_path.unlink()
    elif mutation.startswith("request_"):
        value = campaign.read_json(request_path)
        if mutation == "request_hash":
            value["request_sha256"] = "f" * 64
        elif mutation == "request_payload":
            value["payload"]["messages"][0]["content"] = "changed target evidence"
        else:
            value["request_id"] = "f" * 32
        write_json(request_path, value)
    elif mutation.startswith("response_"):
        value = campaign.read_json(response_path)
        if mutation == "response_usage":
            value["response"]["usage"]["prompt_tokens"] = 101
        else:
            value["response"]["model"] = "unpriced-model"
        write_json(response_path, value)
    elif mutation.startswith("ledger_"):
        column = "known_cost_usd" if mutation == "ledger_cost" else "reserved_usd"
        with sqlite3.connect(ledger.path) as connection:
            connection.execute(f"UPDATE requests SET {column}='0.0001' WHERE id=?", (request,))
    else:
        value = campaign.read_json(result_path)
        value["provider"]["calls" if mutation == "result_calls" else "recorded_cost_usd"] = 0
        write_json(result_path, value)
    for operation in ("export", "resume"):
        with pytest.raises(campaign.BudgetError):
            accounting_reader(plan_file, operation)
    assert len(isolated_execution[0]["models"]) == 2


def test_complete_report_uses_exact_usage_and_never_exports_provider_payloads(
    charged_campaign, plan_file
):
    report = accounting_reader(plan_file, "export")
    assert report["accounting"]["known_spend_usd"] == "0.483449"
    assert report["accounting"]["cost_complete"] is True
    assert report["accounting"]["evidence_reconciled"] is True
    arm = report["tasks"][0]["arms"]["baseline"]
    assert arm["provider_calls"] == 1
    assert arm["known_input_tokens"] == 100
    assert arm["known_output_tokens"] == 20
    assert arm["known_cost_usd"] == "0.00045"
    encoded = json.dumps(report)
    assert "private-target-evidence" not in encoded
    assert "private-response" not in encoded
    assert "request_sha256" not in encoded


def test_response_persisted_before_settlement_requires_recovery_not_zero_cost(
    charged_campaign, plan_file
):
    ledger, request, attempt_id, _ = charged_campaign
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE requests SET state='pending', input_tokens=NULL, output_tokens=NULL,"
            "known_cost_usd='0' WHERE id=?",
            (request,),
        )
    path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(path)
    result["provider"] = {"calls": 0, "recorded_cost_usd": 0, "attempt_id": attempt_id}
    write_json(path, result)
    with pytest.raises(campaign.BudgetError, match="charges missing"):
        accounting_reader(plan_file, "export")
    assert ledger.status()["pending_requests"] == 1


def test_pending_before_request_file_stays_incomplete_and_blocks_admission(
    plan_file, isolated_execution
):
    campaign.run_phase(plan_file, "development", execute=True)
    plan, plan_hash = campaign.verify_plan(plan_file)
    ledger = campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3", campaign.budget_config(plan, plan_hash)
    )
    attempt_id, _ = campaign.attempt_identity(plan_hash, plan["tasks"][0], "baseline")
    ledger.reserve(attempt_id, digest("request-before-evidence-write"), 100, 100)
    report = accounting_reader(plan_file, "export")
    assert report["accounting"]["cost_complete"] is False
    assert report["accounting"]["pending_requests"] == 1
    assert report["tasks"][0]["arms"]["baseline"]["cost_complete"] is False
    with pytest.raises(RuntimeError, match="accounting"):
        accounting_reader(plan_file, "resume")
    assert len(isolated_execution[0]["models"]) == 2


def test_partial_unknown_usage_retains_lower_bound_in_report(plan_file, isolated_execution):
    campaign.run_phase(plan_file, "development", execute=True)
    plan, plan_hash = campaign.verify_plan(plan_file)
    ledger = campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3", campaign.budget_config(plan, plan_hash)
    )
    attempt_id, _ = campaign.attempt_identity(plan_hash, plan["tasks"][0], "baseline")
    request = retain_provider_request(plan_file, ledger, attempt_id, output_tokens=None)
    ledger.uncertain(request, "invalid_output_usage", input_tokens=100)
    report = accounting_reader(plan_file, "export")
    assert report["accounting"]["cost_complete"] is False
    assert report["accounting"]["known_spend_usd"] == "0.483249"
    arm = report["tasks"][0]["arms"]["baseline"]
    assert arm["known_cost_usd"] == "0.00025"
    assert arm["known_input_tokens"] == 100
    assert arm["cost_complete"] is False
    assert ledger.status()["uncertain_requests"] == 1


def test_reporting_refuses_an_active_campaign_snapshot(plan_file):
    from scripts.benchmarks.report import build_report

    with (
        campaign.campaign_lock(plan_file.parent),
        pytest.raises(campaign.BudgetError, match="Campaign is active"),
    ):
        build_report(plan_file)
    assert not (plan_file.parent / "budget.sqlite3").exists()


@pytest.mark.parametrize("with_pending_request", [False, True])
def test_registration_without_terminal_evidence_is_a_recovery_state(
    plan_file, isolated_execution, with_pending_request
):
    plan, plan_hash = campaign.verify_plan(plan_file)
    ledger = campaign.BudgetLedger(
        plan_file.parent / "budget.sqlite3", campaign.budget_config(plan, plan_hash)
    )
    attempt_id, attempt_hash = campaign.attempt_identity(plan_hash, plan["tasks"][0], "baseline")
    ledger.register_attempt(attempt_id, attempt_hash)
    if with_pending_request:
        ledger.reserve(attempt_id, digest("reserved-before-evidence"), 100, 100)
    report = accounting_reader(plan_file, "export")
    assert report["accounting"]["cost_complete"] is False
    assert report["tasks"][0]["arms"]["baseline"]["cost_complete"] is False
    with pytest.raises(RuntimeError, match="accounting"):
        campaign.run_phase(plan_file, "development", execute=True)
    assert isolated_execution[0]["models"] == []
    assert not (plan_file.parent / "attempts" / attempt_id / "started.json").exists()


@pytest.mark.parametrize("filename", ["campaign.lock", "attempts"])
def test_accounting_refuses_symlinked_lock_or_attempt_directory(plan_file, tmp_path, filename):
    target = tmp_path / "unrelated-target"
    if filename == "attempts":
        target.mkdir()
    else:
        target.write_text("untouched")
    link = plan_file.parent / filename
    link.symlink_to(target)
    with pytest.raises((campaign.BudgetError, OSError)):
        accounting_reader(plan_file, "export")
    assert not (plan_file.parent / "budget.sqlite3").exists()
    if filename == "campaign.lock":
        assert target.read_text() == "untouched"


@pytest.mark.parametrize("summary", ["absent", None, [], "invalid", 0])
def test_normal_terminal_result_requires_provider_summary(
    charged_campaign, plan_file, isolated_execution, summary
):
    _, _, attempt_id, _ = charged_campaign
    path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(path)
    if summary == "absent":
        del result["provider"]
    else:
        result["provider"] = summary
    write_json(path, result)
    for operation in ("export", "resume"):
        with pytest.raises(campaign.BudgetError, match="valid provider summary"):
            accounting_reader(plan_file, operation)
    assert len(isolated_execution[0]["models"]) == 2


def test_missing_summary_cannot_hide_charge_after_stale_ledger_and_evidence_loss(
    charged_campaign, plan_file, isolated_execution
):
    ledger, _, attempt_id, backup = charged_campaign
    shutil.copyfile(backup, ledger.path)
    directory = plan_file.parent / "attempts" / attempt_id
    shutil.rmtree(directory / "provider")
    result = campaign.read_json(directory / "result.json")
    del result["provider"]
    write_json(directory / "result.json", result)
    for operation in ("export", "resume"):
        with pytest.raises(campaign.BudgetError, match="valid provider summary"):
            accounting_reader(plan_file, operation)
    assert len(isolated_execution[0]["models"]) == 2
    assert ledger.status()["known_spend_usd"] == "0.482999"


@pytest.mark.parametrize("identity", ["absent", None, "other-attempt"])
def test_provider_summary_requires_explicit_matching_attempt_identity(
    charged_campaign, plan_file, identity
):
    _, _, attempt_id, _ = charged_campaign
    path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(path)
    if identity == "absent":
        del result["provider"]["attempt_id"]
    else:
        result["provider"]["attempt_id"] = identity
    write_json(path, result)
    for operation in ("export", "resume"):
        with pytest.raises(campaign.BudgetError, match="call count differs"):
            accounting_reader(plan_file, operation)


def test_synthetic_interruption_retains_known_cost_without_claiming_completeness(
    charged_campaign, plan_file, isolated_execution
):
    ledger, _, attempt_id, _ = charged_campaign
    path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(path)
    del result["provider"]
    result.update(
        status="interrupted",
        cleanup_confirmed=False,
        accounting_recovery_required=True,
        submitted=False,
        resolved=False,
        grade=None,
    )
    write_json(path, result)
    report = accounting_reader(plan_file, "export")
    assert report["accounting"]["known_spend_usd"] == "0.483449"
    assert report["accounting"]["cost_complete"] is False
    arm = report["tasks"][0]["arms"]["baseline"]
    assert arm["known_cost_usd"] == "0.00045"
    assert arm["cost_complete"] is False
    assert arm["resolved"] is False
    assert ledger.status()["settled_cost_usd"] == "0.00045"
    with pytest.raises(RuntimeError, match="cleanup"):
        accounting_reader(plan_file, "resume")
    assert len(isolated_execution[0]["models"]) == 2


@pytest.mark.parametrize("mutation", ["ordinary_error", "no_recovery_flag", "cleanup_claim"])
def test_provider_summary_omission_requires_the_full_synthetic_recovery_shape(
    charged_campaign, plan_file, mutation
):
    _, _, attempt_id, _ = charged_campaign
    path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(path)
    del result["provider"]
    result.update(
        status="interrupted",
        cleanup_confirmed=False,
        accounting_recovery_required=True,
        submitted=False,
        resolved=False,
        grade=None,
    )
    if mutation == "ordinary_error":
        result["status"] = "executor_error"
    elif mutation == "no_recovery_flag":
        del result["accounting_recovery_required"]
    else:
        result["cleanup_confirmed"] = True
    write_json(path, result)
    with pytest.raises(campaign.BudgetError, match="valid provider summary"):
        accounting_reader(plan_file, "export")


def test_interrupted_status_with_serialized_provider_is_still_a_recovery_state(
    charged_campaign, plan_file
):
    _, _, attempt_id, _ = charged_campaign
    path = plan_file.parent / "attempts" / attempt_id / "result.json"
    result = campaign.read_json(path)
    result["status"] = "interrupted"
    write_json(path, result)
    report = accounting_reader(plan_file, "export")
    assert report["accounting"]["known_spend_usd"] == "0.483449"
    assert report["accounting"]["cost_complete"] is False
    assert report["tasks"][0]["arms"]["baseline"]["cost_complete"] is False
    with pytest.raises(RuntimeError, match="accounting"):
        accounting_reader(plan_file, "resume")
