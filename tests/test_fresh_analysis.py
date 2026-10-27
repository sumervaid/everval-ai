import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_study.fresh_analysis import (  # noqa: E402
    classify,
    contrast,
    exact_binomial_interval,
    exact_mcnemar,
    proportion,
)


def row(task, model, category, repo="a"):
    return {"task_id": task, "model": model, "category": category, "repo": repo}


def test_exact_interval_matches_published_scipy_example():
    assert exact_binomial_interval(7, 50, 0.05) == pytest.approx(
        [0.05819170033997342, 0.26739600249700846]
    )


def test_zero_and_all_successes_never_create_degenerate_certainty():
    low, high = exact_binomial_interval(0, 50, 0.05)
    assert low == 0
    assert high == pytest.approx(1 - 0.025 ** (1 / 50))
    reverse = exact_binomial_interval(50, 50, 0.05)
    assert reverse == pytest.approx([1 - high, 1])


def test_exact_mcnemar_known_discordance_and_no_discordance():
    assert exact_mcnemar(0, 0) == 1
    assert exact_mcnemar(0, 6) == pytest.approx(0.03125)
    assert exact_mcnemar(6, 0) == exact_mcnemar(0, 6)
    assert exact_mcnemar(3, 3) == 1


def test_missing_response_differs_from_an_empty_submitted_patch():
    assert classify(None, None) == "unknown_submission"
    assert classify({"patch": ""}, None) == "invalid_or_absent_submission"
    assert classify({"patch": None}, None) == "invalid_or_absent_submission"


def test_patch_identity_and_incomplete_execution_are_not_silent_failures():
    response = {"patch": "a patch"}
    grade = {
        "patch_sha256": hashlib.sha256(b"a patch").hexdigest(),
        "status": "graded",
        "all_required_tests_observed": False,
        "test_suite_exit_code": 1,
        "resolved": False,
        "cleanup_complete": True,
    }
    assert classify(response, grade) == "unknown_execution"
    grade["all_required_tests_observed"] = True
    assert classify(response, grade) == "verified_unresolved"
    grade["patch_sha256"] = "incorrect"
    with pytest.raises(ValueError, match="bound"):
        classify(response, grade)


def test_unknown_outcomes_widen_full_cohort_bounds_and_disable_interval():
    result = proportion(
        [
            row("1", "a", "verified_resolved"),
            row("2", "a", "unknown_execution"),
            row("3", "a", "invalid_or_absent_submission"),
            row("4", "a", "verified_unresolved"),
        ]
    )
    assert result["planned_tasks"] == 4
    assert result["full_cohort_success_bounds"] == [0.25, 0.5]
    assert result["independent_task_sensitivity_interval"] is None


def test_paired_bounds_are_task_matched_not_difference_of_available_means():
    rows = [
        row("1", "a", "verified_resolved"),
        row("1", "b", "unknown_execution"),
        row("2", "a", "unknown_submission", "b"),
        row("2", "b", "verified_resolved", "b"),
    ]
    result = contrast(rows, "a", "b")
    assert result["full_cohort_difference_bounds"] == [-0.5, 0.5]
    assert result["complete_pairs"] == 0
    assert result["independent_task_exact_mcnemar_p"] is None
    assert result["bonferroni_adjusted_p"] is None
    assert result["leave_one_repository_out_bounds"] == {"a": [0.0, 1.0], "b": [-1.0, 0.0]}


def test_complete_paired_contrast_keeps_multiplicity_and_repository_sensitivity():
    rows = []
    for i in range(6):
        rows.extend(
            [
                row(str(i), "a", "invalid_or_absent_submission", "a" if i < 3 else "b"),
                row(str(i), "b", "verified_resolved", "a" if i < 3 else "b"),
            ]
        )
    result = contrast(rows, "a", "b")
    assert result["full_cohort_difference_bounds"] == [1, 1]
    assert result["right_only_resolved"] == 6
    assert result["independent_task_exact_mcnemar_p"] == 0.03125
    assert result["bonferroni_adjusted_p"] == 0.09375
    assert result["bootstrap_interval"] is None
    assert len(result["repository_results"]) == 2


def test_missing_and_duplicate_task_model_rows_are_rejected():
    with pytest.raises(ValueError, match="both"):
        contrast([row("1", "a", "verified_resolved")], "a", "b")
    with pytest.raises(ValueError, match="Duplicate"):
        contrast(
            [row("1", "a", "verified_resolved"), row("1", "a", "verified_unresolved")],
            "a",
            "b",
        )


@pytest.mark.parametrize("successes,count,alpha", [(0, 0, 0.05), (2, 1, 0.05), (0, 2, 0)])
def test_invalid_binomial_inputs(successes, count, alpha):
    with pytest.raises(ValueError):
        exact_binomial_interval(successes, count, alpha)


def test_accounted_refusal_can_be_analyzed_without_mutation_or_dispatch(tmp_path):
    import json

    from scripts.judge_study import fresh_repositories as study
    from scripts.judge_study.fresh_analysis import binary, verify_terminal_result

    model = "gpt-6-luna"
    body = {"model": model, "input": "public task"}
    job = {"id": "job", "instance_id": "task", "model": model, "body_sha256": study.digest(body)}
    raw = {
        "id": "response",
        "model": model,
        "status": "completed",
        "service_tier": "default",
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "refusal", "refusal": "Declined."}],
            }
        ],
    }
    result = {
        "instance_id": "task",
        "model": model,
        "returned_generator_model": model,
        "provider_response_id": "response",
        "provider_usage": raw["usage"],
        "patch": None,
        "status": "invalid_or_incomplete_response",
        "conservative_cost_usd": "0.0000035",
    }
    from scripts.benchmarks.budget import BudgetLedger

    inference = {
        "jobs": [job],
        "source_sha256": hashlib.sha256(Path(study.__file__).read_bytes()).hexdigest(),
    }
    study.private_json(tmp_path / "inference.json", inference)
    study.private_json(tmp_path / "inference-freeze.json", {"sha256": study.digest(inference)})
    ledger = BudgetLedger(
        tmp_path / "accounting" / (model + ".sqlite3"), study.budget_config(inference, model)
    )
    ledger.register_attempt(job["id"], study.digest({"job": job["id"], "body": job["body_sha256"]}))
    reservation = ledger.reserve(job["id"], job["body_sha256"], 100, 100)
    result["conservative_cost_usd"] = str(ledger.settle(reservation, 10, 2))
    output = tmp_path / "requests" / "job"
    output.mkdir(parents=True)
    raw_bytes = json.dumps(raw).encode()
    for name, value in {
        "request.json": {"body": body, "reservation_id": reservation},
        "result.json": result,
        "response.json": {"http_status": 200, "response": raw},
        "transport-response.json": {
            "http_status": 200,
            "truncated": False,
            "redacted_bytes_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
    }.items():
        (output / name).write_text(json.dumps(value))
    (output / "transport-response.bin").write_bytes(raw_bytes)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    event = verify_terminal_result(tmp_path, job)
    assert event == "provider_refusal_or_policy_rejection"
    assert binary(classify(result, None, event)) == 0
    assert classify(None, None) == "unknown_submission"
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "dispatch-STOP.json").exists()
    with pytest.raises(ValueError, match="binding"):
        verify_terminal_result(tmp_path, {**job, "body_sha256": "altered"})
    original = (output / "result.json").read_bytes()
    (output / "result.json").write_text(json.dumps({**result, "conservative_cost_usd": "999"}))
    with pytest.raises(ValueError, match="accounting"):
        verify_terminal_result(tmp_path, job)
    (output / "result.json").write_bytes(original)
    (output / "request.json").write_text(json.dumps({"body": body, "reservation_id": "wrong"}))
    with pytest.raises(ValueError, match="accounting"):
        verify_terminal_result(tmp_path, job)
    (output / "request.json").write_text(json.dumps({"body": body, "reservation_id": reservation}))
    raw["service_tier"] = "priority"
    raw_bytes = json.dumps(raw).encode()
    (output / "transport-response.bin").write_bytes(raw_bytes)
    (output / "transport-response.json").write_text(
        json.dumps(
            {
                "http_status": 200,
                "truncated": False,
                "redacted_bytes_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            }
        )
    )
    (output / "response.json").write_text(json.dumps({"http_status": 200, "response": raw}))
    with pytest.raises(ValueError, match="binding"):
        verify_terminal_result(tmp_path, job)


def test_required_skips_cannot_be_hidden_by_summary_flags():
    response = {"patch": "a patch"}
    grade = {
        "patch_sha256": hashlib.sha256(b"a patch").hexdigest(),
        "status": "graded",
        "all_required_tests_observed": True,
        "test_statuses": {"fix": "FAILED", "keep": "SKIPPED"},
        "test_suite_exit_code": 1,
        "resolved": False,
        "cleanup_complete": True,
    }
    assert classify(response, grade, required_tests=["fix", "keep"]) == "unknown_execution"
    grade["test_statuses"]["keep"] = "PASSED"
    assert classify(response, grade, required_tests=["fix", "keep"]) == "verified_unresolved"
    grade["resolved"] = True
    with pytest.raises(ValueError, match="contradicts"):
        classify(response, grade, required_tests=["fix", "keep"])
