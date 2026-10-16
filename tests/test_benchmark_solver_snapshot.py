"""Real Git checks for preserving solver snapshots without repository history."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.benchmarks.executor import _solver_snapshot_command


@pytest.fixture
def repository(tmp_path: Path):
    directory = tmp_path / "repository"
    directory.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *arguments],
            cwd=directory,
            env=environment,
            check=check,
            capture_output=True,
        )

    git("-c", "init.defaultBranch=main", "init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@localhost")
    return directory, environment, git


def commit(git, message: str = "Original fixture") -> str:
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", message)
    return git("rev-parse", "HEAD").stdout.decode().strip()


def snapshot(directory: Path, environment: dict[str, str], base: str):
    temporary_before = set(Path("/tmp").glob("everval-snapshot.*"))
    result = subprocess.run(
        ["bash", "-c", _solver_snapshot_command(base)],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert set(Path("/tmp").glob("everval-snapshot.*")) == temporary_before
    return result


def test_snapshot_preserves_ignored_tracked_files_paths_modes_and_one_commit(
    repository, tmp_path: Path
) -> None:
    directory, environment, git = repository
    excludes = tmp_path / "global-excludes"
    excludes.write_text("global-kept.dat\n")
    config = tmp_path / "global-config"
    config.write_text(f"[core]\n\texcludesFile = {excludes}\n")
    environment["GIT_CONFIG_GLOBAL"] = str(config)

    (directory / ".gitignore").write_text("*.so\n.tx/\ngenerated/\n")
    filenames = [
        "global-kept.dat",
        "normal.txt",
        "space name.txt",
        "line\nbreak.txt",
        "brackets[ab].txt",
        ":(glob)literal*.txt",
        "-leading.txt",
        "back\\slash.txt",
        "empty.so",
        ".tx/config",
    ]
    for filename in filenames:
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"" if filename == "empty.so" else filename.encode())
    executable = directory / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (directory / "linked").symlink_to("normal.txt")
    git("add", "--force", "--all")
    base = commit(git)
    expected_tree = git("rev-parse", base + "^{tree}").stdout.strip()
    expected_entries = git("ls-tree", "-rz", base).stdout

    (directory / "future.txt").write_text("Future-only content")
    git("add", "future.txt")
    future = commit(git, "Future solution must disappear")
    git("reset", "--hard", base)
    git("remote", "add", "origin", "https://example.invalid/fixture.git")
    ignored = directory / "generated/cache.txt"
    ignored.parent.mkdir()
    ignored.write_text("Ignored untracked artifact must not enter the snapshot")

    result = snapshot(directory, environment, base)
    assert result.returncode == 0, result.stderr
    assert git("rev-parse", "HEAD^{tree}").stdout.strip() == expected_tree
    assert git("ls-tree", "-rz", "HEAD").stdout == expected_entries
    assert git("rev-list", "--all", "--count").stdout.strip() == b"1"
    assert git("remote").stdout == b""
    assert git("cat-file", "-e", future, check=False).returncode != 0
    assert git("status", "--porcelain").stdout == b""
    assert git("ls-files", "--error-unmatch", "generated/cache.txt", check=False).returncode != 0
    assert ignored.read_text() == "Ignored untracked artifact must not enter the snapshot"


def test_snapshot_refuses_filtered_content_drift_before_synthetic_commit(repository) -> None:
    directory, environment, git = repository
    (directory / ".gitattributes").write_text("payload.txt filter=uppercase\n")
    (directory / "payload.txt").write_text("lowercase\n")
    git("config", "filter.uppercase.clean", "tr a-z A-Z")
    git("add", "--all")
    base = commit(git)
    assert git("show", base + ":payload.txt").stdout == b"LOWERCASE\n"
    assert (directory / "payload.txt").read_text() == "lowercase\n"
    assert git("diff", "--exit-code", base).returncode == 0

    result = snapshot(directory, environment, base)
    assert result.returncode != 0
    assert git("rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_snapshot_rejects_gitlinks_before_removing_original_history(repository) -> None:
    directory, environment, git = repository
    (directory / "ordinary.txt").write_text("original\n")
    git("add", "--all")
    linked_commit = commit(git)
    git("update-index", "--add", "--cacheinfo", f"160000,{linked_commit},submodule")
    base = commit(git, "Unsupported submodule")
    (directory / "submodule").mkdir()
    expected_tree = git("rev-parse", base + "^{tree}").stdout

    result = snapshot(directory, environment, base)
    assert result.returncode != 0
    assert "Tracked submodules" in result.stderr
    assert git("rev-parse", "HEAD").stdout.decode().strip() == base
    assert git("rev-parse", "HEAD^{tree}").stdout == expected_tree


def test_snapshot_rejects_modified_tracked_content_before_history_removal(repository) -> None:
    directory, environment, git = repository
    (directory / "ordinary.txt").write_text("original\n")
    git("add", "--all")
    base = commit(git)
    (directory / "ordinary.txt").write_text("unexpected modification\n")

    result = snapshot(directory, environment, base)
    assert result.returncode != 0
    assert git("rev-parse", "HEAD").stdout.decode().strip() == base


@pytest.mark.parametrize("base", ["", "HEAD", "a" * 39, "a" * 40 + "; touch injected"])
def test_snapshot_rejects_non_frozen_commit_identifiers(base: str) -> None:
    with pytest.raises(ValueError, match="Invalid frozen base commit"):
        _solver_snapshot_command(base)
