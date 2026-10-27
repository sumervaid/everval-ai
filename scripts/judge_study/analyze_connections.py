"""Offline reporting after the explicit policy quarantine and bounded cost review.

The original human cohort remains incomplete. A 63-question sensitivity is
separately labeled post hoc; no original endpoint or missingness rule is edited.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from cvals.judge_statistics import analyze_calibration, compare_calibrated_judges
from scripts.benchmarks.model import private_json
from scripts.judge_study import analyze, connection_recovery, driver, recovery


def posthoc_report(protocol: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    value = copy.deepcopy(protocol)
    value["items"] = [
        i
        for i in value["items"]
        if i["partition"] == "holdout" and i["item_id"] != recovery.POLICY_ITEM
    ]
    retained = {i["item_id"] for i in value["items"]}
    observations = []
    for job in jobs:
        if job["item_id"] not in retained:
            continue
        result = job["result"] or {}
        observations.append(
            {
                "item_id": job["item_id"],
                "judge_id": job["judge_id"],
                "repetition": job["repetition"],
                "order": job["order"],
                "status": job["status"]
                if job["status"] in {"completed", "abstained", "invalid", "error", "missing"}
                else "missing",
                "preference": result.get("preference"),
                "scores": result.get("scores"),
            }
        )
    options = {key: value[key] for key in ("alpha", "resamples", "seed", "min_families")}
    report = analyze_calibration(
        value["items"],
        observations,
        repetitions=value["repetitions"],
        planned_comparisons=max(value["planned_comparisons"], len(value["judges"])),
        judge_models={j["id"]: j["model"] for j in value["judges"]},
        primary_endpoint=value["primary_endpoint"],
        **options,
    )
    contrasts = [
        compare_calibrated_judges(
            report,
            pair["left"],
            pair["right"],
            planned_comparisons=value["planned_comparisons"],
            **options,
        )
        for pair in value["primary_contrasts"]
    ]
    core = analyze.public_report({"analysis": report, "paired_contrasts": contrasts})
    return {
        "analysis_role": "posthoc_policy_quarantine_sensitivity",
        "excluded_item": recovery.POLICY_ITEM,
        "independent_questions": len(value["items"]),
        "reason": "One source question was quarantined after an observed provider policy rejection. This exclusion was not in the original protocol; it is not a replacement confirmatory result.",
        "original_agreement_definition": core,
        "delivered_agreement": analyze.delivered_agreement(
            value, [j for j in jobs if j["item_id"] in retained], "holdout"
        ),
    }


def run(root: Path) -> Path:
    plan = driver.load_plan(root)
    continuation = connection_recovery.load(root)
    policy = recovery.load(root)
    budgets = connection_recovery.accounting(root, plan, continuation)
    states = [ledger.status() for ledger in budgets.values()]
    if any(s["pending_requests"] or s["unreviewed_uncertain_requests"] for s in states):
        raise ValueError("Active or unreviewed unknown requests block final reporting")
    if sum(s["reviewed_uncertain_requests"] for s in states) != 2:
        raise ValueError("Reporting requires exactly both bounded reviewed uncertainties")
    missing = {
        j["job_id"]
        for j in plan["jobs"]
        if not (root / "requests" / (j["job_id"] + ".result.json")).exists()
    }
    if missing != set(continuation["quarantined_unattempted_jobs"]):
        raise ValueError("Unrelated frozen jobs remain incomplete")
    target = root / "analysis"
    target.mkdir(exist_ok=True, mode=0o700)
    service = driver.JudgeCalibration(driver.Settings(root / "workspace"))
    for partition in ("dev", "holdout"):
        driver.collect(root, plan, partition)
    state = driver.snapshot(root, plan)
    state.update(
        billing_complete=False,
        bounded_reviewed_unknown_requests=2,
        bounded_reviewed_reservation_usd=str(
            recovery.RESERVATION + connection_recovery.CONNECTION_RESERVATION
        ),
        policy_continuation_sha256=driver.digest(policy),
        connection_continuation_sha256=driver.digest(continuation),
    )
    public: dict[str, Any] = {
        "schema_version": 1,
        "plan_sha256": driver.digest(plan),
        "cost_upper_bound": state,
        "planned_requests": len(plan["jobs"]),
        "missing_request_count": len(missing),
        "complete_frozen_grid": False,
        "complete_allowed_continuation": True,
        "quarantined_unattempted_count": len(missing),
        "provider_policy_rejection_count": 1,
        "connection_failure_count": 1,
        "policy_continuation": {
            key: policy[key]
            for key in (
                "created_at",
                "decision",
                "policy_item",
                "analysis_deviation",
                "billing_complete",
            )
        },
        "policy_continuation_sha256": driver.digest(policy),
        "connection_continuation_sha256": driver.digest(continuation),
        "connection_continuation": {
            key: continuation[key]
            for key in ("created_at", "decision", "analysis_deviation", "billing_complete")
        },
        "heldout": {},
        "development": {},
        "secondary_delivered_agreement": {},
    }
    for cohort, identifier in plan["campaigns"].items():
        protocol = driver.read(root / "approved" / (cohort + ".json"))
        with service.store._connect() as db:
            jobs = service._jobs(db, identifier)
        heldout = [j for j in jobs if j["partition"] == "holdout"]
        public["secondary_delivered_agreement"][cohort] = analyze.delivered_agreement(
            protocol, heldout, "holdout"
        )
        for partition in ("dev", "holdout"):
            report = service.analyze(identifier, partition)
            private_json(target / (cohort + "-" + partition + ".json"), report)
            public["heldout" if partition == "holdout" else "development"][cohort] = (
                analyze.public_report(report)
            )
        if cohort == "human":
            public["posthoc_policy_quarantine_sensitivity"] = posthoc_report(protocol, heldout)
            public["human_panel"] = analyze.panel_agreement_bound(
                [i for i in protocol["items"] if i["partition"] == "holdout"]
            )
        else:
            public["repository_sensitivity"] = analyze.repository_sensitivity(
                driver.read(target / "execution-holdout.json"), protocol
            )
    private_json(target / "public-results.json", public)
    return target / "public-results.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"aggregate_export": str(run(args.root))}))


if __name__ == "__main__":
    main()
