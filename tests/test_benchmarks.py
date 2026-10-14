"""Historical admission requires real controls, reviewed wording and local evidence."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cvals.benchmarks import Benchmarks, assert_collection_admitted
from cvals.config import Settings
from cvals.models import Collection, ExperimentCreate, Scenario, content_hash
from cvals.sandbox import run_repository_tests

DIGEST = "sha256:" + "a" * 64


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repair(tmp_path: Path) -> Path:
    path = tmp_path / "repository"
    path.mkdir()
    git(path, "init", "--quiet")
    git(path, "config", "user.name", "Fixture maintainer")
    git(path, "config", "user.email", "fixture@example.test")
    (path / "solution.py").write_text("def total(values):\n    return len(values)\n")
    original = "from solution import total\n\ndef test_empty():\n    assert total([]) == 0\n"
    (path / "test_solution.py").write_text(original)
    (path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "1.0"\n')
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Base state")
    (path / "solution.py").write_text("def total(values):\n    return sum(values)\n")
    (path / "test_solution.py").write_text(
        original + "\ndef test_values():\n    assert total([2, 4]) == 6\n"
    )
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "CANARY_SOLUTION_TITLE")
    return path


@pytest.fixture
def framework_repair(tmp_path: Path) -> Path:
    path = tmp_path / "framework-repository"
    path.mkdir()
    git(path, "init", "--quiet")
    git(path, "config", "user.name", "Fixture maintainer")
    git(path, "config", "user.email", "fixture@example.test")
    (path / "solution.py").write_text("def total(values):\n    return len(values)\n")
    (path / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef empty_values():\n    return []\n"
    )
    original = (
        "import pytest\nfrom solution import total\n\n"
        "@pytest.mark.parametrize('values', [[], [1]])\n"
        "def test_baseline(values, empty_values):\n"
        "    assert total(values) == len(values)\n"
        "    assert total(empty_values) == 0\n"
    )
    (path / "test_solution.py").write_text(original)
    (path / "pyproject.toml").write_text('[project]\nname = "framework-fixture"\nversion = "1.0"\n')
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Base fixtures and parametrized regressions")
    (path / "solution.py").write_text("def total(values):\n    return sum(values)\n")
    (path / "test_solution.py").write_text(
        original
        + (
            "\n@pytest.mark.parametrize(('values', 'expected'), [([2, 4], 6), ([10], 10)])\n"
            "def test_numeric_values(values, expected, empty_values):\n"
            "    assert total(values) == expected\n"
            "    assert total(empty_values) == 0\n"
        )
    )
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Correct numeric values using existing fixtures")
    return path


@pytest.fixture
def workspace(
    tmp_path: Path, repair: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Benchmarks, str, str]:
    benchmarks = Benchmarks(Settings(tmp_path / "workspace"))
    project = benchmarks.projects.connect(str(repair))
    discovered = benchmarks.projects.discover(project["id"])
    candidate = next(item for item in discovered["candidates"] if item["status"] == "discovered")
    monkeypatch.setattr("cvals.sandbox._image", lambda settings: DIGEST)
    return benchmarks, project["id"], candidate["id"]


def review(benchmarks: Benchmarks, project: str, candidate: str) -> dict[str, Any]:
    return benchmarks.review(
        project,
        candidate,
        "Totals must add the numeric values and retain empty-list behavior.",
        "Fixture reviewer",
        "Describes observable requirements without code, commit title or reference tests.",
    )


def fake_cases(
    files: dict, entrypoint: str, cases: list, settings: Settings, timeout_seconds: int = 20
) -> dict:
    assert settings.sandbox_image == DIGEST
    fixed = "return sum(values)" in files["solution.py"]
    actuals = []
    for case in cases:
        actual = sum(case["args"][0]) if fixed else len(case["args"][0])
        actuals.append(
            {
                "id": case["id"],
                "status": "passed" if actual == case["expected"] else "failed",
                "actual": {"value": actual},
            }
        )
    failures = sum(item["status"] != "passed" for item in actuals)
    return {
        "passed": failures == 0,
        "tests_run": len(cases),
        "failures": failures,
        "errors": 0,
        "skipped": 0,
        "image_digest": DIGEST,
        "test_cases": actuals,
        "output": "",
    }


def install_fake(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def run(*args: Any, **kwargs: Any) -> dict:
        calls.append({"files": args[0], "cases": args[2]})
        return fake_cases(*args, **kwargs)

    monkeypatch.setattr("cvals.sandbox.run_python_cases", run)
    return calls


def test_reviewed_admission_repeats_controls_and_has_no_public_grader_leak(workspace, monkeypatch):
    benchmarks, project, candidate = workspace
    calls = install_fake(monkeypatch)
    review(benchmarks, project, candidate)
    validation = benchmarks.validate(project, candidate)
    assert validation["status"] == "valid", validation["reasons"]
    assert len(calls) == 12  # Three fresh base/reference runs of two independent controls.
    assert len(validation["evidence"]) == 3
    scenario = Scenario.model_validate(validation["scenario"])
    public = json.dumps(scenario.agent_view())
    assert "return sum(values)" not in public
    assert "CANARY_SOLUTION_TITLE" not in public
    assert "test_values" not in public and "reference_commit" not in public
    assert public.count(DIGEST) == 1
    assert validation["scenario_hash"] == content_hash(validation["scenario"])
    assert {check.expected["phase"] for check in scenario.checks} == {"historical", "regression"}


def test_mechanical_success_without_review_cannot_release(workspace, monkeypatch):
    benchmarks, project, candidate = workspace
    install_fake(monkeypatch)
    original = benchmarks.projects.candidate(project, candidate)
    monkeypatch.setattr(
        benchmarks.projects,
        "candidate",
        lambda *args: {**original, "issue_statement": "Numeric totals should add all values."},
    )
    result = benchmarks.validate(project, candidate)
    assert result["status"] == "needs_review", result["reasons"]
    with pytest.raises(ValueError, match="No candidates"):
        benchmarks.release(project, "unreviewed")
    review(benchmarks, project, candidate)
    with pytest.raises(ValueError, match="No candidates"):
        benchmarks.release(project, "review-without-revalidation")


def test_no_regression_assertion_is_quarantined(workspace, monkeypatch):
    benchmarks, project, candidate = workspace
    install_fake(monkeypatch)
    snapshot = benchmarks.projects.snapshot

    def without_regression(*args, **kwargs):
        result = snapshot(*args, **kwargs)
        if args[1] == benchmarks.projects.candidate(project, candidate)["base_commit"]:
            result["files"]["test_solution.py"] = (
                "from solution import total\n\ndef test_single():\n    assert total([1]) == 1\n"
            )
        return result

    monkeypatch.setattr(benchmarks.projects, "snapshot", without_regression)
    review(benchmarks, project, candidate)
    validation = benchmarks.validate(project, candidate)
    assert validation["status"] == "quarantined"
    assert "regression" in validation["reasons"][0]


@pytest.mark.parametrize(
    "failure",
    [
        "infrastructure",
        "base_passes",
        "reference_fails",
        "regression_fails",
        "zero_tests",
        "exception",
        "flaky",
    ],
)
def test_invalid_controls_are_not_admitted(workspace, monkeypatch, failure):
    benchmarks, project, candidate = workspace
    review(benchmarks, project, candidate)
    count = 0

    def run(*args, **kwargs):
        nonlocal count
        count += 1
        result = fake_cases(*args, **kwargs)
        fixed = "sum(values)" in args[0]["solution.py"]
        historic = args[2][0]["args"] == [[2, 4]]
        if failure == "infrastructure":
            result["infrastructure_error"] = "missing dependency"
        elif failure == "zero_tests":
            result["tests_run"] = 0
        elif failure == "base_passes" and not fixed and historic:
            result["passed"] = True
        elif (
            failure == "reference_fails" and fixed or failure == "regression_fails" and not historic
        ):
            result["passed"] = False
        elif failure == "exception":
            result["test_cases"][0]["actual"] = {"exception": "ImportError"}
        elif failure == "flaky" and count > 4:
            result["test_cases"][0]["id"] = "changed-id"
        return result

    monkeypatch.setattr("cvals.sandbox.run_python_cases", run)
    result = benchmarks.validate(project, candidate)
    assert result["status"] == "quarantined", result
    with pytest.raises(ValueError, match="No candidates"):
        benchmarks.release(project, "invalid")


def test_immutable_release_and_all_insertion_paths_enforce_attestation(workspace, monkeypatch):
    benchmarks, project, candidate = workspace
    install_fake(monkeypatch)
    review(benchmarks, project, candidate)
    validation = benchmarks.validate(project, candidate)
    release = benchmarks.release(project, "v1")
    collection = benchmarks.store.get_collection(release["collection_ids"]["all"])
    benchmarks.store.ensure_fixture_systems()
    request = ExperimentCreate(
        name="Released", collection_id=collection["id"], system_ids=["fixture-reference"]
    )
    run = benchmarks.store.create_experiment(request)
    assert benchmarks.store.rerun_experiment(run["id"])["collection_hash"] == content_hash(
        collection
    )
    assert (
        benchmarks.store.import_submissions(
            collection["id"], "Replay", content_hash(collection), {}
        )["status"]
        == "queued"
    )
    with pytest.raises(ValueError, match="immutable"):
        benchmarks.store.delete_collection(collection["id"])
    modified = Collection.model_validate(collection).model_copy(update={"version": "changed"})
    with pytest.raises(ValueError, match="immutable"):
        benchmarks.store.save_collection(modified)
    with pytest.raises(ValueError, match="immutable"):
        benchmarks.release(project, "v1")
    with (
        benchmarks.store._connect(write=True) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute("DELETE FROM benchmark_validations WHERE id=?", (validation["id"],))
    with benchmarks.store._connect(write=True) as connection:
        connection.execute("DROP TRIGGER benchmark_validations_no_update")
        connection.execute(
            "UPDATE benchmark_validations SET data='{}' WHERE id=?", (validation["id"],)
        )
    with pytest.raises(ValueError, match="integrity"):
        benchmarks.store.rerun_experiment(run["id"])
    with pytest.raises(ValueError, match="integrity"):
        benchmarks.store.import_submissions(
            collection["id"], "Bad replay", content_hash(collection), {}
        )


def test_self_reported_validity_and_copied_release_ids_cannot_bypass_admission(
    workspace, monkeypatch
):
    benchmarks, project, candidate = workspace
    install_fake(monkeypatch)
    review(benchmarks, project, candidate)
    result = benchmarks.validate(project, candidate)
    scenario = Scenario.model_validate(result["scenario"])
    scenario.provenance["validation_status"] = "valid"
    collection = Collection(name="Claimed valid", scenarios=[scenario])
    saved = benchmarks.store.save_collection(collection)
    benchmarks.store.ensure_fixture_systems()
    request = ExperimentCreate(
        name="Forgery", collection_id=saved["id"], system_ids=["fixture-reference"]
    )
    with pytest.raises(ValueError, match="locally validated"):
        benchmarks.store.create_experiment(request)
    with pytest.raises(ValueError, match="locally validated"):
        benchmarks.store.import_submissions(saved["id"], "Forgery", content_hash(saved), {})
    release = benchmarks.release(project, "real")
    forged = benchmarks.store.get_collection(release["collection_ids"]["all"])
    forged["scenarios"][0]["instruction"] = "Changed after validation"
    with benchmarks.store._connect() as connection, pytest.raises(ValueError, match="modified"):
        assert_collection_admitted(connection, forged)


def test_new_review_requires_revalidation_old_release_remains_frozen(workspace, monkeypatch):
    benchmarks, project, candidate = workspace
    install_fake(monkeypatch)
    review(benchmarks, project, candidate)
    benchmarks.validate(project, candidate)
    first = benchmarks.release(project, "v1")
    benchmarks.review(
        project,
        candidate,
        "Revised observable requirements for numeric totals.",
        "Reviewer",
        "Clarified wording.",
    )
    with pytest.raises(ValueError, match="No candidates"):
        benchmarks.release(project, "v2")
    benchmarks.validate(project, candidate)
    second = benchmarks.release(project, "v2")
    assert first["members"][0]["partition"] == second["members"][0]["partition"]
    assert benchmarks.diff(first["id"], second["id"])["changed"] == [
        first["members"][0]["scenario_id"]
    ]
    exported = benchmarks.export(first["id"])
    assert exported["contains_private_grader_data"]
    assert exported["release_hash"] == content_hash(first)
    assert (
        exported["collections"]["all"]["scenarios"][0]["instruction"]
        != exported["validations"][0]["scenario"]["instruction"] + "changed"
    )


def test_project_operation_lock_recovers_when_released(workspace):
    benchmarks, project, _ = workspace
    with (
        benchmarks._lock(project),
        pytest.raises(ValueError, match="already running"),
        benchmarks._lock(project),
    ):
        pass
    with benchmarks._lock(project):
        pass


def test_prepared_profile_freezes_private_tests_and_unchanged_regression(workspace, monkeypatch):
    benchmarks, project, candidate = workspace
    profile = benchmarks.configure(project, "prepared:pytest", plugins=["example_plugin"])
    assert profile["image_digest"] == DIGEST and profile["plugins"] == ["example_plugin"]
    review(benchmarks, project, candidate)
    seen = []

    def run(files, hidden_files, selectors, settings, timeout_seconds, plugins):
        seen.append((files, hidden_files, selectors))
        assert settings.sandbox_image == DIGEST
        assert "pyproject.toml" in hidden_files and "pyproject.toml" not in files
        assert plugins == ["example_plugin"]
        historic = selectors == ["test_solution.py::test_values"]
        failed = historic and "len(values)" in files["solution.py"]
        return {
            "passed": not failed,
            "tests_run": 1,
            "failures": int(failed),
            "errors": 0,
            "skipped": 0,
            "image_digest": DIGEST,
            "test_cases": [{"id": selectors[0], "status": "failed" if failed else "passed"}],
        }

    monkeypatch.setattr("cvals.sandbox.run_repository_tests", run)
    result = benchmarks.validate(project, candidate)
    assert result["status"] == "valid", result["reasons"]
    assert len(seen) == 12
    assert "test_values" not in seen[1][1]["test_solution.py"]  # Exact base regression harness.
    assert result["scenario"]["inputs"]["environment"]["kind"] == "prepared-pytest"


def runtime(payload: dict, *, code: int = 0) -> dict:
    return {
        "output": "__CVALS_PYTEST__" + json.dumps(payload),
        "exit_code": code,
        "image_digest": DIGEST,
        "running": False,
        "timed_out": False,
        "output_truncated": False,
        "oom_killed": False,
    }


def evidence(node: str = "test_solution.py::test_total") -> dict:
    return {
        "exit_code": 0,
        "collected": [node],
        "collection_errors": 0,
        "records": [
            {"id": node, "when": phase, "outcome": "passed", "exception": None, "xfail": False}
            for phase in ("setup", "call", "teardown")
        ],
    }


@pytest.mark.parametrize(
    "broken",
    ["zero", "setup", "import", "skipped", "duplicate", "wrong_selection", "exit", "missing"],
)
def test_repository_evidence_reconciliation_rejects_invalidity(tmp_path, monkeypatch, broken):
    payload = evidence()
    code = 0
    if broken == "zero":
        payload["collected"] = []
    elif broken == "setup":
        payload["records"][0]["outcome"] = "failed"
    elif broken == "import":
        payload["records"][1].update(outcome="failed", exception="ModuleNotFoundError")
        payload["exit_code"] = code = 1
    elif broken == "skipped":
        payload["records"][1]["outcome"] = "skipped"
    elif broken == "duplicate":
        payload["records"].append(payload["records"][0])
    elif broken == "wrong_selection":
        payload = evidence("test_solution.py::test_other")
    elif broken == "exit":
        code = 1
    elif broken == "missing":
        payload["records"].pop()
    monkeypatch.setattr(
        "cvals.sandbox._execute", lambda *args, **kwargs: runtime(payload, code=code)
    )
    result = run_repository_tests(
        {"solution.py": "pass"},
        {"test_solution.py": "pass"},
        ["test_solution.py::test_total"],
        Settings(tmp_path),
    )
    assert not result["passed"] and result["infrastructure_error"]


def test_candidate_cannot_add_conftest_or_private_configuration(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("cvals.sandbox._execute", lambda *args, **kwargs: called.append(True))
    for path in ("conftest.py", "pyproject.toml", "tests/helper.py", "test_solution.py"):
        with pytest.raises(ValueError, match="private"):
            run_repository_tests(
                {"solution.py": "pass", path: "pass"},
                {"test_solution.py": "pass"},
                ["test_solution.py::test_total"],
                Settings(tmp_path),
            )
    assert not called


@pytest.mark.sandbox
@pytest.mark.skipif(
    os.getenv("CVALS_TEST_BENCHMARKS") != "1",
    reason="Opt in with an existing prepared pytest image",
)
def test_live_prepared_repo_repeated_admission_and_missing_dependency(tmp_path, framework_repair):
    settings = replace(Settings.from_env(), data_dir=tmp_path / "workspace")
    benchmarks = Benchmarks(settings)
    project = benchmarks.projects.connect(str(framework_repair))
    candidate = next(
        item
        for item in benchmarks.projects.discover(project["id"])["candidates"]
        if item["status"] == "discovered"
    )
    benchmarks.configure(project["id"], os.environ["CVALS_TEST_PYTEST_IMAGE"])
    review(benchmarks, project["id"], candidate["id"])
    result = benchmarks.validate(project["id"], candidate["id"])
    assert result["status"] == "valid", result["reasons"]
    assert len(result["evidence"]) == 3
    assert all(
        check["actual"]["tests_run"] == 2
        for cycle in result["evidence"]
        for stage in ("base", "reference")
        for check in cycle[stage]
    )
    profile = benchmarks.profile(project["id"])
    isolated = replace(settings, sandbox_image=profile["image_digest"])
    missing = run_repository_tests(
        {"solution.py": "pass"},
        {
            "test_solution.py": "import dependency_that_does_not_exist\n\ndef test_total():\n    assert False\n"
        },
        ["test_solution.py::test_total"],
        isolated,
    )
    assert missing["tests_run"] == 0 and missing["infrastructure_error"]


@pytest.mark.sandbox
@pytest.mark.skipif(os.getenv("CVALS_TEST_SANDBOX") != "1", reason="Requires isolated Docker")
def test_live_function_admission_uses_repeated_host_compared_controls(tmp_path, repair):
    settings = replace(Settings.from_env(), data_dir=tmp_path / "workspace")
    benchmarks = Benchmarks(settings)
    project = benchmarks.projects.connect(str(repair))
    candidate = next(
        item
        for item in benchmarks.projects.discover(project["id"])["candidates"]
        if item["status"] == "discovered"
    )
    review(benchmarks, project["id"], candidate["id"])
    result = benchmarks.validate(project["id"], candidate["id"])
    assert result["status"] == "valid", result["reasons"]
    assert len(result["evidence"]) == 3
    for cycle in result["evidence"]:
        assert [check["passed"] for check in cycle["base"]] == [False, True]
        assert all(check["passed"] for check in cycle["reference"])
    release = benchmarks.release(project["id"], "live-function-validation")
    assert release["members"][0]["scenario_hash"] == result["scenario_hash"]


def test_queued_benchmark_stops_before_provider_when_grader_changes(workspace, monkeypatch):
    from cvals.engine import Engine

    benchmarks, project, candidate = workspace
    install_fake(monkeypatch)
    review(benchmarks, project, candidate)
    benchmarks.validate(project, candidate)
    release = benchmarks.release(project, "before-grader-change")
    benchmarks.store.ensure_fixture_systems()
    queued = benchmarks.store.create_experiment(
        ExperimentCreate(
            name="Queued before upgrade",
            collection_id=release["collection_ids"]["all"],
            system_ids=["fixture-reference"],
        )
    )
    monkeypatch.setattr("cvals.benchmarks.grader_hash", lambda: "changed")
    calls = []
    monkeypatch.setattr("cvals.engine.submit", lambda *args: calls.append(True))
    result = Engine(benchmarks.store, benchmarks.settings).run_experiment(queued["id"])
    assert result["status"] == "failed"
    assert result["trials"][0]["status"] == "error"
    assert result["trials"][0]["attempts"] == []
    assert result["trials"][0]["cost_usd"] == 0
    assert "admission" in result["trials"][0]["error"]
    assert not calls
