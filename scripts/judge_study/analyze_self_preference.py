"""Offline, task-weighted analysis of prospective same-identifier preferences.

No model calls occur here. Execution strata are test outcomes rather than human
labels, and own-output preference is descriptive rather than causal self-bias.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

from cvals.judge_calibration import normalize_response
from cvals.judge_statistics import family_mean_interval
from scripts.judge_study import self_preference_plan as planner
from scripts.judge_study import self_preference_run as runner
from scripts.judge_study.responses import judge_response

STRATA = (
    "all",
    "both resolved",
    "only own resolved",
    "only other resolved",
    "both unresolved",
    "any execution grade unknown",
)


def stratum(job: dict[str, Any], own: str) -> str:
    other = next(model for model in job["generators"] if model != own)
    a, b = job["execution_grades"][own], job["execution_grades"][other]
    if a not in ("resolved", "unresolved") or b not in ("resolved", "unresolved"):
        return "any execution grade unknown"
    if a == b:
        return "both " + a
    return "only own resolved" if a == "resolved" else "only other resolved"


def preference(job: dict[str, Any], observation: dict[str, Any], own: str) -> float | None:
    if observation["status"] != "completed":
        return None
    label = observation.get("preference")
    if label == "tie":
        return 0.5
    if label not in ("A", "B"):
        raise ValueError("Completed judgment has no valid categorical preference")
    return float(job["displayed_generators"][label] == own)


def summarize(plan: dict[str, Any], observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if len(plan["jobs"]) != 360:
        raise ValueError("The full planned judgment grid is required")
    index = {
        (job["instance_id"], tuple(job["generators"]), job["judge"], job["order"]): job
        for job in plan["jobs"]
    }
    if len(index) != 360 or set(observations) != {job["id"] for job in plan["jobs"]}:
        raise ValueError("Observed rows must preserve every unique planned cell")
    tasks = {job["instance_id"] for job in plan["jobs"]}
    if len(tasks) != 20:
        raise ValueError("The original twenty selected tasks must be retained")
    output = {}
    rules = plan["analysis"]["uncertainty"]
    for model in planner.MODELS:
        own_jobs = [
            job for job in plan["jobs"] if job["judge"] == model and model in job["generators"]
        ]
        strata = {}
        for name in STRATA:
            eligible = [job for job in own_jobs if name == "all" or stratum(job, model) == name]
            by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
            pairs: dict[tuple[str, tuple[str, ...]], list[tuple[float, float]]] = defaultdict(list)
            counts = Counter()
            own_statuses, third_statuses = Counter(), Counter()
            expected_pairs = set()
            for job in eligible:
                task, pair = job["instance_id"], tuple(job["generators"])
                by_task[task].append(job)
                expected_pairs.add((task, pair))
                own = observations[job["id"]]
                third_job = index[(task, pair, job["third_model_judge"], job["order"])]
                third = observations[third_job["id"]]
                own_statuses[own["status"]] += 1
                third_statuses[third["status"]] += 1
                counts["own_attempted"] += bool(own.get("attempted"))
                counts["third_attempted"] += bool(third.get("attempted"))
                a, b = preference(job, own, model), preference(third_job, third, model)
                counts["own_valid"] += a is not None
                counts["third_valid"] += b is not None
                if a is not None and b is not None:
                    pairs[(task, pair)].append((a, b))
                    counts["matched_cells"] += 1
            family_own, family_third, family_diff = (
                defaultdict(list),
                defaultdict(list),
                defaultdict(list),
            )
            complete_tasks = 0
            for task, jobs in by_task.items():
                task_pairs = {tuple(job["generators"]) for job in jobs}
                if all(len(pairs[(task, pair)]) == 2 for pair in task_pairs):
                    complete_tasks += 1
                for pair in task_pairs:
                    values = pairs[(task, pair)]
                    if values:
                        family_own[task].append(mean(a for a, _ in values))
                        family_third[task].append(mean(b for _, b in values))
                        family_diff[task].append(mean(a - b for a, b in values))
            complete = counts["matched_cells"] == len(eligible)
            summaries = {}
            for metric, families, low in (
                ("own_preference", family_own, 0),
                ("third_model_preference_for_same_patch", family_third, 0),
                ("own_minus_third", family_diff, -1),
            ):
                value = family_mean_interval(
                    families,
                    alpha=rules["alpha"],
                    planned_comparisons=rules["planned_comparisons"],
                    lower=low,
                    upper=1,
                    resamples=rules["resamples"],
                    seed=rules["seed"],
                    min_families=rules["min_families"],
                )
                if not complete:
                    value["interval"] = None
                    value["bounded_sensitivity_interval"] = None
                    value["undefined_reason"] = "incomplete_matched_family_grid"
                value["role"] = (
                    "Exploratory descriptive interval; no simultaneous coverage or causal self-bias claim."
                )
                value["estimate_scope"] = (
                    "Observed matched orders, then equally weighted pairs within task, then equally weighted tasks."
                )
                summaries[metric] = value
            strata[name] = {
                "selected_task_families": 20,
                "eligible_task_families": len(by_task),
                "observed_matched_task_families": len(family_diff),
                "complete_matched_task_families": complete_tasks,
                "eligible_generator_pairs": len(expected_pairs),
                "scheduled_matched_cells": len(eligible),
                "own_attempted": counts["own_attempted"],
                "third_attempted": counts["third_attempted"],
                "own_valid": counts["own_valid"],
                "third_valid": counts["third_valid"],
                "matched_cells": counts["matched_cells"],
                "complete_matched_grid": complete,
                "own_statuses": dict(own_statuses),
                "third_statuses": dict(third_statuses),
                **summaries,
            }
        output[model] = strata
    return {
        "schema_version": 1,
        "planned_jobs": 360,
        "selected_task_families": 20,
        "models": list(planner.MODELS),
        "observed_statuses": dict(Counter(row["status"] for row in observations.values())),
        "attempted_jobs": sum(bool(row.get("attempted")) for row in observations.values()),
        "identity_claim": plan["identity_claim"],
        "analysis_role": plan["analysis_role"],
        "uncertainty": rules,
        "limitations": plan["analysis"]["limitations"],
        "by_model": output,
    }


def collect(root: Path, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {}
    for model, model_state in state["models"].items():
        for row in model_state["requests"]:
            if row["attempt_id"] in rows:
                raise ValueError("A job has more than one admitted request")
            rows[row["attempt_id"]] = {**row, "model": model}
    jobs = {job["id"]: job for job in plan["jobs"]}
    if not rows.keys() <= jobs.keys():
        raise ValueError("Accounting contains an unplanned job")
    observations = {}
    for identifier, job in jobs.items():
        path = root / "requests" / identifier
        row = rows.get(identifier)
        if row is None:
            if any(
                (path / name).exists()
                for name in ("request.json", "response.json", "result.json", "transport.json")
            ):
                raise ValueError("Evidence exists without an admitted request")
            observations[identifier] = {
                "status": "unattempted",
                "preference": None,
                "attempted": False,
                "availability": job["status"],
            }
            continue
        request = runner.read(path / "request.json")
        if (
            job["status"] != "ready"
            or row["model"] != job["judge"]
            or row["request_hash"] != job["body_sha256"]
            or row["input_ceiling"] != job["input_ceiling"]
            or row["output_ceiling"] != job["output_ceiling"]
            or Decimal(row["reserved_usd"]) != Decimal(job["reservation_usd"])
            or request["request_id"] != row["id"]
            or request["body_sha256"] != job["body_sha256"]
            or planner.digest(request["body"]) != job["body_sha256"]
            or Decimal(request["reservation_usd"]) != Decimal(job["reservation_usd"])
        ):
            raise ValueError("Retained request and ledger do not bind the frozen job")
        result_path = path / "result.json"
        if not result_path.exists():
            if row["state"] not in ("uncertain", "pending"):
                raise ValueError("Settled request lacks its retained judgment result")
            observations[identifier] = {
                "status": "error",
                "preference": None,
                "attempted": True,
                "billing": "unknown_full_reservation_retained",
            }
            continue
        result = runner.read(result_path)
        response = runner.read(path / "response.json")
        transport = runner.read(path / "transport.json")
        if response["http_status"] != transport["http_status"] or response[
            "response"
        ] != runner.strict_json(transport["body_utf8"]):
            raise ValueError("Retained transport differs from the parsed response")
        handling = judge_response(response, runner.RUBRIC)
        normalized = normalize_response(handling.text, ["A", "B"], runner.RUBRIC)
        if handling.error is not None:
            normalized = {"status": "error", "preference": None, "rationale": None, "scores": None}
        raw = response["response"]
        usage = raw["usage"]
        if (
            type(usage.get("input_tokens")) is not int
            or type(usage.get("output_tokens")) is not int
            or raw.get("service_tier", "default") not in (None, "default")
        ):
            raise ValueError("Retained provider usage or tier is unpriced")
        profile = planner.PROFILES[job["judge"]]
        cost = (
            Decimal(usage["input_tokens"]) * Decimal(profile["input"])
            + Decimal(usage["output_tokens"]) * Decimal(profile["output"])
        ) / Decimal(1000000)
        if (
            row["state"] != "settled"
            or result["job_id"] != identifier
            or result["instance_id"] != job["instance_id"]
            or result["judge"] != job["judge"]
            or result["request_id"] != row["id"]
            or result["returned_judge_model"] != job["judge"]
            or raw["model"] != job["judge"]
            or result["provider_response_id"] != raw.get("id")
            or result["provider_usage"] != usage
            or usage["input_tokens"] != row["input_tokens"]
            or usage["output_tokens"] != row["output_tokens"]
            or Decimal(result["conservative_cost_usd"]) != cost
            or Decimal(row["known_cost_usd"]) != cost
            or result["normalized"] != normalized
            or result["response_handling"] != handling.metadata()
            or response["response_handling"] != handling.metadata()
        ):
            raise ValueError(
                "Retained judgment, provider identity or accounting differs from recomputation"
            )
        observations[identifier] = {**normalized, "attempted": True}
    return observations


def analyze(root: Path) -> dict[str, Any]:
    plan, dispatch = runner.load(root)
    state = runner.snapshot(runner.ledgers(root, dispatch))
    observations = collect(root, plan, state)
    result = summarize(plan, observations)
    result["dispatch_sha256"] = planner.digest(dispatch)
    result["prepared_sha256"] = planner.digest(plan)
    result["accounting"] = {
        key: state[key]
        for key in (
            "committed_upper_bound_usd",
            "settled_upper_bound_usd",
            "requests",
            "unknown_requests",
        )
    }
    result["private_evidence_digest"] = planner.digest(
        {
            job["id"]: hashlib.sha256(
                (root / "requests" / job["id"] / "result.json").read_bytes()
            ).hexdigest()
            if (root / "requests" / job["id"] / "result.json").exists()
            else None
            for job in plan["jobs"]
        }
    )
    planner.write_private(root / "analysis" / "aggregate.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    value = analyze(args.root)
    print(
        json.dumps({"attempted_jobs": value["attempted_jobs"], "accounting": value["accounting"]})
    )


if __name__ == "__main__":
    main()
