import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_study.fresh_repositories import (  # noqa: E402
    context_command,
    controls_valid,
    private_json,
    private_root,
    select_environment,
    select_tasks,
)


def test_admission_requires_observed_failures_and_regression_controls() -> None:
    task = {"FAIL_TO_PASS": ["fix"], "PASS_TO_PASS": ["keep"]}
    base = {
        "status": "graded",
        "resolved": False,
        "test_suite_exit_code": 1,
        "all_required_tests_observed": True,
        "test_statuses": {"fix": "FAILED", "keep": "PASSED"},
    }
    gold = {
        "status": "graded",
        "resolved": True,
        "test_suite_exit_code": 0,
        "all_required_tests_observed": True,
        "test_statuses": {"fix": "PASSED", "keep": "PASSED"},
    }
    assert controls_valid(task, base, gold)
    for invalid in (
        {**base, "status": "infrastructure_error"},
        {**base, "test_suite_exit_code": 2},
        {**base, "test_suite_exit_code": None},
        {**base, "all_required_tests_observed": False},
        {**base, "test_statuses": {"fix": "SKIPPED", "keep": "PASSED"}},
        {**base, "test_statuses": {"fix": "FAILED", "keep": "FAILED"}},
        {**base, "test_statuses": {"fix": "FAILED"}},
    ):
        assert not controls_valid(task, invalid, gold)
    assert not controls_valid(task, base, {**gold, "resolved": False})
    assert not controls_valid(task, base, {**gold, "test_suite_exit_code": 1})
    assert not controls_valid(task, base, {**gold, "test_suite_exit_code": False})
    assert not controls_valid({"FAIL_TO_PASS": [], "PASS_TO_PASS": ["keep"]}, base, gold)


def test_environment_and_order_do_not_depend_on_reference_or_model_outcomes() -> None:
    task = {"repo": "psf/requests", "version": "2.4", "instance_id": "requests-a"}
    environments = [
        {"repo": "psf/requests", "version": "2.3", "instance_id": "old"},
        {"repo": "psf/requests", "version": "2.4", "instance_id": "match"},
    ]
    assert select_environment(task, environments)["instance_id"] == "match"
    changed = {**task, "patch": "LEAK", "resolved_by_model": True}
    assert select_environment(changed, environments) == select_environment(task, environments)
    assert [x["instance_id"] for x in select_tasks([task])] == [
        x["instance_id"] for x in select_tasks([changed])
    ]


def test_source_extraction_never_accepts_reference_fields() -> None:
    task = {
        "problem_statement": "Correct the response header behavior",
        "patch": "REFERENCE_SECRET",
        "test_patch": "HIDDEN_TEST_SECRET",
        "hints_text": "FUTURE_DISCUSSION_SECRET",
        "FAIL_TO_PASS": ["PRIVATE_TEST"],
        "PASS_TO_PASS": ["PRIVATE_REGRESSION"],
    }
    command = context_command(task)
    for secret in (
        "REFERENCE_SECRET",
        "HIDDEN_TEST_SECRET",
        "FUTURE_DISCUSSION_SECRET",
        "PRIVATE_TEST",
        "PRIVATE_REGRESSION",
    ):
        assert secret not in command
    assert "git" in command and "ls-files" in command
    assert "symlink" in command
    assert "tests" in command


def test_durable_evidence_is_private_and_cannot_be_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    private_json(target, {"valid": False})
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        private_json(target, {"valid": True})


def test_artifacts_cannot_resolve_to_source_tree() -> None:
    with pytest.raises(ValueError, match="outside"):
        private_root(Path(__file__).resolve().parents[1] / "benchmark-data")


def test_request_is_bounded_and_reference_material_never_enters_prompt() -> None:
    from scripts.judge_study.fresh_repositories import MAX_BODY, PROFILES, canonical, request_body

    task = {
        "problem_statement": "Repair unicode α behavior " * 80,
        "patch": "SECRET_REFERENCE",
        "test_patch": "SECRET_CHECKS",
    }
    context = {"files": [{"path": "module.py", "excerpt": "π = 'source'\n" * 3000}]}
    context["selection"] = "base lexical retrieval"
    for model in PROFILES:
        body = request_body(task, context, model)
        encoded = canonical(body)
        assert len(encoded) <= MAX_BODY
        assert b"SECRET_REFERENCE" not in encoded
        assert b"SECRET_CHECKS" not in encoded
        assert body["store"] is False
        assert body["service_tier"] == "default"


def test_all_150_worst_case_reservations_fit_frozen_model_budgets() -> None:
    from decimal import Decimal

    from scripts.judge_study.fresh_repositories import FRAMING, MAX_BODY, OUTPUT_TOKENS, PROFILES

    total = Decimal(0)
    for profile in PROFILES.values():
        bound = (
            50
            * (
                (MAX_BODY + FRAMING) * Decimal(profile["input"])
                + OUTPUT_TOKENS * Decimal(profile["output"])
            )
            / 1_000_000
        )
        assert bound <= Decimal(profile["cap"])
        total += Decimal(profile["cap"])
    assert total == 12


def test_context_hash_is_bound_to_base_control_evidence() -> None:
    from scripts.judge_study.fresh_repositories import digest, validate_context

    context = {
        "selection": "base lexical retrieval",
        "files": [{"path": "module.py", "excerpt": "1: value = 2"}],
    }
    validate_context(context, digest(context))
    changed = {**context, "files": [{"path": "module.py", "excerpt": "1: reference_fix = 3"}]}
    with pytest.raises(ValueError, match="differs"):
        validate_context(changed, digest(context))
    with pytest.raises(ValueError, match="one to eight"):
        validate_context({"selection": "none", "files": []})
    with pytest.raises(ValueError, match="unsupported"):
        validate_context(
            {"selection": "bad", "files": [{"path": "../tests/private.py", "excerpt": "SECRET"}]}
        )


def test_complete_issue_is_never_silently_truncated() -> None:
    from scripts.judge_study.fresh_repositories import ISSUE_BYTES, request_body

    context = {
        "selection": "base lexical retrieval",
        "files": [{"path": "module.py", "excerpt": "1: x = 1"}],
    }
    issue = "x" * ISSUE_BYTES + " critical final requirement"
    with pytest.raises(ValueError, match="never truncate"):
        request_body({"problem_statement": issue}, context, "gpt-6-luna")


def test_final_answer_selector_ignores_commentary_and_reasoning() -> None:
    from scripts.judge_study.fresh_repositories import select_final_text

    response = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "I will inspect the function."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": '{"patch": "diff"}'}],
            },
        ]
    }
    assert select_final_text(response) == '{"patch": "diff"}'
    assert select_final_text({"output": [response["output"][-1], response["output"][-1]]}) is None


def test_final_answer_selector_handles_legacy_without_guessing() -> None:
    from scripts.judge_study.fresh_repositories import select_final_text

    message = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": '{"patch": ""}'}],
    }
    assert select_final_text({"output": [message]}) == '{"patch": ""}'
    assert select_final_text({"output": [message, message]}) is None
    assert (
        select_final_text(
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "phase": "commentary",
                        "content": message["content"],
                    }
                ]
            }
        )
        is None
    )
    assert (
        select_final_text(
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "phase": "final_answer",
                        "content": [{"type": "refusal", "refusal": "Cannot comply"}],
                    }
                ]
            }
        )
        is None
    )
    assert select_final_text({"output": None}) is None


def test_observed_identity_removes_only_exact_redundant_aliases() -> None:
    from scripts.judge_study.fresh_repositories import observed_test_names

    before = {
        "test_statuses": {
            "test_polynomial": "FAILED",
            "sympy/polys/tests/test_tools.py:test_polynomial": "FAILED",
            "test_keep": "PASSED",
        }
    }
    after = {"test_statuses": {"test_polynomial": "PASSED", "test_keep": "PASSED"}}
    assert observed_test_names(before) == observed_test_names(after)
    qualified = "sympy/a.py:test_polynomial"
    assert observed_test_names({"test_statuses": {qualified: "FAILED"}}) == {qualified}
    pytest_id = "tests/test_tools.py::TestClass::test_polynomial"
    assert observed_test_names({"test_statuses": {pytest_id: "FAILED"}}) == {pytest_id}


def test_observed_identity_rejects_conflicting_or_ambiguous_aliases() -> None:
    from scripts.judge_study.fresh_repositories import observed_test_names

    assert (
        observed_test_names(
            {
                "test_statuses": {
                    "test_x": "PASSED",
                    "sympy/a.py:test_x": "FAILED",
                }
            }
        )
        is None
    )
    assert (
        observed_test_names(
            {
                "test_statuses": {
                    "test_x": "FAILED",
                    "sympy/a.py:test_x": "FAILED",
                    "sympy/b.py:test_x": "FAILED",
                }
            }
        )
        is None
    )


def test_reused_execution_is_bound_to_frozen_raw_evidence(tmp_path: Path) -> None:
    from scripts.judge_study.fresh_repositories import digest, execution_lineage

    result = {"status": "graded"}
    source = {"result_sha256": digest(result), "execution_runner_sha256": "old", "reused": True}
    manifest = {"executions": {"task/1/base": source}}
    private_json(tmp_path / "reused-execution.json", manifest)
    plan = {
        "source_sha256": "new",
        "amendment": {
            "reused_execution_manifest_sha256": digest(manifest),
        },
    }
    assert execution_lineage(tmp_path, plan, "task", 1, "base", result) == source
    with pytest.raises(ValueError, match="result changed"):
        execution_lineage(tmp_path, plan, "task", 1, "base", {"status": "different"})
    assert execution_lineage(tmp_path, plan, "task", 2, "base", result)["reused"] is False


def test_final_selector_rejects_ambiguous_message_metadata() -> None:
    from scripts.judge_study.fresh_repositories import select_final_text

    final = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": "{}"}],
    }
    assert select_final_text({"output": [final]}) == "{}"
    for update in ({"role": "user"}, {"status": "in_progress"}, {"phase": "unknown"}):
        assert select_final_text({"output": [{**final, **update}]}) is None
    for other in ({**final, "phase": None}, {**final, "phase": "unknown"}):
        assert select_final_text({"output": [other, final]}) is None
    commentary = {**final, "phase": "commentary"}
    assert select_final_text({"output": [final, commentary]}) is None


def test_control_pause_preserves_completed_base_without_starting_gold(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.judge_study import fresh_repositories as runner

    task = {"instance_id": "sample", "problem_statement": "Repair x", "patch": "reference"}
    plan = {"tasks": [{"instance_id": "sample"}], "target_admitted": 50}
    monkeypatch.setattr(runner, "load", lambda root: plan)
    monkeypatch.setattr(runner, "task_for", lambda root, entry: task)
    calls = []

    def grade(task, entry, patch, out, **kwargs):
        calls.append(patch)
        result = {"status": "graded"}
        runner.private_json(out / "result.json", result)
        (tmp_path / "pause-requested").touch()
        return result

    monkeypatch.setattr(runner, "grade", grade)
    runner.run_controls(tmp_path)
    assert calls == [""]
    assert (tmp_path / "controls/sample/base/result.json").exists()
    assert not (tmp_path / "controls/sample/gold").exists()
    assert not (tmp_path / "controls/sample/controls.json").exists()


def test_disjoint_model_executor_opens_only_its_own_ledger(tmp_path: Path, monkeypatch) -> None:
    from scripts.benchmarks import budget
    from scripts.judge_study import fresh_repositories as runner

    opened = []

    class Ledger:
        def __init__(self, path, config, create):
            opened.append(path.name)

        def status(self):
            return {"admissions_blocked": False}

    monkeypatch.setenv("OPENAI_API_KEY", "test-no-requests-dispatched")
    monkeypatch.setattr(runner, "inference_plan", lambda root: {"jobs": []})
    monkeypatch.setattr(runner, "budget_config", lambda plan, model: model)
    monkeypatch.setattr(budget, "BudgetLedger", Ledger)
    runner.run_inference(tmp_path, "gpt-6-luna")
    assert opened == ["gpt-6-luna.sqlite3"]


def test_incomplete_execution_is_not_verified_failure() -> None:
    from scripts.judge_study.fresh_repositories import execution_verdict

    failed = {
        "status": "graded",
        "resolved": False,
        "test_suite_exit_code": 1,
        "all_required_tests_observed": True,
    }
    assert execution_verdict(failed) == "verified_unresolved"
    assert execution_verdict({**failed, "all_required_tests_observed": False}) == "unknown"
    assert execution_verdict({**failed, "status": "infrastructure_error"}) == "unknown"
    assert execution_verdict({**failed, "test_suite_exit_code": 2}) == "unknown"
    assert (
        execution_verdict({**failed, "resolved": True, "test_suite_exit_code": 0})
        == "verified_resolved"
    )
    assert execution_verdict({**failed, "resolved": True}) == "unknown"
