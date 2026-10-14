"""Conservative compilation of local Python history into reviewable scenarios.

Only literal, direct function assertions are supported. This module reads Git
blobs and Python syntax; it never imports, checks out, builds, or executes them.
Acceptance here means extraction succeeded, not that a task is valid.
"""

from __future__ import annotations

import ast
import json
from pathlib import PurePosixPath
from typing import Any

from cvals.ingestion import inspect_repository, snapshot_repository
from cvals.models import Check, Collection, Scenario
from cvals.sandbox import validate_files


def _is_test(path: str) -> bool:
    location = PurePosixPath(path)
    return location.name.startswith("test_") or any(
        part in {"test", "tests"} for part in location.parts
    )


def _literal(node: ast.AST) -> Any:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise ValueError("Function arguments and expected results must be JSON literals") from exc

    def verify(item: Any, depth: int = 0) -> None:
        if depth > 20:
            raise ValueError("Literal exceeds the nesting limit")
        if type(item) in {type(None), bool, int, float, str}:
            return
        if isinstance(item, list):
            for child in item:
                verify(child, depth + 1)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                verify(child, depth + 1)
            return
        raise ValueError("Only JSON-compatible literal arguments and results are supported")

    verify(value)
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Literal must contain finite JSON values") from exc
    if len(encoded.encode()) > 64 * 1024:
        raise ValueError("An assertion literal exceeds 64 KiB")
    return value


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _call(
    node: ast.AST,
    functions: dict[str, str],
    modules: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    if not isinstance(node, ast.Call):
        raise ValueError("An assertion must compare one direct function call with a literal")
    name = _dotted(node.func)
    entrypoint = functions.get(name or "")
    if not entrypoint and name and "." in name:
        module_alias, function = name.rsplit(".", 1)
        module = modules.get(module_alias)
        if module:
            entrypoint = f"{module}:{function}"
    if not entrypoint:
        raise ValueError("The function call must resolve to an explicit absolute module import")
    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
        key.arg is None for key in node.keywords
    ):
        raise ValueError("Expanded function arguments are not supported")
    kwargs: dict[str, Any] = {}
    for key in node.keywords:
        assert key.arg is not None
        if key.arg in kwargs:
            raise ValueError("Duplicate keyword arguments are not supported")
        kwargs[key.arg] = _literal(key.value)
    return entrypoint, {"args": [_literal(arg) for arg in node.args], "kwargs": kwargs}


def _assertion(
    node: ast.stmt,
    functions: dict[str, str],
    modules: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    if (
        isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
    ):
        left, right = node.test.left, node.test.comparators[0]
    elif (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and _dotted(node.value.func) == "self.assertEqual"
        and len(node.value.args) in {2, 3}
        and not node.value.keywords
    ):
        left, right = node.value.args[:2]
    else:
        raise ValueError(
            "Only assert function(...) == literal and self.assertEqual(function(...), literal) are supported"
        )
    if isinstance(left, ast.Call) and not isinstance(right, ast.Call):
        call, expected = left, right
    elif isinstance(right, ast.Call) and not isinstance(left, ast.Call):
        call, expected = right, left
    else:
        raise ValueError("An assertion must compare exactly one function call with a literal")
    entrypoint, case = _call(call, functions, modules)
    case["expected"] = _literal(expected)
    return entrypoint, case


def _docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def extract_function_cases(source: str, filename: str) -> dict[str, list[dict[str, Any]]]:
    """Extract a restricted test language; reject partial coverage and test setup."""
    if len(source.encode()) > 512 * 1024:
        raise ValueError("Test source exceeds the extraction size limit")
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ValueError(f"Test source is not valid Python: {exc.msg}") from exc
    functions: dict[str, str] = {}
    modules: dict[str, str] = {}
    test_functions: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or any(alias.name == "*" for alias in node.names):
                raise ValueError("Relative and wildcard imports need manual review")
            for alias in node.names:
                functions[alias.asname or alias.name] = f"{node.module}:{alias.name}"
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            test_functions.append(node)
        elif isinstance(node, ast.ClassDef):
            if node.decorator_list or len(node.bases) != 1:
                raise ValueError("Decorated or complex test classes need manual review")
            base_name = _dotted(node.bases[0]) or ""
            if (
                base_name not in {"unittest.TestCase", "TestCase"}
                and functions.get(base_name) != "unittest:TestCase"
            ):
                raise ValueError("Only unittest.TestCase classes are supported")
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name.startswith("test"):
                    test_functions.append(child)
                elif not _docstring(child):
                    raise ValueError(
                        "Test class setup, helper methods, and attributes need manual review"
                    )
        elif not _docstring(node):
            raise ValueError("Top-level test setup or executable statements need manual review")
    if not test_functions:
        raise ValueError("No supported test functions were found")
    groups: dict[str, list[dict[str, Any]]] = {}
    for function in test_functions:
        arguments = function.args
        names = [argument.arg for argument in arguments.posonlyargs + arguments.args]
        if (
            function.decorator_list
            or names not in ([], ["self"])
            or arguments.vararg
            or arguments.kwarg
            or arguments.kwonlyargs
            or arguments.defaults
        ):
            raise ValueError(
                "Fixtures, decorators, parameters, and default arguments need manual review"
            )
        assertions = 0
        for statement in function.body:
            if _docstring(statement):
                continue
            entrypoint, case = _assertion(statement, functions, modules)
            case["id"] = f"{filename}:{function.name}:{statement.lineno}"
            groups.setdefault(entrypoint, []).append(case)
            assertions += 1
        if not assertions:
            raise ValueError("Empty test functions cannot establish correctness")
    target_modules = {entrypoint.split(":")[0] for entrypoint in groups}
    imported_modules = set(modules.values()) | {
        entrypoint.split(":")[0] for entrypoint in functions.values()
    }
    if imported_modules - target_modules - {"unittest", "pytest"}:
        raise ValueError("Unrelated imports may change test behavior and need manual review")
    if sum(len(cases) for cases in groups.values()) > 1000:
        raise ValueError("Candidate contains more than 1000 function assertions")
    return groups


def _source_files(snapshot: dict[str, Any]) -> dict[str, str]:
    files = {
        name: text
        for name, text in snapshot["files"].items()
        if name.endswith(".py") and not _is_test(name) and PurePosixPath(name).name != "conftest.py"
    }
    if not files:
        raise ValueError("Commit has no supported Python source files")
    for omission in snapshot["omitted"]:
        if omission["path"].endswith(".py") and not _is_test(omission["path"]):
            raise ValueError(f"Python source snapshot is incomplete: {omission['reason']}")
    return files


def _function_file(entrypoint: str, files: dict[str, str]) -> str:
    module, function = entrypoint.split(":")
    path = module.replace(".", "/") + ".py"
    if path not in files:
        path = module.replace(".", "/") + "/__init__.py"
    if path not in files:
        raise ValueError(
            f"Imported module {module!r} does not map to a source file in the snapshot"
        )
    try:
        tree = ast.parse(files[path], filename=path)
    except SyntaxError as exc:
        raise ValueError(f"Source {path} contains invalid Python") from exc
    if not any(isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body):
        raise ValueError(
            f"Function {entrypoint!r} must exist directly in both base and reference snapshots"
        )
    return path


def mine_repository(path: str, limit: int = 20) -> dict[str, Any]:
    """Build unvalidated task drafts and a complete accepted/rejected candidate ledger."""
    inspection = inspect_repository(path, limit=limit)
    scenarios: list[Scenario] = []
    candidates: list[dict[str, Any]] = []
    accepted = 0
    for candidate in inspection["candidates"]:
        record = {
            **candidate,
            "status": "rejected",
            "scenario_ids": [],
            "reasons": [],
            "validation_status": "not_run",
        }
        try:
            if not candidate["base_commit"]:
                raise ValueError(candidate["reason"] or "A single parent commit is required")
            test_paths = [
                name
                for name in candidate["changed_files"]
                if name.endswith(".py") and _is_test(name)
            ]
            source_paths = [
                name
                for name in candidate["changed_files"]
                if name.endswith(".py") and not _is_test(name)
            ]
            if not test_paths or not source_paths:
                raise ValueError("A candidate must change both Python source and Python tests")
            before = snapshot_repository(path, candidate["base_commit"])
            after = snapshot_repository(path, candidate["commit"], include_tests=True)
            base_files, reference_files = _source_files(before), _source_files(after)
            validate_files(base_files)
            validate_files(reference_files)
            groups: dict[str, list[dict[str, Any]]] = {}
            for test_path in sorted(test_paths):
                if test_path not in after["files"]:
                    raise ValueError("Deleted, binary, or oversized test files need manual review")
                extracted = extract_function_cases(after["files"][test_path], test_path)
                for entrypoint, cases in extracted.items():
                    groups.setdefault(entrypoint, []).extend(cases)
            compiled: list[Scenario] = []
            for index, (entrypoint, cases) in enumerate(sorted(groups.items()), start=1):
                _function_file(entrypoint, base_files)
                target = _function_file(entrypoint, reference_files)
                if target not in source_paths:
                    raise ValueError(
                        "Extracted assertions must target a function in changed source"
                    )
                identifier = f"mined-{candidate['commit'][:16]}-{index}"
                compiled.append(
                    Scenario(
                        id=identifier,
                        name=f"{entrypoint} · {candidate['subject']}"[:200],
                        domain="coding",
                        description="Historical repair draft extracted from literal assertions. Review instructions and validate before use.",
                        instruction=(
                            f"Repair {entrypoint} in {target} for this historical change: {candidate['subject']}. "
                            "Preserve the public function interface and existing behavior. "
                            "Return a JSON object with files mapping source paths to complete UTF-8 source."
                        ),
                        inputs={"files": base_files},
                        reference_output={"files": reference_files},
                        checks=[
                            Check(
                                id="historical-function-cases",
                                name="Historical function behavior",
                                kind="python_tests",
                                path="files",
                                expected={
                                    "files": base_files,
                                    "tests": "",
                                    "entrypoint": entrypoint,
                                    "cases": cases,
                                },
                            )
                        ],
                        tags=["historical", "python", "needs-review", "function-cases"],
                        provenance={
                            "origin": "local_git_history",
                            "repository_path": inspection["path"],
                            "base_commit": candidate["base_commit"],
                            "reference_commit": candidate["commit"],
                            "original_subject": candidate["subject"],
                            "test_files": sorted(test_paths),
                            "extractor_version": "literal-python-assertions-v1",
                            "license_status": "review_required",
                            "instruction_origin": "commit_subject",
                            "hindsight_review_required": True,
                            "validation_status": "not_run",
                            "negative_controls": [{"files": base_files}],
                            "omitted_base_files": before["omitted"],
                            "omitted_reference_files": after["omitted"],
                            "limitations": [
                                "Only literal direct function assertions are extracted",
                                "Source snapshots contain Python files only; dependencies are not installed",
                                "No repository code, builds, hooks, or tests were executed during extraction",
                                "Fail-before/pass-after evidence requires separate isolated validation",
                            ],
                        },
                    )
                )
            scenarios.extend(compiled)
            accepted += 1
            record.update(
                status="accepted",
                scenario_ids=[scenario.id for scenario in compiled],
                extracted_cases=sum(len(cases) for cases in groups.values()),
                reasons=[
                    "Extraction succeeded; task wording, licensing, and isolated validation still require review"
                ],
            )
        except ValueError as exc:
            record["reasons"] = [str(exc)]
        candidates.append(record)
    collection = (
        Collection(
            name=f"Historical Python tasks · {PurePosixPath(inspection['path']).name}"[:200],
            description="Unvalidated task drafts from local Git history. Acceptance means extraction only.",
            domain="coding",
            scenarios=scenarios,
        )
        if scenarios
        else None
    )
    return {
        "collection": collection,
        "candidates": candidates,
        "counts": {
            "inspected": len(candidates),
            "accepted": accepted,
            "rejected": len(candidates) - accepted,
            "scenarios": len(scenarios),
        },
        "executed_repository_code": False,
    }
