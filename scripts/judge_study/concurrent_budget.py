"""Process-owned concurrent reservations over the unchanged durable study ledger.

Pending requests can coexist only when this process owns every pending lease.
A restarted process has no leases and must stop for explicit accounting review.
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from typing import Any

from scripts.benchmarks.budget import BudgetError, BudgetLedger, _hash, _label, _now, _text, _tokens


class ConcurrentLedger(BudgetLedger):
    """Admit at most four owned requests; SQLite serializes monetary admission."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._owned: set[str] = set()
        self._lease_lock = threading.Lock()

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
            if (
                connection.execute("SELECT id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
                is None
            ):
                raise BudgetError("Register the attempt's public configuration hash first")
            if connection.execute(
                "SELECT id FROM requests WHERE attempt_id=?", (attempt_id,)
            ).fetchone():
                raise BudgetError("A study job can have only one admitted request")
            rows = list(connection.execute("SELECT * FROM requests"))
            pending = {row["id"] for row in rows if row["state"] == "pending"}
            if any(row["state"] == "uncertain" for row in rows):
                raise BudgetError("Uncertain usage blocks all further admissions")
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
