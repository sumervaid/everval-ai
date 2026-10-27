"""Prospective selection and prompt boundaries for the optional follow-up."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.judge_study import self_preference_plan as study  # noqa: E402


def fixture_manifest() -> tuple[dict, dict]:
    records = {
        f"example__repo-{i}": {
            "instance_id": f"example__repo-{i}",
            "repo": "example/repo",
            "problem_statement": f"Fix issue {i}.",
            "patch": "PRIVATE_GOLD_PATCH",
            "test_patch": "PRIVATE_HIDDEN_TEST",
            "FAIL_TO_PASS": ["test_fix"],
            "PASS_TO_PASS": ["test_existing"],
        }
        for i in range(50)
    }
    manifest = {
        "tasks": [
            {
                "instance_id": key,
                "repo": "example/repo",
                "task_sha256": study.digest(value),
            }
            for key, value in records.items()
        ],
        "jobs": [
            {"id": study.digest([key, model]), "instance_id": key, "model": model}
            for key in records
            for model in study.MODELS
        ],
    }
    return manifest, records


def fixture_run(tmp_path: Path, *, missing: bool = False) -> tuple[Path, Path, dict]:
    fresh, output = tmp_path / "fresh" / "amendment", tmp_path / "followup"
    manifest, records = fixture_manifest()
    study.write_private(fresh / "inference.json", manifest)
    study.write_private(fresh / "inference-freeze.json", {"sha256": study.digest(manifest)})
    selection = study.freeze_selection(fresh, output)
    for key, value in records.items():
        study.write_private(fresh / "tasks" / (key + ".json"), value)
    selected = {x["instance_id"] for x in selection["tasks"]}
    omitted = selection["tasks"][0]["instance_id"]
    for job in manifest["jobs"]:
        if job["instance_id"] not in selected:
            continue
        if missing and job["instance_id"] == omitted and job["model"] == study.MODELS[0]:
            continue
        study.write_private(
            fresh / "requests" / job["id"] / "result.json",
            {
                "instance_id": job["instance_id"],
                "model": job["model"],
                "returned_generator_model": job["model"],
                "status": "completed",
                "patch": "--- a/source.py\n+++ b/source.py\n@@ -1 +1 @@\n-old\n+new",
            },
        )
        study.write_private(
            fresh / "grades" / job["id"] / "result.json",
            {
                "instance_id": job["instance_id"],
                "status": "graded",
                "cleanup_complete": True,
                "resolved": True,
                "patch_sha256": hashlib.sha256(
                    study.read(fresh / "requests" / job["id"] / "result.json")["patch"].encode()
                ).hexdigest(),
                "all_required_tests_observed": True,
                "missing_required_tests": [],
                "test_statuses": {"test_fix": "PASSED", "test_existing": "PASSED"},
                "test_suite_exit_code": 0,
            },
        )
    return fresh, output, selection


def test_selection_is_outcome_independent_and_order_invariant() -> None:
    manifest, _ = fixture_manifest()
    first = study.select(manifest)
    manifest["tasks"].reverse()
    manifest["jobs"].reverse()
    for task in manifest["tasks"]:
        task.update(resolved=False, patch="DO_NOT_READ", quality=0)
    assert study.select(manifest) == first
    assert len(first) == 20
    assert all(set(x) == {"instance_id", "repo", "task_sha256"} for x in first)


def test_selection_requires_full_fifty_by_three_grid() -> None:
    manifest, _ = fixture_manifest()
    manifest["jobs"].pop()
    with pytest.raises(ValueError, match="one job per task"):
        study.select(manifest)
    manifest["tasks"].pop()
    with pytest.raises(ValueError, match="50 distinct"):
        study.select(manifest)


def test_full_preparation_keeps_identity_and_grades_out_of_prompts(tmp_path: Path) -> None:
    fresh, output, _ = fixture_run(tmp_path)
    result = study.prepare(fresh, output)
    assert len(result["jobs"]) == result["ready_jobs"] == 360
    assert result["dispatch_authorized"] is False
    for job in result["jobs"]:
        content = json.dumps(
            {"instructions": job["body"]["instructions"], "input": job["body"]["input"]}
        )
        for forbidden in [
            *study.MODELS,
            "PRIVATE_GOLD_PATCH",
            "PRIVATE_HIDDEN_TEST",
            "resolved",
            "execution_grade",
        ]:
            assert forbidden not in content
        assert job["input_ceiling"] >= len(study.canonical(job["body"]))
    assert sum(float(x["cap"]) for x in result["profiles"].values()) == pytest.approx(7.95)


def test_missing_generation_retains_selected_task_and_all_slots(tmp_path: Path) -> None:
    fresh, output, selection = fixture_run(tmp_path, missing=True)
    result = study.prepare(fresh, output)
    assert len(result["jobs"]) == 360
    missing = [job for job in result["jobs"] if job["status"] == "missing_generation"]
    assert len(missing) == 12
    assert {job["instance_id"] for job in missing} == {selection["tasks"][0]["instance_id"]}
    assert all(job["body"] is None and job["reservation_usd"] is None for job in missing)


def test_mutated_source_cannot_change_frozen_selection(tmp_path: Path) -> None:
    fresh, output, _ = fixture_run(tmp_path)
    manifest = study.read(fresh / "inference.json")
    manifest["tasks"][0]["repo"] = "different/repo"
    (fresh / "inference.json").write_bytes(study.canonical(manifest))
    with pytest.raises(ValueError, match="Fresh inference source changed"):
        study.prepare(fresh, output)


def test_oversized_pair_is_never_truncated() -> None:
    with pytest.raises(ValueError, match="never truncate"):
        study.body_for(
            "Fix this.", {"A": "x" * study.MAX_REQUEST_BYTES, "B": "patch"}, study.MODELS[0]
        )


def test_generator_identifier_mismatch_is_missing_not_self_identity(tmp_path: Path) -> None:
    fresh, output, selection = fixture_run(tmp_path)
    manifest = study.read(fresh / "inference.json")
    identifier = selection["tasks"][0]["instance_id"]
    job = next(
        x
        for x in manifest["jobs"]
        if x["instance_id"] == identifier and x["model"] == study.MODELS[0]
    )
    path = fresh / "requests" / job["id"] / "result.json"
    result = study.read(path)
    result["returned_generator_model"] = "unverified-different-alias"
    path.write_bytes(study.canonical(result))
    prepared = study.prepare(fresh, output)
    assert sum(x["status"] == "missing_generation" for x in prepared["jobs"]) == 12
    assert prepared["identity_claim"].startswith("Same returned API identifier only")


def test_grade_unknown_is_not_invented_failure() -> None:
    args = {"instance_id": "task", "patch": "patch", "required": {"test"}}
    value = {
        "instance_id": "task",
        "patch_sha256": hashlib.sha256(b"patch").hexdigest(),
        "status": "graded",
        "cleanup_complete": True,
        "resolved": False,
        "all_required_tests_observed": True,
        "missing_required_tests": [],
        "test_statuses": {"test": "FAILED"},
        "test_suite_exit_code": 1,
    }
    assert study.grade_status(None, **args) == "missing"
    assert study.grade_status(value, **args) == "unresolved"
    assert study.grade_status({**value, "all_required_tests_observed": False}, **args) == "unknown"
    assert study.grade_status({**value, "test_statuses": {}}, **args) == "unknown"
    assert study.grade_status({**value, "status": "infrastructure_error"}, **args) == "unknown"
    assert (
        study.grade_status(
            {
                **value,
                "resolved": True,
                "test_statuses": {"test": "PASSED"},
                "test_suite_exit_code": 0,
            },
            **args,
        )
        == "resolved"
    )


@pytest.mark.parametrize("kind", ["request", "result", "response"])
def test_selection_rejects_any_prior_generation_artifact(tmp_path: Path, kind: str) -> None:
    manifest, _ = fixture_manifest()
    fresh = tmp_path / "fresh"
    study.write_private(fresh / "inference.json", manifest)
    study.write_private(fresh / "inference-freeze.json", {"sha256": study.digest(manifest)})
    study.write_private(fresh / "requests" / "job" / (kind + ".json"), {})
    with pytest.raises(ValueError, match="precede every generation request"):
        study.freeze_selection(fresh, tmp_path / "output")


@pytest.mark.parametrize("table", ["requests", "attempts"])
def test_selection_rejects_any_prior_ledger_admission(tmp_path: Path, table: str) -> None:
    manifest, _ = fixture_manifest()
    fresh = tmp_path / "fresh"
    study.write_private(fresh / "inference.json", manifest)
    study.write_private(fresh / "inference-freeze.json", {"sha256": study.digest(manifest)})
    (fresh / "accounting").mkdir()
    with sqlite3.connect(fresh / "accounting" / "model.sqlite3") as db:
        db.execute("CREATE TABLE " + table + " (id TEXT)")
        db.execute("INSERT INTO " + table + " VALUES ('already-started')")
    with pytest.raises(ValueError, match="precede every generation ledger"):
        study.freeze_selection(fresh, tmp_path / "output")


@pytest.mark.parametrize("field,value", [("instance_id", "other-task"), ("patch_sha256", "0" * 64)])
def test_grade_cannot_be_substituted_between_tasks_or_patches(
    tmp_path: Path, field: str, value: str
) -> None:
    fresh, output, selection = fixture_run(tmp_path)
    manifest = study.read(fresh / "inference.json")
    identifier = selection["tasks"][0]["instance_id"]
    job = next(x for x in manifest["jobs"] if x["instance_id"] == identifier)
    path = fresh / "grades" / job["id"] / "result.json"
    grade = study.read(path)
    grade[field] = value
    path.write_bytes(study.canonical(grade))
    with pytest.raises(ValueError, match="Grade"):
        study.prepare(fresh, output)


def test_incomplete_grade_remains_unknown_in_prepared_strata(tmp_path: Path) -> None:
    fresh, output, selection = fixture_run(tmp_path)
    manifest = study.read(fresh / "inference.json")
    identifier = selection["tasks"][0]["instance_id"]
    job = next(x for x in manifest["jobs"] if x["instance_id"] == identifier)
    path = fresh / "grades" / job["id"] / "result.json"
    grade = study.read(path)
    grade.update(
        resolved=False, all_required_tests_observed=False, missing_required_tests=["test_fix"]
    )
    path.write_bytes(study.canonical(grade))
    result = study.prepare(fresh, output)
    assert result["sources"][job["id"]]["execution_grade"] == "unknown"


def test_raw_artifacts_cannot_be_written_into_checkout() -> None:
    with pytest.raises(ValueError, match="outside the source checkout"):
        study.private_output(Path(__file__).resolve().parents[1] / "docs" / "raw-followup")


def test_empty_patch_is_preserved_as_missing_generation(tmp_path: Path) -> None:
    fresh, output, selection = fixture_run(tmp_path)
    manifest = study.read(fresh / "inference.json")
    identifier = selection["tasks"][0]["instance_id"]
    job = next(x for x in manifest["jobs"] if x["instance_id"] == identifier)
    path = fresh / "requests" / job["id"] / "result.json"
    result = study.read(path)
    result["patch"] = ""
    path.write_bytes(study.canonical(result))
    grade = fresh / "grades" / job["id"] / "result.json"
    grade.write_bytes(study.canonical({"instance_id": identifier, "status": "no_patch"}))
    prepared = study.prepare(fresh, output)
    assert sum(x["status"] == "missing_generation" for x in prepared["jobs"]) == 12


def test_exploratory_uncertainty_rules_are_frozen_per_stratum(tmp_path: Path) -> None:
    fresh, output, _ = fixture_run(tmp_path)
    plan = study.prepare(fresh, output)
    rules = plan["analysis"]["uncertainty"]
    assert plan["profiles"]["gpt-6.1-sol"]["ceiling"] == 1536
    assert rules["alpha"] == 0.05
    assert rules["min_families"] == 20
    assert rules["resamples"] == 5000
    assert "exploratory" in rules["role"]
    assert "constant family means" in rules["degenerate_rule"]


@pytest.mark.parametrize("exit_code", [0, None, False, -1])
def test_unresolved_requires_actual_failure_evidence(exit_code: int | None) -> None:
    grade = {
        "instance_id": "task",
        "patch_sha256": hashlib.sha256(b"patch").hexdigest(),
        "status": "graded",
        "cleanup_complete": True,
        "resolved": False,
        "all_required_tests_observed": True,
        "missing_required_tests": [],
        "test_statuses": {"test": "PASSED"},
        "test_suite_exit_code": exit_code,
    }
    assert (
        study.grade_status(grade, instance_id="task", patch="patch", required={"test"}) == "unknown"
    )
    grade["test_suite_exit_code"] = 1
    assert (
        study.grade_status(grade, instance_id="task", patch="patch", required={"test"})
        == "unresolved"
    )


def test_quarantined_source_retains_grid_without_judging_existing_patches(tmp_path: Path) -> None:
    fresh, output, selection = fixture_run(tmp_path)
    task_id = selection["tasks"][0]["instance_id"]
    quarantine = study.quarantine_directory(fresh) / (
        hashlib.sha256(task_id.encode()).hexdigest() + ".json"
    )
    study.write_private(quarantine, {"instance_id": task_id, "policy_quarantine": True})
    prepared = study.prepare(fresh, output)
    excluded = [job for job in prepared["jobs"] if job["instance_id"] == task_id]
    assert len(excluded) == 18
    assert all(
        job["status"] == "provider_policy_quarantine" and job["body"] is None for job in excluded
    )
    assert prepared["planned_jobs"] == 360
    assert prepared["ready_jobs"] == 342
    assert prepared["quarantine_sha256"][task_id] == study.digest(study.read(quarantine))


def test_quarantine_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    fresh, output, selection = fixture_run(tmp_path)
    task_id = selection["tasks"][0]["instance_id"]
    quarantine = study.quarantine_directory(fresh) / (
        hashlib.sha256(task_id.encode()).hexdigest() + ".json"
    )
    study.write_private(quarantine, {"instance_id": "different-task", "policy_quarantine": True})
    with pytest.raises(ValueError, match="Quarantine identity"):
        study.prepare(fresh, output)


def test_initial_quarantined_selected_task_cannot_be_frozen(tmp_path: Path) -> None:
    manifest, _ = fixture_manifest()
    fresh, output = tmp_path / "fresh" / "amendment", tmp_path / "output"
    study.write_private(fresh / "inference.json", manifest)
    study.write_private(fresh / "inference-freeze.json", {"sha256": study.digest(manifest)})
    task_id = study.select(manifest)[0]["instance_id"]
    quarantine = study.quarantine_directory(fresh) / (
        hashlib.sha256(task_id.encode()).hexdigest() + ".json"
    )
    study.write_private(quarantine, {"instance_id": task_id, "policy_quarantine": True})
    with pytest.raises(ValueError, match="already quarantined"):
        study.freeze_selection(fresh, output)
    assert not (output / "selection.json").exists()


@pytest.mark.parametrize("resolved", [True, False])
def test_incomplete_cleanup_is_unknown_execution_not_a_stratum(resolved: bool) -> None:
    patch = "candidate patch"
    grade = {
        "instance_id": "task",
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "status": "graded",
        "resolved": resolved,
        "cleanup_complete": False,
        "all_required_tests_observed": True,
        "missing_required_tests": [],
        "test_statuses": {"test": "PASSED" if resolved else "FAILED"},
        "test_suite_exit_code": 0 if resolved else 1,
    }
    assert (
        study.grade_status(grade, instance_id="task", patch=patch, required={"test"}) == "unknown"
    )
