"""One additional, exactly bound connection uncertainty under the original scope.

The continuation manifest narrows dispatch to untouched jobs and validates the
retained transport evidence. Neither review edits a request or releases money.
"""

from __future__ import annotations

import copy
import sqlite3
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
)
from scripts.judge_study.recovery_budget import SNAPSHOT_FIELDS, ReviewedConcurrentLedger


class ConnectionReviewedLedger(ReviewedConcurrentLedger):
    """Preserve the first review and allow exactly one separately reviewed failure."""

    def __init__(
        self,
        path: Path,
        config: BudgetConfig,
        *,
        create: bool = False,
        recovery_context: RecoveryContext | None = None,
        approved_request: dict[str, Any] | None = None,
        connection_request: dict[str, Any] | None = None,
    ) -> None:
        self._connection_request = copy.deepcopy(connection_request)
        if connection_request is not None:
            if (
                recovery_context is None
                or approved_request is None
                or set(connection_request) != SNAPSHOT_FIELDS
            ):
                raise BudgetError("The original review and exact connection snapshot are required")
            if (
                connection_request["state"] != "uncertain"
                or connection_request["reason"] != "ConnectError"
                or connection_request["input_tokens"] is not None
                or connection_request["output_tokens"] is not None
                or Decimal(connection_request["known_cost_usd"]) != 0
            ):
                raise BudgetError("Only the exact reviewed no-usage ConnectError is eligible")
            _label(connection_request["id"], "request_id")
            _label(connection_request["attempt_id"], "attempt_id")
            _hash(connection_request["request_hash"], "request_hash")
            if (
                connection_request["id"] == approved_request["id"]
                or connection_request["attempt_id"] == approved_request["attempt_id"]
                or connection_request["attempt_id"] not in dict(recovery_context.allowed_attempts)
            ):
                raise BudgetError("The connection request must belong to the original scope")
        super().__init__(
            path,
            config,
            create=create,
            recovery_context=recovery_context,
            approved_request=approved_request,
        )

    def _check_review_bound(self, row: sqlite3.Row) -> None:
        if (
            self._connection_request is not None
            and self._review_snapshot(row) == self._connection_request
        ):
            # All original token/pricing/monetary checks still apply. The reason
            # substitution is an in-memory view, never a ledger modification.
            original_reason_view = dict(row)
            original_reason_view["reason"] = "UnknownUsage"
            BudgetLedger._check_review_bound(self, original_reason_view)  # type: ignore[arg-type]
            return
        super()._check_review_bound(row)
