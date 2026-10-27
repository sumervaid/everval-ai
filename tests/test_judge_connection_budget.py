"""Two narrowly reviewed uncertainties never become a generic billing exemption."""

import hashlib
import importlib
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
budget = importlib.import_module("scripts.benchmarks.budget")
concurrent = importlib.import_module("scripts.judge_study.concurrent_budget")
policy = importlib.import_module("scripts.judge_study.recovery_budget")
connection = importlib.import_module("scripts.judge_study.connection_budget")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def snapshot(ledger, request_id):
    with ledger._connection() as db:
        return ledger._review_snapshot(ledger._request(db, request_id))


def review(ledger, request_id, decision):
    with ledger._connection() as db:
        row = ledger._request(db, request_id)
        history = [dict(value) for value in db.execute("SELECT * FROM events ORDER BY sequence")]
        args = {
            "request_hash": row["request_hash"],
            "reserved_usd": Decimal(row["reserved_usd"]),
            "decision_sha256": digest(decision),
            "evidence_sha256": digest(decision + "-evidence"),
            "event_prefix_count": len(history),
            "event_prefix_sha256": ledger._events_digest(history),
        }
    ledger.record_bounded_review(request_id, **args)
    return args


def reserve(ledger, name):
    ledger.register_attempt(name, digest(name + "-config"))
    return ledger.reserve(name, digest(name + "-request"), 100, 100)


def case(tmp_path, cap=".01"):
    config = budget.BudgetConfig(
        campaign_id="two-reviews",
        campaign_config_hash=digest("plan"),
        pricing_version="fixed",
        user_ceiling_usd=Decimal(cap),
        admission_ceiling_usd=Decimal(cap),
        attempt_cap_usd=min(Decimal(cap), Decimal(".001")),
        input_rate_per_million=Decimal("1"),
        output_rate_per_million=Decimal("1"),
    )
    original = concurrent.ConcurrentLedger(tmp_path / "ledger.sqlite3", config)
    first = reserve(original, "policy")
    original.uncertain(first, "ValueError")
    first_snapshot = snapshot(original, first)
    context = budget.RecoveryContext(
        plan_sha256=config.campaign_config_hash,
        amendment_sha256=digest("original-scope"),
        allowed_attempts=tuple(
            (name, digest(name + "-config")) for name in ("connection", "next", "third")
        ),
    )
    reviewed = policy.ReviewedConcurrentLedger(
        original.path,
        config,
        recovery_context=context,
        approved_request=first_snapshot,
    )
    review(reviewed, first, "policy-decision")
    second = reserve(reviewed, "connection")
    reviewed.uncertain(second, "ConnectError")
    second_snapshot = snapshot(reviewed, second)
    latest = connection.ConnectionReviewedLedger(
        original.path,
        config,
        recovery_context=context,
        approved_request=first_snapshot,
        connection_request=second_snapshot,
    )
    return latest, context, first_snapshot, second_snapshot, first, second


def test_preserves_both_rows_and_original_review_verbatim(tmp_path):
    ledger, context, first_row, second_row, first, second = case(tmp_path)
    with ledger._connection() as db:
        first_event = dict(
            db.execute("SELECT * FROM events WHERE kind='bounded_unknown_reviewed'").fetchone()
        )
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(ledger, "next")
    args = review(ledger, second, "connection-decision")
    ledger.record_bounded_review(second, **args)
    assert snapshot(ledger, first) == first_row
    assert snapshot(ledger, second) == second_row
    with ledger._connection() as db:
        events = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM events WHERE kind='bounded_unknown_reviewed' ORDER BY sequence"
            )
        ]
        assert events[0] == first_event
        assert len(events) == 2
        assert all(
            json.loads(event["data"])["amendment_sha256"] == context.amendment_sha256
            for event in events
        )
    assert ledger.status()["committed_spend_usd"] == "0.0004"
    next_id = reserve(ledger, "next")
    ledger.settle(next_id, 0, 0)
    assert ledger.status()["committed_spend_usd"] == "0.0004"
    for name in ("policy", "connection"):
        with pytest.raises(budget.BudgetError, match="quarantined"):
            ledger.reserve(name, digest("new-body"), 100, 100)


def test_third_unknown_cannot_be_reviewed_or_bypassed(tmp_path):
    ledger, _, _, _, _, second = case(tmp_path)
    review(ledger, second, "connection-decision")
    third = reserve(ledger, "next")
    ledger.uncertain(third, "ConnectError")
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(ledger, "third")
    with pytest.raises(budget.BudgetError, match="exact frozen"):
        review(ledger, third, "unapproved-decision")


def test_both_reserves_remain_inside_unchanged_cap(tmp_path):
    ledger, _, _, _, _, second = case(tmp_path, cap=".0005")
    review(ledger, second, "connection-decision")
    with pytest.raises(budget.BudgetError, match="admission ceiling"):
        reserve(ledger, "next")


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "ReadTimeout"),
        ("input_tokens", 1),
        ("output_tokens", 1),
        ("known_cost_usd", "0.000001"),
        ("state", "pending"),
        ("attempt_id", "outside"),
    ],
)
def test_requires_exact_no_usage_connection_snapshot(tmp_path, field, value):
    ledger, context, first_row, second_row, _, _ = case(tmp_path)
    second_row[field] = value
    with pytest.raises(budget.BudgetError):
        connection.ConnectionReviewedLedger(
            ledger.path,
            ledger.config,
            recovery_context=context,
            approved_request=first_row,
            connection_request=second_row,
        )


def test_snapshot_substitution_and_mutation_cannot_expand_authority(tmp_path):
    ledger, context, first_row, second_row, _, second = case(tmp_path)
    forged = dict(second_row, updated_at="changed")
    wrong = connection.ConnectionReviewedLedger(
        ledger.path,
        ledger.config,
        recovery_context=context,
        approved_request=first_row,
        connection_request=forged,
    )
    with pytest.raises(budget.BudgetError, match="exact frozen"):
        review(wrong, second, "connection-decision")
    second_row["reason"] = "OtherError"
    review(ledger, second, "connection-decision")  # constructor keeps its own exact copy
    reserve(ledger, "next")


def test_original_event_tampering_still_blocks_second_scope(tmp_path):
    ledger, _, _, _, _, second = case(tmp_path)
    review(ledger, second, "connection-decision")
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE events SET recorded_at='changed' WHERE sequence=1")
    with pytest.raises(budget.BudgetError, match="event prefix"):
        reserve(ledger, "next")


def test_other_models_have_no_connection_exception(tmp_path):
    ledger, context, first_row, _, _, second = case(tmp_path)
    unapproved = connection.ConnectionReviewedLedger(
        ledger.path,
        ledger.config,
        recovery_context=context,
        approved_request=first_row,
    )
    with pytest.raises(budget.BudgetError, match="exact frozen"):
        review(unapproved, second, "connection-decision")
    with pytest.raises(budget.BudgetError, match="Unreviewed"):
        reserve(unapproved, "next")
