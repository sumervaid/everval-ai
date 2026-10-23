#!/usr/bin/env python3
"""Prospective small-model study analysis. No model execution or paid calls.

The study-specific mix resource unit is measured generation wall seconds. This
script does not put runtime measurements in the application's USD fields.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from cvals.models import canonical_json
from cvals.posttraining_analysis import analyze_regression

SEEDS = (17, 29, 43)
STEPS = (0, 80, 160, 320)
DEVELOPMENT_STEPS = STEPS[:3]
VALIDATION_STEP = STEPS[-1]
FAMILIES_PER_DOMAIN = 32
DOMAINS = ("routing", "extraction", "arithmetic", "injection")
DOMAIN_COUNT = len(DOMAINS)
THRESHOLD = 0.10
BUDGET_FRACTION = 0.70
BASELINE_SEED = 20261022
BASELINE_DRAWS = 32


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _finite(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return number


def _validated(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows or len(rows) > 3072:
        raise ValueError("Study observations require 1..3072 rows")
    identities = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("An observation must be a JSON object")
        if type(row.get("seed")) is not int or row["seed"] not in SEEDS:
            raise ValueError("An observation has an unplanned training seed")
        if type(row.get("step")) is not int or row["step"] not in STEPS:
            raise ValueError("An observation has an unplanned checkpoint step")
        for field in ("case_id", "family_id", "domain", "eval_id", "status", "split"):
            if not isinstance(row.get(field), str) or not row[field] or len(row[field]) > 300:
                raise ValueError(f"{field} requires a bounded nonempty string")
        if row["domain"] not in DOMAINS or row["split"] != (
            "development" if row["seed"] == 17 else "validation"
        ):
            raise ValueError("Observation domain or seed split differs from the frozen study")
        if row["status"] not in {"pending", "running", "completed", "failed", "cancelled", "error"}:
            raise ValueError("Unrecognized trial status")
        if row["eval_id"] not in {row["domain"] + ".p0", row["domain"] + ".p1"}:
            raise ValueError("Evaluation must be the frozen domain/paraphrase identity")
        if row["case_id"] != row["family_id"] + "-" + row["eval_id"].rsplit(".", 1)[1]:
            raise ValueError("Case identity must bind its underlying family and paraphrase")
        identity = (row["seed"], row["step"], row["case_id"])
        if identity in identities:
            raise ValueError(
                "Duplicate scheduled observation; attempts cannot overwrite one another"
            )
        identities.add(identity)
        if row["status"] == "completed":
            if type(row.get("passed")) is not bool:
                raise ValueError("Completed observations require an explicit boolean grade")
        elif row.get("passed") is not None:
            raise ValueError("An ungraded observation cannot claim a pass or failure")
        if row.get("elapsed_seconds") is not None:
            _finite(row["elapsed_seconds"], "elapsed_seconds", positive=True)
        if row.get("cost_usd") is not None and _finite(row["cost_usd"], "cost_usd") != 0:
            raise ValueError("This local-only protocol permits no hosted-provider spending")
        canonical_json(row)  # Also reject nonfinite or unserializable auxiliary evidence.
    return sorted(rows, key=lambda row: (row["seed"], row["step"], row["case_id"]))


def _strict_json(text: str) -> tuple[bool, Any]:
    def constant(value: str) -> None:
        raise ValueError("Nonstandard JSON constant")

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Nonfinite JSON number")
        return result

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON member")
            value[key] = item
        return value

    try:
        return True, json.loads(
            text, parse_constant=constant, parse_float=finite_float, object_pairs_hook=unique_object
        )
    except (ValueError, RecursionError):
        return False, None


def response_diagnostics(raw_text: str, expected_answer: str) -> dict[str, bool]:
    """Independently distinguish syntax, shape and exact answer fidelity."""
    if not isinstance(raw_text, str) or len(raw_text) > 100_000:
        raise ValueError("A completed response needs bounded original raw text")
    syntax, parsed = _strict_json(raw_text)
    is_object = syntax and isinstance(parsed, dict)
    contract = is_object and set(parsed) == {"answer"} and isinstance(parsed["answer"], str)
    correct = is_object and parsed.get("answer") == expected_answer
    return {
        "json_syntax_valid": syntax,
        "json_object_valid": is_object,
        "strict_answer_contract_valid": contract,
        "exact_answer_correct": correct,
        "primary_passed": contract and correct,
    }


def _bounded_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Artifact manifests require relative contained paths")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise ValueError("Artifact path escapes its frozen root")
    return target


def load_case_registry(protocol_path: Path) -> dict[str, dict[str, Any]]:
    """Verify frozen task bytes and return references, without model execution."""
    protocol = json.loads(protocol_path.read_text())
    checks = (
        (tuple(protocol["seeds"]), SEEDS),
        (tuple(protocol["checkpoint_steps"]), STEPS),
        (set(protocol["domains"]), set(DOMAINS)),
        (protocol["analysis"]["budget_fraction"], BUDGET_FRACTION),
        (protocol["analysis"]["practical_threshold"], THRESHOLD),
        (protocol["analysis"]["alpha_per_run"], 0.05 / 3),
        (protocol["analysis"]["max_looks"], 3),
        (protocol["analysis"]["min_clusters"], 5),
        (protocol["analysis"]["baseline_seed"], BASELINE_SEED),
        (protocol["analysis"]["baseline_draws"], BASELINE_DRAWS),
        (protocol["families_per_domain"], FAMILIES_PER_DOMAIN),
    )
    if any(actual != expected for actual, expected in checks):
        raise ValueError("This analysis implementation differs from the frozen protocol")
    manifest_path = _bounded_file(protocol_path.parent, protocol["dataset"]["manifest_path"])
    manifest_data = manifest_path.read_bytes()
    if hashlib.sha256(manifest_data).hexdigest() != protocol["dataset"]["manifest_sha256"]:
        raise ValueError("Dataset manifest differs from the frozen protocol")
    manifest = json.loads(manifest_data)
    if manifest["files_sha256"] != protocol["dataset"]["files"]:
        raise ValueError("Frozen dataset file manifests disagree")
    for relative, expected_sha in protocol["dataset"]["files"].items():
        data = _bounded_file(manifest_path.parent, relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError(f"Frozen dataset artifact changed: {relative}")
    registry = {}
    for split, domains in manifest["collections"].items():
        for domain, definition in domains.items():
            collection = json.loads(
                _bounded_file(manifest_path.parent, definition["path"]).read_text()
            )
            for scenario in collection["scenarios"]:
                provenance = scenario["provenance"]
                case_id = scenario["id"]
                if (
                    case_id in registry
                    or provenance["split"] != split
                    or provenance["study_domain"] != domain
                ):
                    raise ValueError("Frozen case metadata is ambiguous")
                answer = scenario["reference_output"]["answer"]
                if not isinstance(answer, str):
                    raise ValueError("Frozen reference answer must be a string")
                registry[case_id] = {
                    "split": split,
                    "domain": domain,
                    "family_id": provenance["task_family"],
                    "eval_id": provenance["eval_id"],
                    "answer": answer,
                }
    if len(registry) != 512:
        raise ValueError("Frozen dataset must have 512 distinct development/validation prompts")
    return registry


def validate_case_evidence(rows: list[dict[str, Any]], registry: dict[str, dict[str, Any]]) -> None:
    """Fail if case metadata, exported output, or primary grade differs from raw evidence."""
    for row in _validated(rows):
        expected = registry.get(row["case_id"])
        if expected is None or any(
            row[key] != expected[key] for key in ("split", "domain", "family_id", "eval_id")
        ):
            raise ValueError("Observation does not match a frozen task")
        if row["status"] != "completed":
            continue
        metrics = response_diagnostics(row.get("raw_text"), expected["answer"])
        if row["passed"] != metrics["primary_passed"]:
            raise ValueError("Exported grade disagrees with independent frozen-answer grading")
        _, parsed = _strict_json(row["raw_text"])
        expected_output = parsed if metrics["json_object_valid"] else {}
        if canonical_json(row.get("output")) != canonical_json(expected_output):
            raise ValueError("Exported output differs from the unmodified model response")


def verify_durable_projection(rows: list[dict[str, Any]], durable: list[dict[str, Any]]) -> None:
    """Compare the requested checkpoint grid exactly with durable trial projections."""
    rows = _validated(rows)
    grid = {(row["seed"], row["step"]) for row in rows}
    retained = _validated([row for row in durable if (row["seed"], row["step"]) in grid])
    if canonical_json(rows) != canonical_json(retained):
        raise ValueError("Exported observations differ from durable Store trial evidence")


def validate_checkpoint_evidence(rows: list[dict[str, Any]], protocol_path: Path) -> None:
    """Bind all observations to retained checkpoint artifacts and the execution plan."""
    from cvals.config import Settings
    from cvals.store import Store
    from scripts.posttraining.adapter import adapter_revision, checkpoint_identity
    from scripts.posttraining.model_runtime import fingerprint
    from scripts.posttraining.run_study import observations_from_store

    study = protocol_path.parent
    protocol_sha = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    protocol = json.loads(protocol_path.read_text())
    state = json.loads((study / "execution-plan.json").read_text())
    if state["protocol_sha256"] != protocol_sha:
        raise ValueError("Execution plan belongs to another protocol")
    if Path(state["workspace"]).resolve() != (study / "workspace").resolve():
        raise ValueError("Execution plan workspace is not the retained study workspace")
    store = Store(Settings(data_dir=study / "workspace", allow_remote_providers=False))
    verify_durable_projection(rows, observations_from_store(state, store))
    base = study / "qwen-mlx-4bit"
    if fingerprint(base) != protocol["model"]["converted_manifest"]:
        raise ValueError("Base model artifact changed")
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["seed"], row["step"]].append(row)
    seen_weights = set()
    root = Path(__file__).resolve().parents[2]
    for (seed, step), items in groups.items():
        checkpoint = state["checkpoints"][f"{seed}:{step}"]
        identity = checkpoint_identity(base, study / f"training/seed-{seed}/checkpoint-{step}")
        revision = adapter_revision(
            identity,
            model_id=f"qwen05b-s{seed}-step{step}",
            max_tokens=32,
            max_prompt_tokens=512,
            requirements_lock=study / "runtime-freeze.txt",
            runtime_source=root / "scripts/posttraining/model_runtime.py",
        )
        if (
            checkpoint["weight_sha256"] != identity["weight_sha256"]
            or checkpoint["adapter_revision"] != revision
        ):
            raise ValueError("Recorded checkpoint identity differs from retained artifacts")
        if identity["weight_sha256"] in seen_weights:
            raise ValueError("Distinct scheduled checkpoints reused the same weight artifact")
        seen_weights.add(identity["weight_sha256"])
        linked = {link["experiment_id"] for link in checkpoint["experiments"]}
        for row in items:
            if (
                any(
                    row.get(key) != checkpoint[key]
                    for key in ("weight_sha256", "adapter_revision", "checkpoint_id")
                )
                or row.get("experiment_id") not in linked
            ):
                raise ValueError("Observation provenance differs from its scheduled checkpoint")


def _grid(rows: list[dict[str, Any]], expected: set[tuple[int, int]]) -> dict[str, str]:
    actual = {(row["seed"], row["step"]) for row in rows}
    if actual != expected:
        raise ValueError("Observation checkpoint grid differs from the frozen study phase")
    eval_domains = {row["eval_id"]: row["domain"] for row in rows}
    if len(eval_domains) != 8 or len(set(eval_domains.values())) != DOMAIN_COUNT:
        raise ValueError("The study requires eight evaluations across four domains")
    by_checkpoint: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_checkpoint[row["seed"], row["step"]].append(row)
    seed_cases: dict[int, set[tuple[str, str, str, str, str]]] = {}
    for (seed, _step), items in by_checkpoint.items():
        if len(items) != FAMILIES_PER_DOMAIN * DOMAIN_COUNT * 2:
            raise ValueError("Every checkpoint must retain all 256 scheduled rows")
        cases = {
            (r["case_id"], r["family_id"], r["eval_id"], r["domain"], r["split"]) for r in items
        }
        if seed in seed_cases and seed_cases[seed] != cases:
            raise ValueError("Checkpoint task identities or metadata changed within a seed")
        seed_cases[seed] = cases
        for domain in sorted(set(eval_domains.values())):
            families: dict[str, set[str]] = defaultdict(set)
            for row in items:
                if row["domain"] == domain:
                    families[row["family_id"]].add(row["eval_id"])
            if len(families) != FAMILIES_PER_DOMAIN or any(
                evaluations != {domain + ".p0", domain + ".p1"} for evaluations in families.values()
            ):
                raise ValueError("Each domain requires 32 families with exactly two paraphrases")
    if 29 in seed_cases and 43 in seed_cases and seed_cases[29] != seed_cases[43]:
        raise ValueError("Validation seeds must use the same frozen withheld cohort")
    if 17 in seed_cases:
        development = {row[1] for row in seed_cases[17]}
        for seed in (29, 43):
            if seed in seed_cases and development & {row[1] for row in seed_cases[seed]}:
                raise ValueError("Development and validation family identities overlap")
    return eval_domains


def _effects(rows: list[dict[str, Any]]) -> dict[tuple[int, int, str], dict[str, Any]]:
    indexed = {(row["seed"], row["step"], row["case_id"]): row for row in rows}
    grouped: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["step"] != 0:
            grouped[row["seed"], row["step"], row["eval_id"]].append(row)
    result = {}
    for key, items in grouped.items():
        paired = [(indexed.get((r["seed"], 0, r["case_id"])), r) for r in items]
        complete = all(
            a is not None and a["status"] == b["status"] == "completed" for a, b in paired
        )
        times = [r["elapsed_seconds"] for r in items if r.get("elapsed_seconds") is not None]
        result[key] = {
            "delta": mean(float(b["passed"]) - float(a["passed"]) for a, b in paired)
            if complete
            else None,
            "wall_seconds": sum(times) if len(times) == len(items) else None,
            "known_wall_seconds": sum(times),
            "scheduled": len(items),
            "completed_pairs": sum(
                a is not None and a["status"] == b["status"] == "completed" for a, b in paired
            ),
        }
    return result


def freeze_mix(rows: list[dict[str, Any]], *, protocol_sha256: str) -> dict[str, Any]:
    """Build a deterministic selection using only the entire allowed development phase."""
    if len(protocol_sha256) != 64 or any(c not in "0123456789abcdef" for c in protocol_sha256):
        raise ValueError("A frozen protocol SHA-256 is required")
    rows = _validated(rows)
    domains = _grid(rows, {(17, step) for step in DEVELOPMENT_STEPS})
    if any(row["status"] != "completed" or row.get("elapsed_seconds") is None for row in rows):
        raise ValueError(
            "Mix fitting requires complete development grades and measured positive runtime"
        )
    effects = _effects(rows)
    evaluations = sorted(domains)
    checkpoints = STEPS[1:3]
    costs = {
        e: mean(effects[17, step, e]["wall_seconds"] for step in checkpoints) for e in evaluations
    }
    full_seconds = sum(costs.values())
    ceiling = full_seconds * BUDGET_FRACTION
    by_domain = {
        d: sorted(e for e in evaluations if domains[e] == d) for d in sorted(set(domains.values()))
    }

    def loss(selected: set[str]) -> float:
        losses = []
        for step in checkpoints:
            for domain_evaluations in by_domain.values():
                selected_here = selected.intersection(domain_evaluations)
                if not selected_here:
                    losses.append(3.0)
                    continue
                reference = mean(effects[17, step, e]["delta"] for e in domain_evaluations)
                estimate = mean(effects[17, step, e]["delta"] for e in selected_here)
                losses.append(
                    abs(reference - estimate)
                    + float((reference < -THRESHOLD) != (estimate < -THRESHOLD))
                )
        return mean(losses)

    selected = {min(es, key=lambda e: (costs[e], e)) for es in by_domain.values()}
    if sum(costs[e] for e in selected) > ceiling:
        raise ValueError("The frozen time ceiling cannot cover all protected domains")
    while True:
        spent, current = sum(costs[e] for e in selected), loss(selected)
        candidates = [e for e in evaluations if e not in selected and spent + costs[e] <= ceiling]
        if not candidates:
            break
        best = min(candidates, key=lambda e: (-(current - loss(selected | {e})) / costs[e], e))
        if current - loss(selected | {best}) <= 0:
            break
        selected.add(best)
    feasible = []
    for count in range(1, len(evaluations) + 1):
        for subset in itertools.combinations(evaluations, count):
            if set(domains[e] for e in subset) != set(by_domain):
                continue
            seconds = sum(costs[e] for e in subset)
            if seconds <= ceiling:
                feasible.append(
                    {
                        "evaluations": list(subset),
                        "development_wall_seconds": seconds,
                        "development_loss": loss(set(subset)),
                    }
                )
    stratified = [subset for subset in feasible if len(subset["evaluations"]) == DOMAIN_COUNT]
    rng = random.Random(BASELINE_SEED)
    random_indices = [rng.randrange(len(feasible)) for _ in range(BASELINE_DRAWS)]
    stratified_indices = [rng.randrange(len(stratified)) for _ in range(BASELINE_DRAWS)]
    strategies = {
        "selected": sorted(selected),
        "full_suite": evaluations,
        **{
            f"random-{i:02}": feasible[index]["evaluations"]
            for i, index in enumerate(random_indices)
        },
        **{
            f"stratified-{i:02}": stratified[index]["evaluations"]
            for i, index in enumerate(stratified_indices)
        },
    }
    result = {
        "schema_version": 1,
        "method": "prospective_greedy_wall_seconds_v1",
        "protocol_sha256": protocol_sha256,
        "development_observations_sha256": digest(rows),
        "development_grid": [[17, step] for step in DEVELOPMENT_STEPS],
        "development_families": sorted({row["family_id"] for row in rows}),
        "eval_domains": domains,
        "cost_unit": "wall_seconds",
        "budget_fraction": BUDGET_FRACTION,
        "budget_wall_seconds": ceiling,
        "full_suite_development_wall_seconds": full_seconds,
        "practical_threshold": THRESHOLD,
        "protected_domains": sorted(by_domain),
        "development_costs_wall_seconds": costs,
        "strategies": strategies,
        "strategy_development_loss": {name: loss(set(es)) for name, es in strategies.items()},
        "feasible_subsets": feasible,
        "stratified_feasible_subsets": stratified,
        "baseline_seed": BASELINE_SEED,
        "baseline_draws": BASELINE_DRAWS,
        "random_draw_indices": random_indices,
        "stratified_draw_indices": stratified_indices,
        "cash_provider_fees_usd": 0.0,
        "limitations": [
            "Runtime forecasts use development generation times, not total operational or training time.",
            "A common ceiling does not mean identical actual strategy cost.",
            "Repeated baseline draws are not independent model experiments.",
            "Descriptive full-reference threshold crossings are not confirmed statistical regressions.",
        ],
    }
    result["selection_sha256"] = digest(result)
    return result


def verify_selection(
    selection: dict[str, Any], *, protocol_sha256: str, development_rows: list[dict[str, Any]]
) -> None:
    """Fail closed before phase-two inference if protocol/evidence/selection changed."""
    expected = freeze_mix(development_rows, protocol_sha256=protocol_sha256)
    if canonical_json(selection) != canonical_json(expected):
        raise ValueError(
            "Frozen selection does not match the protocol and complete development evidence"
        )


def _strategy_metrics(
    effects: dict[tuple[int, int, str], dict[str, Any]],
    evaluations: list[str],
    domains: dict[str, str],
) -> dict[str, Any]:
    cells = []
    for seed in (29, 43):
        for domain in sorted(set(domains.values())):
            full = [
                effects[seed, VALIDATION_STEP, e]["delta"] for e in domains if domains[e] == domain
            ]
            own = [
                effects[seed, VALIDATION_STEP, e]["delta"]
                for e in evaluations
                if domains[e] == domain
            ]
            reference = mean(full) if full and all(v is not None for v in full) else None
            estimate = mean(own) if own and all(v is not None for v in own) else None
            cells.append(
                {
                    "seed": seed,
                    "step": VALIDATION_STEP,
                    "domain": domain,
                    "reference_delta": reference,
                    "estimated_delta": estimate,
                }
            )
    comparable = [
        row
        for row in cells
        if row["reference_delta"] is not None and row["estimated_delta"] is not None
    ]
    positives = [row for row in comparable if row["reference_delta"] < -THRESHOLD]
    negatives = [row for row in comparable if row["reference_delta"] >= -THRESHOLD]
    detected = sum(row["estimated_delta"] < -THRESHOLD for row in positives)
    false_alerts = sum(row["estimated_delta"] < -THRESHOLD for row in negatives)
    observed_costs = [
        effects[seed, VALIDATION_STEP, e]["wall_seconds"] for seed in (29, 43) for e in evaluations
    ]
    return {
        "evaluations": evaluations,
        "cells": cells,
        "comparable_cells": len(comparable),
        "unknown_cells": len(cells) - len(comparable),
        "reference_regressions": len(positives),
        "unknown_reference_cells": sum(row["reference_delta"] is None for row in cells),
        "unknown_estimate_cells": sum(row["estimated_delta"] is None for row in cells),
        "detected_regressions": detected,
        "missed_regressions": len(positives) - detected,
        "detection_recall": detected / len(positives) if positives else None,
        "reference_nonregressions": len(negatives),
        "false_alerts": false_alerts,
        "false_alert_rate": false_alerts / len(negatives) if negatives else None,
        "mean_absolute_delta_error": mean(
            abs(row["reference_delta"] - row["estimated_delta"]) for row in comparable
        )
        if comparable
        else None,
        "wall_seconds": sum(observed_costs) if all(v is not None for v in observed_costs) else None,
        "known_wall_seconds": sum(
            effects[seed, VALIDATION_STEP, e]["known_wall_seconds"]
            for seed in (29, 43)
            for e in evaluations
        ),
        "runtime_complete": all(v is not None for v in observed_costs),
    }


def analyze_study(
    rows: list[dict[str, Any]],
    selection: dict[str, Any],
    *,
    protocol_sha256: str,
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    validate_case_evidence(rows, registry)
    rows = _validated(rows)
    domains = _grid(rows, {(seed, step) for seed in SEEDS for step in STEPS})
    development = [row for row in rows if row["seed"] == 17 and row["step"] in DEVELOPMENT_STEPS]
    verify_selection(selection, protocol_sha256=protocol_sha256, development_rows=development)
    if domains != selection["eval_domains"]:
        raise ValueError("Validation evaluation domains differ from frozen selection")
    trajectories, comparisons = [], []
    for seed in SEEDS:
        for domain in sorted(set(domains.values())):
            baseline = [
                r for r in rows if r["seed"] == seed and r["step"] == 0 and r["domain"] == domain
            ]
            for step in STEPS:
                items = [
                    r
                    for r in rows
                    if r["seed"] == seed and r["step"] == step and r["domain"] == domain
                ]
                completed = [r for r in items if r["status"] == "completed"]
                diagnostics = [
                    response_diagnostics(r["raw_text"], registry[r["case_id"]]["answer"])
                    for r in completed
                ]
                counts = {
                    key: sum(value[key] for value in diagnostics)
                    for key in (
                        "json_syntax_valid",
                        "json_object_valid",
                        "strict_answer_contract_valid",
                        "exact_answer_correct",
                    )
                }
                object_count = counts["json_object_valid"]
                trajectories.append(
                    {
                        "seed": seed,
                        "step": step,
                        "domain": domain,
                        "scheduled": len(items),
                        "completed": len(completed),
                        "unknown": len(items) - len(completed),
                        "passes": sum(r["passed"] for r in completed),
                        "complete_pass_rate": mean(r["passed"] for r in completed)
                        if len(completed) == len(items)
                        else None,
                        "response_diagnostics": {
                            **{
                                key: {
                                    "count": count,
                                    "denominator": len(completed),
                                    "rate": count / len(completed) if completed else None,
                                }
                                for key, count in counts.items()
                                if key != "exact_answer_correct"
                            },
                            "exact_answer_accuracy_given_valid_json_object": {
                                "count": counts["exact_answer_correct"],
                                "denominator": object_count,
                                "rate": counts["exact_answer_correct"] / object_count
                                if object_count
                                else None,
                            },
                        },
                    }
                )
                if step == 0:
                    continue
                expected = [
                    {"scenario_id": r["case_id"], "repetition": 0, "cluster_id": r["family_id"]}
                    for r in baseline
                ]

                def measurements(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
                    return [
                        {
                            "scenario_id": r["case_id"],
                            "repetition": 0,
                            "cluster_id": r["family_id"],
                            "status": r["status"],
                            "score": float(r["passed"]) if r["status"] == "completed" else None,
                        }
                        for r in values
                    ]

                args = {
                    "baseline": measurements(baseline),
                    "candidate": measurements(items),
                    "expected_pairs": expected,
                    "alpha": 0.05 / 3,
                    "planned_looks": 3,
                    "planned_domains": DOMAIN_COUNT,
                    "min_clusters": 5,
                    "practical_threshold": THRESHOLD,
                    "look_index": STEPS[1:].index(step) + 1,
                }
                result = analyze_regression(**args)
                # Sensitivity: treat every instance of a surface form as one dependent cluster.
                template_by_case = {r["case_id"]: r["eval_id"] for r in baseline}
                sensitivity_args = {
                    **args,
                    **{
                        key: [
                            {**r, "cluster_id": template_by_case[r["scenario_id"]]}
                            for r in args[key]
                        ]
                        for key in ("baseline", "candidate", "expected_pairs")
                    },
                }
                sensitivity = analyze_regression(**sensitivity_args)
                comparisons.append(
                    {
                        "seed": seed,
                        "step": step,
                        "domain": domain,
                        **result,
                        "template_dependence_sensitivity": sensitivity,
                        "inference_scope": "exploratory generated-population study",
                    }
                )
    effects = _effects(rows)
    strategies = {
        name: _strategy_metrics(effects, es, domains)
        for name, es in selection["strategies"].items()
    }
    full_cost = strategies["full_suite"]["wall_seconds"]
    for result in strategies.values():
        observed = result["wall_seconds"]
        result["counterfactual_generation_time_saved_fraction"] = (
            1 - observed / full_cost if observed is not None and full_cost else None
        )
        result["development_forecast_wall_seconds_for_two_checkpoints"] = 2 * sum(
            selection["development_costs_wall_seconds"][e] for e in result["evaluations"]
        )
        result["exceeds_frozen_two_checkpoint_ceiling"] = (
            observed > 2 * selection["budget_wall_seconds"] if observed is not None else None
        )
    return {
        "schema_version": 1,
        "protocol_sha256": protocol_sha256,
        "observations_sha256": digest(rows),
        "selection_sha256": selection["selection_sha256"],
        "scheduled_generations": len(rows),
        "completed_generations": sum(r["status"] == "completed" for r in rows),
        "known_generation_wall_seconds": sum(
            r["elapsed_seconds"] for r in rows if r.get("elapsed_seconds") is not None
        ),
        "generation_runtime_complete": all(r.get("elapsed_seconds") is not None for r in rows),
        "cash_provider_fees_usd": sum(r["cost_usd"] for r in rows if r.get("cost_usd") is not None),
        "cash_fee_accounting_complete": all(r.get("cost_usd") is not None for r in rows),
        "hardware_energy_cost_usd": None,
        "trajectories": trajectories,
        "comparisons": comparisons,
        "mix_validation": strategies,
        "limitations": [
            "Actual small-model SFT on generated diagnostic tasks; no representative enterprise or frontier-model claim.",
            "Thirty-two underlying record families per domain share two surface templates; independence is conditional on the generator.",
            "Validation seeds share task families and are not additional independent task samples.",
            "Wide bounded intervals and the two-template sensitivity can remain inconclusive.",
            "JSON validity and exact grading are separate descriptive measurements; no human semantic calibration is claimed.",
            "Mix validation has eight primary cells; full-suite threshold crossings are descriptive, not ground truth.",
            "Subset time savings are counterfactual; this experiment actually executed the full suite.",
            "Measured times retain first-call compilation and cache/order effects. A separately executed subset can have different runtime.",
        ],
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Observation file exceeds 64 MiB")
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_once(path: Path, value: dict[str, Any]) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(
                "Existing analysis artifact differs; do not overwrite research evidence"
            )
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "report"))
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from scripts.posttraining.run_study import verify_protocol

    if args.protocol.name != "frozen-protocol.json":
        raise ValueError("Use the immutable frozen-protocol.json in the study artifact root")
    _, protocol_sha256, _ = verify_protocol(args.protocol.parent)
    rows = _read_rows(args.observations)
    registry = load_case_registry(args.protocol)
    validate_case_evidence(rows, registry)
    validate_checkpoint_evidence(rows, args.protocol)
    if args.action == "freeze":
        value = freeze_mix(rows, protocol_sha256=protocol_sha256)
    else:
        if args.selection is None:
            parser.error("report requires --selection")
        value = analyze_study(
            rows,
            json.loads(args.selection.read_text()),
            protocol_sha256=protocol_sha256,
            registry=registry,
        )
    _write_once(args.output, value)
    print(
        json.dumps(
            {
                "artifact": str(args.output),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
