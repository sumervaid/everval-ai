"""Decision-boundary and evidence-integrity checks for the confirmation study."""

from __future__ import annotations

import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_study.confirmatory_analysis import (  # noqa: E402
    DEFAULT_SETTINGS,
    analyze_bundle,
    paired_interval,
    power_sensitivity,
    simulate_operating_characteristics,
    student_t_critical,
)


def bundle(count=100):
    result = {
        "settings": {**deepcopy(DEFAULT_SETTINGS), "resamples": 500},
        "families": [],
        "arms": ["terra-standard", "terra-checklist", "sol-standard"],
        "jobs": [],
        "observations": [],
    }
    for i in range(count):
        family = str(i)
        result["families"].append(
            {
                "family_id": family,
                "domain": "code" if i % 2 else "general",
                "human_votes": ["A"] * 11 + ["B"] * 10,
            }
        )
        for arm_index, arm in enumerate(result["arms"]):
            preference = ("A" if i % 2 else "B") if arm_index == 0 else ("B" if i % 2 else "A")
            for order_index, order in enumerate([["A", "B"], ["B", "A"]]):
                job = f"{family}-{arm_index}-{order_index}"
                result["jobs"].append(
                    {
                        "job_id": job,
                        "family_id": family,
                        "arm_id": arm,
                        "kind": "primary",
                        "order": order,
                        "repetition": 0,
                    }
                )
                cost = Decimal("0.001") * (arm_index + 1) + Decimal("0.000001") * i * (
                    arm_index + 1
                )
                result["observations"].append(
                    {
                        "job_id": job,
                        "status": "completed",
                        "preference": preference,
                        "cost_lower_usd": str(cost),
                        "cost_upper_usd": str(cost),
                    }
                )
    return result


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(1, 6.3137515148), (2, 2.9199855804), (10, 1.8124611228), (30, 1.6972608866)],
)
def test_student_t_critical_matches_reference_quantiles(degrees, expected):
    assert student_t_critical(degrees) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("degrees", [0, True, 1.2])
def test_student_t_rejects_invalid_degrees(degrees):
    with pytest.raises(ValueError):
        student_t_critical(degrees)


def test_paired_intervals_are_reproducible_and_use_independent_family_count():
    values = [-0.05, 0.05] * 100
    first = paired_interval(values, resamples=1000, seed=41)
    assert first == paired_interval(values, resamples=1000, seed=41)
    assert first["families"] == 200
    assert first["estimate"] == pytest.approx(0)
    assert -0.01 < first["bootstrap_lower"] < 0 < first["bootstrap_upper"] < 0.01
    assert -0.01 < first["t_lower"] < 0 < first["t_upper"] < 0.01


@pytest.mark.parametrize("values", [[0.0] * 100, [0.2] * 100, [-0.4] * 100])
def test_degenerate_distributions_never_claim_certainty(values):
    interval = paired_interval(values, resamples=500)
    assert interval["undefined_reason"] == "degenerate_family_distribution"
    assert interval["bootstrap_lower"] is interval["t_lower"] is None


def test_too_few_families_and_nonfinite_values_fail_closed():
    assert paired_interval([0.0, 0.1])["undefined_reason"] == "insufficient_independent_families"
    with pytest.raises(ValueError):
        paired_interval([float("nan")] * 100)


def test_complete_two_order_grid_can_pass_frozen_sequence_without_pseudoreplication():
    report = analyze_bundle(bundle())
    assert report["families"] == 100
    assert report["scheduled_calls"] == 600
    assert report["complete_frozen_grid"]
    assert [row["decision"] for row in report["comparisons"]] == ["adopt", "adopt"]
    assert all(
        row["quality_difference_candidate_minus_baseline"]["families"] == 100
        for row in report["comparisons"]
    )
    assert all(row["primary_calls_per_evaluated_question"] == 2 for row in report["comparisons"])


def test_additional_orders_do_not_increase_independent_n_or_change_identical_score():
    one_order = bundle()
    one_order["jobs"] = [job for job in one_order["jobs"] if job["order"] == ["A", "B"]]
    keep = {job["job_id"] for job in one_order["jobs"]}
    one_order["observations"] = [row for row in one_order["observations"] if row["job_id"] in keep]
    single, doubled = analyze_bundle(one_order), analyze_bundle(bundle())
    assert (
        single["comparisons"][0]["quality_difference_candidate_minus_baseline"]
        == doubled["comparisons"][0]["quality_difference_candidate_minus_baseline"]
    )


def test_individual_votes_and_families_have_the_declared_weights():
    data = bundle()
    data["families"][0]["human_votes"] = ["A", "B", "B"]
    data["families"][1]["human_votes"] = ["A"] * 100
    report = analyze_bundle(data)
    scores = [(2 / 3), 1.0] + [10 / 21 if i % 2 == 0 else 11 / 21 for i in range(2, 100)]
    assert report["arms"][0]["delivered_agreement"] == pytest.approx(sum(scores) / 100)


@pytest.mark.parametrize("status", ["invalid", "abstained", "error"])
def test_attempted_failures_stay_in_full_denominator_as_zero(status):
    data = bundle()
    row = next(row for row in data["observations"] if row["job_id"] == "0-0-0")
    row.update(status=status, preference=None)
    report = analyze_bundle(data)
    expected = 0.5 - (10 / 21) / 200
    assert report["arms"][0]["delivered_agreement"] == pytest.approx(expected)
    assert report["arms"][0]["primary_statuses"][status] == 1
    assert report["families"] == 100


def test_any_unattempted_slot_prevents_adoption():
    data = bundle()
    data["observations"].pop()
    report = analyze_bundle(data)
    assert report["unattempted_calls"] == 1
    assert [row["decision"] for row in report["comparisons"]] == ["inconclusive", "inconclusive"]
    assert report["comparisons"][0]["payback_evaluated_questions"] is None


def test_later_nominal_pass_cannot_bypass_failed_first_gate():
    data = bundle()
    for row in data["observations"]:
        arm_index = row["job_id"].split("-")[1]
        if arm_index == "1":
            row["preference"] = "A"
        elif arm_index == "2":
            row["preference"] = "B"
    first, second = analyze_bundle(data)["comparisons"]
    assert first["decision"] == "do_not_adopt"
    assert not first["quality_gate_passed"]
    assert second["quality_gate_passed"] and second["cost_gate_passed"]
    assert second["decision"] == "descriptive_only"
    assert second["payback_evaluated_questions"] is None


def test_unknown_candidate_cost_uses_full_upper_reservation():
    data = bundle()
    for row in data["observations"]:
        if row["job_id"].split("-")[1] == "0":
            row.update(cost_lower_usd="0", cost_upper_usd="1")
    first = analyze_bundle(data)["comparisons"][0]
    assert not first["cost_gate_passed"]
    assert first["decision"] == "do_not_adopt"
    assert Decimal(first["candidate_primary_cost_upper_usd"]) == 200


def test_unknown_baseline_cost_cannot_manufacture_savings():
    data = bundle()
    for row in data["observations"]:
        if row["job_id"].split("-")[1] == "1":
            row.update(cost_lower_usd="0", cost_upper_usd="1")
    first = analyze_bundle(data)["comparisons"][0]
    assert not first["cost_gate_passed"]
    assert first["estimated_saving_fraction_lower_accounting"] is None


def test_payback_includes_all_calibration_arms_and_extra_setup_cost():
    data = bundle()
    data["calibration_cost_upper_usd"] = "2"
    first = analyze_bundle(data)["comparisons"][0]
    saving = Decimal(first["saving_per_evaluated_question_usd"])
    expected = int((Decimal(2) / saving).to_integral_value(rounding="ROUND_CEILING"))
    assert first["payback_evaluated_questions"] == expected
    assert first["payback_provider_judgments"] == 2 * expected
    data["calibration_cost_upper_usd"] = "0"
    with pytest.raises(ValueError, match="omit"):
        analyze_bundle(data)


@pytest.mark.parametrize("change", ["observation", "slot", "family"])
def test_duplicate_evidence_is_rejected(change):
    data = bundle()
    key = {"observation": "observations", "slot": "jobs", "family": "families"}[change]
    data[key].append(deepcopy(data[key][0]))
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        analyze_bundle(data)


def test_pairing_requires_identical_orders_and_no_reference_leakage():
    data = bundle()
    data["jobs"][0]["order"] = ["B", "A"]
    with pytest.raises(ValueError, match="Duplicate"):
        analyze_bundle(data)
    data = bundle()
    data["observations"][1]["displayed_preference"] = data["observations"][1]["preference"]
    with pytest.raises(ValueError, match="mapping"):
        analyze_bundle(data)


@pytest.mark.parametrize("value", ["NaN", "-1", 0.001])
def test_money_requires_finite_nonnegative_exact_decimal_strings(value):
    data = bundle()
    data["observations"][0]["cost_upper_usd"] = value
    with pytest.raises(ValueError):
        analyze_bundle(data)


def test_power_plan_keeps_fractional_paired_sd_assumptions_explicit():
    report = power_sensitivity(1942)
    row = next(row for row in report["assumptions"] if row["assumed_family_difference_sd"] == 0.15)
    assert row["families_for_80_percent_power"] == 1392
    assert row["power_at_true_zero_difference"] == pytest.approx(0.902, abs=0.0001)
    assert power_sensitivity(1000)["families"] == 1000


def test_seeded_null_simulations_report_uncertainty_and_do_not_call_models():
    kwargs = {"families": 100, "simulations": 20, "resamples": 200, "seed": 19}
    first = simulate_operating_characteristics(**kwargs)
    assert first == simulate_operating_characteristics(**kwargs)
    assert len(first["scenarios"]) == 4
    for row in first["scenarios"]:
        low, high = row["gate_pass_rate_monte_carlo_95_interval"]
        assert 0 <= low <= row["quality_gate_pass_rate"] <= high <= 1
        assert row["replicates"] == 20
    assert first["scenarios"][0]["true_difference"] == pytest.approx(-0.01)


def frozen_transport_fixture(tmp_path, monkeypatch, *, timeout=False):
    import json

    import httpx
    import test_confirmatory_run as controls

    from scripts.judge_study import confirmatory_run as transport

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    config = controls.configuration()
    config["analysis"] = deepcopy(DEFAULT_SETTINGS)
    config["evidence_files"] = ["eligible-manifest.json"]
    protocol = controls.protocol()
    protocol["judges"][1]["id"] = "terra-checklist"
    protocol["primary_contrasts"][0]["right"] = "terra-checklist"
    transport.save_once(tmp_path / "protocol.json", protocol)
    transport.save_once(tmp_path / "configuration.json", config)
    transport.save_once(
        tmp_path / "prior-budget.json", {"conservative_accounted_total_usd": "15.14292900"}
    )
    transport.save_once(
        tmp_path / "eligible-manifest.json",
        {
            "selected": [
                {"item_id": item["item_id"], "domain": "control"} for item in protocol["items"]
            ]
        },
    )
    plan = transport.freeze(tmp_path)

    def respond(request):
        if timeout:
            raise httpx.ReadTimeout("Injected no-network timeout", request=request)
        model = json.loads(request.content)["model"]
        return httpx.Response(200, json=controls.response(model))

    if timeout:
        with pytest.raises(RuntimeError, match="stopped"):
            transport.run(tmp_path, transport=httpx.MockTransport(respond))
    else:
        transport.run(tmp_path, transport=httpx.MockTransport(respond))
    return plan


def test_root_loader_checks_real_frozen_plan_ledger_and_phase_mapping(tmp_path, monkeypatch):
    from scripts.judge_study.confirmatory_analysis import analyze_root

    plan = frozen_transport_fixture(tmp_path, monkeypatch)
    report = analyze_root(tmp_path)
    assert report["complete_frozen_grid"]
    assert report["scheduled_calls"] == len(plan["jobs"]) == 12
    assert report["families"] == 2
    assert report["provenance"]["accounting"]["provider_requests"] == 12
    assert report["comparisons"][0]["decision"] == "inconclusive"
    assert set(report["comparisons"][0]["domains"]) == {"control"}
    assert Decimal(report["cost_upper_usd"]) > 0


def test_unsettled_usage_remains_reserved_and_incomplete_in_root_loader(tmp_path, monkeypatch):
    from scripts.judge_study.confirmatory_analysis import analyze_root

    frozen_transport_fixture(tmp_path, monkeypatch, timeout=True)
    report = analyze_root(tmp_path)
    assert report["unattempted_calls"] > 0
    assert Decimal(report["cost_upper_usd"]) > 0
    assert Decimal(report["cost_lower_usd"]) == 0
    assert all(row["decision"] == "inconclusive" for row in report["comparisons"])


@pytest.mark.parametrize("field", ["cost", "preference", "request"])
def test_root_loader_rejects_tampered_evidence(tmp_path, monkeypatch, field):
    import json

    from scripts.judge_study.confirmatory_analysis import analyze_root

    frozen_transport_fixture(tmp_path, monkeypatch)
    path = next((tmp_path / "requests").glob("*.result.json"))
    result = json.loads(path.read_text())
    if field == "cost":
        result["cost_usd"] = "0"
    elif field == "preference":
        result["normalized"]["preference"] = "tie"
    else:
        result["request_sha256"] = "0" * 64
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        analyze_root(tmp_path)


def test_sparse_quality_differences_withhold_inference_before_any_gate():
    data = bundle()
    baseline = {row["job_id"]: row for row in data["observations"]}
    for row in data["observations"]:
        family, arm, order = row["job_id"].split("-")
        if arm == "0" and int(family) >= 10:
            row["preference"] = baseline[f"{family}-1-{order}"]["preference"]
    first = analyze_bundle(data)["comparisons"][0]
    quality = first["quality_difference_candidate_minus_baseline"]
    assert quality["nonzero_family_differences"] == 10
    assert quality["minimum_discordant_families"] == 20
    assert quality["undefined_reason"] == "insufficient_discordant_families"
    assert quality["bootstrap_lower"] is quality["t_lower"] is None
    assert first["decision"] == "inconclusive"
    assert not first["quality_gate_passed"]


def test_reference_manifest_tampering_is_detected_before_analysis(tmp_path, monkeypatch):
    import json

    from scripts.judge_study.confirmatory_analysis import analyze_root

    frozen_transport_fixture(tmp_path, monkeypatch)
    path = tmp_path / "eligible-manifest.json"
    record = json.loads(path.read_text())
    record["selected"][0]["domain"] = "changed"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="changed"):
        analyze_root(tmp_path)
