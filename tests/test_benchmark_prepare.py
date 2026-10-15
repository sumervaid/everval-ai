"""Boundaries for the provider-free external benchmark preparation tool."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def prepare() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts/benchmarks/prepare.py"
    spec = importlib.util.spec_from_file_location("everval_benchmark_prepare_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_artifact_is_immutable(prepare: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    prepare.immutable(target, b"original")
    with pytest.raises(ValueError, match="differs"):
        prepare.immutable(target, b"replacement")
    assert target.read_bytes() == b"original"


def test_symlink_artifact_is_rejected(prepare: ModuleType, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.write_bytes(b"original")
    target = tmp_path / "record.json"
    target.symlink_to(other)
    with pytest.raises(ValueError, match="symlink"):
        prepare.immutable(target, b"original")
    assert other.read_bytes() == b"original"


def test_output_directory_cannot_be_checkout(
    prepare: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    script = project / "scripts/benchmarks/prepare.py"
    monkeypatch.setattr(prepare, "__file__", str(script))
    with pytest.raises(ValueError, match="outside"):
        prepare.private_root(str(project / "private-data"))
    assert not (project / "private-data").exists()


def test_verified_download_reuse_has_no_network(
    prepare: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "asset"
    target.write_bytes(b"verified")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("A verified local artifact must not trigger network access")

    monkeypatch.setattr(prepare.urllib.request, "urlopen", unexpected)
    prepare.download(
        "https://example.invalid/asset", target, hashlib.sha256(b"verified").hexdigest()
    )


def test_corrupt_existing_download_is_not_replaced(
    prepare: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "asset"
    target.write_bytes(b"corrupt")
    monkeypatch.setattr(
        prepare.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("Corrupt cache must fail before network access"),
    )
    with pytest.raises(ValueError, match="hash differs"):
        prepare.download("https://example.invalid/asset", target, "0" * 64)
    assert target.read_bytes() == b"corrupt"


@pytest.mark.parametrize("body,expected", [(b"oversized", b"oversized"), (b"bad", b"good")])
def test_download_size_and_hash_fail_closed(
    prepare: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    expected: bytes,
) -> None:
    monkeypatch.setattr(prepare, "MAX_DOWNLOAD", 4)
    monkeypatch.setattr(
        prepare.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(body),
    )
    target = tmp_path / "asset"
    with pytest.raises(ValueError, match="size/hash"):
        prepare.download(
            "https://example.invalid/asset", target, hashlib.sha256(expected).hexdigest()
        )
    assert not target.exists()


def test_remote_docker_socket_is_rejected(prepare: ModuleType) -> None:
    with pytest.raises(ValueError, match="local Unix"):
        prepare.docker_prefix("tcp://remote.example:2375")
    assert prepare.docker_prefix("unix:///tmp/docker.sock") == [
        "docker",
        "--host",
        "unix:///tmp/docker.sock",
    ]


def test_docker_client_receives_no_provider_environment(
    prepare: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setenv("HTTP_PROXY", "http://test-placeholder.invalid")
    environment = prepare.docker_environment(tmp_path)
    assert set(environment) == {"PATH", "DOCKER_CONFIG", "DOCKER_BUILDKIT", "TMPDIR"}
    assert json.loads((tmp_path / "docker-client/config.json").read_text()) == {}


def test_command_deadline_terminates_process(prepare: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(TimeoutError, match="deadline"):
        prepare.bounded_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            dict(os.environ),
            tmp_path / "timeout.log",
            0.05,
            1024,
        )


def test_command_log_limit_terminates_process(prepare: ModuleType, tmp_path: Path) -> None:
    log = tmp_path / "overflow.log"
    with pytest.raises(ValueError, match="output"):
        prepare.bounded_process(
            [sys.executable, "-c", "print('x' * 100000)"],
            dict(os.environ),
            log,
            10,
            100,
        )
    assert log.stat().st_size <= 100


def test_build_without_verified_assets_never_starts_docker(
    prepare: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        prepare,
        "bounded_process",
        lambda *args, **kwargs: pytest.fail(
            "Missing task assets must fail before Docker execution"
        ),
    )
    result = prepare.build_one(
        tmp_path,
        {"instance_id": "psf__requests-3362", "task_sha256": "0" * 64},
        ["docker"],
        60,
    )
    assert result["preparation_status"] == "failed"
    assert result["image_id"] is None
    assert result["reason"].startswith("FileNotFoundError")
    assert (tmp_path / result["artifacts"] / "preparation.json").exists()


def test_build_requires_both_verified_assets(prepare, tmp_path, monkeypatch):
    instance = "psf__requests-3362"
    source = tmp_path / "sources/swe-bench-tasks" / prepare.TASK_REVISION / "tasks" / instance
    source.mkdir(parents=True)
    (source / "Dockerfile").write_text("FROM example")
    (source / "environment.yml").write_text("unverified")
    monkeypatch.setattr(
        prepare,
        "asset_manifest",
        lambda: {
            "files": [
                {"path": f"tasks/{instance}/Dockerfile", "sha256": prepare.digest(b"FROM example")}
            ]
        },
    )
    with pytest.raises(ValueError, match="Both required"):
        prepare.verified_build_assets(tmp_path, instance)


@pytest.mark.parametrize("defect", ["revision", "duplicate"])
def test_build_asset_manifest_revision_and_unique_paths(prepare, tmp_path, monkeypatch, defect):
    spec = {"revision": prepare.TASK_REVISION, "files": [{"path": "a"}]}
    if defect == "revision":
        spec["revision"] = "different"
    else:
        spec["files"].append({"path": "a"})
    raw = json.dumps(spec).encode()
    (tmp_path / "preparation-assets.json").write_bytes(raw)
    monkeypatch.setattr(prepare, "__file__", str(tmp_path / "prepare.py"))
    monkeypatch.setattr(prepare, "ASSETS_SHA", prepare.digest(raw))
    with pytest.raises(ValueError, match="revision|unique"):
        prepare.asset_manifest()


@pytest.mark.parametrize("field", ["selection_sha256", "task_sha256"])
def test_cached_image_binds_current_task(prepare, field):
    task = {"task_sha256": "1" * 64}
    record = {
        "selection_sha256": prepare.SELECTION_SHA,
        "task_sha256": task["task_sha256"],
        "preparation_status": "ready",
        "cleanup_complete": True,
        "image_id": "sha256:" + "2" * 64,
    }
    prepare.validate_cached(record, task)
    record[field] = "0" * 64
    with pytest.raises(ValueError, match="frozen"):
        prepare.validate_cached(record, task)


@pytest.mark.parametrize("filename", ["campaign.json", "preparation-blocked.json"])
def test_frozen_or_unresolved_preparation_cannot_mutate(prepare, tmp_path, filename):
    (tmp_path / filename).write_text("{}")
    with pytest.raises(ValueError, match="frozen|blocked"):
        prepare.require_unfrozen(tmp_path)


def test_tooling_disables_config_and_uses_private_temporary_storage(prepare, tmp_path, monkeypatch):
    monkeypatch.setenv("UV_INDEX_URL", "https://private.invalid/simple")
    monkeypatch.setenv("TMPDIR", "/unowned")
    environment = prepare.tooling_environment(tmp_path)
    assert environment["UV_NO_CONFIG"] == "1"
    assert "UV_INDEX_URL" not in environment
    assert Path(environment["TMPDIR"]).is_relative_to(tmp_path)
    assert Path(environment["TMPDIR"]).is_dir()


def test_preparation_lock_rejects_a_second_owner(prepare, tmp_path):
    with (
        prepare.preparation_lock(tmp_path),
        pytest.raises(ValueError, match="owns"),
        prepare.preparation_lock(tmp_path),
    ):
        pytest.fail("Second owner acquired the same lock")


def test_interrupted_docker_build_retains_durable_block(prepare, tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "verified_build_assets", lambda *a: tmp_path)
    monkeypatch.setattr(
        prepare,
        "adapt",
        lambda *a: (
            "FROM scratch\n",
            {},
            {"adapted_dockerfile_sha256": "a" * 64, "recipe_bundle_sha256": "b" * 64},
        ),
    )

    def timeout(*args, **kwargs):
        raise TimeoutError("deadline")

    monkeypatch.setattr(prepare, "bounded_process", timeout)
    record = prepare.build_one(
        tmp_path, {"instance_id": "psf__requests-3362", "task_sha256": "1" * 64}, ["docker"], 60
    )
    assert record["cleanup_complete"] is False
    assert record["reason_code"] == "docker_build_interrupted_cleanup_unknown"
    prepare.settle_preparation(tmp_path, record)
    marker = tmp_path / "preparation-blocked.json"
    retained = json.loads(marker.read_text())
    assert retained["state"] == "cleanup_unresolved"
    # A changed status index cannot bypass the separate owned-work marker.
    (tmp_path / "runtime-images.json").write_text("{}")
    with pytest.raises(ValueError, match="blocked"):
        prepare.require_unfrozen(tmp_path)
    assert (tmp_path / record["artifacts"] / "preparation.json").exists()


def test_nonzero_docker_client_is_not_daemon_cleanup_proof(prepare, tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "verified_build_assets", lambda *a: tmp_path)
    monkeypatch.setattr(
        prepare,
        "adapt",
        lambda *a: (
            "FROM scratch\n",
            {},
            {"adapted_dockerfile_sha256": "a" * 64, "recipe_bundle_sha256": "b" * 64},
        ),
    )
    monkeypatch.setattr(prepare, "bounded_process", lambda *a: 137)
    record = prepare.build_one(
        tmp_path, {"instance_id": "psf__requests-3362", "task_sha256": "1" * 64}, ["docker"], 60
    )
    assert record["cleanup_complete"] is False
    assert record["reason_code"] == "docker_build_client_failed_cleanup_unknown"
    assert record["preparation_status"] == "failed"
    prepare.atomic_json(tmp_path / "runtime-images.json", {"psf__requests-3362": record})
    prepare.settle_preparation(tmp_path, record)
    assert (tmp_path / "preparation-blocked.json").exists()
    assert (tmp_path / record["artifacts"] / "preparation.json").exists()


def test_cannot_release_another_preparations_marker(prepare, tmp_path):
    marker = tmp_path / "preparation-blocked.json"
    marker.write_text('{"ownership_token":"first"}')
    with pytest.raises(ValueError, match="ownership"):
        prepare.settle_preparation(
            tmp_path, {"ownership_token": "second", "cleanup_complete": True}
        )
    assert marker.exists()


def test_tooling_reuse_detects_installed_version_drift(prepare, tmp_path, monkeypatch):
    artifacts = tmp_path / "tooling/original"
    artifacts.mkdir(parents=True)
    original = b"example==1.0\n"
    (artifacts / "installed-packages.txt").write_bytes(original)
    record = {
        "artifacts": "tooling/original",
        "artifact_sha256": {"installed-packages.txt": prepare.digest(original)},
    }

    def drift(command, environment, target, timeout, maximum):
        target.write_bytes(b"example==2.0\n")
        return 0

    monkeypatch.setattr(prepare, "bounded_process", drift)
    with pytest.raises(ValueError, match="drifted"):
        prepare.verify_tooling_reuse(tmp_path, record)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_preparation_lock_rejects_nonregular_files(prepare, tmp_path, kind):
    lock = tmp_path / ".preparation.lock"
    if kind == "symlink":
        other = tmp_path / "other"
        other.touch()
        lock.symlink_to(other)
    else:
        os.mkfifo(lock)
    with pytest.raises((OSError, ValueError)), prepare.preparation_lock(tmp_path):
        pytest.fail("Nonregular lock was accepted")


def test_native_adaptation_preserves_semantic_pins_and_verifies_resolver(prepare, tmp_path):
    environment = (
        "name: testbed\nchannels:\n  - defaults\ndependencies:\n"
        "  - python=3.9.20=h123_0\n  - pytest=7.4.4=h123_0\n"
        "  - openssl=3.0.15=h123_0\n"
        "  - pip:\n    - requests==2.32.0\n"
    )
    (tmp_path / "environment.yml").write_text(environment)
    original = (
        "FROM --platform=linux/amd64 ubuntu:jammy\n"
        "RUN wget Miniconda3-py311_23.11.0-2-Linux-x86_64.sh\n"
        "RUN <<EOF_abc\n#!/bin/bash\nset -euxo pipefail\n"
        "cat <<'EOF_env' > /root/environment.yml\n"
        + environment
        + "EOF_env\nconda env create -f /root/environment.yml\nEOF_abc\n"
    )
    (tmp_path / "Dockerfile").write_text(original)
    recipe, scripts, provenance = prepare.adapt(tmp_path, shared_conda=False)
    assert provenance["preserved_version_pins"] == ["python=3.9.20", "pytest=7.4.4"]
    assert provenance["relaxed_platform_or_runtime_pins"] == ["openssl=3.0.15=h123_0"]
    assert "    - requests==2.32.0" in scripts["prepare-1.sh"]
    assert "linux/arm64" in recipe and "linux/amd64" not in recipe
    assert "2.9.0-0/micromamba-linux-aarch64" in recipe
    assert provenance["resolver"]["sha256"] in recipe
    assert "sha256sum -c -" in recipe
    assert "channel_priority: flexible" in recipe
    assert "repodata_shards_threads: 1" in recipe
    assert provenance["source_dockerfile_sha256"] == prepare.digest(original.encode())
    assert (tmp_path / "Dockerfile").read_text() == original


def test_tooling_reuse_detects_interpreter_drift(prepare, tmp_path, monkeypatch):
    artifacts = tmp_path / "tooling/original"
    artifacts.mkdir(parents=True)
    contents = {
        "installed-packages.txt": b"example==1.0\n",
        "python-version.txt": b"Python 3.12.14\n",
    }
    for name, data in contents.items():
        (artifacts / name).write_bytes(data)
    record = {
        "artifacts": "tooling/original",
        "artifact_sha256": {name: prepare.digest(data) for name, data in contents.items()},
    }

    def drift(command, environment, target, timeout, maximum):
        target.write_bytes(
            b"Python 3.13.0\n" if command[-1] == "--version" else contents["installed-packages.txt"]
        )
        return 0

    monkeypatch.setattr(prepare, "bounded_process", drift)
    with pytest.raises(ValueError, match="Python version drifted"):
        prepare.verify_tooling_reuse(tmp_path, record)


def _recipe_fixture(directory: Path, python: str = "3.9.20") -> None:
    environment = (
        "name: testbed\nchannels:\n  - defaults\ndependencies:\n"
        f"  - python={python}=h123_0\n  - pytest=7.4.4=h123_0\n"
    )
    (directory / "environment.yml").write_text(environment)
    (directory / "Dockerfile").write_text(
        "FROM --platform=linux/amd64 ubuntu:jammy\n"
        "RUN wget Miniconda3-py311_23.11.0-2-Linux-x86_64.sh\n"
        "RUN <<EOF_abc\n#!/bin/bash\nset -euxo pipefail\n"
        "cat <<'EOF_env' > /root/environment.yml\n"
        + environment
        + "EOF_env\nconda env create -f /root/environment.yml\n"
        "chmod -R 777 /testbed\nEOF_abc\n"
    )


def test_worktree_permissions_preserve_original_executable_bits(prepare, tmp_path):
    import stat
    import subprocess

    _recipe_fixture(tmp_path)
    _, scripts, provenance = prepare.adapt(tmp_path, shared_conda=False)
    assert provenance["worktree_permissions"]["replacements"] == 1
    assert "chmod -R 777 /testbed" not in scripts["prepare-1.sh"]
    assert "chmod -R a+rwX /testbed" in scripts["prepare-1.sh"]
    worktree = tmp_path / "original-fixture"
    worktree.mkdir()
    plain = worktree / "module.py"
    executable = worktree / "runner"
    plain.write_text("fixture")
    executable.write_text("fixture")
    plain.chmod(0o644)
    executable.chmod(0o755)
    subprocess.run(["chmod", "-R", "a+rwX", str(worktree)], check=True)
    assert stat.S_IMODE(plain.stat().st_mode) & 0o111 == 0
    assert stat.S_IMODE(executable.stat().st_mode) & 0o111 == 0o111
    assert stat.S_IMODE(plain.stat().st_mode) & 0o222 == 0o222
    assert stat.S_IMODE(worktree.stat().st_mode) & 0o333 == 0o333


def test_context_identity_includes_copied_script_contents(prepare, tmp_path):
    _recipe_fixture(tmp_path, "3.9.20")
    first_recipe, first_scripts, first = prepare.adapt(tmp_path, shared_conda=False)
    _recipe_fixture(tmp_path, "3.9.21")
    second_recipe, second_scripts, second = prepare.adapt(tmp_path, shared_conda=False)
    assert first_recipe == second_recipe
    assert first_scripts != second_scripts
    assert first["adapted_dockerfile_sha256"] == second["adapted_dockerfile_sha256"]
    assert first["recipe_bundle_sha256"] != second["recipe_bundle_sha256"]


@pytest.mark.parametrize("size,ready", [(3 * 1024 * 1024, True), (17 * 1024 * 1024, False)])
def test_package_record_capture_accepts_realistic_size_but_remains_bounded(
    prepare, tmp_path, monkeypatch, size, ready
):
    image_id = "sha256:" + "2" * 64
    monkeypatch.setattr(prepare, "verified_build_assets", lambda *args: tmp_path)
    monkeypatch.setattr(
        prepare,
        "adapt",
        lambda *args: (
            "FROM scratch\n",
            {},
            {"adapted_dockerfile_sha256": "a" * 64, "recipe_bundle_sha256": "b" * 64},
        ),
    )
    original_process = prepare.bounded_process
    observed_limits = {}

    def fake_docker(command, environment, log, timeout, limit):
        if command[1] == "build":
            Path(command[command.index("--iidfile") + 1]).write_text(image_id)
            log.write_text("Built original fixture\n")
            return 0
        if command[1] == "run":
            name = Path(command[-1]).name
            observed_limits[name] = limit
            if name == "conda-package-records.json":
                return original_process(
                    [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {size})"],
                    environment,
                    log,
                    timeout,
                    limit,
                )
            log.write_text("{}")
            return 0
        assert command[1:3] == ["image", "inspect"]
        log.write_text(json.dumps([{"Id": image_id, "Architecture": "arm64"}]))
        return 0

    monkeypatch.setattr(prepare, "bounded_process", fake_docker)
    monkeypatch.setattr(prepare, "remove_owned_container", lambda *args: True)
    record = prepare.build_one(
        tmp_path, {"instance_id": "psf__requests-3362", "task_sha256": "1" * 64}, ["docker"], 60
    )
    assert (record["preparation_status"] == "ready") is ready
    assert record["cleanup_complete"] is True
    assert observed_limits.pop("conda-package-records.json") == 16 * 1024 * 1024
    assert set(observed_limits.values()) == {2 * 1024 * 1024}
    if ready:
        assert record["image_id"] == image_id
    else:
        assert "exceeded the preparation limit" in record["reason"]
        assert record["image_id"] is None
    prepare.settle_preparation(tmp_path, record)
    assert not (tmp_path / "preparation-blocked.json").exists()


def _shared_recipe_fixture(directory: Path, requirements: tuple[str, ...]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    environment = (
        "name: testbed\nchannels:\n  - defaults\ndependencies:\n"
        "  - python=3.9.20=h123_0\n  - pip=23.3.1=h123_0\n  - pytest=7.4.4=h123_0\n"
        + (
            "  - pip:\n" + "".join("      - " + item + "\n" for item in requirements)
            if requirements
            else ""
        )
    )
    (directory / "environment.yml").write_text(environment)
    (directory / "Dockerfile").write_text(
        "FROM --platform=linux/amd64 ubuntu:jammy\n"
        "RUN wget Miniconda3-py311_23.11.0-2-Linux-x86_64.sh\n"
        "RUN <<EOF_conda\n#!/bin/bash\nset -euxo pipefail\n"
        "source /opt/miniconda3/bin/activate\n"
        "cat <<'EOF_env' > /root/environment.yml\n"
        + environment
        + "\nEOF_env\nconda env create -f /root/environment.yml\n"
        "conda activate testbed\nEOF_conda\n"
        "RUN <<EOF_repo\n#!/bin/bash\nset -euxo pipefail\n"
        "source /opt/miniconda3/bin/activate\nconda activate testbed\n"
        "printf 'original fixture repository setup\\n'\n"
        "chmod -R 777 /testbed\nEOF_repo\n"
    )


def test_shared_conda_stage_reuses_identical_pins_without_reordering_pip(prepare, tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    _shared_recipe_fixture(first, ("idna==3.10", "packaging==24.0"))
    _shared_recipe_fixture(second, ("packaging==24.0", "idna==3.10"))
    left = prepare.adapt(first)
    right = prepare.adapt(second)
    assert left[1]["prepare-1.sh"] == right[1]["prepare-1.sh"]
    assert left[2]["shared_conda_stage"] == right[2]["shared_conda_stage"]
    assert left[1]["prepare-pip.sh"] != right[1]["prepare-pip.sh"]
    assert "idna==3.10\npackaging==24.0\nEVERVAL_PIP_REQUIREMENTS" in left[1]["prepare-pip.sh"]
    assert "packaging==24.0\nidna==3.10\nEVERVAL_PIP_REQUIREMENTS" in right[1]["prepare-pip.sh"]
    assert left[2]["recipe_bundle_sha256"] != right[2]["recipe_bundle_sha256"]
    assert left[2]["separate_pip_stage"]["argv"] == [
        "/opt/miniconda3/envs/testbed/bin/python",
        "-m",
        "pip",
        "install",
        "-r",
        "/root/everval-pip-requirements.txt",
        "--no-input",
    ]


def test_sharing_preserves_resolver_prefix_repository_script_and_permission_fix(prepare, tmp_path):
    _shared_recipe_fixture(tmp_path, ("idna==3.10",))
    baseline = prepare.adapt(tmp_path, shared_conda=False)
    split = prepare.adapt(tmp_path)
    anchor = prepare.SHARED_FIRST_ANCHOR
    assert baseline[0].split(anchor)[0] == split[0].split(anchor)[0]
    assert split[0] == baseline[0].replace(anchor, anchor + prepare.SHARED_PIP_ANCHOR, 1)
    assert split[1]["prepare-2.sh"] == baseline[1]["prepare-2.sh"]
    assert "chmod -R a+rwX /testbed" in split[1]["prepare-2.sh"]
    assert split[2]["worktree_permissions"] == baseline[2]["worktree_permissions"]
    assert split[2]["recipe_bundle_sha256"] == prepare.digest(
        prepare.canonical(
            {
                "dockerfile_sha256": prepare.digest(split[0].encode()),
                "scripts": {name: prepare.digest(body.encode()) for name, body in split[1].items()},
            }
        )
    )


def test_absent_pip_stage_uses_the_same_shared_conda_stage(prepare, tmp_path):
    empty, installed = tmp_path / "empty", tmp_path / "installed"
    _shared_recipe_fixture(empty, ())
    _shared_recipe_fixture(installed, ("idna==3.10",))
    first, second = prepare.adapt(empty), prepare.adapt(installed)
    assert first[1]["prepare-1.sh"] == second[1]["prepare-1.sh"]
    assert "prepare-pip.sh" not in first[1]
    assert first[2]["separate_pip_stage"]["requirements_count"] == 0


@pytest.mark.parametrize("mutation", ["extra_command", "unpinned_pip", "unsupported_requirement"])
def test_shared_stage_rejects_unreviewed_template_or_requirement_changes(
    prepare, tmp_path, mutation
):
    _shared_recipe_fixture(tmp_path, ("idna==3.10",))
    recipe = (tmp_path / "Dockerfile").read_text()
    environment = (tmp_path / "environment.yml").read_text()
    if mutation == "extra_command":
        recipe = recipe.replace(
            "conda activate testbed\nEOF_conda", "conda activate testbed\necho extra\nEOF_conda"
        )
    elif mutation == "unpinned_pip":
        environment = environment.replace("  - pip=23.3.1=h123_0\n", "")
        recipe = recipe.replace("  - pip=23.3.1=h123_0\n", "")
    else:
        environment = environment.replace("idna==3.10", "idna>=3.10")
        recipe = recipe.replace("idna==3.10", "idna>=3.10")
    (tmp_path / "environment.yml").write_text(environment)
    (tmp_path / "Dockerfile").write_text(recipe)
    with pytest.raises(ValueError):
        prepare.adapt(tmp_path)


def test_shared_stage_never_mutates_the_unsplit_adaptation(prepare, tmp_path):
    import copy

    _shared_recipe_fixture(tmp_path, ("idna==3.10",))
    baseline = prepare.adapt(tmp_path, shared_conda=False)
    before = copy.deepcopy(baseline)
    prepare._split_environment(
        baseline, pins=baseline[2]["preserved_version_pins"], pip_lines=["      - idna==3.10"]
    )
    assert baseline == before


def test_offline_build_wheels_use_only_exact_installed_versions(prepare, monkeypatch):
    namespace = {"__name__": "wheelhouse_test"}
    exec(compile(prepare.WHEELHOUSE_SCRIPT, "<wheelhouse>", "exec"), namespace)
    metadata = namespace["metadata"]
    versions = {"setuptools": "75.1.0", "wheel": "0.40.0", "packaging": "24.0"}

    def version(name):
        if name not in versions:
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(metadata, "version", version)
    assert namespace["selected_requirements"]() == [
        "setuptools==75.1.0",
        "wheel==0.40.0",
        "packaging==24.0",
    ]
    del versions["setuptools"]
    with pytest.raises(metadata.PackageNotFoundError):
        namespace["selected_requirements"]()


def test_offline_wheelhouse_is_source_bound_and_keeps_evaluation_offline(prepare, tmp_path):
    _shared_recipe_fixture(tmp_path, ("idna==3.10",))
    dockerfile, scripts, provenance = prepare.adapt(tmp_path)
    assert scripts["prepare-wheelhouse.py"] == prepare.WHEELHOUSE_SCRIPT
    assert "ENV PIP_FIND_LINKS=/opt/everval-wheels" in dockerfile
    assert "PIP_NO_INDEX" not in dockerfile
    assert provenance["offline_build_wheels"]["script_sha256"] == prepare.digest(
        scripts["prepare-wheelhouse.py"].encode()
    )
    assert provenance["build_scripts"]["prepare-wheelhouse.py"] == prepare.digest(
        scripts["prepare-wheelhouse.py"].encode()
    )
    assert "--only-binary=:all:" in scripts["prepare-wheelhouse.py"]
    assert "--no-deps" in scripts["prepare-wheelhouse.py"]
    assert "sha256" in scripts["prepare-wheelhouse.py"]


def test_offline_wheel_manifest_records_downloads_and_rejects_incomplete_artifacts(
    prepare, tmp_path, monkeypatch
):
    namespace = {"__name__": "wheelhouse_test"}
    exec(compile(prepare.WHEELHOUSE_SCRIPT, "<wheelhouse>", "exec"), namespace)
    namespace["Path"] = lambda value: tmp_path / value.lstrip("/")
    namespace["selected_requirements"] = lambda: ["setuptools==75.1.0", "wheel==0.40.0"]
    recorded = []

    def download(command, *, check):
        assert check
        recorded.append(command)
        directory = tmp_path / "opt/everval-wheels"
        (directory / "setuptools-75.1.0-py3-none-any.whl").write_bytes(b"setuptools fixture")
        (directory / "wheel-0.40.0-py3-none-any.whl").write_bytes(b"wheel fixture")

    monkeypatch.setattr(namespace["subprocess"], "run", download)
    namespace["main"]()
    manifest = json.loads((tmp_path / "opt/everval-provenance/offline-wheels.json").read_text())
    assert recorded[0][-2:] == ["setuptools==75.1.0", "wheel==0.40.0"]
    assert manifest["requirements"] == recorded[0][-2:]
    assert manifest["evaluation_network"] == "none"
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(b"setuptools fixture").hexdigest()
    (tmp_path / "opt/everval-wheels/wheel-0.40.0-py3-none-any.whl").unlink()
    monkeypatch.setattr(namespace["subprocess"], "run", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="wheel count"):
        namespace["main"]()


def test_declared_backends_get_pinned_absent_only_fallbacks(prepare, monkeypatch):
    namespace = {"__name__": "wheelhouse_test"}
    exec(
        compile(prepare.wheelhouse_script("pytest-dev__pytest-11148"), "<wheelhouse>", "exec"),
        namespace,
    )
    metadata = namespace["metadata"]
    versions = {"setuptools": "75.1.0", "wheel": "0.44.0", "packaging": "23.1", "tomli": "2.0.1"}

    def version(name):
        if name not in versions:
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(metadata, "version", version)
    requirements = namespace["selected_requirements"]()
    assert "setuptools-scm==7.1.0" in requirements
    assert "typing-extensions==4.12.2" in requirements
    assert "packaging==23.1" in requirements
    assert "flit-core==3.9.0" not in requirements
    versions["typing-extensions"] = "4.5.0"
    assert "typing-extensions==4.5.0" in namespace["selected_requirements"]()
    assert "typing-extensions==4.12.2" not in namespace["selected_requirements"]()
    namespace["NEEDED_BACKENDS"] = ("flit-core",)
    requirements = namespace["selected_requirements"]()
    assert "flit-core==3.9.0" in requirements
    assert "setuptools-scm==7.1.0" not in requirements
    del versions["setuptools"]
    with pytest.raises(metadata.PackageNotFoundError):
        namespace["selected_requirements"]()


def test_required_backend_is_bound_to_task_recipe(prepare, tmp_path):
    assert prepare.required_backends("sphinx-doc__sphinx-11445") == ("flit-core",)
    assert "NEEDED_BACKENDS = ('flit-core',)" in prepare.wheelhouse_script(
        "sphinx-doc__sphinx-11445"
    )
    source = tmp_path / "pytest-dev__pytest-11148"
    _shared_recipe_fixture(source, ("idna==3.10",))
    _, scripts, provenance = prepare.adapt(source)
    assert "NEEDED_BACKENDS = ('setuptools-scm',)" in scripts["prepare-wheelhouse.py"]
    assert provenance["offline_build_wheels"]["required_backends"] == ["setuptools-scm"]
    assert provenance["build_scripts"]["prepare-wheelhouse.py"] == prepare.digest(
        scripts["prepare-wheelhouse.py"].encode()
    )


def test_sphinx_adaptation_rejects_an_unverified_recipe(prepare, tmp_path):
    source = tmp_path / "sphinx-doc__sphinx-11445"
    _shared_recipe_fixture(source, ("idna==3.10",))
    with pytest.raises(ValueError, match="recipe differs"):
        prepare.adapt(source)


def test_pinned_backend_artifact_hash_is_checked_before_manifest(prepare, tmp_path, monkeypatch):
    namespace = {"__name__": "wheelhouse_test"}
    exec(
        compile(prepare.wheelhouse_script("sphinx-doc__sphinx-11445"), "<wheelhouse>", "exec"),
        namespace,
    )
    namespace["Path"] = lambda value: tmp_path / value.lstrip("/")
    namespace["selected_requirements"] = lambda: ["flit-core==3.9.0"]
    directory = tmp_path / "opt/everval-wheels"
    directory.mkdir(parents=True)
    pin = namespace["FALLBACKS"]["flit-core"]
    (directory / pin["filename"]).write_bytes(b"corrupt fixture")
    monkeypatch.setattr(namespace["subprocess"], "run", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="wheel hash differs"):
        namespace["main"]()
    assert not (tmp_path / "opt/everval-provenance/offline-wheels.json").exists()
    pin["sha256"] = hashlib.sha256(b"corrupt fixture").hexdigest()
    namespace["main"]()
    result = json.loads((tmp_path / "opt/everval-provenance/offline-wheels.json").read_text())
    assert result["verified_pinned_backend_artifacts"][0]["sha256"] == pin["sha256"]


@pytest.fixture
def cached_preparation(prepare, tmp_path, monkeypatch):
    task = {"instance_id": "sphinx-doc__sphinx-11445", "task_sha256": "1" * 64}
    earlier = {"preparation_status": "failed", "reason": "retained earlier attempt"}
    existing = {
        "selection_sha256": prepare.SELECTION_SHA,
        "task_sha256": task["task_sha256"],
        "preparation_status": "ready",
        "cleanup_complete": True,
        "image_id": "sha256:" + "2" * 64,
        "artifacts": "runtime-builds/old",
        "previous_preparations": [earlier],
    }
    directory = tmp_path / existing["artifacts"]
    directory.mkdir(parents=True)
    (directory / "build.log").write_bytes(b"retained original build evidence")
    (directory / "preparation.json").write_text(json.dumps(existing))
    prepare.atomic_json(tmp_path / "runtime-images.json", {task["instance_id"]: existing})
    monkeypatch.setattr(prepare, "selection", lambda root: {"tasks": [task]})
    inspected = []

    def inspect(command, **kwargs):
        inspected.append(command)
        return SimpleNamespace(returncode=0, stdout=existing["image_id"] + " arm64", stderr="")

    monkeypatch.setattr(prepare.subprocess, "run", inspect)
    arguments = SimpleNamespace(
        command="build",
        instance_id=[task["instance_id"]],
        timeout_seconds=60,
        docker_host="unix:///tmp/test-docker.sock",
        retry_failed=False,
        rebuild=False,
    )
    return task, existing, arguments, inspected


def test_explicit_rebuild_retains_history_and_original_artifacts(
    prepare, tmp_path, monkeypatch, cached_preparation
):
    task, existing, arguments, inspected = cached_preparation
    arguments.rebuild = True
    old_artifacts = {
        file: file.read_bytes() for file in (tmp_path / existing["artifacts"]).iterdir()
    }
    calls = []

    def build(root, selected, prefix, timeout):
        calls.append(selected["instance_id"])
        assert inspected == [
            [
                "docker",
                "--host",
                arguments.docker_host,
                "image",
                "inspect",
                "--format",
                "{{.Id}} {{.Architecture}}",
                existing["image_id"],
            ]
        ]
        assert not (root / "preparation-blocked.json").exists()
        record = {
            "preparation_status": "ready",
            "cleanup_complete": True,
            "image_id": "sha256:" + "3" * 64,
            "ownership_token": "fresh-build",
            "artifacts": "runtime-builds/new",
        }
        directory = root / record["artifacts"]
        directory.mkdir(parents=True)
        prepare.immutable(directory / "preparation.json", prepare.json_bytes(record))
        prepare.immutable(
            root / "preparation-blocked.json",
            prepare.json_bytes({"ownership_token": record["ownership_token"]}),
        )
        return record

    monkeypatch.setattr(prepare, "build_one", build)
    assert prepare.execute(arguments, tmp_path) == 0
    current = json.loads((tmp_path / "runtime-images.json").read_text())[task["instance_id"]]
    assert calls == [task["instance_id"]]
    assert current["image_id"] != existing["image_id"]
    assert current["previous_preparations"] == [
        *existing["previous_preparations"],
        {key: value for key, value in existing.items() if key != "previous_preparations"},
    ]
    assert all(file.read_bytes() == original for file, original in old_artifacts.items())
    assert (tmp_path / current["artifacts"] / "preparation.json").exists()
    assert not (tmp_path / "preparation-blocked.json").exists()


def test_rebuild_requires_explicit_selection_before_any_preparation(prepare, tmp_path, monkeypatch):
    monkeypatch.setattr(
        prepare,
        "selection",
        lambda root: pytest.fail("Missing selector must fail before preparation"),
    )
    arguments = SimpleNamespace(command="build", instance_id=None, rebuild=True)
    with pytest.raises(ValueError, match="--rebuild requires.*--instance-id"):
        prepare.execute(arguments, tmp_path)
    assert not (tmp_path / "runtime-images.json").exists()


@pytest.mark.parametrize("retry_failed", [False, True])
def test_ready_image_reuse_is_unchanged_without_rebuild(
    prepare, tmp_path, monkeypatch, cached_preparation, retry_failed
):
    task, existing, arguments, inspected = cached_preparation
    arguments.retry_failed = retry_failed
    before = (tmp_path / "runtime-images.json").read_bytes()
    monkeypatch.setattr(
        prepare, "build_one", lambda *args: pytest.fail("Ready images require explicit --rebuild")
    )
    assert prepare.execute(arguments, tmp_path) == 0
    assert len(inspected) == 1
    assert (tmp_path / "runtime-images.json").read_bytes() == before


@pytest.mark.parametrize(
    "returncode,identity",
    [(1, ""), (0, "sha256:" + "4" * 64 + " arm64"), (0, "sha256:" + "2" * 64 + " amd64")],
)
def test_rebuild_cannot_bypass_existing_image_identity(
    prepare, tmp_path, monkeypatch, cached_preparation, returncode, identity
):
    task, existing, arguments, inspected = cached_preparation
    arguments.rebuild = True
    before = (tmp_path / "runtime-images.json").read_bytes()
    monkeypatch.setattr(
        prepare.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=identity, stderr=""),
    )
    monkeypatch.setattr(
        prepare, "build_one", lambda *args: pytest.fail("Image mismatch must block rebuild")
    )
    with pytest.raises(ValueError, match="Cached image is missing or differs"):
        prepare.execute(arguments, tmp_path)
    assert (tmp_path / "runtime-images.json").read_bytes() == before


@pytest.mark.parametrize("filename", ["campaign.json", "preparation-blocked.json"])
def test_rebuild_cannot_bypass_frozen_or_unresolved_state(
    prepare, tmp_path, monkeypatch, cached_preparation, filename
):
    task, existing, arguments, inspected = cached_preparation
    arguments.rebuild = True
    (tmp_path / filename).write_text("{}")
    monkeypatch.setattr(
        prepare, "build_one", lambda *args: pytest.fail("Blocked state must prevent rebuild")
    )
    with pytest.raises(ValueError, match="frozen|blocked"):
        prepare.execute(arguments, tmp_path)
    assert inspected == []


def test_cli_rebuild_reaches_locked_execution_with_explicit_selector(
    prepare, tmp_path, monkeypatch
):
    arguments = [
        "prepare.py",
        "--output-dir",
        str(tmp_path),
        "build",
        "--instance-id",
        "sphinx-doc__sphinx-11445",
        "--rebuild",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(prepare, "private_root", lambda value: tmp_path)

    def execute(parsed, root):
        assert parsed.rebuild is True
        assert parsed.retry_failed is False
        assert parsed.instance_id == ["sphinx-doc__sphinx-11445"]
        with pytest.raises(ValueError, match="owns"), prepare.preparation_lock(root):
            pytest.fail("CLI execution must retain the preparation lock")
        return 0

    monkeypatch.setattr(prepare, "execute", execute)
    assert prepare.main() == 0
