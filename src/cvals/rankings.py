"""Observed system rankings on saved, frozen benchmark cohorts.

This view never runs a model, combines benchmark scores, or selects a favorable
rerun. Cells retain the latest evidence, including incomplete execution.
"""

from __future__ import annotations

import math
from fractions import Fraction
from statistics import mean
from typing import TYPE_CHECKING, Any

from cvals.comparison import evidence_issues
from cvals.models import content_hash
from cvals.reports import build_report

if TYPE_CHECKING:
    from cvals.store import Store

STORE_LIST_LIMIT = 500
DEFAULT_WINDOW = 100
METRICS = ("quality", "cost", "latency")


def _images(experiment: dict[str, Any]) -> list[str]:
    return sorted(
        {
            check["actual"]["image_digest"]
            for trial in experiment["trials"]
            for check in trial.get("checks", [])
            if isinstance(check.get("actual"), dict)
            and isinstance(check["actual"].get("image_digest"), str)
        }
    )


def _cohort(experiment: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    snapshot = experiment["snapshot"]
    collection = snapshot["collection"]
    contract = {
        key: snapshot.get(key)
        for key in (
            "grader_revision",
            "harness_revision",
            "protocol",
            "sandbox_image_requested",
            "repetitions",
            "budget_usd",
        )
    }
    contract["observed_verifier_images"] = _images(experiment)
    contract["scheduled_system_count"] = len(snapshot["systems"])
    contract_hash = content_hash(contract)
    identity = content_hash(
        {"collection_hash": experiment["collection_hash"], "execution_contract": contract}
    )
    return identity, {
        "id": identity,
        "name": collection["name"],
        "version": collection["version"],
        "domain": collection["domain"],
        "collection_id": collection["id"],
        "collection_hash": experiment["collection_hash"],
        "benchmark_release_id": collection.get("benchmark_release_id"),
        "benchmark_partition": collection.get("benchmark_partition"),
        "contract_hash": contract_hash,
        "execution_contract": contract,
        "repetitions": snapshot["repetitions"],
        "scenario_count": len(collection["scenarios"]),
        "experiment_count": 0,
        "latest_created_at": experiment["created_at"],
        "ranked_systems": {metric: 0 for metric in METRICS},
    }


def _system(system: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": content_hash(system),
        "system_ids": [system["id"]],
        "name": system["name"],
        "model": system.get("model", ""),
        "kind": system["kind"],
        "harness": system.get("harness", "single-turn"),
        "fixture_control": system["kind"] in {"reference", "incomplete"},
        "submission_replay": system["kind"] == "replay",
        "external_adapter": system.get("harness") == "external-agent",
    }


def _cell(
    experiment: dict[str, Any],
    benchmark_id: str,
    system: dict[str, Any],
    summary: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    trials = [trial for trial in experiment["trials"] if trial["system_id"] == system["id"]]
    scheduled = summary["scheduled"]
    durations = [
        trial["duration_ms"]
        for trial in trials
        if type(trial.get("duration_ms")) in {int, float}
        and math.isfinite(trial["duration_ms"])
        and trial["duration_ms"] >= 0
    ]
    duration_complete = bool(scheduled) and len(durations) == scheduled
    issues = list(evidence)
    snapshot = experiment["snapshot"]
    if any(
        not snapshot.get(key)
        for key in ("grader_revision", "harness_revision", "protocol", "sandbox_image_requested")
    ):
        issues.append("The frozen execution contract is incomplete.")
    verified = not issues
    if summary["fixture_control"]:
        issues.append("Fixture control; not measured model performance.")
    if summary["submission_replay"]:
        issues.append("Recorded submissions; not new independent model execution.")
    finalized = experiment["status"] in {"completed", "failed"}
    if not finalized:
        issues.append("Run is unfinished or cancelled; rankings are withheld.")
    graded = bool(scheduled) and summary["completed"] == scheduled
    if not graded:
        issues.append("Some scheduled trials have no completed grade.")
    quality = bool(verified and finalized and graded and system["kind"] == "http")
    if not summary["cost_complete"]:
        issues.append("Cost is incomplete; known spending is a lower bound.")
    if not duration_complete:
        issues.append("Some scheduled attempts have no recorded duration.")
    if not summary["passed"]:
        issues.append("No passing attempt; cost per success is unavailable.")
    if system.get("harness") == "external-agent":
        issues.append(
            "External adapter timing measures its service request; internal stages are self-reported."
        )
    result = {
        "benchmark_id": benchmark_id,
        "system_id": content_hash(system),
        "source_system_id": system["id"],
        "experiment_id": experiment["id"],
        "experiment_name": experiment["name"],
        "created_at": experiment["created_at"],
        "snapshot_hash": experiment["snapshot_hash"],
        "status": experiment["status"],
        "pass_rate": summary["pass_rate"],
        "passed": summary["passed"],
        "scheduled": scheduled,
        "completed": summary["completed"],
        "completion_rate": summary["completed"] / scheduled if scheduled else None,
        "errors": summary["errors"],
        "incomplete": summary["incomplete"],
        "ci_low": summary["ci_low"],
        "ci_high": summary["ci_high"],
        "interval_method": summary["interval_method"],
        "known_cost_usd": summary["known_cost_usd"],
        "cost_usd": summary["cost_usd"],
        "cost_complete": summary["cost_complete"],
        "cost_per_success_usd": summary["cost_per_pass"],
        "mean_duration_ms": mean(durations) if durations else None,
        "median_duration_ms": summary["median_duration_ms"],
        "p95_duration_ms": summary["p95_duration_ms"],
        "duration_complete": duration_complete,
        "duration_recorded": len(durations),
        "ranks": dict.fromkeys(METRICS),
        "eligibility": {
            "quality": quality,
            "cost": quality and summary["cost_complete"] and summary["cost_per_pass"] is not None,
            "latency": quality and duration_complete,
        },
        "issues": issues,
    }
    return result


def _rank(cells: list[dict[str, Any]], metric: str) -> int:
    eligible = [cell for cell in cells if cell["eligibility"][metric]]

    def value(cell: dict[str, Any]) -> Fraction | float:
        if metric == "quality":
            return -Fraction(cell["passed"], cell["scheduled"])
        if metric == "cost":
            return float(cell["cost_per_success_usd"])
        return float(cell["p95_duration_ms"])

    ordered = sorted(eligible, key=lambda cell: (value(cell), cell["system_id"]))
    previous = None
    rank = 0
    for index, cell in enumerate(ordered, start=1):
        score = value(cell)
        if index == 1 or score != previous:
            rank = index
        cell["ranks"][metric] = rank
        previous = score
    return len(eligible)


def _withhold_obscured_variants(
    histories: dict[tuple[str, str], list[dict[str, Any]]],
    cells: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Missing observations cannot turn a failed rerun into a new ranked cohort.

    Fully observed, graded image variants stay separate. An incomplete newer
    attempt cannot establish such a variant, so older cells for the same frozen
    requested contract and configuration remain visible without ranks.
    """
    withheld = 0
    for history in histories.values():
        images_expected = any(event["image_observed"] for event in history)
        uncertain = [
            event
            for event in history
            if not event["execution_complete"] or (images_expected and not event["image_complete"])
        ]
        for event in history:
            cell = event["cell"]
            key = (cell["benchmark_id"], cell["system_id"])
            retained = cells.get(key)
            if retained is None or retained is not cell:
                continue
            cell = retained
            newer = next((other for other in uncertain if other["order"] > event["order"]), None)
            image_unknown = images_expected and not event["image_complete"]
            if newer is None and not image_unknown:
                continue
            if any(cell["eligibility"].values()):
                withheld += 1
            cell["eligibility"] = dict.fromkeys(METRICS, False)
            if image_unknown:
                cell["issues"].append(
                    "Verifier image evidence is incomplete for this saved run; ranks are withheld."
                )
            if newer is not None:
                cell["issues"].append(
                    "Newer run "
                    + newer["cell"]["experiment_id"]
                    + " has incomplete execution or verifier image evidence for this frozen "
                    "contract and configuration; this older result is retained without ranks."
                )
    return withheld


def build_rankings(store: Store, *, limit: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Return metadata-only cells from a bounded recent-run window.

    Rows identify complete frozen system configurations. Columns identify frozen
    collection and execution contracts. Within each cell, creation time and run
    ID choose the latest record regardless of outcome. Tied observations share
    competition ranks (1, 1, 3); no overall cross-benchmark score is computed.
    """
    if type(limit) is not int or not 1 <= limit <= STORE_LIST_LIMIT:
        raise ValueError("Rankings window must be an integer from 1 to 500")
    summaries = sorted(
        store.list_experiments(),
        key=lambda item: (item["created_at"], item["id"]),
        reverse=True,
    )
    selected = summaries[:limit]
    truncated = len(summaries) > limit or len(summaries) >= STORE_LIST_LIMIT
    benchmarks: dict[str, dict[str, Any]] = {}
    systems: dict[str, dict[str, Any]] = {}
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    histories: dict[tuple[str, str], list[dict[str, Any]]] = {}
    skipped = []
    warnings = [
        "Ranks are descriptive observations within one frozen cohort, not proof of superiority.",
        "Latest evidence is selected by run creation time, never by best score; absent cells are not zero.",
        "All scheduled attempts remain in pass-rate denominators. Incomplete grades remain visible and unranked.",
        "Fixture controls and recorded submissions remain visible and unranked.",
        "Cost ranks use complete cost per passing attempt; latency ranks use complete P95 duration coverage.",
        "Benchmark versions, repetition counts, budgets and execution-image variants remain separate.",
        "Repeated tasks are not independent samples; intervals do not correct benchmark selection bias.",
    ]
    if truncated:
        warnings.append(
            f"Only the most recent {limit} runs are scanned; older evidence may be outside this view."
        )
    for item in selected:
        try:
            experiment = store.get_experiment(item["id"])
            evidence = evidence_issues(experiment)
            if evidence:
                raise ValueError("Saved experiment evidence is inconsistent")
            benchmark_id, benchmark = _cohort(experiment)
            report = build_report(experiment)
            summaries_by_system = {summary["system_id"]: summary for summary in report["systems"]}
            current_cells = []
            current_systems = []
            for system in experiment["systems"]:
                system_metadata = _system(system)
                current_systems.append(system_metadata)
                current_cells.append(
                    _cell(
                        experiment,
                        benchmark_id,
                        system,
                        summaries_by_system[system["id"]],
                        evidence,
                    )
                )
        except (KeyError, ValueError, TypeError):
            skipped.append(item["id"])
            continue
        if benchmark_id not in benchmarks:
            benchmarks[benchmark_id] = benchmark
        benchmarks[benchmark_id]["experiment_count"] += 1
        requested_cohort = content_hash(
            {
                "collection_hash": benchmark["collection_hash"],
                "execution_contract": {
                    key: value
                    for key, value in benchmark["execution_contract"].items()
                    if key != "observed_verifier_images"
                },
            }
        )
        for system_metadata, cell in zip(current_systems, current_cells, strict=True):
            systems.setdefault(system_metadata["id"], system_metadata)
            # Newest runs were read first. Never replace an incomplete cell with
            # an older successful result.
            cells.setdefault((benchmark_id, cell["system_id"]), cell)
            trials = [
                trial
                for trial in experiment["trials"]
                if trial["system_id"] == cell["source_system_id"]
            ]
            image_coverage = [bool(_images({"trials": [trial]})) for trial in trials]
            histories.setdefault((requested_cohort, cell["system_id"]), []).append(
                {
                    "order": (experiment["created_at"], experiment["id"]),
                    "cell": cell,
                    "image_observed": any(image_coverage),
                    "image_complete": bool(image_coverage) and all(image_coverage),
                    "execution_complete": experiment["status"] in {"completed", "failed"}
                    and bool(cell["scheduled"])
                    and cell["completed"] == cell["scheduled"],
                }
            )
    obscured = _withhold_obscured_variants(histories, cells)
    if obscured:
        warnings.append(
            f"{obscured} retained cells have ranks withheld because newer execution or "
            "verifier image evidence is incomplete. Inspect their run references."
        )
    if skipped:
        warnings.append(
            f"{len(skipped)} recent runs could not be verified. Rankings are withheld rather than silently using older evidence."
        )
        for cell in cells.values():
            cell["eligibility"] = dict.fromkeys(METRICS, False)
            cell["issues"].append("Recent evidence could not be verified; history is incomplete.")
    for benchmark_id, benchmark in benchmarks.items():
        own = [cell for (identity, _), cell in cells.items() if identity == benchmark_id]
        benchmark["ranked_systems"] = {metric: _rank(own, metric) for metric in METRICS}
    benchmark_list = sorted(
        benchmarks.values(), key=lambda row: (row["name"].casefold(), row["version"], row["id"])
    )
    system_list = sorted(
        systems.values(), key=lambda row: (row["name"].casefold(), row["model"], row["id"])
    )
    return {
        "schema_version": 1,
        "benchmarks": benchmark_list,
        "systems": system_list,
        "cells": sorted(cells.values(), key=lambda cell: (cell["benchmark_id"], cell["system_id"])),
        "metric_definitions": {
            "quality": "Observed pass rate across all scheduled trials; higher is better.",
            "cost": "Complete configured cost per passing attempt; lower is better.",
            "latency": "P95 duration across all scheduled attempts with complete timing; lower is better.",
            "ties": "Equal observed values share competition ranks: 1, 1, 3.",
            "system_identity": "Fingerprint of the complete frozen system configuration.",
            "timing_scope": "Recorded attempt duration, including errors; external adapters measure service-request time.",
        },
        "window": {
            "limit": limit,
            "listed_runs": len(summaries),
            "scanned_runs": len(selected),
            "truncated": truncated,
            "store_list_limit": STORE_LIST_LIMIT,
        },
        "counts": {
            "benchmarks": len(benchmarks),
            "systems": len(systems),
            "cells": len(cells),
            "ranked_cells": sum(cell["eligibility"]["quality"] for cell in cells.values()),
            "experiments_scanned": len(selected),
            "experiments_skipped": len(skipped),
        },
        "unverified_experiment_ids": skipped,
        "warnings": warnings,
    }
