"""Bound work for repeated offline grading of untrusted structured outputs."""

from __future__ import annotations

from typing import Any

from cvals.models import Scenario, canonical_json

PURE_CHECKS = {"equals", "contains", "numeric", "json_schema", "citation", "forbidden"}


def count_json_nodes(value: Any, depth: int = 0) -> int:
    if depth > 40:
        raise ValueError("Offline grading JSON exceeds its depth limit")
    if isinstance(value, dict):
        return 1 + sum(count_json_nodes(item, depth + 1) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(count_json_nodes(item, depth + 1) for item in value)
    return 1


def assert_schema_bounded(value: Any, depth: int = 0) -> None:
    """Allow a structural subset without expensive composition or uniqueness."""
    if depth > 12 or count_json_nodes(value) > 256:
        raise ValueError("Offline schema exceeds its depth or 256-node work limit")
    if isinstance(value, bool):
        return
    if not isinstance(value, dict):
        raise ValueError("Offline schema must be an object or boolean")
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "enum",
        "const",
        "description",
        "title",
        "minProperties",
        "maxProperties",
    }
    if set(value) - allowed:
        raise ValueError("Offline JSON schema uses unsupported or unbounded validators")
    properties = value.get("properties", {})
    if isinstance(properties, dict):
        for item in properties.values():
            assert_schema_bounded(item, depth + 1)
    for key in ("items", "additionalProperties"):
        if key in value:
            assert_schema_bounded(value[key], depth + 1)
    prefix = value.get("prefixItems", [])
    if isinstance(prefix, list):
        for item in prefix:
            assert_schema_bounded(item, depth + 1)


def assert_pure_work(scenario: Scenario | dict[str, Any], output: dict[str, Any]) -> None:
    """Fail closed before bounded automatic grading; this does not execute checks."""
    value = scenario.model_dump(mode="json") if isinstance(scenario, Scenario) else scenario
    try:
        if len(canonical_json(value).encode()) > 512 * 1024:
            raise ValueError("Offline grading scenario exceeds 512 KiB")
        if len(canonical_json(output).encode()) > 128 * 1024:
            raise ValueError("Offline grading output exceeds 128 KiB")
        count_json_nodes(value)
        output_nodes = count_json_nodes(output)
    except (RecursionError, TypeError) as exc:
        raise ValueError("Offline grading requires bounded JSON") from exc
    checks = value.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 100:
        raise ValueError("Offline grading requires a bounded nonempty check list")
    work = 0
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in PURE_CHECKS:
            raise ValueError("Automatic pure grading cannot execute custom or repository checks")
        expected = check.get("expected")
        if check["kind"] == "json_schema":
            assert_schema_bounded(expected)
        # Equality, membership, and schema literal comparisons can multiply work.
        factor = count_json_nodes(expected)
        work += factor * output_nodes
    if work > 2_000_000:
        raise ValueError("Offline grading exceeds the bounded work budget")
