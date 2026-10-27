"""Flattened review authority retains every charge and cannot expand dispatch."""

import importlib
import sqlite3
from decimal import Decimal

import pytest
from test_judge_recovery_budget import case, digest, reserve, review

connection = importlib.import_module("scripts.judge_study.connection_budget")
flat = importlib.import_module("scripts.judge_study.flat_budget")
budget = importlib.import_module("scripts.benchmarks.budget")


def snapshot(ledger, request_id):
    with ledger._connection() as db:
        return ledger._review_snapshot(ledger._request(db, request_id))


def three_failures(tmp_path, cap=".01"):
    original, first_ledger, context, first_row, first = case(tmp_path, cap)
    review(first_ledger, first)
    second = reserve(first_ledger, 0)
    first_ledger.uncertain(second, "ConnectError")
    second_row = snapshot(first_ledger, second)
    second_ledger = connection.ConnectionReviewedLedger(
        original.path,
        original.config,
        recovery_context=context,
        approved_request=first_row,
        connection_request=second_row,
    )
    review(second_ledger, second)
    third = reserve(second_ledger, 1)
    second_ledger.uncertain(third, "ValueError")
    approved = [first_row, second_row, snapshot(second_ledger, third)]
    allowed = {f"new-{i}": digest(f"new-{i}-request") for i in range(2, 7)}
    ledger = flat.FlatReviewedLedger(
        original.path,
        original.config,
        recovery_context=context,
        approved_requests=approved,
        dispatch_allowlist=allowed,
    )
    return ledger, context, approved, allowed, (first, second, third)


def test_original_reviews_and_three_full_reservations_are_preserved(tmp_path):
    ledger, _, snapshots, _, requests = three_failures(tmp_path)
    with ledger._connection() as db:
        before = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM events WHERE kind='bounded_unknown_reviewed' ORDER BY sequence"
            )
        ]
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(ledger, 2)
    review(ledger, requests[2])
    with ledger._connection() as db:
        after = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM events WHERE kind='bounded_unknown_reviewed' ORDER BY sequence"
            )
        ]
    assert after[:2] == before
    assert len(after) == 3
    assert [snapshot(ledger, key) for key in requests] == snapshots
    assert ledger.status()["committed_spend_usd"] == "0.0006"
    admitted = reserve(ledger, 2)
    ledger.settle(admitted, 0, 0)
    state = ledger.status()
    assert state["reviewed_uncertain_requests"] == 3
    assert state["committed_spend_usd"] == "0.0006"


def test_dispatch_is_narrowed_even_for_pre_registered_jobs_and_request_bodies(tmp_path):
    ledger, _, _, _, requests = three_failures(tmp_path)
    review(ledger, requests[2])
    ledger.register_attempt("new-7", digest("new-7-config"))
    with pytest.raises(budget.BudgetError, match="narrowed dispatch"):
        ledger.reserve("new-7", digest("new-7-request"), 100, 100)
    ledger.register_attempt("new-2", digest("new-2-config"))
    with pytest.raises(budget.BudgetError, match="narrowed dispatch"):
        ledger.reserve("new-2", digest("different-body"), 100, 100)
    for attempt in ("rejected", "new-0", "new-1"):
        with pytest.raises(budget.BudgetError, match="narrowed dispatch"):
            ledger.reserve(attempt, digest(attempt + "-request"), 100, 100)


def test_fourth_uncertainty_stops_every_admission_and_cannot_reuse_reviews(tmp_path):
    ledger, _, _, _, requests = three_failures(tmp_path)
    review(ledger, requests[2])
    fourth = reserve(ledger, 2)
    ledger.uncertain(fourth, "ValueError")
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(ledger, 3)
    with pytest.raises(budget.BudgetError, match="exact frozen exception"):
        review(ledger, fourth)


def test_all_three_reservations_count_against_the_original_cap(tmp_path):
    ledger, _, _, _, requests = three_failures(tmp_path, ".0007")
    review(ledger, requests[2])
    with pytest.raises(budget.BudgetError, match="admission ceiling"):
        reserve(ledger, 2)


def test_callers_cannot_mutate_approved_snapshots_or_dispatch_after_construction(tmp_path):
    ledger, _, approved, allowed, requests = three_failures(tmp_path)
    approved[2]["updated_at"] = "changed"
    allowed["new-7"] = digest("new-7-request")
    review(ledger, requests[2])
    reserve(ledger, 2)
    with pytest.raises(budget.BudgetError, match="narrowed dispatch"):
        reserve(ledger, 7)


@pytest.mark.parametrize("change", ["reason", "usage", "fourth", "snapshot", "dispatch_scope"])
def test_unapproved_exception_or_dispatch_changes_fail_closed(tmp_path, change):
    ledger, context, approved, allowed, requests = three_failures(tmp_path)
    if change == "reason":
        approved[2]["reason"] = "UnknownUsage"
    elif change == "usage":
        approved[2]["input_tokens"] = 1
    elif change == "fourth":
        approved.append(dict(approved[2], id="fourth"))
    elif change == "snapshot":
        approved[2]["updated_at"] = "changed"
    else:
        allowed["outside"] = digest("outside-request")
    if change == "snapshot":
        changed = flat.FlatReviewedLedger(
            ledger.path,
            ledger.config,
            recovery_context=context,
            approved_requests=approved,
            dispatch_allowlist=allowed,
        )
        with pytest.raises(budget.BudgetError, match="exact frozen exception"):
            review(changed, requests[2])
    else:
        with pytest.raises(budget.BudgetError):
            flat.FlatReviewedLedger(
                ledger.path,
                ledger.config,
                recovery_context=context,
                approved_requests=approved,
                dispatch_allowlist=allowed,
            )


def test_four_owned_leases_remain_the_limit_and_restart_cannot_adopt_them(tmp_path):
    ledger, context, approved, allowed, requests = three_failures(tmp_path)
    review(ledger, requests[2])
    for index in range(2, 6):
        reserve(ledger, index)
    with pytest.raises(budget.BudgetError, match="Four requests"):
        reserve(ledger, 6)
    restarted = flat.FlatReviewedLedger(
        ledger.path,
        ledger.config,
        recovery_context=context,
        approved_requests=approved,
        dispatch_allowlist=allowed,
    )
    with pytest.raises(budget.BudgetError, match="Unowned pending"):
        reserve(restarted, 6)
    assert Decimal(ledger.status()["committed_spend_usd"]) == Decimal(".0014")


def test_original_review_event_prefix_is_still_verified(tmp_path):
    ledger, _, _, _, requests = three_failures(tmp_path)
    review(ledger, requests[2])
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE events SET recorded_at='changed' WHERE sequence=1")
    with pytest.raises(budget.BudgetError, match="event prefix"):
        reserve(ledger, 2)


def test_models_without_approved_exceptions_receive_no_billing_bypass(tmp_path):
    original, _, context, _, request_id = case(tmp_path)
    ledger = flat.FlatReviewedLedger(
        original.path,
        original.config,
        recovery_context=context,
        approved_requests=[],
        dispatch_allowlist={"new-0": digest("new-0-request")},
    )
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(ledger, 0)
    with pytest.raises(budget.BudgetError, match="exact frozen exception"):
        review(ledger, request_id)
