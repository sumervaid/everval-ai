"""Transactional workspace and repository spending limits across experiments."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.models import canonical_json, utc_now


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS spend_limits(
        scope TEXT PRIMARY KEY, data TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS spend_ledger(
        trial_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, scope TEXT NOT NULL,
        started_at TEXT NOT NULL, committed REAL NOT NULL, uncertain INTEGER NOT NULL
    )""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS spend_ledger_period ON spend_ledger(started_at,scope)"
    )


def project_scope(connection: sqlite3.Connection, collection: dict[str, Any]) -> str:
    release_id = collection.get("benchmark_release_id")
    if (
        release_id
        and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='benchmark_releases'"
        ).fetchone()
    ):
        row = connection.execute(
            "SELECT data FROM benchmark_releases WHERE id=?", (release_id,)
        ).fetchone()
        if row:
            project = json.loads(row[0]).get("project_id")
            if project:
                return "project:" + str(project)
    return "workspace"


def _usage(connection: sqlite3.Connection, scope: str, now: str) -> dict[str, float]:
    suffix = "" if scope == "workspace" else " AND scope=?"
    values: tuple[Any, ...] = () if scope == "workspace" else (scope,)
    return {
        period: float(
            connection.execute(
                "SELECT coalesce(sum(committed),0) FROM spend_ledger WHERE started_at>=?" + suffix,
                (start, *values),
            ).fetchone()[0]
        )
        for period, start in (
            ("daily_usd", now[:10]),
            ("monthly_usd", now[:7]),
            ("lifetime_usd", ""),
        )
    }


def admit(
    connection: sqlite3.Connection, experiment_id: str, trial_id: str, scope: str, amount: float
) -> str | None:
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("Spending reservation must be finite and nonnegative")
    now = utc_now()
    for governed in dict.fromkeys(("workspace", scope)):
        row = connection.execute(
            "SELECT data FROM spend_limits WHERE scope=?", (governed,)
        ).fetchone()
        if row is None:
            continue
        limits = json.loads(row[0])
        used = _usage(connection, governed, now)
        for period in ("daily_usd", "monthly_usd", "lifetime_usd"):
            if limits.get(period) is not None and used[period] + amount > limits[period] + 1e-12:
                return f"{governed} {period} would exceed its cumulative spending limit"
    connection.execute(
        "INSERT INTO spend_ledger VALUES(?,?,?,?,?,?)",
        (trial_id, experiment_id, scope, now, amount, 1),
    )
    return None


def settle(connection: sqlite3.Connection, trial: dict[str, Any]) -> None:
    row = connection.execute(
        "SELECT committed FROM spend_ledger WHERE trial_id=?", (trial["id"],)
    ).fetchone()
    if row is None:
        return
    cost = trial.get("cost_usd")
    metadata = trial.get("provider_metadata", {})
    uncertain = cost is None or bool(metadata.get("budget_uncertain"))
    partial = metadata.get("known_cost_usd")
    known = cost if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0 else 0
    if (
        isinstance(partial, (int, float))
        and not isinstance(partial, bool)
        and math.isfinite(partial)
        and partial >= 0
    ):
        known = max(known, partial)
    committed = max(row[0], known) if uncertain else known
    connection.execute(
        "UPDATE spend_ledger SET committed=?, uncertain=? WHERE trial_id=?",
        (committed, int(uncertain), trial["id"]),
    )


class SpendLimits:
    def __init__(self, settings: Settings):
        from cvals.store import Store

        self.store = Store(settings)
        self.store.init()

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                {
                    **json.loads(row["data"]),
                    "usage": _usage(connection, row["scope"], utc_now()),
                }
                for row in connection.execute("SELECT * FROM spend_limits ORDER BY scope")
            ]

    def configure(
        self,
        scope: str = "workspace",
        *,
        daily_usd: float | None = None,
        monthly_usd: float | None = None,
        lifetime_usd: float | None = None,
    ) -> dict[str, Any]:
        if scope != "workspace":
            if not scope.startswith("project:"):
                raise ValueError("Scope must be workspace or project:PROJECT_ID")
            from cvals.projects import Projects

            Projects(self.store.settings).get(scope.removeprefix("project:"))
        limits = {
            "scope": scope,
            "daily_usd": daily_usd,
            "monthly_usd": monthly_usd,
            "lifetime_usd": lifetime_usd,
            "updated_at": utc_now(),
        }
        for field in ("daily_usd", "monthly_usd", "lifetime_usd"):
            value = limits[field]
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("Spending limits must be finite nonnegative dollar amounts")
        with self.store._connect(write=True) as connection:
            # Existing paid history counts when a limit is introduced after earlier experiments.
            for row in connection.execute(
                "SELECT e.snapshot,t.data FROM trials t JOIN experiments e ON e.id=t.experiment_id"
            ):
                snapshot, trial = json.loads(row[0]), json.loads(row[1])
                if not trial.get("started_at"):
                    continue
                system = next(
                    (s for s in snapshot["systems"] if s["id"] == trial["system_id"]), None
                )
                if not system or system["kind"] != "http":
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO spend_ledger VALUES(?,?,?,?,?,?)",
                    (
                        trial["id"],
                        trial["experiment_id"],
                        snapshot.get("budget_scope")
                        or project_scope(connection, snapshot["collection"]),
                        trial["started_at"],
                        trial.get("reserved_cost_usd") or 0,
                        1,
                    ),
                )
                settle(connection, trial)
            connection.execute(
                "INSERT INTO spend_limits VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET data=excluded.data",
                (scope, canonical_json(limits)),
            )
            append_audit(connection, "spending.limit_set", scope, limits)
        return limits
