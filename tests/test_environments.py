"""Preparation accepts package declarations as data and isolates installation."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

import cvals.environments as module
from cvals.config import Settings
from cvals.environments import (
    Environments,
    _wheel_archive,
    detect_requirements,
    validate_requirements,
)
from cvals.projects import Projects

DIGEST = "sha256:" + "a" * 64
PREPARED = "sha256:" + "b" * 64


def test_pep508_requirements_and_default_runner() -> None:
    values = validate_requirements(["requests>=2; python_version >= '3.10'", "Flask[async]==3.0"])
    assert "pytest==8.4.2" in values
    assert any(value.startswith("requests>=2;") for value in values)
    assert validate_requirements(["pytest==8.3.5"]) == ["pytest==8.3.5"]
    assert validate_requirements([]) == ["pytest==8.4.2"]


@pytest.mark.parametrize(
    "value",
    [
        "-r requirements.txt",
        "--extra-index-url=https://private.example",
        "-e .",
        "./local",
        "/tmp/local",
        "git+https://example.test/repo",
        "name @ https://example.test/pkg.whl",
        "name @ file:///tmp/package",
        "package\n--index-url=https://example.test",
        "package\\",
    ],
)
def test_local_url_and_option_requirements_fail_closed(value: str) -> None:
    with pytest.raises(ValueError):
        validate_requirements([value])


def test_static_declarations_preserve_manifest_and_ignore_setup_code(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    files = {
        "pyproject.toml": '[project]\ndependencies=["Flask>=3"]\n[project.optional-dependencies]\ntest=["pytest==8.4.2"]\n[build-system]\nrequires=["dangerous-backend"]\n',
        "requirements-dev.txt": "# reviewed packages\ncoverage>=7  # tools\n",
        "setup.py": f"open({str(marker)!r}, 'w').write('wrong')\n",
        "uv.lock": "version = 1",
    }
    detected = detect_requirements(files)
    assert set(detected["requirements"]) == {"Flask>=3", "coverage>=7", "pytest==8.4.2"}
    assert set(detected["declaration_hashes"]) == {"pyproject.toml", "requirements-dev.txt"}
    assert len(detected["notices"]) == 2
    assert not marker.exists()


@pytest.mark.parametrize(
    "files",
    [
        {"setup.py": "raise RuntimeError('do not execute')"},
        {"pyproject.toml": '[project]\ndynamic=["dependencies"]'},
        {"pyproject.toml": '[project]\ndependencies="requests"'},
        {
            "pyproject.toml": '[project]\ndependencies=[]\n[dependency-groups]\ntest=[{include-group="dev"}]'
        },
        {"pyproject.toml": "[not-valid"},
        {"requirements.txt": "-r requirements/base.txt"},
        {"requirements.txt": "local @ file:///private/package"},
    ],
)
def test_unsupported_dependency_declarations_require_review(files: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        detect_requirements(files)


def wheel_payload(
    *,
    name: str = "example-1.0-py3-none-any.whl",
    member_path: str = "example/__init__.py",
    symlink: bool = False,
) -> bytes:
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        member = zipfile.ZipInfo(member_path)
        member.external_attr = (0o120777 if symlink else 0o100644) << 16
        archive.writestr(member, b"VALUE = 1\n")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(wheel.getvalue())
        archive.addfile(member, io.BytesIO(wheel.getvalue()))
    return output.getvalue()


def test_wheel_archive_records_hashes_without_host_extraction() -> None:
    archive, manifest = _wheel_archive(wheel_payload())
    assert len(manifest) == 1 and len(manifest[0]["sha256"]) == 64
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stored:
        assert stored.getnames() == ["wheelhouse", "wheelhouse/example-1.0-py3-none-any.whl"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "../evil.whl"},
        {"name": "source.tar.gz"},
        {"member_path": "../escape"},
        {"member_path": "/etc/secret"},
        {"symlink": True},
    ],
)
def test_wheel_paths_and_links_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _wheel_archive(wheel_payload(**kwargs))


def test_wheel_expansion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "MAX_EXPANDED_BYTES", 1)
    with pytest.raises(ValueError, match="Expanded"):
        _wheel_archive(wheel_payload())


@pytest.fixture
def project(tmp_path: Path) -> tuple[Settings, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "solution.py").write_text("def double(value):\n    return value * 2\n")
    (repository / "requirements.txt").write_text("pytest==8.4.2\n")
    for args in [
        ["init", "--quiet"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--quiet",
            "-m",
            "Initial source",
        ],
    ]:
        subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
    settings = Settings(data_dir=tmp_path / "workspace")
    record = Projects(settings).connect(str(repository))
    return settings, record["id"]


def test_dependency_cache_and_atomic_metadata(
    project: tuple[Settings, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, project_id = project
    monkeypatch.setattr(
        module,
        "_image",
        lambda settings: (
            settings.sandbox_image if settings.sandbox_image.startswith("sha256:") else DIGEST
        ),
    )
    environments = Environments(settings)
    builds = []

    def build(plan: dict[str, Any]) -> dict[str, Any]:
        builds.append(plan)
        return {
            "image_digest": PREPARED,
            "pip_freeze": ["pytest==8.4.2"],
            "wheel_manifest": [],
            "prepared_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(environments, "_build", build)
    first = environments.prepare(project_id)
    second = environments.prepare(project_id)
    assert first["image_digest"] == PREPARED
    assert not first["cache_hit"] and second["cache_hit"]
    assert len(builds) == 1
    assert len(environments.list(project_id)) == 1
    assert first["source_manifest_hash"] == second["source_manifest_hash"]
    assert environments.audit.verify()["valid"]
    environments.prepare(project_id, refresh=True)
    assert len(builds) == 2


def test_preparation_container_policy_has_no_host_mounts(
    project: tuple[Settings, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    environments = Environments(project[0])
    commands = []

    def docker(
        args: list[str], settings: Settings, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module, "_docker", docker)
    environments._create("download", DIGEST, "pass", ["pytest==8.4.2"], download=True)
    environments._create("install", DIGEST, "pass", ["pytest==8.4.2"], download=False)
    assert "--network=bridge" in commands[0] and "--read-only" in commands[0]
    assert "--user=65532:65532" in commands[0]
    assert "--network=none" in commands[1]
    for args in commands:
        assert "--cap-drop=ALL" in args and "--security-opt=no-new-privileges" in args
        assert not any(arg in {"-v", "--volume", "--mount"} for arg in args)
        assert not any("TOKEN" in arg or "HOME=" in arg for arg in args)


def test_failed_preparation_removes_all_containers(
    project: tuple[Settings, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    environments = Environments(project[0])
    removed = []
    monkeypatch.setattr(environments, "_create", lambda *args, **kwargs: None)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("Timed out")

    def docker(
        args: list[str], settings: Settings, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "rm":
            removed.append(args[-1])
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(environments, "_start", fail)
    monkeypatch.setattr(module, "_docker", docker)
    with pytest.raises(ValueError, match="Timed out"):
        environments._build({"base_image_digest": DIGEST, "requirements": ["pytest==8.4.2"]})
    assert len(removed) == 2
    assert any("download" in name for name in removed) and any(
        "install" in name for name in removed
    )


@pytest.mark.skipif(
    os.getenv("CVALS_TEST_ENVIRONMENTS") != "1",
    reason="Set CVALS_TEST_ENVIRONMENTS=1 for Docker and public wheel download checks",
)
def test_live_prepared_pytest_image(project: tuple[Settings, str]) -> None:
    from dataclasses import replace

    settings = replace(project[0], docker_host=os.getenv("CVALS_DOCKER_HOST"))
    environments = Environments(settings)
    record = environments.prepare(project[1], packages=["pytest==8.4.2"])
    assert "pytest==8.4.2" in record["pip_freeze"]
    assert record["image_digest"].startswith("sha256:")
    assert len(record["wheel_manifest"]) >= 1
    assert environments.prepare(project[1], packages=["pytest==8.4.2"])["cache_hit"]
    assert "secret" not in json.dumps(record).lower()


def test_orphan_cleanup_preserves_other_workspaces_and_recent_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    settings = Settings(data_dir=tmp_path / "workspace")
    workspace = module._workspace_label(settings)
    now = datetime.now(UTC)
    records = {
        "a" * 12: {
            "Name": "/cvals-env-install-old",
            "Created": (now - timedelta(hours=1)).isoformat(),
            "Config": {
                "Labels": {"cvals.environment.transient": "true", "cvals.workspace": workspace}
            },
        },
        "b" * 12: {
            "Name": "/cvals-env-download-new",
            "Created": now.isoformat(),
            "Config": {
                "Labels": {"cvals.environment.transient": "true", "cvals.workspace": workspace}
            },
        },
        "c" * 12: {
            "Name": "/unrelated",
            "Created": (now - timedelta(hours=1)).isoformat(),
            "Config": {
                "Labels": {"cvals.environment.transient": "true", "cvals.workspace": workspace}
            },
        },
        "d" * 12: {
            "Name": "/cvals-env-install-other",
            "Created": (now - timedelta(hours=1)).isoformat(),
            "Config": {
                "Labels": {
                    "cvals.environment.transient": "true",
                    "cvals.workspace": "another-workspace",
                }
            },
        },
    }
    deleted = []

    def docker(
        args: list[str], settings: Settings, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "ps":
            data = "\n".join(records).encode()
        elif args[0] == "inspect":
            data = json.dumps(records[args[-1]]).encode()
        else:
            assert args[0] == "rm"
            deleted.append(args[-1])
            data = b""
        return subprocess.CompletedProcess(args, 0, stdout=data, stderr=b"")

    monkeypatch.setattr(module, "_docker", docker)
    result = module.cleanup_environments(settings)
    assert deleted == ["a" * 12]
    assert result["removed"] == deleted and result["images_removed"] == []
    with pytest.raises(ValueError, match="ten minutes"):
        module.cleanup_environments(settings, minimum_age_seconds=30)
