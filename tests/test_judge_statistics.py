"""Hand-computable agreement examples and failure modes for frozen judge studies."""

from __future__ import annotations

from copy import deepcopy

import pytest

from cvals.judge_statistics import (
    agreement_summary,
    analyze_calibration,
    exact_mcnemar_power,
    family_mean_interval,
    krippendorff_alpha,
    paired_power_plan,
)


def test_confusion_and_kappa_use_observed_marginals():
    result = agreement_summary(["A", "A", "B"], ["A", "B", "B"])
    assert result["raw_agreement"] == pytest.approx(2 / 3)
    assert result["kappa"] == pytest.approx(0.4)
    assert result["confusion"]["A"]["B"] == 1
    assert result["classes"]["A"]["sensitivity"] == 0.5
    assert result["classes"]["A"]["specificity"] == 1
    assert result["classes"]["B"]["false_positive"] == 1


def test_undefined_kappa_is_not_perfect_reliability():
    result = agreement_summary(["A", "A"], ["A", "A"])
    assert result["raw_agreement"] == 1
    assert result["kappa"] is None
    assert result["kappa_undefined_reason"] == "no_expected_disagreement"
    assert result["classes"]["A"]["specificity"] is None


def test_missing_labels_are_not_agreements_or_false_errors():
    result = agreement_summary(["A", "B", None, "A"], ["A", None, "B", "abstain"])
    assert result["scheduled"] == 4
    assert result["paired"] == 1
    assert result["paired_coverage"] == 0.25
    assert result["missing_reference"] == 1
    assert result["missing_observation"] == 2
    assert result["raw_agreement"] == 1
    assert result["classes"]["B"]["false_negative"] == 0
    assert agreement_summary([None], [None])["raw_agreement"] is None


def test_weighted_kappa_requires_frozen_order():
    result = agreement_summary(
        ["low", "mid", "high"],
        ["low", "high", "high"],
        labels=["low", "mid", "high"],
        weights="linear",
    )
    assert result["kappa"] == pytest.approx(2 / 3)
    with pytest.raises(ValueError, match="category"):
        agreement_summary(["unknown"], ["A"])


def test_nominal_alpha_uses_coincidences_and_finite_sample_expected_disagreement():
    result = krippendorff_alpha([["A", "A"], ["A", "B"], ["B", "B"]])
    assert result["alpha"] == pytest.approx(4 / 9)
    assert result["observed_disagreement"] == pytest.approx(1 / 3)
    assert result["expected_disagreement"] == pytest.approx(0.6)
    assert result["coincidence_ratings"] == 6


def test_alpha_missingness_and_no_variation():
    result = krippendorff_alpha([["A", "A"], ["B", None], ["B", "B"]])
    assert result["alpha"] == 1
    assert result["eligible_items"] == 2
    assert result["observed_ratings"] == 5
    assert result["coincidence_ratings"] == 4
    assert result["items_with_fewer_than_two_ratings"] == 1
    assert krippendorff_alpha([["A", None]])["undefined_reason"] == "no_pairable_ratings"
    assert krippendorff_alpha([["A", "A"]])["alpha"] is None
    assert krippendorff_alpha([["A", "B"], ["B", "A"]])["alpha"] == pytest.approx(-0.5)


def test_ordinal_alpha_uses_cumulative_marginal_distances():
    rows = [["low", "low"], ["low", "mid"], ["high", "high"]]
    ordinal = krippendorff_alpha(rows, labels=["low", "mid", "high"], level="ordinal")
    nominal = krippendorff_alpha(rows, labels=["low", "mid", "high"])
    assert ordinal["observed_disagreement"] == pytest.approx(4 / 3)
    assert ordinal["expected_disagreement"] == pytest.approx(6)
    assert ordinal["alpha"] == pytest.approx(7 / 9)
    assert nominal["alpha"] == pytest.approx(6 / 11)


def test_one_family_cannot_gain_precision_from_repeated_measurements():
    one = family_mean_interval({"family": [0.0, 1.0]})
    repeated = family_mean_interval({"family": [0.0, 1.0] * 100})
    assert one["families"] == repeated["families"] == 1
    assert one["estimate"] == repeated["estimate"] == 0.5
    assert one["interval"] is repeated["interval"] is None
    assert one["bounded_sensitivity_interval"] == repeated["bounded_sensitivity_interval"]


def test_degenerate_bootstrap_never_reports_zero_width_certainty():
    result = family_mean_interval({str(i): [1.0] for i in range(50)})
    assert result["estimate"] == 1
    assert result["interval"] is None
    assert result["undefined_reason"] == "degenerate_family_distribution"
    low, high = result["bounded_sensitivity_interval"]
    assert low < high == 1


def test_family_weighting_seed_and_multiplicity_are_explicit():
    rows = {str(i): [i / 39] * (i + 1) for i in range(40)}
    a = family_mean_interval(rows, seed=42)
    b = family_mean_interval(rows, seed=42)
    assert a == b
    assert a["estimate"] == pytest.approx(0.5)
    assert a["families"] == 40
    assert a["interval"][0] < 0.5 < a["interval"][1]
    many = family_mean_interval(rows, planned_comparisons=100)
    assert many["interval"] is None
    assert many["undefined_reason"] == "insufficient_resamples_for_allocated_alpha"
    assert many["comparison_alpha"] == 0.0005


def test_exact_mcnemar_power_has_hand_computable_small_sample_case():
    # All six pairs discordant; rejection occurs only at 0 or 6 wins.
    assert exact_mcnemar_power(6, 1.0, 0.0) == pytest.approx(2 / 64)
    assert exact_mcnemar_power(6, 1.0, 1.0) == 1
    assert exact_mcnemar_power(5, 1.0, 1.0) == 0
    # Half discordant: with six pairs only D=6 can reject.
    assert exact_mcnemar_power(6, 0.5, 0.5) == pytest.approx(1 / 64)
    assert exact_mcnemar_power(20, 0.4, 0.2) == pytest.approx(exact_mcnemar_power(20, 0.4, -0.2))
    assert exact_mcnemar_power(30, 0, 0) == 0


def test_power_plan_reports_underpowered_and_unattainable_designs():
    plan = paired_power_plan(50, 0.2, difference=0.1)
    assert plan["power_at_assumed_difference"] == pytest.approx(0.24118855539253034, abs=1e-12)
    assert plan["minimum_detectable_difference"] == pytest.approx(0.1757, abs=0.0001)
    impossible = paired_power_plan(5, 1.0)
    assert impossible["minimum_detectable_difference"] is None
    assert impossible["maximum_power_at_feasible_difference"] == 0
    adjusted = paired_power_plan(50, 0.2, planned_comparisons=2)
    assert adjusted["minimum_detectable_difference"] > plan["minimum_detectable_difference"]
    assert adjusted["comparison_alpha"] == 0.025


@pytest.mark.parametrize(
    "arguments", [(True, 0.2, 0.1), (0, 0.2, 0.1), (20, 0.2, 0.3), (20, float("nan"), 0.1)]
)
def test_power_rejects_invalid_assumptions(arguments):
    with pytest.raises(ValueError):
        exact_mcnemar_power(*arguments)


def item(index=0, *, source="human_collected", label="A", family=None):
    return {
        "item_id": f"item-{index}",
        "task_id": f"task-{index}",
        "family_id": family or f"family-{index}",
        "partition": "holdout",
        "generator_models": {"A": "model-own", "B": "model-other"},
        "reference": {
            "source": source,
            "label": label,
            "reviewer_labels": [
                {"reviewer_id": "human-1", "label": label},
                {"reviewer_id": "human-2", "label": label},
            ],
        },
    }


def observation(identity, order, preference, repetition=0, status="completed"):
    return {
        "item_id": identity,
        "judge_id": "judge",
        "repetition": repetition,
        "order": order,
        "status": status,
        "preference": preference,
    }


def complete_rows(items, *, repetitions=1, always_first=False):
    return [
        observation(i["item_id"], order, order[0] if always_first else i["reference"]["label"], rep)
        for i in items
        for rep in range(repetitions)
        for order in (["A", "B"], ["B", "A"])
    ]


def test_canonical_order_invariance_and_position_bias_are_distinct():
    cohort = [item()]
    invariant = analyze_calibration(cohort, complete_rows(cohort))["judges"][0]
    biased = analyze_calibration(cohort, complete_rows(cohort, always_first=True))["judges"][0]
    assert invariant["order"]["swap_inconsistency"]["estimate"] == 0
    assert invariant["order"]["first_position_choice_rate"] == 0.5
    assert invariant["order"]["directional_position_effect"]["estimate"] == 0
    assert biased["order"]["swap_inconsistency"]["estimate"] == 1
    assert biased["order"]["directional_position_effect"]["estimate"] == 1
    assert biased["repeatability"]["same_order_disagreement"]["estimate"] is None


def test_same_order_noise_prevents_mislabeling_all_swap_changes_as_bias():
    cohort = [item()]
    rows = [
        observation("item-0", ["A", "B"], "A", 0),
        observation("item-0", ["B", "A"], "B", 0),
        observation("item-0", ["A", "B"], "B", 1),
        observation("item-0", ["B", "A"], "A", 1),
    ]
    result = analyze_calibration(cohort, rows, repetitions=2)["judges"][0]
    assert result["order"]["swap_inconsistency"]["estimate"] == 1
    assert result["repeatability"]["same_order_disagreement"]["estimate"] == 1
    assert result["repeatability"]["excess_swap_disagreement"]["estimate"] == 0
    assert result["order"]["directional_position_effect"]["estimate"] == 0


def test_missing_grid_and_zero_observation_judges_remain_visible():
    cohort = [item()]
    rows = [observation("item-0", ["A", "B"], "A")]
    result = analyze_calibration(cohort, rows, judge_models={"judge": "one", "absent": "two"})
    absent, partial = result["judges"]
    assert absent["judge_id"] == "absent"
    assert absent["status_counts"]["missing"] == absent["scheduled"] == 2
    assert absent["agreement"]["raw_agreement"] is None
    assert partial["agreement"]["raw_agreement"] == 1
    assert partial["family_agreement"]["interval"] is None
    assert partial["family_agreement"]["complete_cohort"] is False
    assert partial["order"]["matched_order_pairs"] == 0


def test_repetitions_do_not_inflate_family_count_or_primary_precision():
    cohort = [item(i, label="A" if i % 2 else "B") for i in range(25)]
    rows = complete_rows(cohort)
    for row in rows:
        row["preference"] = "A"
    repeat_rows = rows + [{**row, "repetition": 1} for row in rows]
    once = analyze_calibration(cohort, rows)["judges"][0]["family_agreement"]
    twice = analyze_calibration(cohort, repeat_rows, repetitions=2)["judges"][0]["family_agreement"]
    assert once == twice
    assert once["families"] == 25


def test_self_preference_conditions_on_reference_and_requires_exact_identity():
    cohort = [item(label="A")]
    rows = complete_rows(cohort)
    result = analyze_calibration(cohort, rows, judge_models={"judge": "model-own"})["judges"][0]
    assert result["self_preference"]["own_choice_rate"] == 1
    assert result["self_preference"]["reference_conditioned_difference"]["estimate"] == 0
    cohort[0]["reference"]["label"] = "B"
    result = analyze_calibration(cohort, rows, judge_models={"judge": "model-own"})["judges"][0]
    assert result["self_preference"]["reference_conditioned_difference"]["estimate"] == 1
    result = analyze_calibration(cohort, rows, judge_models={"judge": "same-provider-other-model"})[
        "judges"
    ][0]
    assert result["self_preference"]["eligible_items"] == 0


def test_scripted_ratings_cannot_become_human_validity_evidence():
    cohort = [item(i, source="scripted") for i in range(25)]
    result = analyze_calibration(cohort, complete_rows(cohort))
    assert result["human_reliability"]["human_source_items"] == 0
    assert result["human_reliability"]["alpha"]["alpha"] is None
    assert result["judges"][0]["reference_mode"] == "scripted"
    assert result["judges"][0]["family_agreement"]["interval"] is None
    assert (
        result["judges"][0]["family_agreement"]["undefined_reason"]
        == "reference_provenance_not_empirical_or_mixed"
    )


def test_reference_strata_preserve_sources_without_pooled_empirical_claim():
    cohort = [item(0), item(1, source="execution")]
    result = analyze_calibration(cohort, complete_rows(cohort))["judges"][0]
    assert set(result["reference_strata"]) == {"human_collected", "execution"}
    assert result["reference_mode"] == "mixed_sources"
    assert result["family_agreement"]["interval"] is None


def test_human_reliability_retains_pre_adjudication_disagreement():
    cohort = [item(0), item(1, label="B"), item(2)]
    cohort[2]["reference"]["reviewer_labels"][1]["label"] = "B"
    result = analyze_calibration(cohort, [])["human_reliability"]
    assert result["reviewer_pairs"][0]["raw_agreement"] == pytest.approx(2 / 3)
    assert result["alpha"]["alpha"] == pytest.approx(4 / 9)


def test_frozen_identity_and_partition_validation():
    cohort = [item()]
    rows = complete_rows(cohort)
    with pytest.raises(ValueError, match="duplicate judgment"):
        analyze_calibration(cohort, rows + rows[:1])
    with pytest.raises(ValueError, match="unplanned item"):
        analyze_calibration(cohort, [{**rows[0], "item_id": "outside"}])
    with pytest.raises(ValueError, match="canonical preference"):
        analyze_calibration(cohort, [{**rows[0], "preference": "abstain"}])
    duplicate_task = [item(0), {**item(1), "task_id": "task-0"}]
    with pytest.raises(ValueError, match="different families"):
        analyze_calibration(duplicate_task, [])
    partitions = [item(0), {**item(1), "partition": "dev"}]
    with pytest.raises(ValueError, match="separately"):
        analyze_calibration(partitions, [])
    duplicate_rater = deepcopy(cohort)
    duplicate_rater[0]["reference"]["reviewer_labels"][1]["reviewer_id"] = "human-1"
    with pytest.raises(ValueError, match="reviewer identities"):
        analyze_calibration(duplicate_rater, [])


def test_human_vote_endpoint_preserves_disagreement_without_inventing_consensus():
    cohort = [item(label=None)]
    cohort[0]["reference"]["reviewer_labels"] = [
        {"reviewer_id": "one", "label": "A"},
        {"reviewer_id": "two", "label": "B"},
    ]
    rows = [
        observation("item-0", ["A", "B"], "A"),
        observation("item-0", ["B", "A"], "A"),
    ]
    result = analyze_calibration(cohort, rows, primary_endpoint="human_votes")["judges"][0]
    assert result["family_agreement"]["complete_cohort"] is False
    assert result["primary"]["complete_cohort"] is True
    assert result["primary"]["estimate"] == 0.5
    assert result["human_vote_agreement"]["kappa"] == 0
    assert result["item_scores"][0]["reference_score"] is None


def test_vote_counts_do_not_change_equal_item_estimand():
    cohort = [item(0), item(1)]
    cohort[0]["reference"]["reviewer_labels"] = [
        {"reviewer_id": f"reviewer-{n}", "label": "A" if n < 3 else "B"} for n in range(4)
    ]
    cohort[1]["reference"]["reviewer_labels"] = [{"reviewer_id": "other", "label": "B"}]
    rows = complete_rows(cohort)
    result = analyze_calibration(cohort, rows, primary_endpoint="human_votes")["judges"][0]
    assert result["primary"]["estimate"] == pytest.approx((0.75 + 0) / 2)
    assert result["human_vote_agreement"]["raw_agreement"] == 0.6


def test_paired_contrasts_preserve_full_cohort_and_allocated_alpha():
    from cvals.judge_statistics import compare_calibrated_judges

    cohort = [item(i, label="A" if i % 2 else "B") for i in range(25)]
    before = [{**row, "preference": "A"} for row in complete_rows(cohort)]
    after = [{**row, "judge_id": "candidate"} for row in complete_rows(cohort)]
    report = analyze_calibration(cohort, before + after)
    contrast = compare_calibrated_judges(report, "judge", "candidate", planned_comparisons=3)
    assert contrast["direction"] == "right_minus_left"
    assert contrast["expected_items"] == contrast["matched_items"] == 25
    assert contrast["difference"]["estimate"] == pytest.approx(13 / 25)
    assert contrast["difference"]["interval"][0] > 0
    assert contrast["difference"]["comparison_alpha"] == pytest.approx(0.05 / 3)
    partial = analyze_calibration(cohort, before + after[:-1])
    incomplete = compare_calibrated_judges(partial, "judge", "candidate")
    assert incomplete["complete_cohort"] is False
    assert incomplete["matched_items"] == 24
    assert incomplete["difference"]["interval"] is None
    with pytest.raises(ValueError, match="different judge"):
        compare_calibrated_judges(report, "judge", "judge")


def test_paired_contrast_refuses_changed_family_identity():
    from cvals.judge_statistics import compare_calibrated_judges

    cohort = [item()]
    before = complete_rows(cohort)
    after = [{**row, "judge_id": "candidate"} for row in before]
    report = analyze_calibration(cohort, before + after)
    report["judges"][0]["item_scores"][0]["family_id"] = "changed"
    with pytest.raises(ValueError, match="family assignments"):
        compare_calibrated_judges(report, "judge", "candidate")


def test_fractional_family_power_plan_is_separate_from_exact_binary_power():
    from cvals.judge_statistics import paired_mean_power_plan

    zero = paired_mean_power_plan(64, 0.25, difference=0, planned_comparisons=3)
    assert zero["power_at_assumed_difference"] == pytest.approx(0.05 / 3)
    positive = paired_mean_power_plan(64, 0.25, difference=0.1, planned_comparisons=3)
    negative = paired_mean_power_plan(64, 0.25, difference=-0.1, planned_comparisons=3)
    assert positive["power_at_assumed_difference"] == negative["power_at_assumed_difference"]
    assert positive["minimum_detectable_difference"] == pytest.approx(0.1011, abs=0.0001)
    high_variance = paired_mean_power_plan(64, 0.5, difference=0.1, planned_comparisons=3)
    assert high_variance["power_at_assumed_difference"] < positive["power_at_assumed_difference"]
    assert (
        high_variance["minimum_detectable_difference"] > positive["minimum_detectable_difference"]
    )
    with pytest.raises(ValueError, match="positive"):
        paired_mean_power_plan(64, 0)


def test_reserved_abstention_and_non_pair_categories_cannot_change_denominators():
    with pytest.raises(ValueError, match="reserved"):
        agreement_summary(["A"], ["A"], labels=["A", "abstain"])
    with pytest.raises(ValueError, match="canonical"):
        analyze_calibration([item()], [], labels=["A", "B", "tie", "other"])
