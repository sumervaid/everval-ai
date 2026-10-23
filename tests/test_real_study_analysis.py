"""Tests of prospective measurement rules using fabricated evidence only."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from scripts.posttraining.analyze_real_study import (
    DOMAINS,
    SEEDS,
    STEPS,
    _write_once,
    analyze_study,
    freeze_mix,
    load_case_registry,
    response_diagnostics,
    validate_case_evidence,
    verify_durable_projection,
    verify_selection,
)

PROTOCOL_SHA = "a" * 64


def evidence(full=False):
    rows, registry = [], {}
    for seed in SEEDS if full else (17,):
        split = "development" if seed == 17 else "validation"
        for step in STEPS if full else STEPS[:3]:
            for domain in DOMAINS:
                for index in range(32):
                    family = f"{split}-{domain}-{index:03}"
                    for paraphrase in (0, 1):
                        case = f"{family}-p{paraphrase}"
                        passed = (
                            index < 20
                            if step == 0
                            else index
                            < (10 if seed != 17 and domain == "arithmetic" else 27 - paraphrase * 3)
                        )
                        output = {"answer": "right" if passed else "wrong"}
                        row = {
                            "seed": seed,
                            "step": step,
                            "case_id": case,
                            "family_id": family,
                            "domain": domain,
                            "eval_id": f"{domain}.p{paraphrase}",
                            "split": split,
                            "status": "completed",
                            "passed": passed,
                            "elapsed_seconds": 0.2 + paraphrase * 0.03,
                            "cost_usd": 0.0,
                            "raw_text": json.dumps(output),
                            "output": output,
                        }
                        rows.append(row)
                        registry[case] = {
                            "family_id": family,
                            "domain": domain,
                            "eval_id": row["eval_id"],
                            "split": split,
                            "answer": "right",
                        }
    return rows, registry


def test_selection_is_deterministic_covered_and_budget_feasible():
    rows, _ = evidence()
    selection = freeze_mix(rows, protocol_sha256=PROTOCOL_SHA)
    assert selection == freeze_mix(list(reversed(rows)), protocol_sha256=PROTOCOL_SHA)
    assert selection["cost_unit"] == "wall_seconds"
    assert selection["cash_provider_fees_usd"] == 0.0
    assert len(selection["strategies"]) == 66
    for name, selected in selection["strategies"].items():
        if name == "full_suite":
            continue
        assert {selection["eval_domains"][item] for item in selected} == set(DOMAINS)
        assert (
            sum(selection["development_costs_wall_seconds"][item] for item in selected)
            <= selection["budget_wall_seconds"]
        )


def test_validation_outcomes_never_enter_selection_and_phase1_is_frozen():
    rows, _ = evidence()
    selected = freeze_mix(rows, protocol_sha256=PROTOCOL_SHA)
    verify_selection(selected, protocol_sha256=PROTOCOL_SHA, development_rows=rows)
    changed = copy.deepcopy(rows)
    changed[0]["passed"] = not changed[0]["passed"]
    with pytest.raises(ValueError, match="Frozen selection"):
        verify_selection(selected, protocol_sha256=PROTOCOL_SHA, development_rows=changed)
    with pytest.raises(ValueError, match="Frozen selection"):
        verify_selection(selected, protocol_sha256="b" * 64, development_rows=rows)
    full, _ = evidence(full=True)
    with pytest.raises(ValueError, match="checkpoint grid"):
        freeze_mix(full, protocol_sha256=PROTOCOL_SHA)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "time", "grade", "split", "usd"])
def test_selection_rejects_incomplete_or_misassigned_evidence(mutation):
    rows, _ = evidence()
    if mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "missing":
        rows.pop()
    elif mutation == "time":
        rows[0]["elapsed_seconds"] = None
    elif mutation == "grade":
        rows[0].update(status="error", passed=None)
    elif mutation == "split":
        rows[0]["split"] = "validation"
    else:
        rows[0]["cost_usd"] = 0.001
    with pytest.raises(ValueError):
        freeze_mix(rows, protocol_sha256=PROTOCOL_SHA)


@pytest.mark.parametrize(
    "raw",
    [
        '{"answer":"right","answer":"wrong"}',
        '{"answer":NaN}',
        '{"answer":1e999}',
        'prefix {"answer":"right"}',
        "incomplete {",
    ],
)
def test_strict_json_rejects_invalid_responses_without_repair(raw):
    diagnostic = response_diagnostics(raw, "right")
    assert diagnostic["json_syntax_valid"] is False
    assert diagnostic["primary_passed"] is False


def test_format_and_answer_accuracy_are_separate():
    extra = response_diagnostics('{"answer":"right","explanation":"extra"}', "right")
    assert extra["json_object_valid"]
    assert extra["exact_answer_correct"]
    assert not extra["strict_answer_contract_valid"]
    assert not extra["primary_passed"]
    scalar = response_diagnostics('"right"', "right")
    assert scalar["json_syntax_valid"] and not scalar["json_object_valid"]
    assert not scalar["primary_passed"]


def test_independent_grading_detects_raw_evidence_and_metadata_tampering():
    rows, registry = evidence()
    validate_case_evidence(rows, registry)
    changed = copy.deepcopy(rows)
    changed[0]["raw_text"] = '{"answer":"wrong"}'
    with pytest.raises(ValueError, match="independent"):
        validate_case_evidence(changed, registry)
    changed = copy.deepcopy(rows)
    changed[0]["output"] = {"answer": "silently repaired"}
    with pytest.raises(ValueError, match="unmodified"):
        validate_case_evidence(changed, registry)
    changed = copy.deepcopy(registry)
    changed[rows[0]["case_id"]]["family_id"] = "different-family"
    with pytest.raises(ValueError, match="frozen task"):
        validate_case_evidence(rows, changed)


def test_full_report_keeps_family_and_template_units_and_real_denominators():
    rows, registry = evidence(full=True)
    dev = [r for r in rows if r["seed"] == 17 and r["step"] in STEPS[:3]]
    selection = freeze_mix(dev, protocol_sha256=PROTOCOL_SHA)
    result = analyze_study(rows, selection, protocol_sha256=PROTOCOL_SHA, registry=registry)
    assert result["scheduled_generations"] == 3072
    assert len(result["comparisons"]) == 36
    for item in result["comparisons"]:
        assert item["clusters"] == 32
        assert item["matched_pairs"] == 64
        assert item["comparison_alpha"] == pytest.approx(0.05 / 36)
        assert item["template_dependence_sensitivity"]["clusters"] == 2
        assert item["template_dependence_sensitivity"]["decision"] == "inconclusive"
    selected = result["mix_validation"]["selected"]
    assert selected["comparable_cells"] == 8
    assert selected["reference_regressions"] == 2
    assert selected["detected_regressions"] == 2
    assert selected["false_alert_rate"] == 0
    assert selected["detection_recall"] == 1
    assert 0 < selected["counterfactual_generation_time_saved_fraction"] < 1
    assert result["cash_provider_fees_usd"] == 0
    assert result["hardware_energy_cost_usd"] is None


def test_missing_validation_is_unknown_and_not_an_automatic_failure():
    rows, registry = evidence(full=True)
    dev = [r for r in rows if r["seed"] == 17 and r["step"] in STEPS[:3]]
    selection = freeze_mix(dev, protocol_sha256=PROTOCOL_SHA)
    target = next(
        r for r in rows if r["seed"] == 29 and r["step"] == 320 and r["domain"] == "arithmetic"
    )
    target.update(
        status="failed",
        passed=None,
        elapsed_seconds=None,
        raw_text=None,
        output=None,
        cost_usd=None,
    )
    result = analyze_study(rows, selection, protocol_sha256=PROTOCOL_SHA, registry=registry)
    comparison = next(
        x
        for x in result["comparisons"]
        if x["seed"] == 29 and x["step"] == 320 and x["domain"] == "arithmetic"
    )
    assert comparison["decision"] == "invalid"
    assert result["mix_validation"]["full_suite"]["unknown_cells"] == 1
    assert result["mix_validation"]["full_suite"]["runtime_complete"] is False
    assert result["cash_fee_accounting_complete"] is False


def test_null_recall_when_no_reference_regression_and_format_denominators():
    rows, registry = evidence(full=True)
    for row in rows:
        row.update(
            raw_text='{"answer":"right","extra":1}',
            output={"answer": "right", "extra": 1},
            passed=False,
        )
    dev = [r for r in rows if r["seed"] == 17 and r["step"] in STEPS[:3]]
    selection = freeze_mix(dev, protocol_sha256=PROTOCOL_SHA)
    result = analyze_study(rows, selection, protocol_sha256=PROTOCOL_SHA, registry=registry)
    assert result["mix_validation"]["selected"]["detection_recall"] is None
    measures = result["trajectories"][0]["response_diagnostics"]
    assert measures["exact_answer_accuracy_given_valid_json_object"] == {
        "count": 64,
        "denominator": 64,
        "rate": 1,
    }
    assert measures["strict_answer_contract_valid"]["count"] == 0


def test_write_once_preserves_evidence(tmp_path):
    path = tmp_path / "artifact.json"
    _write_once(path, {"value": 1})
    _write_once(path, {"value": 1})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        _write_once(path, {"value": 2})
    assert path.read_bytes() == before


def test_latency_and_status_exports_bind_durable_store_projection():
    rows, _ = evidence()
    verify_durable_projection(rows, rows)
    changed = copy.deepcopy(rows)
    changed[0]["elapsed_seconds"] *= 100
    with pytest.raises(ValueError, match="durable Store"):
        verify_durable_projection(changed, rows)
    changed = copy.deepcopy(rows)
    changed[0].update(status="error", passed=None)
    with pytest.raises(ValueError, match="durable Store"):
        verify_durable_projection(changed, rows)


def test_frozen_task_files_are_independently_verified(tmp_path):
    from scripts.posttraining.build_dataset import build_dataset

    dataset = tmp_path / "dataset"
    manifest = build_dataset(dataset)
    manifest_path = dataset / "dataset-manifest.json"
    template = (
        Path(__file__).resolve().parents[1]
        / "examples/post-training/real-study-protocol-template.json"
    )
    protocol = json.loads(template.read_text())
    protocol["dataset"]["files"] = manifest["files_sha256"]
    protocol["dataset"]["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    path = tmp_path / "frozen-protocol.json"
    path.write_text(json.dumps(protocol))
    registry = load_case_registry(path)
    assert len(registry) == 512
    target = dataset / "collections/validation-arithmetic.json"
    target.write_text(target.read_text() + " ")
    with pytest.raises(ValueError, match="artifact changed"):
        load_case_registry(path)


def test_runtime_freeze_refuses_preexisting_evaluation_evidence(tmp_path):
    from scripts.posttraining.freeze_protocol import assert_before_evaluation

    assert_before_evaluation(tmp_path)
    (tmp_path / "observations.jsonl").write_text("")
    with pytest.raises(ValueError, match="evaluation has begun"):
        assert_before_evaluation(tmp_path)


def test_initial_freeze_refuses_existing_protocol_and_training(tmp_path):
    from scripts.posttraining.freeze_protocol import freeze_initial

    path = tmp_path / "frozen-protocol.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="already exists"):
        freeze_initial(tmp_path)
    path.unlink()
    (tmp_path / "training").mkdir()
    with pytest.raises(ValueError, match="before any study training"):
        freeze_initial(tmp_path)
