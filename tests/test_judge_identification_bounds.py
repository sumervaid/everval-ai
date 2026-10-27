"""Unobserved outcomes stay in the finite-cohort denominator without imputation."""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
bounds = importlib.import_module("scripts.judge_study.identification_bounds").identification_bounds


def protocol():
    return {
        "judges": [{"id": "base", "model": "same"}, {"id": "checklist", "model": "same"}],
        "repetitions": 1,
        "primary_endpoint": "human_votes",
        "primary_contrasts": [{"left": "base", "right": "checklist"}],
        "items": [
            {
                "item_id": "q",
                "family_id": "family",
                "partition": "holdout",
                "reference": {
                    "source": "human_collected",
                    "reviewer_labels": [{"label": "A"}, {"label": "A"}, {"label": "B"}],
                },
            }
        ],
    }


def observation(judge, order, preference="A", status="completed"):
    return {
        "item_id": "q",
        "judge_id": judge,
        "repetition": 0,
        "order": order,
        "status": status,
        "preference": preference,
    }


def test_missing_upper_bound_is_panel_maximum_with_original_denominator():
    result = bounds(protocol(), [observation("base", ["A", "B"])])
    base, checklist = result["judges"]
    assert base["identification_bounds"] == pytest.approx([1 / 3, 2 / 3])
    assert checklist["identification_bounds"] == pytest.approx([0, 2 / 3])
    assert base["scheduled_judgments"] == 2 and base["unattempted_judgments"] == 1
    assert result["paired_contrasts"][0]["identification_bounds"] == pytest.approx([-2 / 3, 1 / 3])
    assert "not a confidence interval" in result["interpretation"]


def test_attempted_failure_is_zero_not_an_unobserved_judgment():
    rows = [observation("base", ["A", "B"]), observation("base", ["B", "A"], None, "error")]
    result = bounds(protocol(), rows)["judges"][0]
    assert result["identification_bounds"] == pytest.approx([1 / 3, 1 / 3])
    assert result["unattempted_judgments"] == 0


def test_duplicate_or_unplanned_grid_cells_are_rejected():
    row = observation("base", ["A", "B"])
    with pytest.raises(ValueError, match="uniquely"):
        bounds(protocol(), [row, row])
    with pytest.raises(ValueError, match="uniquely"):
        bounds(protocol(), [{**row, "judge_id": "unplanned"}])


def test_missing_human_references_are_not_filled_in():
    value = protocol()
    value["items"][0]["reference"]["reviewer_labels"] = []
    with pytest.raises(ValueError, match="missing reference"):
        bounds(value, [])


def test_family_weighting_does_not_let_duplicate_family_tasks_dominate():
    import copy

    value = protocol()
    template = value["items"][0]
    template["reference"]["reviewer_labels"] = [{"label": "A"}]
    value["items"] = []
    rows = []
    for identity, family, label in (
        ("q1", "shared", "A"),
        ("q2", "shared", "A"),
        ("q3", "other", "B"),
    ):
        item = {**copy.deepcopy(template), "item_id": identity, "family_id": family}
        value["items"].append(item)
        for order in (["A", "B"], ["B", "A"]):
            rows.append({**observation("base", order, label), "item_id": identity})
    result = bounds(value, rows)
    assert result["families"] == 2
    assert result["judges"][0]["identification_bounds"] == [0.5, 0.5]
