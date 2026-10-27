"""Public figures consume aggregates without publishing private study evidence."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "judge_study_publisher", Path(__file__).parents[1] / "scripts/judge_study/publish_results.py"
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
summarize = _module.summarize


def sample_aggregate():
    m = {
        "estimate": 0.5,
        "interval": None,
        "complete_cohort": False,
        "undefined_reason": "incomplete_frozen_cohort",
    }
    statuses = {"completed": 3, "invalid": 1, "abstained": 0, "error": 0, "missing": 0}
    original, amended = {}, {}
    for cohort in ("human", "execution"):
        rows, delivered, contrasts = [], [], []
        for model in ("luna", "terra", "sol"):
            for arm in ("standard", "reference_context"):
                jid = model + "-" + arm
                rows.append(
                    {
                        "judge_id": jid,
                        "model_id": "gpt-6-" + model,
                        "primary": copy.deepcopy(m),
                        "complete_items": 0,
                        "status_counts": statuses.copy(),
                        "scheduled": 4,
                        "agreement": {"raw_agreement": 0.5, "kappa": 0},
                        "human_vote_agreement": {"raw_agreement": 0.625, "kappa": 0.25},
                        "order": {
                            "swap_inconsistency": copy.deepcopy(m),
                            "directional_position_effect": copy.deepcopy(m),
                        },
                        "repeatability": {
                            "same_order_disagreement": copy.deepcopy(m),
                            "excess_swap_disagreement": copy.deepcopy(m),
                        },
                        "self_preference": {"eligible_items": 0},
                        "item_scores": [{"item_id": "SECRET-ITEM", "label": "SECRET-VOTE"}],
                    }
                )
                delivered.append(
                    {
                        "judge_id": jid,
                        "model_id": "gpt-6-" + model,
                        "primary": {
                            "estimate": 0.375,
                            "interval": [0.2, 0.6],
                            "complete_cohort": True,
                        },
                        "coverage": {
                            "scheduled": 4,
                            "attempted": 4,
                            "valid": 3,
                            "statuses": statuses.copy(),
                        },
                        "response": "SECRET-RESPONSE",
                    }
                )
            contrasts.append(
                {
                    "left": model + "-standard",
                    "right": model + "-reference_context",
                    "expected_items": 1,
                    "matched_items": 0,
                    "missing_items": ["SECRET-ITEM"],
                    "complete_cohort": False,
                    "difference": copy.deepcopy(m),
                }
            )
        original[cohort] = {
            "primary_endpoint": "human_votes" if cohort == "human" else "reference",
            "primary_contrasts": contrasts,
            "analysis": {
                "items": 1,
                "families": 1,
                "judges": rows,
                "human_reliability": {
                    "alpha": {"alpha": 0.5},
                    "reviewer_pairs": [{"reviewer_id": "SECRET-REVIEWER"}],
                },
            },
            "protocol": {"responses": "SECRET-PATCH"},
        }
        amended[cohort] = {
            "items": 1,
            "families": 1,
            "judges": delivered,
            "paired_contrasts": copy.deepcopy(contrasts),
        }
    return {
        "plan_sha256": "0" * 64,
        "planned_requests": 48,
        "missing_request_count": 0,
        "complete_frozen_grid": True,
        "heldout": original,
        "secondary_delivered_agreement": amended,
        "human_panel": {
            "items": 1,
            "observed_votes": 2,
            "divided_panels": 0,
            "observed_panel_vote_agreement_upper_bound": 1,
        },
        "cost_upper_bound": {
            "conservative_settled_usd": "0.10",
            "conservative_committed_usd": "0.10",
            "requests": 48,
            "unknown_requests": 0,
            "inference_cap_usd": 30,
            "total_user_cap_usd": 50,
        },
        "private": "SECRET-OTHER",
    }


def test_public_summary_is_an_allowlist_and_retains_distinct_endpoints():
    result = summarize(sample_aggregate(), "1" * 64)
    assert "SECRET" not in json.dumps(result)
    original = result["original_primary"]["human"]["judges"][0]
    secondary = result["secondary_delivered_agreement"]["human"]["judges"][0]
    assert original["primary"]["interval"] is None
    assert original["reference_kappa"] == 0
    assert original["human_vote_kappa"] == 0.25
    assert original["conditional_pooled_human_vote_agreement"] == 0.625
    assert secondary["primary"]["interval"] == [0.2, 0.6]
    assert original["coverage"] == secondary["coverage"]
    assert original["coverage"]["valid"] == 3 and original["coverage"]["attempted"] == 4
    assert result["original_primary"]["human"]["paired_contrasts"][0]["missing_item_count"] == 1
    assert "after development" in result["interpretation"]["secondary"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_public_summary_rejects_nonfinite_or_boolean_statistics(value):
    source = sample_aggregate()
    source["human_panel"]["observed_panel_vote_agreement_upper_bound"] = value
    with pytest.raises(ValueError, match="finite"):
        summarize(source, "1" * 64)


def test_primary_interval_cannot_appear_for_incomplete_grid():
    source = sample_aggregate()
    source["heldout"]["human"]["analysis"]["judges"][0]["primary"]["interval"] = [0.1, 0.9]
    with pytest.raises(ValueError, match="incomplete"):
        summarize(source, "1" * 64)


def test_delivery_counts_cannot_hide_failed_judgments():
    source = sample_aggregate()
    source["secondary_delivered_agreement"]["human"]["judges"][0]["coverage"]["valid"] = 4
    with pytest.raises(ValueError, match="delivery counts"):
        summarize(source, "1" * 64)


def test_delivery_counts_keep_the_scheduled_denominator():
    source = sample_aggregate()
    source["heldout"]["human"]["analysis"]["judges"][0]["status_counts"]["invalid"] = 0
    with pytest.raises(ValueError, match="denominator"):
        summarize(source, "1" * 64)


def test_fractional_counts_are_not_silently_rounded():
    source = sample_aggregate()
    source["heldout"]["human"]["analysis"]["judges"][0]["status_counts"]["completed"] = 3.5
    with pytest.raises(ValueError, match="integers"):
        summarize(source, "1" * 64)
