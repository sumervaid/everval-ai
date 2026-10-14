"""Deterministic graders. Grading failures remain separate from unsuccessful work."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from cvals.models import Check, Scenario, canonical_json

if TYPE_CHECKING:
    from cvals.config import Settings

_MISSING = object()


class EvaluationError(ValueError):
    """The verifier could not establish a valid result."""


def at_path(value: Any, path: str) -> Any:
    current = value
    for key in path.split(".") if path else []:
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdecimal() and int(key) < len(current):
            current = current[int(key)]
        else:
            return _MISSING
    return current


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return expected in actual
    if isinstance(actual, list):
        return (
            all(item in actual for item in expected)
            if isinstance(expected, list)
            else expected in actual
        )
    if isinstance(actual, dict):
        return (
            all(key in actual and actual[key] == value for key, value in expected.items())
            if isinstance(expected, dict)
            else expected in actual
        )
    return False


def _citations(scenario: Scenario, actual: Any, expected: Any) -> tuple[bool, str]:
    if not isinstance(actual, list) or not actual:
        return False, "Provide at least one source citation"
    options = expected if isinstance(expected, dict) else {"source_ids": expected or []}
    required = options.get("source_ids", [])
    sources = scenario.inputs.get("sources", [])
    if isinstance(sources, list):
        sources = {str(s.get("id")): s.get("text", "") for s in sources if isinstance(s, dict)}
    if not isinstance(sources, dict):
        raise EvaluationError("Citation checks require input sources")
    cited = set()
    for item in actual:
        source_id = item.get("source_id") if isinstance(item, dict) else item
        if not isinstance(source_id, str) or source_id not in sources:
            return False, "Citation references an unknown source"
        cited.add(source_id)
        quote = item.get("quote") if isinstance(item, dict) else None
        if options.get("require_quote") and (not isinstance(quote, str) or not quote.strip()):
            return False, "A nonempty supporting quotation is required"
        source_text = sources[source_id]
        if isinstance(source_text, dict):
            source_text = source_text.get("text", "")
        if quote is not None and (
            not isinstance(quote, str)
            or not isinstance(source_text, str)
            or quote not in source_text
        ):
            return False, "Quotation does not occur verbatim in its cited source"
    if not set(required).issubset(cited):
        return False, "Required source citations are missing"
    return True, "Source identifiers and quotations verified; semantic entailment is not assessed"


def _grade(
    scenario: Scenario, check: Check, actual: Any, settings: Settings
) -> tuple[bool, str, Any]:
    if actual is _MISSING:
        return False, "Required output path is missing", None
    expected = check.expected
    if check.kind == "equals":
        passed = canonical_json(actual) == canonical_json(expected)
    elif check.kind == "contains":
        passed = _contains(actual, expected)
    elif check.kind == "forbidden":
        items = expected if isinstance(expected, list) else [expected]
        passed = all(not _contains(actual, item) for item in items)
    elif check.kind == "numeric":
        passed = (
            not isinstance(actual, bool)
            and isinstance(actual, (int, float))
            and math.isfinite(actual)
            and abs(actual - expected) <= check.tolerance
        )
    elif check.kind == "json_schema":
        errors = list(Draft202012Validator(expected).iter_errors(actual))
        return (
            not errors,
            "JSON Schema satisfied"
            if not errors
            else "Output does not satisfy the declared JSON Schema",
            actual,
        )
    elif check.kind == "citation":
        passed, message = _citations(scenario, actual, expected)
        return passed, message, actual
    else:
        from cvals.sandbox import run_python_cases, run_python_tests

        if not isinstance(actual, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in actual.items()
        ):
            return False, "Code submission must map filenames to source text", actual
        base = expected.get("files", scenario.inputs.get("files", {}))
        if not isinstance(base, dict):
            raise EvaluationError("Python task base files must be an object")
        try:
            if "cases" in expected:
                result = run_python_cases(
                    {**base, **actual}, expected["entrypoint"], expected["cases"], settings
                )
            else:
                result = run_python_tests({**base, **actual}, expected["tests"], settings)
        except ValueError as exc:
            raise EvaluationError(str(exc)) from exc
        if result.get("infrastructure_error") or not result.get("tests_run"):
            raise EvaluationError("Verifier failed or collected zero tests")
        return (
            bool(result["passed"]),
            f"{result['tests_run']} tests executed in {result['image_digest']}",
            result,
        )
    return passed, "Check passed" if passed else "Output does not satisfy this check", actual


def evaluate(
    scenario: Scenario, output: dict[str, Any], settings: Settings
) -> list[dict[str, Any]]:
    """Return inspectable check evidence; never execute host-side task content."""
    results = []
    for check in scenario.checks:
        try:
            passed, message, actual = _grade(scenario, check, at_path(output, check.path), settings)
        except (RecursionError, TypeError, KeyError) as exc:
            raise EvaluationError(f"Invalid verifier configuration for {check.id}") from exc
        results.append(
            {
                "id": check.id,
                "name": check.name,
                "kind": check.kind,
                "passed": passed,
                "score": float(passed),
                "expected": check.expected,
                "actual": actual,
                "message": message,
                "required": check.required,
                "weight": check.weight,
            }
        )
    return results
