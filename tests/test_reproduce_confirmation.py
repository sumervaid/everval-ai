"""Public aggregate reproduction and malformed-evidence boundaries."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_study.confirmatory_analysis import DEFAULT_SETTINGS  # noqa: E402
from scripts.judge_study.reproduce_confirmation import (  # noqa: E402
    read_json,
    reproduce,
    verify_expected,
)


def fixture():
    rows = []
    arms = ["terra-standard", "terra-checklist", "sol-standard"]
    total = Decimal(0)
    for index in range(40):
        sign = 1 if index % 2 else -1
        row = {
            "family_id": f"family-{index:03}",
            "domain": "code" if index % 2 else "general",
            "arms": {},
        }
        for position, arm in enumerate(arms):
            score = (
                0.7 + sign * 0.01 if position == 0 else 0.7 - sign * 0.01 if position == 1 else 0.65
            )
            cost = Decimal(position + 1) * (Decimal("0.001") + Decimal(index) * Decimal("0.000001"))
            total += cost
            row["arms"][arm] = {
                "delivered_agreement": score,
                "estimated_cost_usd": str(cost),
                "judgments": 2,
            }
        rows.append(row)
    return {
        "schema_version": 1,
        "frozen_analyzer_sha256": hashlib.sha256(
            (
                Path(__file__).resolve().parents[1] / "scripts/judge_study/confirmatory_analysis.py"
            ).read_bytes()
        ).hexdigest(),
        "settings": deepcopy(DEFAULT_SETTINGS),
        "aggregation": {
            "families": 40,
            "scheduled_judgments": 240,
            "observed_judgments": 240,
            "unattempted_judgments": 0,
        },
        "arms": arms,
        "families": rows,
        "calibration_cost_upper_usd": str(total),
    }


def test_reproduces_family_weighting_frozen_gates_and_exact_payback():
    report = reproduce(fixture())
    assert report["families"] == 40
    assert report["scheduled_calls"] == 240
    assert [arm["delivered_agreement"] for arm in report["arms"]] == pytest.approx([0.7, 0.7, 0.65])
    assert [row["decision"] for row in report["comparisons"]] == ["adopt", "adopt"]
    first, second = report["comparisons"]
    assert first["quality_difference_candidate_minus_baseline"]["families"] == 40
    assert first["quality_difference_candidate_minus_baseline"]["estimate"] == pytest.approx(0)
    assert second["quality_difference_candidate_minus_baseline"]["estimate"] == pytest.approx(0.05)
    assert first["estimated_saving_fraction_lower_accounting"] == pytest.approx(0.5)
    assert second["estimated_saving_fraction_lower_accounting"] == pytest.approx(2 / 3)
    assert first["payback_evaluated_questions"] == 240
    assert second["payback_evaluated_questions"] == 120
    assert second["payback_provider_judgments"] == 240


def test_first_failure_blocks_second_confirmatory_claim():
    data = fixture()
    for family in data["families"]:
        family["arms"]["terra-checklist"]["delivered_agreement"] = 1.0
    report = reproduce(data)
    assert report["comparisons"][0]["decision"] == "do_not_adopt"
    assert report["comparisons"][1]["quality_gate_passed"]
    assert report["comparisons"][1]["decision"] == "descriptive_only"
    assert report["comparisons"][1]["payback_evaluated_questions"] is None


@pytest.mark.parametrize(
    "change", ["duplicate", "missing_arm", "wrong_orders", "unattempted", "private_field"]
)
def test_rejects_incomplete_duplicate_or_overexposed_aggregate_records(change):
    data = fixture()
    if change == "duplicate":
        data["families"][1]["family_id"] = data["families"][0]["family_id"]
    elif change == "missing_arm":
        del data["families"][0]["arms"]["sol-standard"]
    elif change == "wrong_orders":
        data["families"][0]["arms"]["terra-standard"]["judgments"] = 1
    elif change == "unattempted":
        data["aggregation"]["unattempted_judgments"] = 1
    else:
        data["families"][0]["human_votes"] = ["A"]
    with pytest.raises(ValueError):
        reproduce(data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_rejects_invalid_agreement_values(value):
    data = fixture()
    data["families"][0]["arms"]["terra-standard"]["delivered_agreement"] = value
    with pytest.raises(ValueError):
        reproduce(data)


@pytest.mark.parametrize("value", ["NaN", "-0.1", 0.001])
def test_rejects_ambiguous_or_invalid_costs(value):
    data = fixture()
    data["families"][0]["arms"]["terra-standard"]["estimated_cost_usd"] = value
    with pytest.raises(ValueError):
        reproduce(data)


def test_rejects_changed_method_or_margin():
    data = fixture()
    data["frozen_analyzer_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="frozen analyzer"):
        reproduce(data)
    data = fixture()
    data["settings"]["margin"] = 0.05
    with pytest.raises(ValueError, match="frozen confirmation"):
        reproduce(data)


def test_comparison_tolerates_roundoff_and_rejects_substantive_changes():
    verify_expected(
        {"value": 0.1, "decision": True}, {"value": 0.1 + 1e-14, "decision": True, "extra": 1}
    )
    with pytest.raises(ValueError, match="Numerical mismatch"):
        verify_expected({"value": 0.1}, {"value": 0.11})
    with pytest.raises(ValueError, match="Value mismatch"):
        verify_expected({"decision": True}, {"decision": 1})


@pytest.mark.parametrize("text", ['{"duplicate":1,"duplicate":2}', '{"value":NaN}', "[]"])
def test_json_reader_rejects_ambiguous_or_nonfinite_data(tmp_path, text):
    path = tmp_path / "data.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_json(path)


def test_json_reader_preserves_exact_decimal_strings(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"cost": "0.0000001"}))
    assert read_json(path) == {"cost": "0.0000001"}
