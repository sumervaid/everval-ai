"""Offline study analysis and aggregate-only publication export. No provider calls."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from cvals.judge_statistics import compare_calibrated_judges, family_mean_interval
from scripts.benchmarks.model import private_json
from scripts.judge_study import driver


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove per-item scores and all reviewer identifiers from an analysis."""
    result = copy.deepcopy(report)
    analysis = result["analysis"]
    for judge in analysis["judges"]:
        judge.pop("item_scores", None)
    reliability = analysis["human_reliability"]
    pairs = reliability.pop("reviewer_pairs", [])
    reliability["reviewer_pair_count"] = len(pairs)
    return result


def panel_agreement_bound(items: list[dict[str, Any]]) -> dict[str, Any]:
    maxima, divided, votes = [], 0, 0
    for item in items:
        counts = Counter(
            r["label"]
            for r in item["reference"]["reviewer_labels"]
            if r["label"] in {"A", "B", "tie"}
        )
        if not counts:
            raise ValueError("Each retained human panel needs an observed vote")
        maxima.append(max(counts.values()) / sum(counts.values()))
        divided += len(counts) > 1
        votes += sum(counts.values())
    return {
        "items": len(items),
        "observed_votes": votes,
        "divided_panels": divided,
        "observed_panel_vote_agreement_upper_bound": sum(maxima) / len(maxima),
        "interpretation": "Maximum categorical agreement against these fixed observed panels; not a universal capability ceiling.",
    }


def repository_sensitivity(report: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    analysis = copy.deepcopy(report["analysis"])
    repositories = {
        item["item_id"]: item["task"]["inputs"]["repository"]
        for item in protocol["items"]
        if item["partition"] == "holdout"
    }
    analysis["families"] = len(set(repositories.values()))
    options = {key: protocol[key] for key in ("alpha", "resamples", "seed", "min_families")}
    for judge in analysis["judges"]:
        values: dict[str, list[float]] = defaultdict(list)
        for row in judge["item_scores"]:
            row["family_id"] = repositories[row["item_id"]]
            if row["score"] is not None:
                values[row["family_id"]].append(row["score"])
        complete = judge["primary"]["complete_cohort"]
        judge["primary"] = family_mean_interval(
            values,
            planned_comparisons=max(len(analysis["judges"]), protocol["planned_comparisons"]),
            **options,
        )
        judge["primary"]["complete_cohort"] = complete
        if not complete:
            judge["primary"]["interval"] = None
            judge["primary"]["bounded_sensitivity_interval"] = None
            judge["primary"]["undefined_reason"] = "incomplete_frozen_cohort"
    contrasts = [
        compare_calibrated_judges(
            analysis,
            pair["left"],
            pair["right"],
            planned_comparisons=protocol["planned_comparisons"],
            **options,
        )
        for pair in protocol["primary_contrasts"]
    ]
    return {
        "purpose": "Exploratory repository-cluster sensitivity",
        "repository_count": analysis["families"],
        "interpretation": "Repositories receive equal weight after averaging their tasks. The small selected repository set cannot establish a population-of-repositories effect.",
        "judge_scores": [
            {"judge_id": j["judge_id"], "metric": j["primary"]} for j in analysis["judges"]
        ],
        "paired_contrasts": contrasts,
    }


def delivered_agreement(
    protocol: dict[str, Any], jobs: list[dict[str, Any]], partition: str
) -> dict[str, Any]:
    """Pre-holdout secondary endpoint: observed failed deliveries score zero."""
    items = [i for i in protocol["items"] if i["partition"] == partition]
    attempted_statuses = {"completed", "abstained", "invalid", "error"}
    endpoint = (
        "delivered_human_vote_agreement"
        if protocol["primary_endpoint"] == "human_votes"
        else "delivered_execution_agreement"
    )
    report: dict[str, Any] = {
        "items": len(items),
        "families": len({i["family_id"] for i in items}),
        "judges": [],
    }
    options = {key: protocol[key] for key in ("alpha", "resamples", "seed", "min_families")}
    expected_per_item = protocol["repetitions"] * 2
    for judge in protocol["judges"]:
        counts: Counter[str] = Counter()
        scores, family_scores = [], defaultdict(list)
        for item in items:
            rows = [
                j for j in jobs if j["item_id"] == item["item_id"] and j["judge_id"] == judge["id"]
            ]
            references = (
                [
                    r["label"]
                    for r in item["reference"]["reviewer_labels"]
                    if r["label"] in {"A", "B", "tie"}
                ]
                if protocol["primary_endpoint"] == "human_votes"
                else [item["reference"]["label"]]
            )
            complete = (
                len(rows) == expected_per_item
                and bool(references)
                and all(r in {"A", "B", "tie"} for r in references)
            )
            total = 0.0
            for row in rows:
                status = row["status"] if row["status"] in attempted_statuses else "missing"
                counts[status] += 1
                if status == "missing":
                    complete = False
                if status == "completed":
                    preference = (row["result"] or {}).get("preference")
                    if preference not in {"A", "B", "tie"}:
                        raise ValueError("Completed observations require a canonical valid label")
                    if references:
                        total += sum(preference == ref for ref in references) / len(references)
            counts["missing"] += max(0, expected_per_item - len(rows))
            score = total / expected_per_item if complete else None
            if score is not None:
                family_scores[item["family_id"]].append(score)
            scores.append(
                {
                    "item_id": item["item_id"],
                    "task_id": item["task_id"],
                    "family_id": item["family_id"],
                    "score": score,
                }
            )
        planned = len(items) * expected_per_item
        complete = counts["missing"] == 0 and all(row["score"] is not None for row in scores)
        metric = family_mean_interval(
            family_scores,
            planned_comparisons=max(len(protocol["judges"]), protocol["planned_comparisons"]),
            **options,
        )
        metric["complete_cohort"] = complete
        if not complete:
            metric["interval"] = None
            metric["bounded_sensitivity_interval"] = None
            metric["undefined_reason"] = "missing_or_unattempted_scheduled_judgments"
        report["judges"].append(
            {
                "judge_id": judge["id"],
                "model_id": judge["model"],
                "primary_endpoint": endpoint,
                "reference_mode": items[0]["reference"]["source"],
                "primary": metric,
                "item_scores": scores,
                "coverage": {
                    "scheduled": planned,
                    "attempted": sum(counts[x] for x in attempted_statuses),
                    "valid": counts["completed"],
                    "valid_delivery_rate": counts["completed"] / planned,
                    "statuses": {
                        status: counts[status]
                        for status in sorted(attempted_statuses | {"missing"})
                    },
                },
            }
        )
    contrasts = [
        compare_calibrated_judges(
            report,
            pair["left"],
            pair["right"],
            planned_comparisons=protocol["planned_comparisons"],
            **options,
        )
        for pair in protocol["primary_contrasts"]
    ]
    sensitivity = (
        repository_sensitivity({"analysis": report}, protocol)
        if endpoint == "delivered_execution_agreement" and partition == "holdout"
        else None
    )
    for judge in report["judges"]:
        judge.pop("item_scores")
    return {
        "endpoint": endpoint,
        "analysis_role": "secondary_preholdout_amendment",
        "repository_sensitivity": sensitivity,
        "reason": "Development responses included invalid or abstained deliveries; strict original primary inference requires complete valid grids and is retained unchanged.",
        "formula": "Equal-family mean of item means over every scheduled order/repetition; valid categorical decisions receive reference agreement, observed abstain/invalid/error receive zero. Missing or unattempted judgments block inferential intervals.",
        "allocation": {
            "alpha": protocol["alpha"],
            "paired_contrasts": protocol["planned_comparisons"],
            "contrast_alpha": protocol["alpha"] / protocol["planned_comparisons"],
            "scope": "Separate secondary exploratory family; not a replacement for the original primary or a claim of joint primary-plus-secondary familywise control.",
        },
        "items": len(items),
        "families": report["families"],
        "judges": report["judges"],
        "paired_contrasts": contrasts,
    }


def run(root: Path, *, allow_incomplete: bool = False) -> Path:
    plan = driver.load_plan(root)
    if (root / "operational-amendment.json").exists():
        from scripts.judge_study.parallel import amendment

        amendment(root)
    state = driver.snapshot(root, plan)
    if state["unknown_requests"]:
        raise ValueError("Wait for active calls or reconcile unknown charges before final analysis")
    missing = [
        job["job_id"]
        for job in plan["jobs"]
        if not (root / "requests" / (job["job_id"] + ".result.json")).exists()
    ]
    if missing and not allow_incomplete:
        raise ValueError(
            "Frozen grid is incomplete; no final report without explicit partial-analysis declaration"
        )
    target = root / "analysis"
    target.mkdir(mode=0o700, exist_ok=True)
    service = driver.JudgeCalibration(driver.Settings(root / "workspace"))
    for partition in ("dev", "holdout"):
        driver.collect(root, plan, partition)
    public: dict[str, Any] = {
        "schema_version": 1,
        "plan_sha256": driver.digest(plan),
        "cost_upper_bound": state,
        "planned_requests": len(plan["jobs"]),
        "missing_request_count": len(missing),
        "complete_frozen_grid": not missing,
        "heldout": {},
        "development": {},
        "secondary_delivered_agreement": {},
    }
    for cohort, identifier in plan["campaigns"].items():
        protocol = driver.read(root / "approved" / (cohort + ".json"))
        with service.store._connect() as db:
            jobs = service._jobs(db, identifier)
        public["secondary_delivered_agreement"][cohort] = delivered_agreement(
            protocol, [j for j in jobs if j["partition"] == "holdout"], "holdout"
        )
        for partition in ("dev", "holdout"):
            report = service.analyze(identifier, partition)
            private_json(target / (cohort + "-" + partition + ".json"), report)
            public["heldout" if partition == "holdout" else "development"][cohort] = public_report(
                report
            )
    human_protocol = driver.read(root / "approved" / "human.json")
    public["human_panel"] = panel_agreement_bound(
        [item for item in human_protocol["items"] if item["partition"] == "holdout"]
    )
    execution_protocol = driver.read(root / "approved" / "execution.json")
    public["repository_sensitivity"] = repository_sensitivity(
        driver.read(target / "execution-holdout.json"), execution_protocol
    )
    private_json(target / "public-results.json", public)
    return target / "public-results.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            {"aggregate_export": str(run(args.root, allow_incomplete=args.allow_incomplete))}
        )
    )


if __name__ == "__main__":
    main()
