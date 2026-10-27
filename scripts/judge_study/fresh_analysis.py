"""Frozen analysis for the finite-cohort fresh repair study.

Missing execution is bounded, never imputed as an observed failed repair.
Binomial calculations are explicitly independent-task sensitivity analyses.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import math
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.judge_study import fresh_dispatch as dispatch
from scripts.judge_study import fresh_repositories as study

ALPHA = 0.05
MODELS = tuple(study.PROFILES)
CONTRASTS = tuple(itertools.combinations(MODELS, 2))


def exact_binomial_interval(successes: int, count: int, alpha: float) -> list[float]:
    """Invert binomial tails (Clopper–Pearson), including zero/all successes."""
    if (
        type(successes) is not int
        or type(count) is not int
        or not 0 <= successes <= count <= 1000
        or count == 0
        or not 0 < alpha < 1
    ):
        raise ValueError("Invalid binomial interval inputs")

    def cdf(k: int, p: float) -> float:
        return math.fsum(math.comb(count, j) * p**j * (1 - p) ** (count - j) for j in range(k + 1))

    def solve(k: int, target: float) -> float:
        low, high = 0.0, 1.0
        for _ in range(80):
            mid = (low + high) / 2
            if cdf(k, mid) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    lower = 0.0 if successes == 0 else solve(successes - 1, 1 - alpha / 2)
    upper = 1.0 if successes == count else solve(successes, alpha / 2)
    return [lower, upper]


def exact_mcnemar(left_wins: int, right_wins: int) -> float:
    if any(type(n) is not int or n < 0 for n in (left_wins, right_wins)):
        raise ValueError("Discordant counts must be nonnegative integers")
    discordant = left_wins + right_wins
    if discordant > 1000:
        raise ValueError("Study supports at most 1000 discordant pairs")
    tail = sum(math.comb(discordant, k) for k in range(min(left_wins, right_wins) + 1))
    return min(1.0, 2 * tail / 2**discordant)


def classify(
    response: dict[str, Any] | None,
    grade: dict[str, Any] | None,
    provider_event: str | None = None,
    required_tests: list[str] | None = None,
) -> str:
    if response is None or provider_event == "provider_error":
        return "unknown_submission"
    if provider_event == "provider_refusal_or_policy_rejection":
        return "provider_refusal_or_policy_rejection"
    if response.get("patch") is None or response.get("patch") == "":
        return "invalid_or_absent_submission"
    if not isinstance(response.get("patch"), str):
        raise ValueError("Malformed retained patch")
    if grade is None:
        return "unknown_execution"
    if grade.get("patch_sha256") != hashlib.sha256(response["patch"].encode()).hexdigest():
        raise ValueError("Execution grade is not bound to the generated patch")
    if grade.get("status") == "invalid_patch":
        return "invalid_or_absent_submission"
    if grade.get("cleanup_complete") is not True:
        return "unknown_execution"
    if required_tests is not None:
        if not required_tests:
            raise ValueError("Required-test contract cannot be empty")
        statuses = grade.get("test_statuses", {})
        if not isinstance(statuses, dict) or any(
            statuses.get(test) not in {"PASSED", "FAILED"} for test in required_tests
        ):
            return "unknown_execution"
        if grade.get("resolved") is True and any(
            statuses[test] != "PASSED" for test in required_tests
        ):
            raise ValueError("Resolved summary contradicts required-test outcomes")
    verdict = study.execution_verdict(grade)
    return verdict if verdict != "unknown" else "unknown_execution"


def binary(category: str) -> int | None:
    if category == "verified_resolved":
        return 1
    if category in {
        "verified_unresolved",
        "invalid_or_absent_submission",
        "provider_refusal_or_policy_rejection",
    }:
        return 0
    if category in {"unknown_execution", "unknown_submission"}:
        return None
    raise ValueError("Unknown outcome category")


def proportion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        raise ValueError("Empty cohort")
    categories = Counter(row["category"] for row in rows)
    values = [binary(row["category"]) for row in rows]
    successes = sum(x == 1 for x in values)
    unknown = values.count(None)
    return {
        "planned_tasks": count,
        "categories": dict(sorted(categories.items())),
        "verified_resolutions": successes,
        "unknown_outcomes": unknown,
        "full_cohort_success_bounds": [successes / count, (successes + unknown) / count],
        "independent_task_sensitivity_interval": (
            exact_binomial_interval(successes, count, ALPHA / len(MODELS)) if unknown == 0 else None
        ),
        "interval_method": "Clopper-Pearson; independent Bernoulli tasks assumed",
        "interval_alpha": ALPHA / len(MODELS),
    }


def contrast(rows: list[dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row["model"] in {left, right}:
            per_task = indexed.setdefault(row["task_id"], {})
            if row["model"] in per_task:
                raise ValueError("Duplicate model/task outcome")
            per_task[row["model"]] = row
    if not indexed or any(set(values) != {left, right} for values in indexed.values()):
        raise ValueError("Each planned task needs both outcome rows, including missing markers")
    paired = []
    for task, values in indexed.items():
        if values[left]["repo"] != values[right]["repo"]:
            raise ValueError("Paired repository identities differ")
        a, b = binary(values[left]["category"]), binary(values[right]["category"])
        lower = (b if b is not None else 0) - (a if a is not None else 1)
        upper = (b if b is not None else 1) - (a if a is not None else 0)
        paired.append((task, values[left]["repo"], a, b, lower, upper))
    n = len(paired)
    complete = all(a is not None and b is not None for _, _, a, b, _, _ in paired)
    left_wins = sum(a == 1 and b == 0 for _, _, a, b, _, _ in paired)
    right_wins = sum(a == 0 and b == 1 for _, _, a, b, _, _ in paired)
    p_value = exact_mcnemar(left_wins, right_wins) if complete else None
    bounds = [sum(row[4] for row in paired) / n, sum(row[5] for row in paired) / n]
    repo_bounds = {}
    leave_one_out = {}
    for repo in sorted({row[1] for row in paired}):
        group = [row for row in paired if row[1] == repo]
        repo_bounds[repo] = {
            "tasks": len(group),
            "difference_bounds": [
                sum(row[4] for row in group) / len(group),
                sum(row[5] for row in group) / len(group),
            ],
        }
        rest = [row for row in paired if row[1] != repo]
        if rest:
            leave_one_out[repo] = [
                sum(row[4] for row in rest) / len(rest),
                sum(row[5] for row in rest) / len(rest),
            ]
    return {
        "left": left,
        "right": right,
        "direction": "right minus left",
        "planned_pairs": n,
        "complete_pairs": sum(a is not None and b is not None for _, _, a, b, _, _ in paired),
        "full_cohort_difference_bounds": bounds,
        "left_only_resolved": left_wins,
        "right_only_resolved": right_wins,
        "independent_task_exact_mcnemar_p": p_value,
        "bonferroni_adjusted_p": min(1.0, len(CONTRASTS) * p_value)
        if p_value is not None
        else None,
        "comparison_alpha": ALPHA / len(CONTRASTS),
        "repository_results": repo_bounds,
        "leave_one_repository_out_bounds": leave_one_out,
        "repository_sensitivity_is_descriptive": True,
        "bootstrap_interval": None,
    }


def verify_terminal_result(root: Path, job: dict[str, Any]) -> str:
    """Verify retained terminal evidence without dispatching or modifying artifacts."""
    output = root / "requests" / job["id"]
    status, raw = dispatch.retained_response(output)
    if not isinstance(raw, dict):
        raise ValueError("Malformed terminal provider envelope")
    request = dispatch.strict_loads((output / "request.json").read_bytes())
    result = dispatch.strict_loads((output / "result.json").read_bytes())
    envelope = dispatch.strict_loads((output / "response.json").read_bytes())
    usage = raw.get("usage")
    if (
        not isinstance(usage, dict)
        or any(
            type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")
        )
        or study.digest(request["body"]) != job["body_sha256"]
        or envelope != {"http_status": status, "response": raw}
        or result["instance_id"] != job["instance_id"]
        or result["model"] != job["model"]
        or raw.get("model") != job["model"]
        or raw.get("service_tier", "default") not in {None, "default"}
        or result["returned_generator_model"] != raw["model"]
        or result["provider_usage"] != usage
        or result.get("provider_response_id") != raw.get("id")
    ):
        raise ValueError("Terminal request, response, identity or accounting binding changed")
    from decimal import Decimal

    from scripts.benchmarks.budget import BudgetLedger

    inference = study.inference_plan(root)
    config = study.budget_config(inference, job["model"])
    ledger = BudgetLedger(
        root / "accounting" / (job["model"] + ".sqlite3"), config, create=False
    ).status()
    requests = [row for row in ledger["requests"] if row["attempt_id"] == job["id"]]
    attempts = [row for row in ledger["attempts"] if row["attempt_id"] == job["id"]]
    if len(requests) != 1 or len(attempts) != 1:
        raise ValueError("Expected exactly one ledger request and attempt per frozen job")
    record = requests[0]
    if (
        record["id"] != request["reservation_id"]
        or record["request_hash"] != job["body_sha256"]
        or record["state"] != "settled"
        or record["input_tokens"] != usage["input_tokens"]
        or record["output_tokens"] != usage["output_tokens"]
        or Decimal(record["known_cost_usd"]) != Decimal(result["conservative_cost_usd"])
        or Decimal(record["known_cost_usd"])
        != config.cost(usage["input_tokens"], usage["output_tokens"])
        or attempts[0]["attempt_hash"]
        != study.digest({"job": job["id"], "body": job["body_sha256"]})
    ):
        raise ValueError("Terminal evidence differs from immutable settled accounting")
    reason, policy = dispatch.response_problem(status, raw, job["model"])
    if reason not in {None, "Provider policy rejection or refusal", "Provider error"}:
        raise ValueError("Unverifiable terminal provider metadata")
    expected_patch = None
    text = study.select_final_text(raw)
    if status == 200 and raw.get("status") == "completed" and text is not None:
        try:
            parsed = dispatch.strict_loads(text)
            if (
                isinstance(parsed, dict)
                and set(parsed) == {"patch"}
                and isinstance(parsed["patch"], str)
            ):
                expected_patch = parsed["patch"]
        except (TypeError, ValueError):
            pass
    if result.get("patch") != expected_patch:
        raise ValueError("Terminal patch differs from strict retained output")
    if policy:
        return "provider_refusal_or_policy_rejection"
    return "provider_error" if reason else "model_output"


def source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def freeze(root: Path) -> None:
    inference, dispatch_manifest = dispatch.verified_plan(root)
    if (root / "requests").exists() and any((root / "requests").iterdir()):
        raise ValueError("Analysis must freeze before any generation artifacts")
    from scripts.benchmarks.budget import BudgetLedger

    for model in MODELS:
        ledger = BudgetLedger(
            root / "accounting" / (model + ".sqlite3"),
            study.budget_config(inference, model),
            create=False,
        )
        if ledger.status()["requests"]:
            raise ValueError("Analysis must freeze before any ledger admission")
    protocol = {
        "schema_version": 1,
        "source_sha256": source_sha256(),
        "inference_sha256": study.digest(inference),
        "dispatch_freeze_sha256": study.digest(dispatch_manifest),
        "selected_task_ids": [row["instance_id"] for row in inference["tasks"]],
        "repository_task_counts": dict(Counter(row["repo"] for row in inference["tasks"])),
        "prospective_power_sha256": study.digest(study.read(root / "prospective-power.json")),
        "power_scope": (
            "The retained independent-pair power calculation is a sensitivity analysis. "
            "Its earlier five-repository wording is a planning assumption; actual repository "
            "counts are frozen above and do not alter the independent-pair arithmetic."
        ),
        "models": list(MODELS),
        "planned_contrasts": [{"left": a, "right": b} for a, b in CONTRASTS],
        "primary_estimand": "finite-cohort end-to-end one-shot verified repair success",
        "unknown_handling": "retain every selected task; bound unknown outcomes as zero to one",
        "invalid_submission_handling": "known end-to-end failure, separate from executed failures",
        "missing_submission_handling": "unknown; never equate unattempted calls with invalid output",
        "refusal_handling": "observed accounted refusal/policy rejection is a separate known end-to-end failure; it never authorizes retry or further source exposure",
        "provider_error_handling": "retained and unknown; unrelated transport/provider failure is not repair evidence",
        "proportion_family_alpha": ALPHA,
        "contrast_family_alpha": ALPHA,
        "separate_sensitivity_families": True,
        "proportion_method": "exact Clopper-Pearson only with complete known outcomes",
        "contrast_method": "exact conditional two-sided McNemar, Bonferroni over three contrasts",
        "inferential_scope": "independent-task sensitivity; no cross-repository generalization",
        "repository_sensitivity": "per-repository counts and leave-one-repository-out bounds",
        "bootstrap": "none",
        "model_outcomes_observed": False,
    }
    dispatch.save_once(root / "analysis-freeze.json", protocol)
    dispatch.save_once(root / "analysis-freeze-sha256.json", {"sha256": study.digest(protocol)})


def analyze(root: Path) -> dict[str, Any]:
    inference, dispatch_manifest = dispatch.verified_plan(root)
    protocol = study.read(root / "analysis-freeze.json")
    if (
        source_sha256() != protocol["source_sha256"]
        or study.digest(protocol) != study.read(root / "analysis-freeze-sha256.json")["sha256"]
        or study.digest(inference) != protocol["inference_sha256"]
        or study.digest(dispatch_manifest) != protocol["dispatch_freeze_sha256"]
    ):
        raise ValueError("Analysis source or frozen evidence changed")
    entries = {row["instance_id"]: row for row in inference["tasks"]}
    rows = []
    for job in inference["jobs"]:
        response_path = root / "requests" / job["id"] / "result.json"
        grade_path = root / "grades" / job["id"] / "result.json"
        response = study.read(response_path) if response_path.exists() else None
        grade = study.read(grade_path) if grade_path.exists() else None
        provider_event = verify_terminal_result(root, job) if response is not None else None
        if grade is not None:
            if grade.get("instance_id") != job["instance_id"]:
                raise ValueError("Grade task identity differs from request")
            if response is None:
                raise ValueError("Grade has no retained response")
            if response.get("patch"):
                entry = entries[job["instance_id"]]
                control = study.read(
                    root / "controls" / job["instance_id"] / "base" / "result.json"
                )
                if (
                    grade.get("environment") != entry["environment"]
                    or grade.get("eval_script_sha256") != control["eval_script_sha256"]
                ):
                    raise ValueError("Grade environment or test script differs from admission")
        task = study.task_for(root, entries[job["instance_id"]])
        required_tests = list(set(task["FAIL_TO_PASS"]) | set(task["PASS_TO_PASS"]))
        category = classify(response, grade, provider_event, required_tests)
        rows.append(
            {
                "task_id": job["instance_id"],
                "repo": entries[job["instance_id"]]["repo"],
                "model": job["model"],
                "category": category,
                "provider_event": provider_event,
                "request_result_sha256": study.digest(response) if response is not None else None,
                "grade_result_sha256": study.digest(grade) if grade is not None else None,
                "returned_generator_model": response.get("returned_generator_model")
                if response is not None
                else None,
            }
        )
    expected = {(task, model) for task in entries for model in MODELS}
    actual = {(row["task_id"], row["model"]) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("Outcome grid differs from the planned complete task/model grid")
    return {
        "analysis_protocol_sha256": study.digest(protocol),
        "inference_sha256": study.digest(inference),
        "outcomes": rows,
        "models": {
            model: proportion([row for row in rows if row["model"] == model]) for model in MODELS
        },
        "contrasts": [contrast(rows, left, right) for left, right in CONTRASTS],
        "accounting_and_screening": study.summary(root),
        "limitations": [
            "Selected public tasks, bounded retrieval, no tools or retries, and native environment substitutions.",
            "Finite-cohort bounds are identification bounds, not confidence intervals.",
            "Independent-task sensitivity does not correct shared-repository dependence or task selection.",
            "Repository omission ranges are descriptive, not uncertainty intervals.",
            "Proportion and contrast families each allocate alpha separately; no joint six-statement guarantee.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "analyze"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = study.private_root(args.root)
    if args.command == "freeze":
        freeze(root)
    else:
        if args.output is None:
            parser.error("--output is required for analysis")
        dispatch.save_once(args.output, analyze(root))


if __name__ == "__main__":
    main()
