"""Bounded recovery retains unknown usage and admits only explicitly reviewed scope."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
budget = importlib.import_module("scripts.benchmarks.budget")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def rows(instance, table):
    assert table in {"events", "requests", "campaign"}
    with sqlite3.connect(instance.path) as connection:
        connection.row_factory = sqlite3.Row
        order = " ORDER BY sequence" if table == "events" else ""
        return [dict(row) for row in connection.execute("SELECT * FROM " + table + order)]


def recovery_case(tmp_path, *, inputs=14713, outputs=8192, **changes):
    config = budget.BudgetConfig(
        campaign_id="recovery-test",
        campaign_config_hash=digest("original-plan"),
        pricing_version="frozen-prices",
        **{"opening_spend_usd": Decimal("0.482999"), **changes},
    )
    original = budget.BudgetLedger(tmp_path / "private" / "budget.sqlite3", config)
    original.register_attempt("interrupted", digest("interrupted-config"))
    request_id = original.reserve("interrupted", digest("interrupted-request"), inputs, outputs)
    original.uncertain(request_id, "UnknownUsage")
    context = budget.RecoveryContext(
        plan_sha256=config.campaign_config_hash,
        amendment_sha256=digest("reviewed-amendment"),
        allowed_attempts=tuple(
            (name, digest(name + "-config")) for name in ("alpha", "beta", "gamma")
        ),
    )
    amended = budget.BudgetLedger(original.path, config, recovery_context=context)
    return original, amended, context, request_id


def review_arguments(instance, request_id, *, decision="decision", evidence="evidence"):
    request = next(row for row in rows(instance, "requests") if row["id"] == request_id)
    history = rows(instance, "events")
    return {
        "request_hash": request["request_hash"],
        "reserved_usd": Decimal(request["reserved_usd"]),
        "decision_sha256": digest(decision),
        "evidence_sha256": digest(evidence),
        "event_prefix_count": len(history),
        "event_prefix_sha256": canonical_digest(history),
    }


def review(instance, request_id, **changes):
    arguments = {**review_arguments(instance, request_id), **changes}
    return instance.record_bounded_review(request_id, **arguments)


def register(instance, name):
    instance.register_attempt(name, digest(name + "-config"))


def reserve(instance, name, request="request", inputs=1000, outputs=1000):
    return instance.reserve(name, digest(name + "-" + request), inputs, outputs)


def test_review_appends_one_event_and_retains_unknown_row_and_budget(tmp_path):
    original, amended, context, request_id = recovery_case(tmp_path)
    before_requests = rows(original, "requests")
    before_events = rows(original, "events")
    before_campaign = rows(original, "campaign")
    before = original.status()
    sequence = review(amended, request_id)
    after = amended.status()

    assert sequence == len(before_events) + 1
    assert rows(amended, "requests") == before_requests
    assert rows(amended, "campaign") == before_campaign
    assert rows(amended, "events")[:-1] == before_events
    event = rows(amended, "events")[-1]
    assert event["kind"] == "bounded_unknown_reviewed"
    assert event["request_id"] == request_id
    data = json.loads(event["data"])
    assert set(data) == {
        "schema_version",
        "plan_sha256",
        "amendment_sha256",
        "allowed_attempts_sha256",
        "attempt_hash",
        "request_record",
        "decision_sha256",
        "evidence_sha256",
        "event_prefix",
    }
    assert data["request_record"] == before_requests[0]
    assert data["event_prefix"] == {
        "count": len(before_events),
        "sha256": canonical_digest(before_events),
    }
    assert data["allowed_attempts_sha256"] == canonical_digest(context.allowed_attempts)
    for field in (
        "known_spend_usd",
        "committed_spend_usd",
        "outstanding_reservations_usd",
        "reserved_ceiling_usd",
        "ledger_config_hash",
        "campaign",
        "cost_complete",
    ):
        assert after[field] == before[field]
    assert after["cost_complete"] is False
    assert after["known_spend_usd"] == "0.482999"
    assert after["committed_spend_usd"] == "0.6017015"
    assert after["bounded_reviewed_reservation_usd"] == "0.1187025"
    assert after["reviewed_uncertain_requests"] == 1
    assert after["unreviewed_uncertain_requests"] == 0
    assert after["quarantined_attempts"] == ["interrupted"]
    assert after["recovery_amendment_sha256"] == context.amendment_sha256
    assert after["admissions_blocked"] is False
    assert before_requests[0]["input_tokens"] is None
    assert before_requests[0]["output_tokens"] is None


def test_exact_review_is_idempotent_after_later_events_but_conflicts_fail(tmp_path):
    _, amended, _, request_id = recovery_case(tmp_path)
    arguments = review_arguments(amended, request_id)
    sequence = amended.record_bounded_review(request_id, **arguments)
    register(amended, "alpha")
    before = rows(amended, "events")
    assert amended.record_bounded_review(request_id, **arguments) == sequence
    for changed in (
        {"decision_sha256": digest("different-decision")},
        {"evidence_sha256": digest("different-evidence")},
        {"event_prefix_sha256": digest("different-prefix")},
        {"event_prefix_count": arguments["event_prefix_count"] + 1},
    ):
        with pytest.raises(budget.BudgetError, match="immutable"):
            amended.record_bounded_review(request_id, **{**arguments, **changed})
    assert rows(amended, "events") == before


def test_stale_event_prefix_cannot_append_review(tmp_path):
    _, amended, _, request_id = recovery_case(tmp_path)
    arguments = review_arguments(amended, request_id)
    register(amended, "alpha")
    before = rows(amended, "events")
    with pytest.raises(budget.BudgetError, match="prefix"):
        amended.record_bounded_review(request_id, **arguments)
    assert rows(amended, "events") == before
    assert amended.status()["admissions_blocked"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_prefix_count", True),
        ("event_prefix_count", 0),
        ("event_prefix_count", -1),
        ("event_prefix_sha256", "bad"),
        ("request_hash", digest("other-request")),
        ("reserved_usd", Decimal("0.000001")),
    ],
)
def test_invalid_review_binding_fails_without_mutation(tmp_path, field, value):
    _, amended, _, request_id = recovery_case(tmp_path)
    before = rows(amended, "events")
    with pytest.raises(budget.BudgetError):
        review(amended, request_id, **{field: value})
    assert rows(amended, "events") == before


def test_stored_reviews_do_not_relax_admission_without_explicit_context(tmp_path):
    original, amended, _, request_id = recovery_case(tmp_path)
    review(amended, request_id)
    register(amended, "alpha")
    ordinary = budget.BudgetLedger(original.path, original.config, create=False)
    status = ordinary.status()
    assert status["admissions_blocked"] is True
    assert status["cost_complete"] is False
    assert status["reviewed_uncertain_requests"] == 0
    assert status["unreviewed_uncertain_requests"] == 1
    assert status["recovery_amendment_sha256"] is None
    with pytest.raises(budget.BudgetError, match="blocks"):
        reserve(ordinary, "alpha")
    with pytest.raises(budget.BudgetError, match="context"):
        review(ordinary, request_id)


def test_allowlist_binds_both_new_and_existing_attempt_identities(tmp_path):
    original, amended, _, request_id = recovery_case(tmp_path)
    review(amended, request_id)
    with pytest.raises(budget.BudgetError, match="allowlist"):
        amended.register_attempt("unlisted", digest("unlisted-config"))
    with pytest.raises(budget.BudgetError, match="allowlist"):
        amended.register_attempt("alpha", digest("different-config"))
    original.register_attempt("unlisted", digest("unlisted-config"))
    with pytest.raises(budget.BudgetError, match="allowlist"):
        reserve(amended, "unlisted")
    original.register_attempt("alpha", digest("different-config"))
    with pytest.raises(budget.BudgetError, match="allowlist"):
        reserve(amended, "alpha")
    register(amended, "beta")
    assert reserve(amended, "beta")


def test_pending_and_new_uncertainties_always_stop_again_until_explicit_review(tmp_path):
    _, amended, _, request_id = recovery_case(tmp_path)
    review(amended, request_id)
    for name in ("alpha", "beta"):
        register(amended, name)
    pending = reserve(amended, "alpha")
    with pytest.raises(budget.BudgetError, match="blocks"):
        reserve(amended, "beta")
    with pytest.raises(budget.BudgetError, match="UnknownUsage"):
        review(amended, pending)
    amended.uncertain(pending, "UnknownUsage")
    status = amended.status()
    assert status["reviewed_uncertain_requests"] == 1
    assert status["unreviewed_uncertain_requests"] == 1
    assert status["admissions_blocked"] is True
    with pytest.raises(budget.BudgetError, match="blocks"):
        reserve(amended, "beta")
    review(amended, pending, decision_sha256=digest("second-decision"))
    status = amended.status()
    assert status["reviewed_uncertain_requests"] == 2
    assert status["bounded_reviewed_reservation_usd"] == "0.1312025"
    assert status["cost_complete"] is False
    assert status["quarantined_attempts"] == ["alpha", "interrupted"]
    with pytest.raises(budget.BudgetError, match="quarantined"):
        reserve(amended, "alpha", request="retry")
    with pytest.raises(budget.BudgetError, match="quarantined"):
        reserve(amended, "interrupted", request="retry")
    assert reserve(amended, "beta")


def test_reviewed_unknown_can_neither_settle_nor_be_rewritten(tmp_path):
    original, amended, _, request_id = recovery_case(tmp_path)
    review(amended, request_id)
    before = rows(amended, "requests")
    for instance in (original, amended):
        with pytest.raises(budget.UsageError, match="cannot silently settle"):
            instance.settle(request_id, 0, 0)
        with pytest.raises(budget.UsageError, match="immutable"):
            instance.uncertain(request_id, "UnknownUsage")
    assert rows(amended, "requests") == before


@pytest.mark.parametrize("reason", ["invalid_usage", "usage_exceeds_ceiling", "TimeoutError"])
def test_other_uncertainty_classes_cannot_be_bounded_reviewed(tmp_path, reason):
    original, amended, _, request_id = recovery_case(tmp_path)
    original.uncertain(request_id, reason)
    with pytest.raises(budget.BudgetError, match="UnknownUsage"):
        review(amended, request_id)
    assert amended.status()["admissions_blocked"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"known_cost_usd": Decimal("1")},
        {"input_tokens": 14714},
        {"output_tokens": 8193},
    ],
)
def test_known_cost_or_usage_above_reservation_is_never_reviewable(tmp_path, changes):
    original, amended, _, request_id = recovery_case(tmp_path)
    original.uncertain(request_id, "UnknownUsage", **changes)
    with pytest.raises(budget.BudgetError, match="exceed"):
        review(amended, request_id)


@pytest.mark.parametrize(
    "mutation",
    [
        "request_snapshot",
        "reservation",
        "amendment",
        "allowlist",
        "prior_event",
        "prefix",
    ],
)
def test_tampered_history_context_or_request_fails_before_admission(tmp_path, mutation):
    _, amended, context, request_id = recovery_case(tmp_path)
    review(amended, request_id)
    register(amended, "alpha")
    if mutation == "amendment":
        amended = budget.BudgetLedger(
            amended.path,
            amended.config,
            recovery_context=replace(context, amendment_sha256=digest("other-amendment")),
        )
    elif mutation == "allowlist":
        amended = budget.BudgetLedger(
            amended.path,
            amended.config,
            recovery_context=replace(context, allowed_attempts=context.allowed_attempts[:1]),
        )
    else:
        with sqlite3.connect(amended.path) as connection:
            if mutation == "request_snapshot":
                connection.execute(
                    "UPDATE requests SET updated_at='tampered' WHERE id=?", (request_id,)
                )
            elif mutation == "reservation":
                connection.execute(
                    "UPDATE requests SET reserved_usd='0.01' WHERE id=?", (request_id,)
                )
            elif mutation == "prior_event":
                connection.execute("UPDATE events SET recorded_at='tampered' WHERE sequence=1")
            else:
                record = connection.execute(
                    "SELECT data FROM events WHERE kind='bounded_unknown_reviewed'"
                ).fetchone()
                data = json.loads(record[0])
                data["event_prefix"]["sha256"] = digest("tampered-prefix")
                connection.execute(
                    "UPDATE events SET data=? WHERE kind='bounded_unknown_reviewed'",
                    (json.dumps(data),),
                )
    with pytest.raises(budget.BudgetError):
        amended.status()
    with pytest.raises(budget.BudgetError):
        reserve(amended, "alpha")


def test_retained_reservation_still_counts_against_campaign_cap(tmp_path):
    _, amended, _, request_id = recovery_case(
        tmp_path,
        inputs=60_000,
        outputs=60_000,
        opening_spend_usd=Decimal("0"),
        user_ceiling_usd=Decimal("2"),
        admission_ceiling_usd=Decimal("1"),
        attempt_cap_usd=Decimal("1"),
    )
    review(amended, request_id)
    register(amended, "alpha")
    assert amended.status()["committed_spend_usd"] == "0.75"
    with pytest.raises(budget.BudgetError, match="campaign admission"):
        reserve(amended, "alpha", inputs=40_000, outputs=40_000)
    reserve(amended, "alpha", inputs=20_000, outputs=20_000)
    assert amended.status()["committed_spend_usd"] == "1"


def test_recovery_never_initializes_a_missing_or_empty_ledger(tmp_path):
    original, _, context, _ = recovery_case(tmp_path)
    missing = tmp_path / "missing" / "budget.sqlite3"
    with pytest.raises(budget.BudgetError, match="Existing accounting"):
        budget.BudgetLedger(missing, original.config, recovery_context=context)
    assert not missing.exists()
    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    with pytest.raises(budget.BudgetError, match="incomplete"):
        budget.BudgetLedger(empty, original.config, recovery_context=context)
    assert empty.stat().st_size == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"plan_sha256": "bad"},
        {"amendment_sha256": "bad"},
        {"allowed_attempts": []},
        {"allowed_attempts": ()},
        {"allowed_attempts": (["alpha", digest("alpha")],)},
        {"allowed_attempts": (("alpha", "bad"),)},
        {"allowed_attempts": (("beta", digest("beta")), ("alpha", digest("alpha")))},
        {"allowed_attempts": (("alpha", digest("alpha")), ("alpha", digest("other")))},
        {"allowed_attempts": (("alpha", digest("same")), ("beta", digest("same")))},
    ],
)
def test_recovery_context_requires_valid_immutable_unique_scope(tmp_path, changes):
    _, _, context, _ = recovery_case(tmp_path)
    with pytest.raises(budget.BudgetError):
        replace(context, **changes)


def test_recovery_context_must_match_original_plan(tmp_path):
    original, _, context, _ = recovery_case(tmp_path)
    with pytest.raises(budget.BudgetError, match="frozen campaign"):
        budget.BudgetLedger(
            original.path,
            original.config,
            recovery_context=replace(context, plan_sha256=digest("different-plan")),
        )


def test_concurrent_identical_review_appends_once_and_keeps_row_unchanged(tmp_path):
    original, amended, context, request_id = recovery_case(tmp_path)
    arguments = review_arguments(amended, request_id)
    before = rows(amended, "requests")
    barrier = threading.Barrier(2)

    def contender():
        instance = budget.BudgetLedger(original.path, original.config, recovery_context=context)
        barrier.wait(timeout=10)
        return instance.record_bounded_review(request_id, **arguments)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sequences = list(pool.map(lambda _: contender(), range(2)))
    assert sequences[0] == sequences[1]
    assert (
        sum(event["kind"] == "bounded_unknown_reviewed" for event in rows(amended, "events")) == 1
    )
    assert rows(amended, "requests") == before


def test_concurrent_recovery_admissions_allow_only_one_pending_request(tmp_path):
    original, amended, context, request_id = recovery_case(tmp_path)
    review(amended, request_id)
    for name in ("alpha", "beta"):
        register(amended, name)
    barrier = threading.Barrier(2)

    def contender(name):
        instance = budget.BudgetLedger(original.path, original.config, recovery_context=context)
        barrier.wait(timeout=10)
        try:
            return reserve(instance, name)
        except budget.BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, ("alpha", "beta")))
    assert sum(value is not None for value in results) == 1
    status = amended.status()
    assert status["pending_requests"] == 1
    assert status["reviewed_uncertain_requests"] == 1
    assert status["admissions_blocked"] is True
