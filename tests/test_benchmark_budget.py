"""Budget accounting tests use synthetic usage only; they make no provider calls."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
budget = importlib.import_module("scripts.benchmarks.budget")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def config(**changes):
    changes = {"opening_spend_usd": Decimal("0.482999"), **changes}
    return budget.BudgetConfig(
        campaign_id="test-campaign",
        campaign_config_hash=digest("frozen-plan"),
        pricing_version="conservative-2026-10-15",
        **changes,
    )


def ledger(tmp_path, **changes):
    instance = budget.BudgetLedger(tmp_path / "private" / "budget.sqlite3", config(**changes))
    instance.register_attempt("attempt-1", digest("public-config-with-repetition-1"))
    return instance


def reserve(instance, request="request-1", inputs=1000, outputs=1000, attempt="attempt-1"):
    return instance.reserve(attempt, digest(request), inputs, outputs)


def test_exact_opening_prices_and_private_storage(tmp_path):
    instance = ledger(tmp_path)
    status = instance.status()
    assert status["known_spend_usd"] == "0.482999"
    assert status["admission_headroom_usd"] == "189.517001"
    assert status["campaign"]["input_rate_per_million"] == "2.5"
    assert status["campaign"]["output_rate_per_million"] == "10"
    assert status["campaign"]["attempt_cap_usd"] == "6"
    assert status["cost_complete"] is True
    assert instance.path.stat().st_mode & 0o777 == 0o600
    assert instance.path.parent.stat().st_mode & 0o777 == 0o700


def test_exact_cost_ignores_ambient_decimal_precision_and_settlement_is_idempotent(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance)
    with localcontext() as context:
        context.prec = 3
        cost = instance.settle(request, 3, 7)
        status = instance.status()
    assert cost == Decimal("0.0000775")
    assert status["known_spend_usd"] == "0.4830765"
    assert status["settled_cost_usd"] == "0.0000775"
    assert status["outstanding_reservations_usd"] == "0"
    assert instance.settle(request, 3, 7) == cost
    with pytest.raises(budget.UsageError, match="immutable"):
        instance.settle(request, 4, 7)
    with pytest.raises(budget.UsageError, match="settled"):
        instance.uncertain(request, "LaterException")
    assert instance.status()["settled_cost_usd"] == "0.0000775"


def test_zero_reported_usage_is_distinct_from_missing_usage(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance)
    assert instance.status()["cost_complete"] is False
    assert instance.settle(request, 0, 0) == Decimal("0")
    assert instance.status()["cost_complete"] is True
    assert instance.status()["known_spend_usd"] == "0.482999"
    reserve(instance, request="next")


def test_attempt_identity_and_request_hash_cannot_reset_spend(tmp_path):
    instance = ledger(tmp_path)
    instance.register_attempt("attempt-1", digest("public-config-with-repetition-1"))
    with pytest.raises(budget.BudgetError, match="different"):
        instance.register_attempt("attempt-1", digest("changed-config"))
    with pytest.raises(budget.BudgetError, match="another identity"):
        instance.register_attempt("renamed", digest("public-config-with-repetition-1"))
    with pytest.raises(budget.BudgetError, match="Register"):
        reserve(instance, attempt="unregistered")
    request = reserve(instance)
    instance.settle(request, 10, 10)
    with pytest.raises(budget.BudgetError, match="already admitted"):
        reserve(instance)


def test_attempt_cap_counts_settled_charges_before_next_reservation(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance, inputs=400_000, outputs=400_000)
    assert instance.settle(request, 400_000, 400_000) == Decimal("5")
    with pytest.raises(budget.BudgetError, match="attempt cap"):
        reserve(instance, "over-cap", inputs=100_000, outputs=100_000)
    assert instance.status()["attempts"][0]["committed_request_cost_usd"] == "5"
    instance.register_attempt("attempt-2", digest("different-public-config"))
    reserve(instance, "other-attempt", inputs=100_000, outputs=100_000, attempt="attempt-2")


def test_campaign_admission_includes_opening_and_other_attempts(tmp_path):
    instance = ledger(
        tmp_path,
        user_ceiling_usd=Decimal("2"),
        admission_ceiling_usd=Decimal("1.5"),
        attempt_cap_usd=Decimal("1"),
    )
    request = reserve(instance, inputs=40_000, outputs=40_000)
    instance.settle(request, 40_000, 40_000)
    instance.register_attempt("attempt-2", digest("next-config"))
    with pytest.raises(budget.BudgetError, match="campaign admission"):
        reserve(instance, inputs=80_000, outputs=80_000, attempt="attempt-2")
    assert instance.status()["known_spend_usd"] == "0.982999"
    assert instance.status()["admission_headroom_usd"] == "0.517001"


def test_exact_boundary_reservation_is_admitted_but_not_one_token_more(tmp_path):
    instance = ledger(
        tmp_path,
        admission_ceiling_usd=Decimal("0.4830115"),
        user_ceiling_usd=Decimal("1"),
        attempt_cap_usd=Decimal("0.0000125"),
    )
    with pytest.raises(budget.BudgetError):
        reserve(instance, inputs=1, outputs=2)
    request = reserve(instance, inputs=1, outputs=1)
    assert instance.status()["committed_spend_usd"] == "0.4830115"
    instance.settle(request, 1, 1)
    assert instance.status()["blocked_reason"] == "admission_ceiling"


def test_concurrent_connections_admit_only_one_request_globally(tmp_path):
    instance = ledger(tmp_path)
    instance.register_attempt("attempt-2", digest("config-2"))
    barrier = threading.Barrier(2)

    def contender(number):
        other = budget.BudgetLedger(instance.path, config())
        barrier.wait(timeout=10)
        try:
            return reserve(other, request=f"request-{number}", attempt=f"attempt-{number}")
        except budget.BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, (1, 2)))
    assert sum(result is not None for result in results) == 1
    status = instance.status()
    assert status["pending_requests"] == 1
    assert len(status["requests"]) == 1
    assert status["committed_spend_usd"] == "0.495499"


def test_pending_request_survives_process_crash_and_blocks_resume(tmp_path):
    instance = ledger(tmp_path)
    script = (
        "import os,sys\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from decimal import Decimal\n"
        "from scripts.benchmarks.budget import BudgetConfig,BudgetLedger\n"
        f"c=BudgetConfig('test-campaign',{digest('frozen-plan')!r},'conservative-2026-10-15',opening_spend_usd=Decimal('0.482999'))\n"
        f"b=BudgetLedger(Path({str(instance.path)!r}),c)\n"
        f"b.reserve('attempt-1',{digest('crashed-request')!r},1000,1000)\n"
        "os._exit(73)\n"
    )
    crashed = subprocess.run([sys.executable, "-c", script], check=False, timeout=20)
    assert crashed.returncode == 73
    resumed = budget.BudgetLedger(instance.path, config())
    assert resumed.status()["pending_requests"] == 1
    assert resumed.status()["cost_complete"] is False
    with pytest.raises(budget.BudgetError, match="Pending or uncertain"):
        reserve(resumed, "after-crash")


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("campaign_id", "another-campaign"),
        ("campaign_config_hash", digest("changed-plan")),
        ("pricing_version", "new-prices"),
        ("opening_spend_usd", Decimal("0.50")),
        ("user_ceiling_usd", Decimal("201")),
        ("admission_ceiling_usd", Decimal("191")),
        ("attempt_cap_usd", Decimal("7")),
        ("input_rate_per_million", Decimal("3")),
        ("output_rate_per_million", Decimal("11")),
    ],
)
def test_resume_rejects_every_frozen_configuration_change(tmp_path, field, changed):
    instance = ledger(tmp_path)
    request = reserve(instance)
    instance.settle(request, 100, 100)
    with pytest.raises(budget.BudgetError, match="Frozen campaign"):
        budget.BudgetLedger(instance.path, replace(config(), **{field: changed}))
    assert instance.status()["settled_cost_usd"] == "0.00125"


def test_unknown_usage_preserves_reservation_and_monotonic_known_lower_bound(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance, inputs=100_000, outputs=100_000)
    instance.uncertain(request, "ReadTimeout", input_tokens=10_000)
    status = instance.status()
    assert status["known_spend_usd"] == "0.507999"
    assert status["committed_spend_usd"] == "1.732999"
    assert status["outstanding_reservations_usd"] == "1.225"
    assert status["cost_complete"] is False
    instance.uncertain(request, "ReconciledPartialBill", known_cost_usd=Decimal("2"))
    instance.uncertain(request, "StillUnknown", known_cost_usd=Decimal("0.01"))
    status = instance.status()
    assert status["known_spend_usd"] == status["committed_spend_usd"] == "2.482999"
    assert status["outstanding_reservations_usd"] == "0"
    assert status["requests"][0]["input_tokens"] == 10_000
    resumed = budget.BudgetLedger(instance.path, config())
    with pytest.raises(budget.BudgetError, match="uncertain"):
        reserve(resumed, "after-unknown")
    with pytest.raises(budget.UsageError, match="explicit review"):
        resumed.settle(request, 10_000, 0)
    with sqlite3.connect(instance.path) as connection:
        events = connection.execute(
            "SELECT data FROM events WHERE kind='request_uncertain'"
        ).fetchall()
    assert [json.loads(row[0])["known_cost_usd"] for row in events] == ["0.025", "2", "2"]


@pytest.mark.parametrize(("inputs", "outputs"), [(100, 1000), (0, 20)])
def test_overreported_usage_is_retained_before_exception(tmp_path, inputs, outputs):
    instance = ledger(tmp_path)
    request = reserve(instance, inputs=10, outputs=10)
    expected = config().cost(inputs, outputs)
    with pytest.raises(budget.UsageError, match="exceeded"):
        instance.settle(request, inputs, outputs)
    # The paid adapter's exception handler can add a reason without erasing cost.
    instance.uncertain(request, "UsageError")
    status = instance.status()
    assert Decimal(status["unsettled_known_lower_bound_usd"]) == expected
    assert status["requests"][0]["input_tokens"] == inputs
    assert status["requests"][0]["output_tokens"] == outputs
    assert status["uncertain_requests"] == 1
    assert status["admissions_blocked"] is True
    assert status["cost_complete"] is False


def test_overage_can_exceed_both_budgets_without_hiding_known_charges(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance)
    instance.uncertain(request, "UnexpectedReportedCharge", known_cost_usd=Decimal("250"))
    status = instance.status()
    assert status["known_spend_usd"] == status["committed_spend_usd"] == "250.482999"
    assert status["admission_headroom_usd"] == status["user_ceiling_headroom_usd"] == "0"
    assert status["admissions_blocked"] is True


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, float("inf"), float("nan"), None])
def test_invalid_usage_is_never_free_or_settled(tmp_path, invalid):
    instance = ledger(tmp_path)
    request = reserve(instance)
    with pytest.raises(budget.UsageError, match="Invalid usage"):
        instance.settle(request, invalid, 100)
    status = instance.status()
    assert status["uncertain_requests"] == 1
    assert status["known_spend_usd"] == "0.483999"
    assert status["outstanding_reservations_usd"] == "0.0115"
    assert status["cost_complete"] is False
    with pytest.raises(budget.BudgetError):
        reserve(instance, "next")


@pytest.mark.parametrize("invalid", [True, 0, -1, 1.5, float("inf"), float("nan"), 1_000_000_001])
def test_invalid_token_ceiling_never_creates_a_request(tmp_path, invalid):
    instance = ledger(tmp_path)
    with pytest.raises(budget.UsageError):
        reserve(instance, inputs=invalid)
    assert instance.status()["requests"] == []


@pytest.mark.parametrize(
    "invalid",
    [
        True,
        0.5,
        Decimal("0"),
        Decimal("-1"),
        Decimal("Infinity"),
        Decimal("NaN"),
        Decimal("1E100"),
        Decimal("0.0000000000000000001"),
    ],
)
def test_prices_and_budgets_require_positive_finite_bounded_decimals(invalid):
    with pytest.raises(budget.BudgetError):
        config(input_rate_per_million=invalid)


def test_invalid_budget_relationships_are_rejected():
    with pytest.raises(budget.BudgetError, match="Opening spend"):
        config(admission_ceiling_usd=Decimal("201"))
    with pytest.raises(budget.BudgetError, match="Opening spend"):
        config(opening_spend_usd=Decimal("190"))
    with pytest.raises(budget.BudgetError, match="Attempt cap"):
        config(attempt_cap_usd=Decimal("190"))


def test_reason_codes_and_database_identity_do_not_accept_raw_secrets_or_symlinks(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance)
    with pytest.raises(budget.BudgetError, match="bounded identifier"):
        instance.uncertain(request, "Bearer secret would be raw diagnostic text")
    assert instance.status()["pending_requests"] == 1
    link = tmp_path / "link.sqlite3"
    link.symlink_to(instance.path)
    with pytest.raises(budget.BudgetError, match="regular file"):
        budget.BudgetLedger(link, config())
    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    with pytest.raises(budget.BudgetError, match="refusing to reset"):
        budget.BudgetLedger(empty, config())


def test_config_tampering_blocks_existing_instance_without_reset(tmp_path):
    instance = ledger(tmp_path)
    with sqlite3.connect(instance.path) as connection:
        connection.execute("UPDATE campaign SET config_hash=?", (digest("changed"),))
    with pytest.raises(budget.BudgetError, match="Frozen campaign"):
        reserve(instance)
    with pytest.raises(budget.BudgetError, match="Frozen campaign"):
        instance.status()


def test_overreported_output_keeps_unused_reservation_when_known_cost_is_smaller(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance, inputs=1000, outputs=10)
    with pytest.raises(budget.UsageError, match="exceeded"):
        instance.settle(request, 0, 20)
    status = instance.status()
    assert status["unsettled_known_lower_bound_usd"] == "0.0002"
    assert status["outstanding_reservations_usd"] == "0.0024"
    assert status["committed_spend_usd"] == "0.485599"
    assert status["admissions_blocked"] is True


def test_sub_nanodollar_cost_remains_exact_and_unknown_lower_bounds_can_retain_it(tmp_path):
    instance = ledger(
        tmp_path,
        input_rate_per_million=Decimal("0.000000000000000001"),
        output_rate_per_million=Decimal("0.000000000000000001"),
    )
    request = reserve(instance, inputs=1, outputs=1)
    with localcontext() as context:
        context.prec = 2
        cost = instance.settle(request, 1, 1)
        status = instance.status()
    assert cost == Decimal("0.000000000000000000000002")
    assert status["known_spend_usd"] == "0.482999000000000000000002"
    pending = reserve(instance, "next-tiny", inputs=1, outputs=1)
    instance.uncertain(pending, "PartialUsage", known_cost_usd=cost)
    assert instance.status()["known_spend_usd"] == "0.482999000000000000000004"
    assert instance.status()["cost_complete"] is False


def test_public_config_property_cannot_be_replaced_after_admission(tmp_path):
    instance = ledger(tmp_path)
    with pytest.raises(AttributeError):
        instance.config = replace(config(), input_rate_per_million=Decimal("1"))
    assert instance.config.input_rate_per_million == Decimal("2.50")


def test_resume_refuses_missing_request_table_instead_of_resetting_cost(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance)
    instance.settle(request, 100, 100)
    with sqlite3.connect(instance.path) as connection:
        connection.execute("DROP TABLE requests")
    with pytest.raises(budget.BudgetError, match="schema is incomplete"):
        budget.BudgetLedger(instance.path, config())
    with sqlite3.connect(instance.path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert "requests" not in names


@pytest.mark.parametrize("stored", ["-1", "NaN", "Infinity"])
def test_invalid_stored_cost_cannot_create_budget_headroom(tmp_path, stored):
    instance = ledger(tmp_path)
    request = reserve(instance)
    instance.settle(request, 100, 100)
    with sqlite3.connect(instance.path) as connection:
        connection.execute("UPDATE requests SET known_cost_usd=?", (stored,))
    with pytest.raises(budget.BudgetError, match="Stored accounting"):
        reserve(instance, "after-corruption")
    with pytest.raises(budget.BudgetError, match="Stored accounting"):
        instance.status()


def test_fresh_campaign_can_explicitly_start_with_zero_prior_spend(tmp_path):
    fresh = budget.BudgetConfig(
        campaign_id="new-campaign",
        campaign_config_hash=digest("new-plan"),
        pricing_version="conservative-2026-10-15",
    )
    instance = budget.BudgetLedger(tmp_path / "fresh" / "budget.sqlite3", fresh)
    assert fresh.opening_spend_usd == Decimal("0")
    assert instance.status()["known_spend_usd"] == "0"
    assert instance.status()["admission_headroom_usd"] == "190"


def test_existing_only_open_never_initializes_missing_accounting(tmp_path):
    path = tmp_path / "absent" / "budget.sqlite3"
    with pytest.raises(budget.BudgetError, match="Existing accounting"):
        budget.BudgetLedger(path, config(), create=False)
    assert not path.parent.exists()


def test_existing_only_open_preserves_known_charges(tmp_path):
    instance = ledger(tmp_path)
    request = reserve(instance)
    instance.settle(request, 321, 47)
    reopened = budget.BudgetLedger(instance.path, config(), create=False)
    assert reopened.status() == instance.status()


@pytest.mark.parametrize("replacement", ["deleted", "different_regular_file", "symlink"])
def test_live_ledger_rejects_disappearance_or_replacement_without_reset(tmp_path, replacement):
    instance = ledger(tmp_path)
    original = instance.path.with_name("original.sqlite3")
    instance.path.rename(original)
    if replacement == "different_regular_file":
        instance.path.write_bytes(original.read_bytes())
    elif replacement == "symlink":
        instance.path.symlink_to(original)
    with pytest.raises(budget.BudgetError, match="disappeared|replaced"):
        reserve(instance)
    if replacement == "deleted":
        assert not instance.path.exists()
    assert budget.BudgetLedger(original, config(), create=False).status()["requests"] == []
