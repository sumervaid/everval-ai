"""Domain validation proves reference behavior and rejects negative controls."""

from __future__ import annotations

from typing import Any

from cvals.evaluators import evaluate
from cvals.models import Collection


def _passed(checks: list[dict[str, Any]]) -> bool:
    required = [check for check in checks if check.get("required", True)]
    return bool(checks) and all(check["passed"] for check in required or checks)


def validate_collection(collection: Collection, settings: Any) -> dict[str, Any]:
    """Keep control failures visible; infrastructure failure never validates a task."""
    results = []
    for scenario in collection.scenarios:
        errors: list[str] = []
        reference: list[dict[str, Any]] = []
        controls: list[dict[str, Any]] = []
        if scenario.reference_output is None:
            errors.append("A reference output is required to validate a scenario")
        else:
            try:
                reference = evaluate(scenario, scenario.reference_output, settings)
                if not _passed(reference):
                    errors.append("Reference output did not pass the scenario checks")
            except ValueError as exc:
                errors.append(f"Reference evaluation unavailable: {exc}")
        negatives = scenario.provenance.get("negative_controls", [{}])
        if (
            not isinstance(negatives, list)
            or not negatives
            or any(not isinstance(item, dict) for item in negatives)
        ):
            errors.append("Negative controls must be a nonempty list of output objects")
            negatives = []
        for index, control in enumerate(negatives):
            try:
                checks = evaluate(scenario, control, settings)
                rejected = not _passed(checks)
                controls.append({"index": index, "rejected": rejected, "checks": checks})
                if not rejected:
                    errors.append(f"Negative control {index + 1} passed; checks need strengthening")
            except ValueError as exc:
                controls.append({"index": index, "rejected": False, "error": str(exc)})
                errors.append(f"Negative control {index + 1} could not be evaluated: {exc}")
        results.append(
            {
                "id": scenario.id,
                "valid": not errors,
                "errors": errors,
                "checks": reference,
                "negative_controls": controls,
                "evidence_scope": "Reference and negative controls in one validation cycle; no flakiness guarantee",
            }
        )
    return {"valid": all(result["valid"] for result in results), "scenarios": results}
