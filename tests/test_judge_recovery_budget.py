"""Recovery retains unknown charges and only admits explicitly reviewed work."""

import hashlib
import importlib
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
budget = importlib.import_module("scripts.benchmarks.budget")
concurrent = importlib.import_module("scripts.judge_study.concurrent_budget")
recovery = importlib.import_module("scripts.judge_study.recovery_budget")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def case(tmp_path, cap="0.01"):
    config = budget.BudgetConfig(
        campaign_id="recovery",
        campaign_config_hash=digest("plan"),
        pricing_version="fixed",
        user_ceiling_usd=Decimal(cap),
        admission_ceiling_usd=Decimal(cap),
        attempt_cap_usd=min(Decimal(cap), Decimal(".001")),
        input_rate_per_million=Decimal("1"),
        output_rate_per_million=Decimal("1"),
    )
    original = concurrent.ConcurrentLedger(tmp_path / "ledger.sqlite3", config)
    original.register_attempt("rejected", digest("rejected-config"))
    request_id = original.reserve("rejected", digest("rejected-request"), 100, 100)
    original.uncertain(request_id, "ValueError")
    with original._connection() as db:
        snapshot = budget.BudgetLedger._review_snapshot(original._request(db, request_id))
    context = budget.RecoveryContext(
        plan_sha256=config.campaign_config_hash,
        amendment_sha256=digest("approved-manifest"),
        allowed_attempts=tuple((f"new-{i}", digest(f"new-{i}-config")) for i in range(8)),
    )
    reviewed = recovery.ReviewedConcurrentLedger(
        original.path, config, recovery_context=context, approved_request=snapshot
    )
    return original, reviewed, context, snapshot, request_id


def review(instance, request_id):
    with instance._connection() as db:
        row = instance._request(db, request_id)
        history = [dict(value) for value in db.execute("SELECT * FROM events ORDER BY sequence")]
        args = {
            "request_hash": row["request_hash"],
            "reserved_usd": Decimal(row["reserved_usd"]),
            "decision_sha256": digest("decision"),
            "evidence_sha256": digest("exact-http400-invalid-prompt-evidence"),
            "event_prefix_count": len(history),
            "event_prefix_sha256": instance._events_digest(history),
        }
    instance.record_bounded_review(request_id, **args)
    return args


def reserve(instance, index):
    name = f"new-{index}"
    instance.register_attempt(name, digest(name + "-config"))
    return instance.reserve(name, digest(name + "-request"), 100, 100)


def test_review_retains_original_unknown_and_full_reserve(tmp_path):
    original, reviewed, _, snapshot, request_id = case(tmp_path)
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(reviewed, 0)
    args = review(reviewed, request_id)
    reviewed.record_bounded_review(request_id, **args)
    new_id = reserve(reviewed, 0)
    with reviewed._connection() as db:
        assert budget.BudgetLedger._review_snapshot(reviewed._request(db, request_id)) == snapshot
        totals = reviewed._totals(list(db.execute("SELECT * FROM requests")))
    assert totals["committed_request_cost_usd"] == Decimal(".0004")
    assert totals["uncertain_requests"] == 1
    reviewed.settle(new_id, 0, 0)
    assert reviewed.status()["committed_spend_usd"] == "0.0002"
    assert original.status()["admissions_blocked"] is True


def test_no_repeated_requests_or_registered_allowlist_bypass(tmp_path):
    original, reviewed, _, _, request_id = case(tmp_path)
    original.register_attempt("outside", digest("outside-config"))
    review(reviewed, request_id)
    with pytest.raises(budget.BudgetError, match="quarantined"):
        reviewed.reserve("rejected", digest("different-body"), 100, 100)
    reviewed.register_attempt("outside", digest("outside-config"))
    with pytest.raises(budget.BudgetError, match="allowlist"):
        reviewed.reserve("outside", digest("outside-body"), 100, 100)
    new_id = reserve(reviewed, 0)
    reviewed.settle(new_id, 0, 0)
    with pytest.raises(budget.BudgetError, match="one admitted request"):
        reviewed.reserve("new-0", digest("second-body"), 100, 100)


def test_retained_reviewed_reserve_counts_against_unchanged_cap(tmp_path):
    _, reviewed, _, _, request_id = case(tmp_path, cap=".0005")
    review(reviewed, request_id)
    reserve(reviewed, 0)
    with pytest.raises(budget.BudgetError, match="admission ceiling"):
        reserve(reviewed, 1)


def test_new_uncertainty_stops_admissions_and_cannot_reuse_review(tmp_path):
    _, reviewed, _, _, request_id = case(tmp_path)
    review(reviewed, request_id)
    next_id = reserve(reviewed, 0)
    reviewed.uncertain(next_id, "ValueError")
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(reviewed, 1)
    with pytest.raises(budget.BudgetError, match="exact frozen"):
        review(reviewed, next_id)


@pytest.mark.parametrize("change", ["reason", "usage", "snapshot"])
def test_only_exact_frozen_no_usage_value_error_is_eligible(tmp_path, change):
    original, _, context, snapshot, request_id = case(tmp_path)
    if change == "reason":
        snapshot["reason"] = "UnknownUsage"
    elif change == "usage":
        snapshot["input_tokens"] = 1
    else:
        snapshot["updated_at"] = "changed"
    if change != "snapshot":
        with pytest.raises(budget.BudgetError, match="exact reviewed"):
            recovery.ReviewedConcurrentLedger(
                original.path, original.config, recovery_context=context, approved_request=snapshot
            )
    else:
        reviewed = recovery.ReviewedConcurrentLedger(
            original.path, original.config, recovery_context=context, approved_request=snapshot
        )
        with pytest.raises(budget.BudgetError, match="exact frozen"):
            review(reviewed, request_id)


def test_other_models_cannot_review_unknowns_without_exact_approval(tmp_path):
    original, _, context, _, request_id = case(tmp_path)
    reviewed = recovery.ReviewedConcurrentLedger(
        original.path, original.config, recovery_context=context
    )
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(reviewed, 0)
    with pytest.raises(budget.BudgetError, match="exact frozen"):
        review(reviewed, request_id)


def test_four_owned_leases_and_no_adoption_after_restart(tmp_path):
    original, reviewed, context, snapshot, request_id = case(tmp_path)
    review(reviewed, request_id)
    for index in range(8):
        reviewed.register_attempt(f"new-{index}", digest(f"new-{index}-config"))

    def admit(index):
        try:
            return reserve(reviewed, index)
        except budget.BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admit, range(8)))
    assert sum(value is not None for value in results) == 4
    with reviewed._connection() as db:
        totals = reviewed._totals(list(db.execute("SELECT * FROM requests")))
    assert totals["pending_requests"] == 4
    assert totals["committed_request_cost_usd"] == Decimal(".001")
    restarted = recovery.ReviewedConcurrentLedger(
        original.path, original.config, recovery_context=context, approved_request=snapshot
    )
    unattempted = next(i for i, value in enumerate(results) if value is None)
    with pytest.raises(budget.BudgetError, match="Unowned pending"):
        reserve(restarted, unattempted)


def test_event_prefix_tampering_invalidates_review(tmp_path):
    _, reviewed, _, _, request_id = case(tmp_path)
    review(reviewed, request_id)
    with sqlite3.connect(reviewed.path) as db:
        db.execute("UPDATE events SET recorded_at='changed' WHERE sequence=1")
    with pytest.raises(budget.BudgetError, match="event prefix"):
        reserve(reviewed, 0)
