"""Task-level descriptive endpoints and strict provenance for preference audits."""

from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from test_self_preference_plan import fixture_run
from test_self_preference_run import campaign, mock_client, response

from scripts.judge_study import analyze_self_preference as analysis
from scripts.judge_study import self_preference_plan as planner
from scripts.judge_study import self_preference_run as runner


def prepared(tmp_path):
    fresh, root, _ = fixture_run(tmp_path)
    return planner.prepare(fresh, root)


def observations(plan):
    result = {}
    for job in plan["jobs"]:
        # Each generator-judge picks its own patch; the third model ties.
        preference = "tie"
        if job["judge"] in job["generators"]:
            preference = next(
                label
                for label, model in job["displayed_generators"].items()
                if model == job["judge"]
            )
        result[job["id"]] = {"status": "completed", "preference": preference, "attempted": True}
    return result


def test_task_weighting_and_same_identifier_comparator(tmp_path):
    plan = prepared(tmp_path)
    result = analysis.summarize(plan, observations(plan))
    assert result["attempted_jobs"] == 360
    for model in planner.MODELS:
        all_rows = result["by_model"][model]["all"]
        assert all_rows["selected_task_families"] == 20
        assert all_rows["eligible_generator_pairs"] == 40
        assert all_rows["matched_cells"] == 80
        assert all_rows["own_preference"]["families"] == 20
        assert all_rows["own_preference"]["estimate"] == 1
        assert all_rows["third_model_preference_for_same_patch"]["estimate"] == 0.5
        assert all_rows["own_minus_third"]["estimate"] == 0.5
        assert all_rows["own_minus_third"]["interval"] is None
        assert all_rows["own_minus_third"]["undefined_reason"] == "degenerate_family_distribution"
        assert "not causal" in result["limitations"][0]


def test_display_order_does_not_change_canonical_preference(tmp_path):
    plan = prepared(tmp_path)
    observed = observations(plan)
    first = analysis.summarize(plan, observed)
    swapped = deepcopy(plan)
    swapped_obs = deepcopy(observed)
    for job in swapped["jobs"]:
        job["displayed_generators"] = {
            label: job["displayed_generators"][other] for label, other in (("A", "B"), ("B", "A"))
        }
        label = swapped_obs[job["id"]]["preference"]
        if label in ("A", "B"):
            swapped_obs[job["id"]]["preference"] = "B" if label == "A" else "A"
    assert analysis.summarize(swapped, swapped_obs) == first


@pytest.mark.parametrize(
    "status, attempted",
    [("unattempted", False), ("error", True), ("invalid", True), ("abstained", True)],
)
def test_missing_or_nonjudgment_retains_denominator_and_withholds_interval(
    tmp_path, status, attempted
):
    plan = prepared(tmp_path)
    observed = observations(plan)
    job = next(
        job
        for job in plan["jobs"]
        if job["judge"] == planner.MODELS[0] and job["judge"] in job["generators"]
    )
    observed[job["id"]] = {"status": status, "preference": None, "attempted": attempted}
    result = analysis.summarize(plan, observed)["by_model"][planner.MODELS[0]]["all"]
    assert result["scheduled_matched_cells"] == 80
    assert result["matched_cells"] == 79
    assert result["selected_task_families"] == result["observed_matched_task_families"] == 20
    assert result["complete_matched_task_families"] == 19
    assert result["own_minus_third"]["interval"] is None
    assert result["own_minus_third"]["bounded_sensitivity_interval"] is None
    assert result["own_minus_third"]["undefined_reason"] == "incomplete_matched_family_grid"
    assert result["own_statuses"][status] == 1


def test_stratum_needs_its_own_twenty_task_families(tmp_path):
    plan = prepared(tmp_path)
    task = plan["jobs"][0]["instance_id"]
    target = planner.MODELS[0]
    for job in plan["jobs"]:
        if job["instance_id"] == task and target in job["generators"]:
            for model in job["generators"]:
                job["execution_grades"][model] = "resolved" if model == target else "unresolved"
    result = analysis.summarize(plan, observations(plan))["by_model"][target]["only own resolved"]
    assert result["eligible_task_families"] == 1
    assert result["complete_matched_grid"]
    assert result["own_minus_third"]["interval"] is None
    assert result["own_minus_third"]["undefined_reason"] == "too_few_independent_families"


def test_unknown_execution_is_not_treated_as_failed(tmp_path):
    plan = prepared(tmp_path)
    target = planner.MODELS[0]
    for job in plan["jobs"]:
        if target in job["generators"]:
            job["execution_grades"][target] = "unknown"
    result = analysis.summarize(plan, observations(plan))["by_model"][target]
    assert result["any execution grade unknown"]["eligible_task_families"] == 20
    assert result["both unresolved"]["eligible_task_families"] == 0
    assert result["only other resolved"]["eligible_task_families"] == 0


def test_all_planned_cells_required_even_if_unattempted(tmp_path):
    plan = prepared(tmp_path)
    observed = observations(plan)
    observed.pop(next(iter(observed)))
    with pytest.raises(ValueError, match="every unique planned cell"):
        analysis.summarize(plan, observed)


def test_independent_replay_binds_results_usage_phase_and_request(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(request))

    mock_client(monkeypatch, handler)
    runner.run(root)
    result = analysis.analyze(root)
    assert result["attempted_jobs"] == len(calls) == 360
    assert result["accounting"]["unknown_requests"] == 0
    plan, dispatch = runner.load(root)
    path = next((root / "requests").glob("*/result.json"))
    retained = planner.read(path)
    retained["returned_judge_model"] = "different-model"
    path.write_bytes(planner.canonical(retained))
    state = runner.snapshot(runner.ledgers(root, dispatch))
    with pytest.raises(ValueError, match="differs from recomputation"):
        analysis.collect(root, plan, state)


def test_unknown_billing_remains_an_observed_error_not_an_unattempted_cell(tmp_path, monkeypatch):
    root = campaign(tmp_path)
    mock_client(
        monkeypatch, lambda _: httpx.Response(502, json={"error": {"code": "server_error"}})
    )
    with pytest.raises(ValueError, match="missing usage"):
        runner.run(root)
    result = analysis.analyze(root)
    assert result["attempted_jobs"] == 1
    assert result["observed_statuses"] == {"error": 1, "unattempted": 359}
    assert result["accounting"]["unknown_requests"] == 1
