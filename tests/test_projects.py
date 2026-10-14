"""Project onboarding preserves provenance and never executes repository content."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cvals.config import Settings
from cvals.github import GitHub, github_source
from cvals.projects import Projects, _patch_lines, _problem


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def history(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Fixture maintainer")
    git(repo, "config", "user.email", "fixture@example.test")
    (repo / "solution.py").write_text("def total(values):\n    return len(values)\n")
    (repo / "test_solution.py").write_text(
        "from solution import total\n\ndef test_empty():\n    assert total([]) == 0\n"
    )
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "1.0"\n')
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Starting state")
    (repo / "solution.py").write_text("def total(values):\n    return sum(values)\n")
    (repo / "test_solution.py").write_text(
        "from solution import total\n\ndef test_values():\n    assert total([2, 4]) == 6\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Correct numeric totals")
    return repo


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("owner/repo", ("owner", "repo")),
        ("https://github.com/Owner/Repo", ("Owner", "Repo")),
        ("https://github.com/owner/repo.git/", ("owner", "repo")),
    ],
)
def test_github_source_forms(source: str, expected: tuple[str, str]) -> None:
    assert github_source(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "https://evil.example/owner/repo",
        "https://github.com.evil/owner/repo",
        "https://token@github.com/owner/repo",
        "https://github.com/owner/repo?token=x",
        "https://github.com/owner/repo#part",
        "owner/../secret",
        "owner/..",
        "-option/repo",
        "git@github.com:owner/repo.git",
        "http://github.com/owner/repo",
    ],
)
def test_reject_credential_urls_and_ambiguous_sources(source: str) -> None:
    with pytest.raises(ValueError):
        github_source(source)


def test_local_projects_are_durable_idempotent_and_not_github(
    tmp_path: Path, history: Path
) -> None:
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    first = projects.connect(str(history))
    assert projects.connect(str(history))["id"] == first["id"]
    assert len(projects.list()) == 1
    assert first["source_kind"] == "local"
    assert first["metadata"]["languages"] == {"Python": 2}
    discovered = projects.discover(first["id"])
    assert discovered["counts"]["added"] == 2
    assert discovered["counts"]["discovered"] == 1
    assert discovered["counts"]["quarantined"] == 1
    assert all(
        candidate["number"] is None and candidate["merged_at"] is None
        for candidate in discovered["candidates"]
    )
    again = Projects(Settings(data_dir=tmp_path / "workspace")).discover(first["id"])
    assert again["counts"]["unchanged"] == 2
    assert again["counts"]["added"] == again["counts"]["updated"] == 0
    with projects.store._connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM repository_candidate_versions").fetchone()[0]
            == 2
        )


def test_snapshot_preserves_configuration_and_excludes_sensitive_material(
    tmp_path: Path, history: Path
) -> None:
    (history / ".env").write_text("SECRET=do-not-export")
    (history / "key.pem").write_text("do-not-export")
    (history / "shortcut.py").symlink_to("/etc/passwd")
    (history / "binary.dat").write_bytes(b"\0\x01")
    git(history, "add", ".")
    git(history, "commit", "--quiet", "-m", "Unsupported materials")
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    snapshot = projects.snapshot(
        project["id"],
        project["head_sha"],
        include_tests=True,
        max_bytes=32 * 1024 * 1024,
        max_files=2048,
    )
    assert "pyproject.toml" in snapshot["files"]
    assert "test_solution.py" in snapshot["files"]
    assert not {".env", "key.pem", "shortcut.py", "binary.dat"} & set(snapshot["files"])
    assert len(snapshot["omitted"]) == 4
    assert not snapshot["complete"] and not snapshot["includes_git_history"]
    public = projects.snapshot(project["id"], project["head_sha"])
    assert "test_solution.py" not in public["files"]
    assert any(
        item["reason"] == "test material excluded from solver snapshot"
        for item in public["omitted"]
    )


def test_snapshot_and_discovery_do_not_run_host_hooks_or_imports(
    tmp_path: Path, history: Path
) -> None:
    marker = tmp_path / "executed"
    (history / "solution.py").write_text(f"open({str(marker)!r}, 'w').write('bad')\n")
    git(history, "add", ".")
    git(history, "commit", "--quiet", "-m", "Untrusted module")
    hook = history / ".git/hooks/post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    git(history, "config", "core.fsmonitor", str(hook))
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    projects.discover(project["id"])
    projects.snapshot(project["id"], project["head_sha"], include_tests=True)
    assert not marker.exists()


def pull(commit: str, head: str, count: int = 1) -> dict[str, Any]:
    return {
        "number": 7,
        "title": "Correct totals",
        "body": "Totals count rows instead of values.",
        "url": "https://github.com/example/fixture/pull/7",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-03T00:00:00Z",
        "mergedAt": "2026-01-02T00:00:00Z",
        "baseRefName": "main",
        "baseRefOid": "a" * 40,
        "headRefOid": head,
        "mergeCommit": {"oid": commit},
        "commits": {"totalCount": count},
        "closingIssuesReferences": {
            "totalCount": 1,
            "nodes": [
                {
                    "number": 3,
                    "title": "Incorrect invoice total",
                    "body": "Adding 2 and 4 should produce 6.",
                    "url": "https://github.com/example/fixture/issues/3",
                    "createdAt": "2025-12-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            ],
        },
    }


def test_problem_statement_has_cutoff_and_hindsight_provenance() -> None:
    source = pull("b" * 40, "c" * 40)
    statement, provenance = _problem(source)
    assert "Adding 2 and 4" in statement
    assert provenance["source"] == "github_issue"
    assert provenance["source_created_at"] == "2025-12-01T00:00:00Z"
    assert provenance["cutoff"] == "2026-01-02T00:00:00Z"
    assert provenance["review_required"] and not provenance["historical_body_version_verified"]
    assert not provenance["hindsight_risk"]
    source["closingIssuesReferences"]["nodes"][0]["updatedAt"] = "2026-02-01T00:00:00Z"
    assert _problem(source)[1]["hindsight_risk"]


def test_whole_pr_patch_equivalence_preserves_python_whitespace() -> None:
    left = "@@ -1 +1 @@\n-    return 1\n+    return 2"
    same = "diff --git a/x b/x\nindex abc..def\n--- a/x\n+++ b/x\n@@ -1 +1 @@ title\n-    return 1\n+    return 2"
    different = "@@ -1 +1 @@\n-return 1\n+return 2"
    assert _patch_lines(left) == _patch_lines(same)
    assert _patch_lines(left) != _patch_lines(different)
    assert _patch_lines(left) != _patch_lines(left.replace("-1 +1", "-100 +100"))


def test_github_single_commit_uses_actual_parent_and_refreshes_metadata(
    tmp_path: Path, history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    project.update(source_kind="github", owner="example", name="fixture")
    projects._save_project(project, "github:example/fixture")
    reference = git(history, "rev-parse", "HEAD")
    expected_base = git(history, "rev-parse", "HEAD^")
    source = pull(reference, reference)
    monkeypatch.setattr(
        projects.github,
        "merged_pulls",
        lambda *args: {"nodes": [source], "total": 5, "has_more": True},
    )
    monkeypatch.setattr(
        projects.github,
        "pull_files",
        lambda *args: [
            {"path": "solution.py", "status": "modified", "previous_path": None, "patch": None},
            {
                "path": "test_solution.py",
                "status": "modified",
                "previous_path": None,
                "patch": None,
            },
        ],
    )
    first = projects.discover(project["id"])
    candidate = first["candidates"][0]
    assert candidate["source_kind"] == "github" and candidate["number"] == 7
    assert candidate["base_commit"] == expected_base
    assert candidate["base_commit"] != source["baseRefOid"]
    assert candidate["metadata"]["integration"] == "single_commit_integration"
    assert candidate["status"] == "discovered"
    assert first["coverage"]["available_merged_pull_requests"] == 5
    source["title"] = "Clarified totals"
    second = projects.discover(project["id"])
    assert second["counts"]["updated"] == 1
    assert second["candidates"][0]["id"] == candidate["id"]
    with projects.store._connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM repository_candidate_versions").fetchone()[0]
            == 2
        )


def test_multicommit_rebase_ambiguity_is_quarantined(
    tmp_path: Path, history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    project.update(source_kind="github", owner="example", name="fixture")
    source = pull(git(history, "rev-parse", "HEAD"), "c" * 40, count=3)
    monkeypatch.setattr(
        projects.github,
        "pull_files",
        lambda *args: [
            {"path": "another.py", "patch": "@@ -1 +1 @@\n-a\n+b", "status": "modified"}
        ],
    )
    candidate = projects._github_candidate(project, source)
    assert candidate["status"] == "quarantined"
    assert "ambiguous" in candidate["reasons"][0]


def test_repository_api_metadata_does_not_retain_clone_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = GitHub()
    monkeypatch.setattr(
        github,
        "_call",
        lambda args: {
            "full_name": "example/fixture",
            "default_branch": "main",
            "size": 10,
            "temp_clone_token": "SECRET_TOKEN",
            "private": True,
            "license": {"spdx_id": "MIT", "name": "MIT"},
        },
    )
    data = github.repository("example", "fixture")
    assert "SECRET_TOKEN" not in json.dumps(data)
    assert data["private"] is True


@pytest.mark.skipif(
    os.getenv("CVALS_TEST_GITHUB") != "1",
    reason="Set CVALS_TEST_GITHUB=1 for read-only authenticated GitHub checks",
)
def test_live_public_github_metadata() -> None:
    github = GitHub()
    repository = github.repository("pallets-eco", "flask-wtf")
    assert repository["owner"].lower() == "pallets-eco"
    data = github.merged_pulls(repository["owner"], repository["name"], 1)
    assert len(data["nodes"]) == 1 and data["nodes"][0]["mergedAt"]


def test_local_pagination_is_bound_to_head(tmp_path: Path, history: Path) -> None:
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    first = projects.discover(project["id"], limit=1)
    assert first["coverage"]["has_more"]
    second = projects.discover(project["id"], limit=1, cursor=first["coverage"]["next_cursor"])
    assert second["counts"]["total_stored"] == 2
    assert not second["coverage"]["has_more"]
    assert second["coverage"]["next_cursor"] is None
    git(history, "commit", "--quiet", "--allow-empty", "-m", "New history")
    with pytest.raises(ValueError, match="HEAD changed"):
        projects.discover(project["id"], cursor=first["coverage"]["next_cursor"])


def test_github_pagination_passes_opaque_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    github = GitHub()
    commands = []

    def reply(args: list[str]) -> dict[str, Any]:
        commands.append(args)
        return {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [],
                        "totalCount": 3,
                        "pageInfo": {"hasNextPage": True, "endCursor": "opaque:next="},
                    }
                }
            }
        }

    monkeypatch.setattr(github, "_call", reply)
    page = github.merged_pulls("example", "fixture", 2, "opaque:first=")
    assert "after=opaque:first=" in commands[0]
    assert page["next_cursor"] == "opaque:next="
    with pytest.raises(ValueError, match="cursor"):
        github.merged_pulls("example", "fixture", 2, "bad\ncursor")


def test_two_parent_merge_checks_recorded_head(
    tmp_path: Path, history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = git(history, "symbolic-ref", "--short", "HEAD")
    git(history, "checkout", "--quiet", "-b", "feature")
    (history / "extra.py").write_text("VALUE = 1\n")
    git(history, "add", ".")
    git(history, "commit", "--quiet", "-m", "Feature")
    head = git(history, "rev-parse", "HEAD")
    git(history, "checkout", "--quiet", branch)
    base = git(history, "rev-parse", "HEAD")
    git(history, "merge", "--quiet", "--no-ff", "feature", "-m", "Merge feature")
    reference = git(history, "rev-parse", "HEAD")
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    project.update(source_kind="github", owner="example", name="fixture")
    monkeypatch.setattr(
        projects.github, "pull_files", lambda *args: [{"path": "extra.py", "status": "added"}]
    )
    candidate = projects._github_candidate(project, pull(reference, head))
    assert candidate["base_commit"] == base
    assert candidate["status"] == "discovered"
    assert candidate["metadata"]["integration"] == "two_parent_merge"
    rejected = projects._github_candidate(project, pull(reference, "c" * 40))
    assert rejected["status"] == "quarantined"
    assert "second parent" in rejected["reasons"][0]


def test_multicommit_whole_patch_equivalence(
    tmp_path: Path, history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    project.update(source_kind="github", owner="example", name="fixture")
    reference = git(history, "rev-parse", "HEAD")
    patches = [
        {
            "path": name,
            "status": "modified",
            "patch": git(history, "diff", "HEAD^", "HEAD", "--", name),
        }
        for name in ["solution.py", "test_solution.py"]
    ]
    monkeypatch.setattr(projects.github, "pull_files", lambda *args: patches)
    candidate = projects._github_candidate(project, pull(reference, "d" * 40, count=3))
    assert candidate["status"] == "discovered"
    assert candidate["metadata"]["integration"] == "whole_pr_patch_equivalent_single_parent"
    patches[0]["patch"] = patches[0]["patch"].replace("+    return", "+return")
    rejected = projects._github_candidate(project, pull(reference, "d" * 40, count=3))
    assert rejected["status"] == "quarantined"


def test_git_replace_objects_cannot_change_snapshot(tmp_path: Path, history: Path) -> None:
    first = git(history, "rev-parse", "HEAD^")
    current = git(history, "rev-parse", "HEAD")
    git(history, "replace", current, first)
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    snapshot = projects.snapshot(project["id"], current, include_tests=True)
    assert "return sum(values)" in snapshot["files"]["solution.py"]


def test_project_audit_is_atomic(
    tmp_path: Path, history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = Projects(Settings(data_dir=tmp_path / "workspace"))
    project = projects.connect(str(history))
    assert projects.audit.verify()["valid"]
    assert projects.audit.list()[0]["action"] == "project.connected"

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Audit unavailable")

    monkeypatch.setattr(projects.audit, "record", fail)
    with pytest.raises(ValueError, match="Audit unavailable"):
        projects.discover(project["id"])
    assert projects.candidates(project["id"]) == []
