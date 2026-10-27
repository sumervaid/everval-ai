"""Exact reviewed exceptions and a narrower dispatch scope without released charges.

A separately validated immutable manifest binds all snapshots and their review
and provider evidence. The original review context remains unchanged; this layer
only narrows which untouched request bodies may be admitted.
"""

from __future__ import annotations

import copy
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
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


class FlatReviewedLedger(ReviewedConcurrentLedger):
    """Retain exactly the declared three uncertainties and reject every new one."""

    def __init__(
        self,
        path: Path,
        config: BudgetConfig,
        *,
        create: bool = False,
        recovery_context: RecoveryContext,
        approved_requests: list[dict[str, Any]],
        dispatch_allowlist: dict[str, str],
    ) -> None:
        approved = copy.deepcopy(approved_requests)
        if len(approved) not in {0, 3}:
            raise BudgetError("Only the fixed three reviewed exceptions, or none, are permitted")
        original_scope = dict(recovery_context.allowed_attempts)
        by_id: dict[str, dict[str, Any]] = {}
        for row in approved:
            if set(row) != SNAPSHOT_FIELDS or (
                row["state"] != "uncertain"
                or row["reason"] not in {"ValueError", "ConnectError"}
                or row["input_tokens"] is not None
                or row["output_tokens"] is not None
                or Decimal(row["known_cost_usd"]) != 0
            ):
                raise BudgetError("An exact no-usage reviewed exception snapshot is required")
            _label(row["id"], "request_id")
            _label(row["attempt_id"], "attempt_id")
            _hash(row["request_hash"], "request_hash")
            if row["id"] in by_id:
                raise BudgetError("Duplicate reviewed exception identity")
            by_id[row["id"]] = row
        attempts = {row["attempt_id"] for row in approved}
        if len(attempts) != len(approved):
            raise BudgetError("Reviewed exception attempts must be distinct")
        prior = [row for row in approved if row["attempt_id"] not in original_scope]
        if approved and (
            Counter(row["reason"] for row in approved) != {"ValueError": 2, "ConnectError": 1}
            or len(prior) != 1
            or prior[0]["reason"] != "ValueError"
        ):
            raise BudgetError(
                "The fixed policy/connection/policy exceptions must retain their scope"
            )
        allowed = dict(dispatch_allowlist)
        for attempt, request_hash in allowed.items():
            _label(attempt, "attempt_id")
            _hash(request_hash, "request_hash")
            if attempt not in original_scope or attempt in attempts:
                raise BudgetError("Dispatch may only narrow untouched original-scope attempts")
        self._exact_reviews = MappingProxyType(by_id)
        self._dispatch_allowlist = MappingProxyType(allowed)
        super().__init__(
            path,
            config,
            create=create,
            recovery_context=recovery_context,
            approved_request=prior[0] if prior else None,
        )

    def _check_review_bound(self, row: sqlite3.Row) -> None:
        if self._exact_reviews.get(row["id"]) != self._review_snapshot(row):
            raise BudgetError("Only an exact frozen exception may receive this review")
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
        if self._dispatch_allowlist.get(attempt_id) != request_hash:
            raise BudgetError("Request is outside the immutable narrowed dispatch allowlist")
        return super().reserve(attempt_id, request_hash, input_token_ceiling, output_token_ceiling)
