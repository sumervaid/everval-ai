"""Full-denominator bounds stay distinct from imputed point estimates."""

import copy
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
publisher = importlib.import_module("scripts.judge_study.publish_bounds")


def inputs():
    judges = [
        {
            "judge_id": model + "-" + arm,
            "model_id": model,
            "identification_bounds": [0.6, 0.61],
            "scheduled_judgments": 256,
            "unattempted_judgments": 4,
            "status_counts": {
                "completed": 250,
                "abstained": 1,
                "invalid": 1,
                "error": 0,
                "missing": 4,
            },
        }
        for model in ("gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol")
        for arm in ("standard", "reference_context")
    ]
    cohort = {
        "items": 64,
        "families": 64,
        "endpoint": "human_votes",
        "judges": judges,
        "method": "Frozen observations; unknowns bounded.",
        "interpretation": "Not a confidence interval.",
    }
    audit = {
        "plan_sha256": "a" * 64,
        "heldout_identification_bounds": {"human": {"frozen": cohort}},
    }
    published = {
        "plan_sha256": "a" * 64,
        "human_panel": {"items": 64, "observed_panel_vote_agreement_upper_bound": 0.84},
    }
    return audit, published


def test_all_slots_and_both_endpoints_are_retained_without_midpoints():
    audit, published = inputs()
    data = publisher.prepare(audit, published)
    assert data["items"] == 64 and len(data["judges"]) == 6
    assert all(row["bounds"] == [0.6, 0.61] and row["scheduled"] == 256 for row in data["judges"])
    assert data["no_midpoint_estimated"] is True
    assert "estimate" not in str(data["judges"])
    assert "post_hoc" in data["analysis_role"]


@pytest.mark.parametrize("change", ["cohort", "plan", "missing", "upper", "duplicate", "arm"])
def test_incomparable_or_malformed_bounds_are_rejected(change):
    audit, published = inputs()
    cohort = audit["heldout_identification_bounds"]["human"]["frozen"]
    if change == "cohort":
        cohort["items"] = 63
    elif change == "plan":
        published["plan_sha256"] = "b" * 64
    elif change == "missing":
        cohort["judges"][0]["unattempted_judgments"] = 0
    elif change == "upper":
        cohort["judges"][0]["identification_bounds"][1] = 0.9
    elif change == "duplicate":
        cohort["judges"].append(copy.deepcopy(cohort["judges"][0]))
    else:
        cohort["judges"].pop()
    with pytest.raises(ValueError):
        publisher.prepare(audit, published)


def test_phase_aware_variant_is_separate_and_does_not_change_original():
    audit, published = inputs()
    audit["heldout_identification_bounds"]["human"]["phase_aware"] = copy.deepcopy(
        audit["heldout_identification_bounds"]["human"]["frozen"]
    )
    audit["heldout_identification_bounds"]["human"]["phase_aware"]["judges"][0][
        "identification_bounds"
    ] = [0.62, 0.63]
    original = publisher.prepare(audit, published)
    revised = publisher.prepare(audit, published, variant="phase_aware")
    assert original["judges"][0]["bounds"] == [0.6, 0.61]
    assert revised["judges"][0]["bounds"] == [0.62, 0.63]
    assert original["transport"] == "original_frozen"
    assert revised["transport"] == "phase_aware_offline_regrade"
    assert revised["frozen_scores_modified"] is False
