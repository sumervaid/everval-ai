"""Fresh Docker verifiers for small, dependency-free Python submissions.

This boundary contains resource and filesystem effects. Python tests share an
interpreter with a submission, so these results are not an attestation against
deliberately hostile grader manipulation. Treat imported tests as untrusted too.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import selectors
import shutil
import subprocess
import tarfile
import time
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

MAX_FILES = 128
MAX_FILE_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024

_RUNNER = r"""
import contextlib
import io
import json
import sys
import unittest

sys.path.insert(0, "/workspace")
class Evidence(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"id": test.id(), "status": "passed"})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append({"id": test.id(), "status": "failed"})
    def addError(self, test, err):
        super().addError(test, err)
        self.cases.append({"id": test.id(), "status": "error"})
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.cases.append({"id": test.id(), "status": "skipped"})

scope = {"__name__": "__cvals_tests__", "__file__": "/grade/tests.py"}
with open("/grade/tests.py", encoding="utf-8") as stream:
    source = stream.read()
exec(compile(source, "/grade/tests.py", "exec"), scope)
suite = unittest.TestSuite()
for value in list(scope.values()):
    if isinstance(value, type) and issubclass(value, unittest.TestCase):
        if value is not unittest.TestCase:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(value))
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=Evidence).run(suite)
payload = {"tests_run": result.testsRun, "failures": len(result.failures),
           "errors": len(result.errors), "skipped": len(result.skipped),
           "test_cases": result.cases,
           "passed": result.wasSuccessful() and result.testsRun > 0 and not result.skipped}
print("\n__CVALS_RESULT__" + json.dumps(payload, separators=(",", ":")), flush=True)
sys.exit(0 if payload["passed"] else 1)
"""


def validate_files(files: dict[str, str], *, repository_mode: bool = False) -> None:
    """Accept text files only; no paths may escape or replace the verifier."""
    file_limit = 2048 if repository_mode else MAX_FILES
    byte_limit = 16 * 1024 * 1024 if repository_mode else MAX_BUNDLE_BYTES
    if not isinstance(files, dict) or not files or len(files) > file_limit:
        raise ValueError(f"A submission needs 1–{file_limit} text files")
    total = 0
    for name, content in files.items():
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError("Submission files must map paths to UTF-8 text")
        path = PurePosixPath(name)
        if (
            not name
            or len(name) > 240
            or "\\" in name
            or "\x00" in name
            or path.is_absolute()
            or any(part in {"..", ".", ".git", "__pycache__"} for part in name.split("/"))
            or any(not part for part in name.split("/"))
            or any(part.startswith("__cvals") for part in path.parts)
            or (not repository_mode and path.name.startswith("test_"))
            or path.name in {"sitecustomize.py", "usercustomize.py"}
        ):
            raise ValueError(f"Unsafe or reserved submission path: {name!r}")
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ValueError(f"Submission file exceeds {MAX_FILE_BYTES} bytes")
        total += size
    if total > byte_limit:
        raise ValueError(f"Submission exceeds {byte_limit} bytes")


def _test_count(tests: str) -> int:
    if not isinstance(tests, str) or len(tests.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Hidden tests must be bounded Python source")
    try:
        tree = ast.parse(tests)
    except SyntaxError as exc:
        raise ValueError(f"Hidden tests contain invalid Python: {exc.msg}") from exc
    count = sum(
        1
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for method in node.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and method.name.startswith("test")
    )
    if not count:
        raise ValueError("Hidden tests must declare at least one unittest test method")
    return count


def _environment(settings: Any) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR"}}
    if settings.docker_host:
        env["DOCKER_HOST"] = settings.docker_host
    return env


def _docker(
    args: list[str], settings: Any, *, timeout: float = 15, payload: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["docker", *args],
            input=payload,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_environment(settings),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Docker is unavailable or did not respond before the deadline") from exc


def _image(settings: Any) -> str:
    if not shutil.which("docker"):
        raise ValueError("Python grading requires Docker; no host execution fallback is permitted")
    image = settings.sandbox_image
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise ValueError("A local sandbox image must be configured")
    result = _docker(["image", "inspect", "--format", "{{.Id}}", image], settings)
    digest = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(
            "The sandbox image is not available locally. Load an approved Python image "
            "into the configured Docker runtime; automatic image pulls are disabled."
        )
    return digest


def sandbox_status(settings: Any) -> dict[str, Any]:
    try:
        digest = _image(settings)
        return {"available": True, "image_digest": digest, "reason": None}
    except ValueError as exc:
        return {"available": False, "image_digest": None, "reason": str(exc)}


def _archive(files: dict[str, str], grade_files: dict[str, str]) -> bytes:
    stream = io.BytesIO()
    entries = {f"workspace/{name}": content for name, content in files.items()}
    entries.update({f"grade/{name}": content for name, content in grade_files.items()})
    directories = {"workspace", "grade"}
    for path in entries:
        directories.update(
            str(parent) for parent in PurePosixPath(path).parents if str(parent) != "."
        )
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(directories):
            info = tarfile.TarInfo(path)
            info.type = tarfile.DIRTYPE
            info.mode = 0o555
            info.uid = info.gid = 65532
            archive.addfile(info)
        for path, content in sorted(entries.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o444
            info.uid = info.gid = 65532
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def _attach(name: str, settings: Any, timeout: float) -> tuple[int, str, bool, bool]:
    process = subprocess.Popen(
        ["docker", "start", "--attach", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=_environment(settings),
    )
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + timeout
    timed_out = truncated = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                events = selector.select(min(remaining, 0.25))
                if not events:
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                available = MAX_OUTPUT_BYTES - len(output)
                output.extend(chunk[:available])
                if len(chunk) > available:
                    truncated = True
                    break
        if timed_out or truncated:
            process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
    return process.returncode, output.decode("utf-8", "replace"), timed_out, truncated


def run_python_tests(
    files: dict[str, str],
    tests: str,
    settings: Any,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Run arbitrary unittest source in a disposable resource-limited container.

    This compatibility mode shares a Python interpreter with the submission.
    Use function cases when expected-answer secrecy and trusted comparisons are
    required; arbitrary unittest evidence is not resistant to grader manipulation.
    """
    validate_files(files)
    expected_count = _test_count(tests)
    runtime = _execute(files, {"runner.py": _RUNNER, "tests.py": tests}, settings, timeout_seconds)
    result: dict[str, Any] = {
        **runtime,
        "passed": False,
        "tests_run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "test_cases": [],
        "expected_tests": expected_count,
        "integrity": "unit-test process; not resistant to deliberate grader manipulation",
    }
    if runtime["timed_out"] or runtime["output_truncated"] or runtime["oom_killed"]:
        result["errors"] = 1
        result["reason"] = (
            "deadline"
            if runtime["timed_out"]
            else "output_limit"
            if runtime["output_truncated"]
            else "memory_limit"
        )
        return result
    lines = [line for line in runtime["output"].splitlines() if line.startswith("__CVALS_RESULT__")]
    if len(lines) != 1:
        result["errors"] = 1
        result["reason"] = "missing_or_ambiguous_test_evidence"
        return result
    try:
        evidence = json.loads(lines[0][len("__CVALS_RESULT__") :])
        for key in ("tests_run", "failures", "errors", "skipped"):
            if type(evidence.get(key)) is not int or evidence[key] < 0:
                raise ValueError("Invalid test counts")
        if not isinstance(evidence.get("test_cases"), list):
            raise ValueError("Invalid test records")
    except (ValueError, TypeError) as exc:
        raise ValueError("Verifier produced malformed structured test evidence") from exc
    result.update(
        {key: evidence[key] for key in ("tests_run", "failures", "errors", "skipped", "test_cases")}
    )
    result["passed"] = (
        evidence.get("passed") is True
        and result["tests_run"] == expected_count
        and result["failures"] == result["errors"] == result["skipped"] == 0
        and len(result["test_cases"]) == expected_count
        and runtime["exit_code"] == 0
        and not runtime["running"]
    )
    if result["tests_run"] != expected_count:
        result["reason"] = "test_collection_mismatch"
    return result


_CASE_RUNNER = r"""
import importlib
import json
import sys
sys.path.insert(0, "/workspace")
with open("/grade/inputs.json", encoding="utf-8") as stream:
    spec = json.load(stream)
module_name, function_name = spec["entrypoint"].split(":")
function = getattr(importlib.import_module(module_name), function_name)
results = []
for case in spec["cases"]:
    try:
        value = function(*case.get("args", []), **case.get("kwargs", {}))
        json.dumps(value, allow_nan=False)
        results.append({"value": value})
    except Exception as exc:
        results.append({"exception": type(exc).__name__})
print("\n__CVALS_CASES__" + json.dumps(results, separators=(",", ":"), allow_nan=False), flush=True)
"""


def _execute(
    files: dict[str, str],
    grade_files: dict[str, str],
    settings: Any,
    timeout_seconds: int,
    *,
    repository_mode: bool = False,
) -> dict[str, Any]:
    validate_files(files, repository_mode=repository_mode)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise ValueError("The verifier deadline must be between 1 and 120 seconds")
    digest = _image(settings)
    name = f"cvals-verifier-{uuid4().hex}"
    stage_name = f"{name}-stage"
    created = staged = False
    task_image: str | None = None
    try:
        # Docker refuses cp into a read-only rootfs. Prepare a filesystem in a
        # never-started container, then run a separate read-only verifier from it.
        stage = _docker(
            [
                "create",
                "--name",
                stage_name,
                "--label",
                "app=custom-vals-staging",
                "--label",
                "cvals.owner=custom-vals-enterprises",
                "--label",
                f"cvals.workspace={_workspace_label(settings)}",
                "--pull=never",
                "--network=none",
                "--user=65532:65532",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                digest,
                "python",
                "-V",
            ],
            settings,
        )
        if stage.returncode:
            raise ValueError("Cannot create task staging container")
        staged = True
        copy = _docker(
            ["cp", "-", f"{stage_name}:/"], settings, payload=_archive(files, grade_files)
        )
        if copy.returncode:
            raise ValueError(
                f"Cannot transfer task: {copy.stderr.decode('utf-8', 'replace')[:1000]}"
            )
        commit = _docker(["commit", stage_name], settings, timeout=30)
        if commit.returncode:
            raise ValueError("Cannot seal task filesystem into a verifier image")
        task_image = commit.stdout.decode("utf-8", "replace").strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", task_image):
            task_image = None
            raise ValueError("Docker returned an invalid task image digest")
        removal = _docker(["rm", "--force", "--volumes", stage_name], settings)
        if removal.returncode:
            raise ValueError("Cannot remove the task staging container")
        staged = False
        create = _docker(
            [
                "create",
                "--name",
                name,
                "--label",
                "app=custom-vals-verifier",
                "--label",
                "cvals.owner=custom-vals-enterprises",
                "--label",
                f"cvals.workspace={_workspace_label(settings)}",
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
                "--ulimit=nofile=128:128",
                "--ulimit=core=0:0",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--workdir=/workspace",
                "--env=PYTHONDONTWRITEBYTECODE=1",
                "--env=PYTHONHASHSEED=0",
                "--log-driver=none",
                task_image,
                "python",
                "-I",
                "-B",
                "/grade/runner.py",
            ],
            settings,
        )
        if create.returncode:
            raise ValueError(
                f"Cannot create isolated verifier: {create.stderr.decode('utf-8', 'replace')[:1000]}"
            )
        created = True
        _, output, timed_out, truncated = _attach(name, settings, timeout_seconds)
        state = _docker(["inspect", "--format", "{{json .State}}", name], settings)
        if state.returncode:
            raise ValueError("Docker could not report the verifier exit state")
        try:
            container_state = json.loads(state.stdout)
        except (ValueError, TypeError) as exc:
            raise ValueError("Docker returned malformed verifier state") from exc
        return {
            "output": output,
            "image_digest": digest,
            "task_image_digest": task_image,
            "timed_out": timed_out,
            "output_truncated": truncated,
            "oom_killed": bool(container_state.get("OOMKilled")),
            "exit_code": container_state.get("ExitCode"),
            "running": bool(container_state.get("Running")),
        }
    finally:
        errors = []
        for container_name, exists in ((name, created), (stage_name, staged)):
            if exists:
                cleanup = _docker(["rm", "--force", "--volumes", container_name], settings)
                if cleanup.returncode:
                    errors.append(container_name)
        if task_image is not None:
            cleanup = _docker(["image", "rm", "--no-prune", task_image], settings)
            if cleanup.returncode:
                errors.append(task_image)
        if errors:
            raise ValueError(f"Verifier cleanup failed; inspect: {', '.join(errors)}")


def _same_json(actual: Any, expected: Any) -> bool:
    # JSON distinguishes booleans from numbers; Python equality alone does not.
    if type(actual) is not type(expected):
        return (
            type(actual) in {int, float} and type(expected) in {int, float} and actual == expected
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _same_json(a, b) for a, b in zip(actual, expected, strict=True)
        )
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _same_json(actual[key], expected[key]) for key in actual
        )
    return bool(actual == expected)


def run_python_cases(
    files: dict[str, str],
    entrypoint: str,
    cases: list[dict[str, Any]],
    settings: Any,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Run pure-function cases with expected answers kept outside the container.

    A submission can inspect the function inputs, but receives neither expected
    values nor reference code. Container output is data and cannot declare pass.
    The host compares each result with the immutable evaluator-only expectation.
    """
    if not isinstance(entrypoint, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*",
        entrypoint,
    ):
        raise ValueError("Function entrypoint must have the form module:function")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError("Declare between 1 and 1000 function cases")
    public_cases = []
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "args", "kwargs", "expected", "raises"}:
            raise ValueError("Cases use id, args, kwargs, expected, or raises")
        if ("expected" in case) == ("raises" in case):
            raise ValueError("Each case must declare exactly one expected value or exception")
        if not isinstance(case.get("args", []), list) or not isinstance(
            case.get("kwargs", {}), dict
        ):
            raise ValueError("Case arguments must be a list and keyword arguments an object")
        if "raises" in case and (
            not isinstance(case["raises"], str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", case["raises"])
        ):
            raise ValueError("Expected exceptions must be Python exception type names")
        public_cases.append({"args": case.get("args", []), "kwargs": case.get("kwargs", {})})
    try:
        spec = json.dumps({"entrypoint": entrypoint, "cases": public_cases}, allow_nan=False)
        bounded = json.dumps(cases, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Function cases must contain finite JSON values") from exc
    if len(spec.encode()) > MAX_FILE_BYTES or len(bounded.encode()) > MAX_BUNDLE_BYTES:
        raise ValueError("Function cases exceed the verifier size limit")
    runtime = _execute(
        files, {"runner.py": _CASE_RUNNER, "inputs.json": spec}, settings, timeout_seconds
    )
    result: dict[str, Any] = {
        **runtime,
        "passed": False,
        "tests_run": 0,
        "expected_tests": len(cases),
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "test_cases": [],
        "integrity": "Host compares JSON values; expected outputs remain outside the container",
    }
    if runtime["timed_out"] or runtime["output_truncated"] or runtime["oom_killed"]:
        result["errors"] = 1
        result["reason"] = (
            "deadline"
            if runtime["timed_out"]
            else "output_limit"
            if runtime["output_truncated"]
            else "memory_limit"
        )
        return result
    lines = [line for line in runtime["output"].splitlines() if line.startswith("__CVALS_CASES__")]
    if len(lines) != 1 or runtime["exit_code"] != 0 or runtime["running"]:
        result["errors"] = 1
        result["reason"] = "missing_or_ambiguous_case_evidence"
        return result
    try:
        actuals = json.loads(lines[0][len("__CVALS_CASES__") :])
    except ValueError:
        actuals = None
    if not isinstance(actuals, list) or len(actuals) != len(cases):
        result["errors"] = 1
        result["reason"] = "case_count_mismatch"
        return result
    for index, (case, actual) in enumerate(zip(cases, actuals, strict=True)):
        expected = (
            {"exception": case["raises"]} if "raises" in case else {"value": case["expected"]}
        )
        passed = _same_json(actual, expected)
        result["test_cases"].append(
            {
                "id": str(case.get("id", f"case-{index + 1}")),
                "status": "passed" if passed else "failed",
                "expected": expected,
                "actual": actual,
            }
        )
        result["failures"] += int(not passed)
    result["tests_run"] = len(cases)
    result["passed"] = result["failures"] == 0
    return result


def _workspace_label(settings: Any) -> str:
    import hashlib

    return hashlib.sha256(str(settings.data_dir.expanduser().absolute()).encode()).hexdigest()[:24]


def cleanup_sandboxes(settings: Any, min_age_seconds: int = 300) -> dict[str, Any]:
    """Reap stale resources owned by this workspace after an interrupted worker.

    Images are removed without force, so Docker protects any remaining references.
    No global prune, base-image removal, or unrelated container cleanup is used.
    """
    from datetime import UTC, datetime

    if type(min_age_seconds) is not int or min_age_seconds < 300:
        raise ValueError("Sandbox recovery requires a minimum age of 300 seconds")
    report: dict[str, Any] = {
        "available": True,
        "containers_removed": 0,
        "images_removed": 0,
        "errors": [],
    }
    if not shutil.which("docker"):
        return {**report, "available": False}
    filters = [
        "--filter",
        "label=cvals.owner=custom-vals-enterprises",
        "--filter",
        f"label=cvals.workspace={_workspace_label(settings)}",
    ]
    now = datetime.now(UTC)
    for resource in ("container", "image"):
        try:
            args = (
                ["ps", "--all", "--quiet", *filters]
                if resource == "container"
                else ["image", "ls", "--quiet", *filters]
            )
            listing = _docker(args, settings)
            if listing.returncode:
                report["available"] = False
                report["errors"].append(f"Could not list workspace {resource} resources")
                continue
            for identifier in dict.fromkeys(listing.stdout.decode("ascii", "replace").split()):
                if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{12,64}", identifier):
                    continue
                inspected = _docker([resource, "inspect", identifier], settings)
                if inspected.returncode:
                    continue
                try:
                    detail = json.loads(inspected.stdout)[0]
                    labels = detail["Config"]["Labels"] or {}
                    expected_app = {"custom-vals-staging", "custom-vals-verifier"}
                    if (
                        labels.get("cvals.owner") != "custom-vals-enterprises"
                        or labels.get("cvals.workspace") != _workspace_label(settings)
                        or labels.get("app") not in expected_app
                    ):
                        continue
                    created_at = datetime.fromisoformat(detail["Created"].replace("Z", "+00:00"))
                    if (now - created_at).total_seconds() < min_age_seconds:
                        continue
                except (ValueError, TypeError, KeyError, IndexError):
                    report["errors"].append(
                        f"Could not verify ownership and age of {resource} {identifier}"
                    )
                    continue
                remove = (
                    ["rm", "--force", "--volumes", identifier]
                    if resource == "container"
                    else ["image", "rm", "--no-prune", identifier]
                )
                removed = _docker(remove, settings)
                if removed.returncode:
                    report["errors"].append(f"Could not remove stale {resource} {identifier}")
                else:
                    key = "containers_removed" if resource == "container" else "images_removed"
                    report[key] += 1
        except ValueError as exc:
            report["available"] = False
            report["errors"].append(str(exc))
    return report


# Repository tests deliberately run inside the candidate interpreter. This mode
# protects the host and keeps private tests out of agent inputs, but cannot prove
# grader integrity against a candidate that intentionally tampers with pytest.
_PYTEST_RUNNER = r"""
import json
import os
import sys
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
import pytest
sys.path[:0] = ["/workspace", "/workspace/src"]
with open("/grade/spec.json", encoding="utf-8") as stream:
    spec = json.load(stream)
class Evidence:
    def __init__(self):
        self.collected = []
        self.records = []
        self.collection_errors = 0
    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]
        if len(self.collected) > 5000:
            pytest.exit("Collection exceeds 5000 tests", returncode=4)
    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors += 1
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        self.records.append({
            "id": report.nodeid, "when": report.when, "outcome": report.outcome,
            "exception": call.excinfo.type.__name__ if call.excinfo else None,
            "xfail": hasattr(report, "wasxfail"),
        })
evidence = Evidence()
args = ["-q", "--tb=short", "--color=no", "-p", "no:cacheprovider",
        "-o", "addopts=", "--confcutdir=/workspace"]
for plugin in spec["plugins"]:
    args += ["-p", plugin]
args += spec["selectors"]
code = int(pytest.main(args, plugins=[evidence]))
print("\n__CVALS_PYTEST__" + json.dumps({
    "exit_code": code, "collected": evidence.collected,
    "collection_errors": evidence.collection_errors, "records": evidence.records,
}, separators=(",", ":")), flush=True)
sys.exit(code)
"""


def validate_repository_source(files: dict[str, str]) -> None:
    """Validate public source without permitting a private harness override."""
    validate_files(files, repository_mode=True)
    reserved = {
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "pyproject.toml",
        "conftest.py",
    }
    if any(
        PurePosixPath(path).name in reserved
        or PurePosixPath(path).name.startswith("test_")
        or PurePosixPath(path).name.endswith("_test.py")
        or any(part in {"test", "tests", ".github"} for part in PurePosixPath(path).parts)
        for path in files
    ):
        raise ValueError(
            "Candidate source cannot introduce private test paths or pytest configuration"
        )


def run_repository_tests(
    files: dict[str, str],
    hidden_files: dict[str, str],
    selectors: list[str],
    settings: Any,
    timeout_seconds: int = 60,
    plugins: list[str] | None = None,
) -> dict[str, Any]:
    """Run frozen pytest selections in a prepared local image without installing.

    Explicit trusted test files override no candidate paths: overlapping paths are
    rejected, including conftest/config overrides. Host-side reconciliation checks
    collection, phase evidence and the independently observed Docker exit status.
    """
    validate_files(files, repository_mode=True)
    validate_files(hidden_files, repository_mode=True)
    validate_repository_source(files)
    if set(files) & set(hidden_files):
        raise ValueError("Candidate files cannot replace private repository tests or configuration")
    if (
        not isinstance(selectors, list)
        or not 1 <= len(selectors) <= 500
        or len(set(selectors)) != len(selectors)
        or any(
            not isinstance(selector, str)
            or len(selector) > 600
            or not re.fullmatch(r"[A-Za-z0-9_./-]+(?:::[A-Za-z0-9_]+){1,2}", selector)
            or selector.split("::")[0] not in hidden_files
            for selector in selectors
        )
    ):
        raise ValueError("Repository tests require 1–500 explicit frozen test selectors")
    plugins = plugins or []
    if (
        not isinstance(plugins, list)
        or len(plugins) > 16
        or any(
            not isinstance(p, str) or not re.fullmatch(r"[A-Za-z_][\w.]{0,199}", p) for p in plugins
        )
    ):
        raise ValueError("Prepared pytest plugins must be explicit Python module names")
    runtime = _execute(
        {**files, **hidden_files},
        {
            "runner.py": _PYTEST_RUNNER,
            "spec.json": json.dumps({"selectors": selectors, "plugins": plugins}),
        },
        settings,
        timeout_seconds,
        repository_mode=True,
    )
    result: dict[str, Any] = {
        **runtime,
        "passed": False,
        "tests_run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "test_cases": [],
        "integrity": "same-process pytest; not resistant to deliberate grader manipulation",
        "infrastructure_error": None,
    }

    def invalid(reason: str) -> dict[str, Any]:
        result["errors"] = 1
        result["infrastructure_error"] = reason
        return result

    if (
        runtime["timed_out"]
        or runtime["output_truncated"]
        or runtime["oom_killed"]
        or runtime["running"]
    ):
        return invalid("Container exceeded a resource limit or remained running")
    lines = [line for line in runtime["output"].splitlines() if line.startswith("__CVALS_PYTEST__")]
    if len(lines) != 1:
        return invalid("Missing or ambiguous pytest evidence")
    try:
        payload = json.loads(lines[0][len("__CVALS_PYTEST__") :])
        collected = payload["collected"]
        records = payload["records"]
        if (
            not isinstance(collected, list)
            or not 1 <= len(collected) <= 5000
            or any(not isinstance(item, str) or len(item) > 2000 for item in collected)
            or len(set(collected)) != len(collected)
            or not isinstance(records, list)
            or len(records) > 15000
            or payload["collection_errors"] != 0
            or payload["exit_code"] not in {0, 1}
            or payload["exit_code"] != runtime["exit_code"]
        ):
            return invalid("Pytest did not produce complete, nonempty collection and exit evidence")
        for selector in selectors:
            if not any(item == selector or item.startswith(selector + "[") for item in collected):
                return invalid("A frozen selector collected no tests")
        if any(
            not any(item == selector or item.startswith(selector + "[") for selector in selectors)
            for item in collected
        ):
            return invalid("Pytest ran tests outside the frozen selection")
        grouped: dict[str, dict[str, Any]] = {item: {} for item in collected}
        for record in records:
            node, phase = record["id"], record["when"]
            if (
                node not in grouped
                or phase not in {"setup", "call", "teardown"}
                or phase in grouped[node]
            ):
                return invalid("Duplicate or unexpected pytest phase evidence")
            if record["outcome"] not in {"passed", "failed", "skipped"}:
                return invalid("Invalid pytest phase status")
            grouped[node][phase] = record
        for node, phases in grouped.items():
            if set(phases) != {"setup", "call", "teardown"}:
                return invalid("A test did not complete setup, execution and teardown")
            if any(phases[p]["outcome"] != "passed" for p in ("setup", "teardown")):
                return invalid("Test setup or teardown failed")
            call = phases["call"]
            if (
                any(record.get("xfail") for record in phases.values())
                or call["outcome"] == "skipped"
            ):
                result["skipped"] += 1
                return invalid(
                    "Skipped or expected-failure tests cannot establish benchmark validity"
                )
            if call["outcome"] == "failed" and call.get("exception") not in {
                "AssertionError",
                "Failed",
            }:
                return invalid(
                    "A test raised a non-assertion exception; dependency and runtime errors are not valid failures"
                )
            failed = call["outcome"] == "failed"
            result["failures"] += int(failed)
            result["test_cases"].append({"id": node, "status": "failed" if failed else "passed"})
        result["tests_run"] = len(collected)
        if (result["failures"] == 0) != (runtime["exit_code"] == 0):
            return invalid("Docker exit status contradicts pytest evidence")
        result["passed"] = result["failures"] == 0
        return result
    except (ValueError, KeyError, TypeError):
        return invalid("Malformed pytest evidence")
