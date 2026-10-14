"""History compilation is syntax-only and quarantines unsupported test semantics."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cvals.config import Settings
from cvals.domains import validate_collection
from cvals.domains.mining import extract_function_cases, mine_repository


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _history(path: Path, *, source_preamble: str = "") -> str:
    path.mkdir()
    _git(path, "init", "--quiet")
    _git(path, "config", "user.email", "fixture@example.test")
    _git(path, "config", "user.name", "History fixture")
    (path / "solution.py").write_text(
        source_preamble + "def total(values):\n    return len(values)\n"
    )
    (path / "test_solution.py").write_text(
        "from solution import total\n\ndef test_empty():\n    assert total([]) == 0\n"
    )
    _git(path, "add", ".")
    _git(path, "commit", "--quiet", "-m", "Initial total helper")
    base = _git(path, "rev-parse", "HEAD")
    (path / "solution.py").write_text(
        source_preamble + "def total(values):\n    return sum(values)\n"
    )
    (path / "test_solution.py").write_text(
        "from solution import total\n\ndef test_empty():\n    assert total([]) == 0\n"
        "\ndef test_values():\n    assert total([2, 4]) == 6\n    assert total([-1, 1]) == 0\n"
    )
    _git(path, "add", ".")
    _git(path, "commit", "--quiet", "-m", "Correct numeric totals")
    return base


def test_compile_history_without_executing_source_or_exposing_future(tmp_path: Path) -> None:
    repository = tmp_path / "history"
    marker = tmp_path / "executed"
    preamble = f"open({str(marker)!r}, 'w').write('must not happen')\n"
    base = _history(repository, source_preamble=preamble)
    result = mine_repository(str(repository))
    assert result["counts"] == {"inspected": 2, "accepted": 1, "rejected": 1, "scenarios": 1}
    assert not marker.exists()
    assert result["executed_repository_code"] is False
    scenario = result["collection"].scenarios[0]
    assert scenario.provenance["base_commit"] == base
    assert scenario.provenance["validation_status"] == "not_run"
    assert "return len(values)" in scenario.inputs["files"]["solution.py"]
    assert "return sum(values)" in scenario.reference_output["files"]["solution.py"]
    assert "test_solution.py" not in scenario.inputs["files"]
    assert ".git" not in scenario.inputs["files"]
    assert scenario.checks[0].expected["entrypoint"] == "solution:total"
    assert len(scenario.checks[0].expected["cases"]) == 3
    assert result["candidates"][0]["status"] == "accepted"


def test_quarantine_candidate_with_unsupported_test_setup(tmp_path: Path) -> None:
    repository = tmp_path / "history"
    _history(repository)
    (repository / "solution.py").write_text("def total(values):\n    return sum(values) + 1\n")
    (repository / "test_solution.py").write_text(
        "from solution import total\n\ndef test_values():\n"
        "    data = [2, 4]\n    assert total(data) == 7\n"
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "Test with runtime fixture")
    result = mine_repository(str(repository), limit=1)
    assert result["collection"] is None
    assert result["counts"]["rejected"] == 1
    assert "Only assert" in result["candidates"][0]["reasons"][0]


def test_extract_pytest_and_unittest_literal_forms() -> None:
    source = """import unittest
import arithmetic as mathlib
from arithmetic import add as plus

def test_pytest():
    assert plus(1, 2) == 3
    assert 0 == mathlib.add(-1, 1)

class AdditionTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(mathlib.add(2, b=3), 5)
"""
    groups = extract_function_cases(source, "test_arithmetic.py")
    assert set(groups) == {"arithmetic:add"}
    assert groups["arithmetic:add"][0]["expected"] == 3
    assert groups["arithmetic:add"][2]["kwargs"] == {"b": 3}


@pytest.mark.parametrize(
    "body",
    [
        "assert f([1]) > 0",
        "assert f([1]) == sum([1])",
        "assert f(values) == 1",
        "assert f(*[1]) == 1",
        "assert f((1, 2)) == 1",
        "assert f([1]) is None",
        "for value in [1]:\n        assert f(value) == 1",
        "pass",
    ],
)
def test_reject_unsupported_assertions_without_partial_extraction(body: str) -> None:
    source = (
        "from solution import f\n\ndef test_value():\n    assert f([]) == 0\n    " + body + "\n"
    )
    with pytest.raises(ValueError):
        extract_function_cases(source, "test_solution.py")


@pytest.mark.parametrize(
    "source",
    [
        "from solution import *\ndef test_a():\n    assert f(1) == 1\n",
        "from .solution import f\ndef test_a():\n    assert f(1) == 1\n",
        "from solution import f\n@decorator\ndef test_a():\n    assert f(1) == 1\n",
        "from solution import f\ndef test_a(fixture):\n    assert f(1) == 1\n",
        "from solution import f\nopen('/tmp/never', 'w')\ndef test_a():\n    assert f(1) == 1\n",
    ],
)
def test_reject_implicit_or_executable_test_setup(source: str) -> None:
    with pytest.raises(ValueError):
        extract_function_cases(source, "test_solution.py")


@pytest.mark.sandbox
@pytest.mark.skipif(os.getenv("CVALS_TEST_SANDBOX") != "1", reason="Requires isolated Docker")
def test_mined_fix_passes_and_historical_base_fails(tmp_path: Path) -> None:
    repository = tmp_path / "history"
    _history(repository)
    result = mine_repository(str(repository))
    report = validate_collection(result["collection"], Settings.from_env())
    assert report["valid"], report
    assert report["scenarios"][0]["checks"][0]["actual"]["tests_run"] == 3
