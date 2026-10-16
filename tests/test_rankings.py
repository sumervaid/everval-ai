"""Saved-run matrix tests use synthetic evidence and make no provider calls."""

from __future__ import annotations

import copy
import json

import pytest

from cvals.models import content_hash
from cvals.rankings import build_rankings


class SavedRuns:
    def __init__(self, *runs):
        self.runs = {run["id"]: run for run in runs}
        self.loaded = []

    def list_experiments(self):
        return [
            {key: value for key, value in run.items() if key not in {"snapshot", "trials"}}
            for run in self.runs.values()
        ]

    def get_experiment(self, identity):
        self.loaded.append(identity)
        return copy.deepcopy(self.runs[identity])


def system(identity, **changes):
    return {
        "id": identity,
        "name": identity.title(),
        "kind": "http",
        "model": "model-" + identity,
        "harness": "single-turn",
        "max_tokens": 1000,
        "base_url": "https://provider.invalid/v1",
        "api_key_env": "PRIVATE_KEY_NAME",
        "additional_instructions": "PRIVATE_SYSTEM_INSTRUCTIONS",
        **changes,
    }


def run(
    identity="run-old",
    *,
    created="2026-10-16T01:00:00Z",
    systems=None,
    repetitions=1,
    statuses=None,
    passes=None,
    costs=None,
    durations=None,
    collection_version="1",
    image=None,
    **contract,
):
    systems = systems or [system("alpha"), system("beta")]
    collection = {
        "id": "collection-one",
        "name": "Team benchmark",
        "version": collection_version,
        "domain": "data",
        "description": "PRIVATE_COLLECTION_DETAIL",
        "scenarios": [
            {"id": "task-" + str(number), "domain": "data", "instruction": "PRIVATE_TASK_TEXT"}
            for number in range(3)
        ],
        "benchmark_release_id": None,
        "benchmark_partition": None,
    }
    snapshot = {
        "schema_version": 1,
        "collection": collection,
        "systems": systems,
        "repetitions": repetitions,
        "budget_usd": 10,
        "harness_revision": "harness/1",
        "grader_revision": "grader/1",
        "sandbox_image_requested": "pinned-image",
        "protocol": "no retries",
        **contract,
    }
    trials = []
    for item in systems:
        for repetition in range(1, repetitions + 1):
            for number, scenario in enumerate(collection["scenarios"]):
                status = (statuses or {}).get(item["id"], "completed")
                passed = (
                    (passes or {}).get(item["id"], 2) > number if status == "completed" else None
                )
                trials.append(
                    {
                        "id": identity
                        + "-"
                        + item["id"]
                        + "-"
                        + str(repetition)
                        + "-"
                        + str(number),
                        "system_id": item["id"],
                        "scenario_id": scenario["id"],
                        "repetition": repetition,
                        "domain": "data",
                        "status": status,
                        "passed": passed,
                        "score": 1 if passed else 0,
                        "cost_usd": (costs or {}).get(item["id"], 0.25),
                        "duration_ms": (durations or {}).get(item["id"], 100),
                        "checks": [{"actual": {"image_digest": image}}] if image else [],
                        "output": {"private": "PRIVATE_MODEL_OUTPUT"},
                        "provider_metadata": {"trace": [{"content": "PRIVATE_PROVIDER_TRACE"}]},
                    }
                )
    return {
        "id": identity,
        "name": "Run " + identity,
        "created_at": created,
        "status": "completed",
        "snapshot": snapshot,
        "snapshot_hash": content_hash(snapshot),
        "collection_hash": content_hash(collection),
        "systems": systems,
        "total_trials": len(trials),
        "trials": trials,
    }


def cells_by_original(result):
    return {cell["source_system_id"]: cell for cell in result["cells"]}


def test_matrix_ranks_exact_ties_without_blending_benchmarks():
    experiment = run(
        systems=[system("alpha"), system("beta"), system("gamma")],
        passes={"alpha": 2, "beta": 2, "gamma": 1},
        costs={"alpha": 0.5, "beta": 0.25, "gamma": 0.25},
        durations={"alpha": 300, "beta": 100, "gamma": 200},
    )
    result = build_rankings(SavedRuns(experiment))
    cells = cells_by_original(result)
    assert result["schema_version"] == 1
    assert result["counts"]["benchmarks"] == 1
    assert [cells[name]["ranks"]["quality"] for name in ("alpha", "beta", "gamma")] == [1, 1, 3]
    assert cells["beta"]["ranks"]["cost"] == 1
    assert cells["beta"]["cost_per_success_usd"] == 0.375
    assert cells["alpha"]["ranks"]["cost"] == cells["gamma"]["ranks"]["cost"] == 2
    assert cells["beta"]["ranks"]["latency"] == 1
    assert cells["alpha"]["ranks"]["latency"] == 3
    assert result["benchmarks"][0]["ranked_systems"] == {"quality": 3, "cost": 3, "latency": 3}
    assert "overall_rank" not in json.dumps(result)
    assert cells["alpha"]["ci_low"] is not None


def test_latest_is_selected_even_if_older_run_has_better_results():
    old = run(passes={"alpha": 3})
    latest = run("run-new", created="2026-10-17T01:00:00Z", passes={"alpha": 0})
    result = build_rankings(SavedRuns(latest, old))
    cell = cells_by_original(result)["alpha"]
    assert cell["experiment_id"] == "run-new"
    assert cell["pass_rate"] == 0
    assert cell["ranks"]["quality"] == 2
    assert cell["cost_per_success_usd"] is None
    assert cell["ranks"]["cost"] is None
    assert result["benchmarks"][0]["experiment_count"] == 2


@pytest.mark.parametrize(
    "status,trial_status",
    [
        ("queued", "queued"),
        ("running", "running"),
        ("cancelled", "cancelled"),
        ("failed", "error"),
    ],
)
def test_latest_unfinished_or_failed_evidence_never_falls_back(status, trial_status):
    old = run()
    latest = run(
        "run-new",
        created="2026-10-17T01:00:00Z",
        statuses={"alpha": trial_status, "beta": trial_status},
        costs={"alpha": None, "beta": None},
        durations={"alpha": None, "beta": None},
    )
    latest["status"] = status
    result = build_rankings(SavedRuns(old, latest))
    for cell in result["cells"]:
        assert cell["experiment_id"] == "run-new"
        assert cell["scheduled"] == 3
        assert cell["pass_rate"] == 0
        assert cell["completed"] == 0
        assert cell["ranks"] == {"quality": None, "cost": None, "latency": None}
        assert not any(cell["eligibility"].values())
        assert cell["issues"]


def test_cost_and_duration_gaps_do_not_prevent_observed_quality_rank():
    experiment = run(costs={"alpha": None}, durations={"beta": None})
    for trial in experiment["trials"]:
        if trial["system_id"] == "alpha":
            trial["provider_metadata"]["known_cost_usd"] = 0.125
    cells = cells_by_original(build_rankings(SavedRuns(experiment)))
    assert cells["alpha"]["ranks"]["quality"] == 1
    assert cells["alpha"]["cost_complete"] is False
    assert cells["alpha"]["known_cost_usd"] == 0.375
    assert cells["alpha"]["cost_usd"] is None
    assert cells["alpha"]["cost_per_success_usd"] is None
    assert cells["alpha"]["ranks"]["cost"] is None
    assert cells["beta"]["ranks"]["quality"] == 1
    assert cells["beta"]["duration_complete"] is False
    assert cells["beta"]["duration_recorded"] == 0
    assert cells["beta"]["p95_duration_ms"] is None
    assert cells["beta"]["ranks"]["latency"] is None


def test_fixture_and_replay_rows_are_visible_and_unranked():
    experiment = run(
        systems=[
            system("reference", kind="reference"),
            system("incomplete", kind="incomplete"),
            system("recorded", kind="replay"),
            system("measured"),
        ]
    )
    result = build_rankings(SavedRuns(experiment))
    cells = cells_by_original(result)
    assert len(result["systems"]) == 4
    for identity in ("reference", "incomplete", "recorded"):
        assert all(value is None for value in cells[identity]["ranks"].values())
    assert cells["measured"]["ranks"]["quality"] == 1
    assert any(row["fixture_control"] for row in result["systems"])
    assert any(row["submission_replay"] for row in result["systems"])


def test_external_adapter_is_eligible_but_provenance_is_explicit():
    experiment = run(systems=[system("adapter", harness="external-agent", adapter_revision="v1")])
    result = build_rankings(SavedRuns(experiment))
    assert result["systems"][0]["external_adapter"] is True
    assert result["cells"][0]["ranks"]["quality"] == 1
    assert any("self-reported" in issue for issue in result["cells"][0]["issues"])


@pytest.mark.parametrize(
    "changes",
    [
        {"collection_version": "2"},
        {"repetitions": 2},
        {"grader_revision": "grader/2"},
        {"harness_revision": "harness/2"},
        {"protocol": "different"},
        {"budget_usd": 20},
        {"sandbox_image_requested": "new-image"},
        {"image": "sha256:image-2"},
    ],
)
def test_changed_frozen_contracts_have_separate_columns(changes):
    first = run("first", image="sha256:image-1")
    second = run("second", **{"image": "sha256:image-1", **changes})
    result = build_rankings(SavedRuns(first, second))
    assert len(result["benchmarks"]) == 2
    assert len(result["cells"]) == 4
    assert len({column["id"] for column in result["benchmarks"]}) == 2


def test_same_name_or_system_id_does_not_merge_configuration_changes():
    first = run("first", systems=[system("alpha")])
    second = run("second", systems=[system("alpha", max_tokens=2000)])
    result = build_rankings(SavedRuns(first, second))
    assert len(result["benchmarks"]) == 1
    assert len(result["systems"]) == 2
    assert len({row["id"] for row in result["systems"]}) == 2
    assert {row["source_system_id"] for row in result["cells"]} == {"alpha"}


def test_missing_cells_are_absent_and_not_synthetic_zeroes():
    first = run("first", systems=[system("alpha")])
    second = run("second", systems=[system("beta")], collection_version="2")
    result = build_rankings(SavedRuns(first, second))
    assert len(result["benchmarks"]) == len(result["systems"]) == len(result["cells"]) == 2
    actual = {(cell["benchmark_id"], cell["system_id"]) for cell in result["cells"]}
    all_possible = {
        (column["id"], system["id"])
        for column in result["benchmarks"]
        for system in result["systems"]
    }
    assert len(all_possible - actual) == 2


def test_window_is_explicit_bounded_and_deterministic_for_equal_timestamps():
    store = SavedRuns(run("aaa"), run("bbb"), run("ccc"))
    result = build_rankings(store, limit=2)
    assert store.loaded == ["ccc", "bbb"]
    assert result["window"] == {
        "limit": 2,
        "listed_runs": 3,
        "scanned_runs": 2,
        "truncated": True,
        "store_list_limit": 500,
    }
    assert all(cell["experiment_id"] == "ccc" for cell in result["cells"])
    assert any("recent 2 runs" in warning for warning in result["warnings"])


@pytest.mark.parametrize("limit", [0, 501, True, 1.5])
def test_invalid_window_is_rejected(limit):
    with pytest.raises(ValueError, match="1 to 500"):
        build_rankings(SavedRuns(), limit=limit)


def test_output_excludes_private_task_and_system_material():
    result = build_rankings(SavedRuns(run()))
    encoded = json.dumps(result)
    assert "PRIVATE_" not in encoded
    assert "provider.invalid" not in encoded
    assert "api_key_env" not in encoded
    assert "additional_instructions" not in encoded
    assert "scenario_id" not in encoded


def test_unverifiable_recent_record_cannot_reveal_an_older_winner():
    old = run()
    broken = run("new", created="2026-10-17T01:00:00Z")
    broken["snapshot_hash"] = "modified"
    result = build_rankings(SavedRuns(old, broken))
    assert result["counts"]["experiments_skipped"] == 1
    assert result["unverified_experiment_ids"] == ["new"]
    assert result["counts"]["ranked_cells"] == 0
    assert all(not any(cell["eligibility"].values()) for cell in result["cells"])


def test_missing_trial_evidence_does_not_shrink_a_ranked_denominator():
    broken = run()
    broken["trials"].pop()
    result = build_rankings(SavedRuns(broken))
    assert result["counts"]["experiments_skipped"] == 1
    assert not result["cells"]


def test_empty_workspace_is_explicit_and_has_no_fabricated_rankings():
    result = build_rankings(SavedRuns())
    assert result["benchmarks"] == result["systems"] == result["cells"] == []
    assert result["counts"]["ranked_cells"] == 0
    assert result["window"]["scanned_runs"] == 0
    assert result["window"]["truncated"] is False


@pytest.mark.parametrize(
    "status,trial_status",
    [("queued", "queued"), ("running", "running"), ("cancelled", "cancelled"), ("failed", "error")],
)
def test_newer_missing_image_evidence_withholds_old_variant_ranks(status, trial_status):
    old = run("old-observed", image="sha256:verified-image")
    newer = run(
        "new-without-images",
        created="2026-10-17T01:00:00Z",
        statuses={"alpha": trial_status, "beta": trial_status},
        costs={"alpha": None, "beta": None},
        durations={"alpha": None, "beta": None},
    )
    newer["status"] = status
    result = build_rankings(SavedRuns(old, newer))
    assert len(result["benchmarks"]) == 2  # Retain the distinct evidence, including the failed run.
    assert {cell["experiment_id"] for cell in result["cells"]} == {
        "old-observed",
        "new-without-images",
    }
    assert all(not any(cell["eligibility"].values()) for cell in result["cells"])
    assert all(all(rank is None for rank in cell["ranks"].values()) for cell in result["cells"])
    old_cells = [cell for cell in result["cells"] if cell["experiment_id"] == "old-observed"]
    assert all(any("new-without-images" in issue for issue in cell["issues"]) for cell in old_cells)


def test_partial_image_coverage_blocks_only_the_affected_configuration():
    old = run("old-observed", image="sha256:old-image")
    newer = run("new-partial", created="2026-10-17T01:00:00Z", image="sha256:new-image")
    missing = next(trial for trial in newer["trials"] if trial["system_id"] == "alpha")
    missing.update(status="error", passed=None, score=None, checks=[], cost_usd=None)
    result = build_rankings(SavedRuns(old, newer))
    old_cells = {
        cell["source_system_id"]: cell
        for cell in result["cells"]
        if cell["experiment_id"] == "old-observed"
    }
    assert not any(old_cells["alpha"]["eligibility"].values())
    assert old_cells["beta"]["eligibility"]["quality"]
    assert any("new-partial" in issue for issue in old_cells["alpha"]["issues"])
    assert not any("new-partial" in issue for issue in old_cells["beta"]["issues"])


def test_fully_graded_run_without_expected_image_evidence_does_not_create_a_rank():
    old = run("old-observed", image="sha256:verified-image")
    newer = run("new-unobserved", created="2026-10-17T01:00:00Z")
    result = build_rankings(SavedRuns(old, newer))
    assert len(result["cells"]) == 4
    assert all(not any(cell["eligibility"].values()) for cell in result["cells"])
    latest = [cell for cell in result["cells"] if cell["experiment_id"] == "new-unobserved"]
    assert all(
        any("Verifier image evidence is incomplete" in issue for issue in c["issues"])
        for c in latest
    )


def test_completed_observed_image_variants_remain_independently_rankable():
    old = run("old-observed", image="sha256:old-image")
    newer = run("new-observed", created="2026-10-17T01:00:00Z", image="sha256:new-image")
    result = build_rankings(SavedRuns(old, newer))
    assert len(result["benchmarks"]) == 2
    assert len(result["cells"]) == 4
    assert all(all(cell["eligibility"].values()) for cell in result["cells"])
    assert all(cell["ranks"]["quality"] == 1 for cell in result["cells"])


def test_incomplete_run_under_changed_requested_contract_does_not_shadow_old_contract():
    old = run("old-observed", image="sha256:verified-image")
    newer = run(
        "changed-budget",
        created="2026-10-17T01:00:00Z",
        budget_usd=20,
        statuses={"alpha": "error", "beta": "error"},
    )
    newer["status"] = "failed"
    result = build_rankings(SavedRuns(old, newer))
    old_cells = [cell for cell in result["cells"] if cell["experiment_id"] == "old-observed"]
    assert all(all(cell["eligibility"].values()) for cell in old_cells)
