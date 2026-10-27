"""Narrow, explicitly reviewed continuation with the original reserve still charged.

The caller validates the frozen recovery manifest and provider rejection evidence.
Only its exact retained request snapshot can receive a bounded review. Every new
attempt must appear in the immutable allowlist; no failed request is repeated.
"""

from __future__ import annotations

import copy
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.benchmarks.budget import (
    BudgetConfig,
    BudgetError,
    BudgetLedger,
    RecoveryContext,
    _hash,
    _label,
    _now,
    _text,
    _tokens,
)
from scripts.judge_study.concurrent_budget import ConcurrentLedger

SNAPSHOT_FIELDS = {
    "id",
    "attempt_id",
    "request_hash",
    "state",
    "input_ceiling",
    "output_ceiling",
    "reserved_usd",
    "known_cost_usd",
    "input_tokens",
    "output_tokens",
    "reason",
    "created_at",
    "updated_at",
}


class ReviewedConcurrentLedger(ConcurrentLedger):
    """Keep one exact reviewed uncertainty while admitting only unrelated jobs."""

    def __init__(
        self,
        path: Path,
        config: BudgetConfig,
        *,
        create: bool = False,
        recovery_context: RecoveryContext | None = None,
        approved_request: dict[str, Any] | None = None,
    ) -> None:
        self._approved_request = copy.deepcopy(approved_request)
        if approved_request is not None:
            if recovery_context is None or set(approved_request) != SNAPSHOT_FIELDS:
                raise BudgetError("An exact request snapshot and recovery context are required")
            if (
                approved_request["state"] != "uncertain"
                or approved_request["reason"] != "ValueError"
                or approved_request["input_tokens"] is not None
                or approved_request["output_tokens"] is not None
                or Decimal(approved_request["known_cost_usd"]) != 0
            ):
                raise BudgetError("Only the exact reviewed no-usage ValueError is eligible")
            _label(approved_request["id"], "request_id")
            _label(approved_request["attempt_id"], "attempt_id")
            _hash(approved_request["request_hash"], "request_hash")
            if approved_request["attempt_id"] in dict(recovery_context.allowed_attempts):
                raise BudgetError("The reviewed attempt must be excluded from the allowlist")
        super().__init__(path, config, create=create, recovery_context=recovery_context)

    def _check_review_bound(self, row: sqlite3.Row) -> None:
        if (
            self._recovery_context is None
            or self._approved_request is None
            or self._review_snapshot(row) != self._approved_request
        ):
            raise BudgetError("Only the exact frozen rejected request may receive this review")
        # Reuse every original token, monetary and pricing check. The narrowly
        # approved reason substitution exists only in memory, never in the ledger.
        original_reason_view = dict(row)
        original_reason_view["reason"] = "UnknownUsage"
        BudgetLedger._check_review_bound(self, original_reason_view)  # type: ignore[arg-type]

    def reserve(
        self,
        attempt_id: str,
        request_hash: str,
        input_token_ceiling: int,
        output_token_ceiling: int,
    ) -> str:
        _label(attempt_id, "attempt_id")
        _hash(request_hash, "request_hash")
        _tokens(input_token_ceiling, "input_token_ceiling", ceiling=True)
        _tokens(output_token_ceiling, "output_token_ceiling", ceiling=True)
        reservation = self.config.cost(input_token_ceiling, output_token_ceiling)
        with self._lease_lock, self._connection(write=True) as connection:
            registered = connection.execute(
                "SELECT attempt_hash FROM attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if registered is None:
                raise BudgetError("Register the attempt's public configuration hash first")
            rows = list(connection.execute("SELECT * FROM requests"))
            reviewed = self._reviewed_requests(connection, rows)
            if any(row["attempt_id"] == attempt_id and row["id"] in reviewed for row in rows):
                raise BudgetError("The reviewed incomplete attempt is permanently quarantined")
            if self._recovery_context is not None and (
                dict(self._recovery_context.allowed_attempts).get(attempt_id)
                != registered["attempt_hash"]
            ):
                raise BudgetError("Attempt is outside the explicit recovery allowlist")
            if any(row["attempt_id"] == attempt_id for row in rows):
                raise BudgetError("A study job can have only one admitted request")
            pending = {row["id"] for row in rows if row["state"] == "pending"}
            if any(row["state"] == "uncertain" and row["id"] not in reviewed for row in rows):
                raise BudgetError("Unreviewed uncertain usage blocks all further admissions")
            if pending - self._owned:
                raise BudgetError("Unowned pending usage blocks restart admissions")
            if len(pending) >= 4:
                raise BudgetError("Four requests are already in flight for this model")
            totals = self._totals(rows)
            committed = self.config.opening_spend_usd + Decimal(
                totals["committed_request_cost_usd"]
            )
            if committed + reservation > self.config.admission_ceiling_usd:
                raise BudgetError("Request would exceed the campaign admission ceiling")
            if reservation > self.config.attempt_cap_usd:
                raise BudgetError("Request would exceed the attempt cap")
            identifier = uuid.uuid4().hex
            now = _now()
            connection.execute(
                "INSERT INTO requests(id,attempt_id,request_hash,state,input_ceiling,output_ceiling,"
                "reserved_usd,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?,?,?)",
                (
                    identifier,
                    attempt_id,
                    request_hash,
                    input_token_ceiling,
                    output_token_ceiling,
                    _text(reservation),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                "request_reserved",
                {
                    "request_hash": request_hash,
                    "reserved_usd": _text(reservation),
                    "input_token_ceiling": input_token_ceiling,
                    "output_token_ceiling": output_token_ceiling,
                    "operational_concurrency": 4,
                },
                request_id=identifier,
                attempt_id=attempt_id,
            )
            self._owned.add(identifier)
            return identifier
