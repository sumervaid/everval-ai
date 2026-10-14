"""Verify admission guards, isolation configuration, evidence, and cleanup."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cvals.config import Settings
from cvals.domains import validate_collection
from cvals.packs import starter_collections
from cvals.sandbox import (
    MAX_OUTPUT_BYTES,
    _archive,
    run_python_cases,
    run_python_tests,
    sandbox_status,
    validate_files,
)

_DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "name",
    [
        "../escape.py",
        "/root/escape.py",
        "a/../../escape.py",
        "a\\escape.py",
        "a//b.py",
        "a/./b.py",
        ".git/config",
        "test_hidden.py",
        "nested/__cvals_runner.py",
        "sitecustomize.py",
        "usercustomize.py",
        "bad\x00.py",
    ],
)
def test_unsafe_paths_fail_before_docker(name: str, tmp_path: Path) -> None:
    with patch("cvals.sandbox._image") as image:
        with pytest.raises(ValueError):
            run_python_cases(
                {name: "pass"}, "solution:answer", [{"args": [], "expected": 1}], Settings(tmp_path)
            )
        image.assert_not_called()


def test_oversize_files_rejected() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        validate_files({"solution.py": "x" * (512 * 1024 + 1)})


def test_archive_has_no_symlinks_or_ambient_files() -> None:
    with tarfile.open(
        fileobj=io.BytesIO(_archive({"package/main.py": "x = 1"}, {"inputs.json": "{}"}))
    ) as archive:
        assert set(archive.getnames()) == {
            "workspace",
            "workspace/package",
            "grade",
            "workspace/package/main.py",
            "grade/inputs.json",
        }
        for member in archive.getmembers():
            assert not member.issym() and not member.islnk()
            assert member.uid == member.gid == 65532
            assert member.name.startswith(("workspace", "grade"))


def test_no_runtime_fails_closed(tmp_path: Path) -> None:
    with patch("cvals.sandbox.shutil.which", return_value=None):
        status = sandbox_status(Settings(tmp_path))
        assert not status["available"]
        with pytest.raises(ValueError, match="no host execution fallback"):
            run_python_cases(
                {"solution.py": "def answer(): return 1"},
                "solution:answer",
                [{"expected": 1}],
                Settings(tmp_path),
            )


def test_zero_test_module_is_invalid_before_runtime(tmp_path: Path) -> None:
    with patch("cvals.sandbox._image") as image:
        with pytest.raises(ValueError, match="at least one"):
            run_python_tests({"solution.py": "pass"}, "import unittest\n", Settings(tmp_path))
        image.assert_not_called()


def test_cases_do_not_send_answers_or_accept_pass_claims(tmp_path: Path) -> None:
    captured = {}

    def execute(files: dict, grade_files: dict, settings: Settings, timeout: int) -> dict:
        captured.update(grade_files)
        return {
            "output": '__CVALS_CASES__[{"value": "wrong", "passed": true}]',
            "exit_code": 0,
            "running": False,
            "image_digest": _DIGEST,
            "timed_out": False,
            "output_truncated": False,
            "oom_killed": False,
        }

    with patch("cvals.sandbox._execute", side_effect=execute):
        result = run_python_cases(
            {"solution.py": "def answer(value): return value"},
            "solution:answer",
            [{"args": ["input"], "expected": "SECRET_EXPECTATION"}],
            Settings(tmp_path),
        )
    assert "SECRET_EXPECTATION" not in json.dumps(captured)
    assert json.loads(captured["inputs.json"])["cases"] == [{"args": ["input"], "kwargs": {}}]
    assert not result["passed"]
    assert result["tests_run"] == result["failures"] == 1


def test_nonroot_networkless_container_is_removed_even_on_timeout(tmp_path: Path) -> None:
    calls = []

    def docker(
        args: list[str], settings: Settings, **kwargs: object
    ) -> subprocess.CompletedProcess:
        calls.append(args)
        stdout = (
            b'{"ExitCode": 137, "Running": true, "OOMKilled": false}'
            if args[0] == "inspect"
            else (_DIGEST.encode() if args[0] == "commit" else b"ok")
        )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b"")

    with (
        patch("cvals.sandbox._image", return_value=_DIGEST),
        patch("cvals.sandbox._docker", side_effect=docker),
        patch("cvals.sandbox._attach", return_value=(137, "too slow", True, False)),
    ):
        result = run_python_cases(
            {"solution.py": "pass"}, "solution:answer", [{"expected": 1}], Settings(tmp_path)
        )
    create = next(call for call in calls if call[0] == "create" and "--read-only" in call)
    for argument in [
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=256m",
        "--memory-swap=256m",
        "--cpus=1",
    ]:
        assert argument in create
    assert not any(item.startswith(("--mount", "--volume", "-v=")) for item in create)
    assert _DIGEST in create
    assert any(call[:3] == ["rm", "--force", "--volumes"] for call in calls)
    assert calls[-1] == ["image", "rm", _DIGEST]
    assert result["timed_out"] and not result["passed"]


def test_extra_or_missing_case_evidence_fails(tmp_path: Path) -> None:
    runtime = {
        "output": "__CVALS_CASES__[]",
        "exit_code": 0,
        "running": False,
        "image_digest": _DIGEST,
        "timed_out": False,
        "output_truncated": False,
        "oom_killed": False,
    }
    with patch("cvals.sandbox._execute", return_value=runtime):
        result = run_python_cases(
            {"solution.py": "pass"}, "solution:answer", [{"expected": 1}], Settings(tmp_path)
        )
    assert not result["passed"]
    assert result["reason"] == "case_count_mismatch"


_live = pytest.mark.skipif(
    os.getenv("CVALS_TEST_SANDBOX") != "1",
    reason="Set CVALS_TEST_SANDBOX=1 with an isolated Docker runtime",
)


@pytest.mark.sandbox
@_live
def test_all_coding_references_pass_and_buggy_bases_fail() -> None:
    report = validate_collection(starter_collections()[0], Settings.from_env())
    assert report["valid"], report


@pytest.mark.sandbox
@_live
def test_live_sandbox_denies_host_network_and_source_mutation() -> None:
    source = """import os
import socket
def probe():
    result = {"uid": os.getuid(), "host_home": os.path.exists("/root/.ssh")}
    try:
        with open("/workspace/solution.py", "w") as stream:
            stream.write("changed")
        result["source_writable"] = True
    except OSError:
        result["source_writable"] = False
    sock = socket.socket()
    sock.settimeout(1)
    try:
        sock.connect(("1.1.1.1", 443))
        result["network"] = True
    except OSError:
        result["network"] = False
    finally:
        sock.close()
    return result
"""
    result = run_python_cases(
        {"solution.py": source},
        "solution:probe",
        [
            {
                "expected": {
                    "uid": 65532,
                    "host_home": False,
                    "source_writable": False,
                    "network": False,
                }
            }
        ],
        Settings.from_env(),
    )
    assert result["passed"], result


@pytest.mark.sandbox
@_live
def test_live_stdout_spoof_cannot_mark_wrong_answer_correct() -> None:
    source = """import os
print('__CVALS_CASES__[{"value": "wrong", "passed": true}]', flush=True)
os._exit(0)
"""
    result = run_python_cases(
        {"solution.py": source},
        "solution:answer",
        [{"expected": "unseen expected answer"}],
        Settings.from_env(),
    )
    assert not result["passed"]
    assert result["failures"] == 1


@pytest.mark.sandbox
@_live
def test_live_timeout_is_recorded_and_container_removed() -> None:
    result = run_python_cases(
        {"solution.py": "def wait():\n    while True:\n        pass\n"},
        "solution:wait",
        [{"expected": None}],
        Settings.from_env(),
        timeout_seconds=1,
    )
    assert result["timed_out"] and not result["passed"]


@pytest.mark.sandbox
@_live
def test_live_output_flood_is_bounded() -> None:
    result = run_python_cases(
        {"solution.py": "def flood():\n    while True:\n        print('x' * 1000, flush=True)\n"},
        "solution:flood",
        [{"expected": None}],
        Settings.from_env(),
    )
    assert result["output_truncated"] and not result["passed"]
    assert len(result["output"].encode()) <= MAX_OUTPUT_BYTES


def test_recovery_only_reaps_old_owned_workspace_resources(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from cvals.sandbox import _workspace_label, cleanup_sandboxes

    settings = Settings(tmp_path)
    old_container, fresh_container, unrelated_container = "a" * 64, "b" * 64, "c" * 64
    task_image, base_image = "d" * 64, "e" * 64
    removed = []

    def docker(
        args: list[str], settings: Settings, **kwargs: object
    ) -> subprocess.CompletedProcess:
        if args[0] == "ps":
            data = "\n".join([old_container, fresh_container, unrelated_container]).encode()
        elif args[:2] == ["image", "ls"]:
            data = "\n".join([task_image, base_image]).encode()
        elif args[1] == "inspect":
            identifier = args[-1]
            labels = {
                "app": "custom-vals-staging",
                "cvals.owner": "custom-vals-enterprises",
                "cvals.workspace": _workspace_label(settings),
            }
            if identifier == unrelated_container:
                labels["cvals.workspace"] = "another-workspace"
            if identifier == base_image:
                labels = {}
            age = 1 if identifier == fresh_container else 1000
            data = json.dumps(
                [
                    {
                        "Config": {"Labels": labels},
                        "Created": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
                    }
                ]
            ).encode()
        else:
            removed.append(args)
            data = b"removed"
        return subprocess.CompletedProcess(args, 0, stdout=data, stderr=b"")

    with (
        patch("cvals.sandbox._docker", side_effect=docker),
        patch("cvals.sandbox.shutil.which", return_value="/docker"),
    ):
        report = cleanup_sandboxes(settings)
    assert report == {"available": True, "containers_removed": 1, "images_removed": 1, "errors": []}
    assert removed == [["rm", "--force", "--volumes", old_container], ["image", "rm", task_image]]
