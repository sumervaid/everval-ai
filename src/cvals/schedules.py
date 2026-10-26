"""Durable, bounded recurring evaluations of frozen experiment snapshots.

A due occurrence, its experiment, and the next due time commit atomically. A
crash rolls back all three or leaves a recoverable queued experiment. Missed
intervals coalesce into one run and each schedule allows one outstanding run.
"""

from __future__ import annotations

import builtins
import json
import math
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from cvals.models import (
    ExperimentCreate,
    Manifest,
    System,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
)
from cvals.providers import validate_provider

if TYPE_CHECKING:
    from cvals.config import Settings
    from cvals.store import Store


class _ScheduleInput(Manifest):
    name: str = Field(min_length=1, max_length=200)
    interval_minutes: int = Field(ge=5, le=525600, strict=True)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Schedule name must not be blank")
        return value


class ScheduleStore:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.store.init()
        self.init()

    def init(self) -> None:
        with self.store._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS evaluation_schedules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    interval_minutes INTEGER NOT NULL CHECK(interval_minutes >= 5),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    next_run_epoch REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_experiment_id TEXT REFERENCES experiments(id),
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS evaluation_schedules_due
                    ON evaluation_schedules(enabled, next_run_epoch);
                CREATE TABLE IF NOT EXISTS schedule_occurrences (
                    id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES evaluation_schedules(id),
                    scheduled_for REAL NOT NULL,
                    experiment_id TEXT REFERENCES experiments(id),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(schedule_id, scheduled_for)
                );
                CREATE INDEX IF NOT EXISTS schedule_occurrences_pending
                    ON schedule_occurrences(schedule_id, experiment_id);
                CREATE TABLE IF NOT EXISTS schedule_budget_reservations (
                    schedule_id TEXT NOT NULL REFERENCES evaluation_schedules(id),
                    experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    reserved_usd REAL NOT NULL, charged_usd REAL NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY(schedule_id,experiment_id)
                );
                CREATE TABLE IF NOT EXISTS schedule_digests (
                    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL REFERENCES evaluation_schedules(id),
                    data TEXT NOT NULL, hash TEXT NOT NULL
                );
            """)
        with self.store._connect(write=True) as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(evaluation_schedules)")
            }
            if "lifetime_budget_usd" not in columns:
                connection.execute(
                    "ALTER TABLE evaluation_schedules ADD COLUMN lifetime_budget_usd REAL"
                )

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        value["experiment_id"] = value["source_experiment_id"]
        value["next_run_at"] = datetime.fromtimestamp(value.pop("next_run_epoch"), UTC).isoformat()
        value["snapshot_policy"] = "frozen source; missed intervals coalesce"
        return value

    def _admit(self, snapshot: dict[str, Any], state: dict[str, Any]) -> None:
        if state.get("budget_uncertain"):
            raise ValueError(
                "Uncertain provider billing must be resolved before recurring evaluation"
            )
        if any(system["kind"] == "replay" for system in snapshot["systems"]):
            raise ValueError(
                "Recorded submissions cannot produce new evidence on a recurring schedule"
            )
        for value in snapshot["systems"]:
            validate_provider(System.model_validate(value), self.settings, snapshot["budget_usd"])

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._public(row)
                for row in connection.execute(
                    "SELECT * FROM evaluation_schedules ORDER BY created_at DESC"
                )
            ]

    def create(
        self,
        name: str,
        experiment_id: str,
        interval_minutes: int,
        enabled: bool = True,
        lifetime_budget_usd: float | None = None,
        *,
        include_source_in_budget: bool = False,
    ) -> dict[str, Any]:
        with self.store._connect(write=True) as connection:
            return self._create(
                connection,
                name,
                experiment_id,
                interval_minutes,
                enabled,
                lifetime_budget_usd,
                include_source_in_budget=include_source_in_budget,
            )

    def _create(
        self,
        connection: sqlite3.Connection,
        name: str,
        experiment_id: str,
        interval_minutes: int,
        enabled: bool = True,
        lifetime_budget_usd: float | None = None,
        *,
        include_source_in_budget: bool = False,
    ) -> dict[str, Any]:
        """Create within the caller's transaction, including its initial run when requested."""
        if type(include_source_in_budget) is not bool:
            raise ValueError("include_source_in_budget must be a boolean")
        validated = _ScheduleInput(name=name, interval_minutes=interval_minutes, enabled=enabled)
        snapshot, state = self.store._read_experiment(connection, experiment_id)
        cap = state["budget_usd"] if lifetime_budget_usd is None else lifetime_budget_usd
        if (
            isinstance(cap, bool)
            or not isinstance(cap, (int, float))
            or not math.isfinite(cap)
            or cap < 0
        ):
            raise ValueError("Lifetime budget must be a finite nonnegative USD amount")
        if cap < state["budget_usd"]:
            raise ValueError("Lifetime budget must cover at least one full per-run budget")
        if any(system["kind"] == "replay" for system in snapshot["systems"]):
            raise ValueError(
                "Recorded submissions cannot produce new evidence on a recurring schedule"
            )
        if validated.enabled:
            self._admit(snapshot, state)
        if connection.execute("SELECT COUNT(*) FROM evaluation_schedules").fetchone()[0] >= 100:
            raise ValueError("The workspace supports at most 100 schedules")
        id = new_id()
        connection.execute(
            "INSERT INTO evaluation_schedules VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                id,
                validated.name,
                experiment_id,
                validated.interval_minutes,
                int(validated.enabled),
                time.time() + validated.interval_minutes * 60,
                utc_now(),
                None,
                None,
                None,
                cap,
            ),
        )
        if include_source_in_budget:
            connection.execute(
                "INSERT INTO schedule_budget_reservations VALUES(?,?,?,?,?)",
                (id, experiment_id, state["budget_usd"], state["budget_usd"], "reserved"),
            )
        self.store._event(
            connection,
            experiment_id,
            "schedule.created",
            {
                "schedule_id": id,
                "name": validated.name,
                "interval_minutes": validated.interval_minutes,
                "enabled": validated.enabled,
                "source_snapshot_hash": state["snapshot_hash"],
                "lifetime_budget_usd": cap,
            },
        )
        row = connection.execute("SELECT * FROM evaluation_schedules WHERE id=?", (id,)).fetchone()
        return self._public(row)

    def set_enabled(self, id: str, enabled: bool) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self.store._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (id,)
            ).fetchone()
            if row is None:
                raise KeyError(id)
            if bool(row["enabled"]) == enabled and row["last_error"] is None:
                return self._public(row)
            if enabled:
                snapshot, state = self.store._read_experiment(
                    connection, row["source_experiment_id"]
                )
                self._admit(snapshot, state)
                charged = self._budget_usage(connection, id)
                if (
                    row["lifetime_budget_usd"] is None
                    or charged + state["budget_usd"] > row["lifetime_budget_usd"] + 1e-9
                ):
                    raise ValueError(
                        "Set a lifetime budget sufficient to reserve the next full run before enabling"
                    )
            # Resuming starts a new interval instead of immediately draining missed work.
            connection.execute(
                "UPDATE evaluation_schedules SET enabled=?,next_run_epoch=?,last_error=NULL WHERE id=?",
                (int(enabled), time.time() + row["interval_minutes"] * 60, id),
            )
            self.store._event(
                connection,
                row["source_experiment_id"],
                "schedule.enabled" if enabled else "schedule.disabled",
                {"schedule_id": id},
            )
            updated = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (id,)
            ).fetchone()
        return self._public(updated)

    def tick(self) -> builtins.list[str]:
        now = time.time()
        with self.store._connect() as connection:
            due = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM evaluation_schedules WHERE enabled=1 AND next_run_epoch<=? ORDER BY next_run_epoch LIMIT 100",
                    (now,),
                )
            ]
        created = []
        for schedule_id in due:
            with self.store._connect(write=True) as connection:
                schedule = connection.execute(
                    "SELECT * FROM evaluation_schedules WHERE id=? AND enabled=1 AND next_run_epoch<=?",
                    (schedule_id, now),
                ).fetchone()
                if schedule is None:
                    continue
                outstanding = connection.execute(
                    """SELECT 1 FROM experiments e WHERE e.status IN ('queued','running')
                       AND (e.id=? OR e.id IN
                         (SELECT experiment_id FROM schedule_occurrences WHERE schedule_id=?))
                       LIMIT 1""",
                    (schedule["source_experiment_id"], schedule_id),
                ).fetchone()
                if outstanding:
                    # Retry admission in a minute; never enqueue behind an outstanding run.
                    connection.execute(
                        "UPDATE evaluation_schedules SET next_run_epoch=? WHERE id=?",
                        (now + 60, schedule_id),
                    )
                    continue
                due_at = schedule["next_run_epoch"]
                occurrence_id = f"{schedule_id}:{due_at:.6f}"
                if connection.execute(
                    "SELECT 1 FROM schedule_occurrences WHERE id=?", (occurrence_id,)
                ).fetchone():
                    connection.execute(
                        "UPDATE evaluation_schedules SET next_run_epoch=? WHERE id=?",
                        (now + schedule["interval_minutes"] * 60, schedule_id),
                    )
                    continue
                connection.execute("SAVEPOINT scheduled_run")
                try:
                    snapshot, state = self.store._read_experiment(
                        connection, schedule["source_experiment_id"]
                    )
                    self._admit(snapshot, state)
                    if schedule["last_experiment_id"]:
                        _, last = self.store._read_experiment(
                            connection, schedule["last_experiment_id"]
                        )
                        if last.get("budget_uncertain"):
                            raise ValueError(
                                "Previous scheduled run has uncertain billing; schedule disabled"
                            )
                    charged = self._budget_usage(connection, schedule_id)
                    cap = schedule["lifetime_budget_usd"]
                    if cap is None:
                        raise ValueError(
                            "Legacy schedule requires an explicit lifetime budget before another admission"
                        )
                    if charged + state["budget_usd"] > cap + 1e-9:
                        raise ValueError(
                            "Lifetime schedule budget cannot reserve another full run; increase the cap before enabling"
                        )
                    request = ExperimentCreate(
                        name=schedule["name"][:180] + " · scheduled",
                        collection_id=state["collection_id"],
                        system_ids=state["system_ids"],
                        repetitions=state["repetitions"],
                        budget_usd=state["budget_usd"],
                    )
                    experiment_id = self.store._insert_experiment(
                        connection,
                        request,
                        snapshot["collection"],
                        snapshot["systems"],
                        schedule["source_experiment_id"],
                    )
                    connection.execute(
                        "INSERT INTO schedule_budget_reservations VALUES(?,?,?,?,?)",
                        (
                            schedule_id,
                            experiment_id,
                            state["budget_usd"],
                            state["budget_usd"],
                            "reserved",
                        ),
                    )
                except (ValueError, KeyError) as exc:
                    connection.execute("ROLLBACK TO scheduled_run")
                    connection.execute("RELEASE scheduled_run")
                    connection.execute(
                        "INSERT INTO schedule_occurrences VALUES(?,?,?,?,?,?)",
                        (occurrence_id, schedule_id, due_at, None, "admission_failed", utc_now()),
                    )
                    connection.execute(
                        "UPDATE evaluation_schedules SET enabled=0,last_error=?,next_run_epoch=? WHERE id=?",
                        (str(exc)[:2000], now + schedule["interval_minutes"] * 60, schedule_id),
                    )
                    self.store._event(
                        connection,
                        schedule["source_experiment_id"],
                        "schedule.admission_failed",
                        {"schedule_id": schedule_id, "error": str(exc)[:2000]},
                    )
                    continue
                connection.execute("RELEASE scheduled_run")
                connection.execute(
                    "INSERT INTO schedule_occurrences VALUES(?,?,?,?,?,?)",
                    (occurrence_id, schedule_id, due_at, experiment_id, "queued", utc_now()),
                )
                connection.execute(
                    """UPDATE evaluation_schedules
                       SET next_run_epoch=?,last_run_at=?,last_experiment_id=?,last_error=NULL WHERE id=?""",
                    (
                        now + schedule["interval_minutes"] * 60,
                        utc_now(),
                        experiment_id,
                        schedule_id,
                    ),
                )
                self.store._event(
                    connection,
                    experiment_id,
                    "schedule.occurrence",
                    {
                        "schedule_id": schedule_id,
                        "occurrence_id": occurrence_id,
                        "scheduled_for": datetime.fromtimestamp(due_at, UTC).isoformat(),
                    },
                )
                created.append(experiment_id)
        return created

    def _budget_usage(self, connection: sqlite3.Connection, schedule_id: str) -> float:
        total = 0.0
        rows = connection.execute(
            "SELECT r.*,e.state FROM schedule_budget_reservations r "
            "JOIN experiments e ON e.id=r.experiment_id WHERE r.schedule_id=?",
            (schedule_id,),
        ).fetchall()
        for row in rows:
            state = json.loads(row["state"])
            finalized = state["status"] not in {"queued", "running"}
            uncertain = bool(state.get("budget_uncertain"))
            committed = float(state.get("budget_committed_usd", 0))
            charged = (
                max(row["reserved_usd"], committed) if not finalized or uncertain else committed
            )
            status = "uncertain" if uncertain else "settled" if finalized else "reserved"
            connection.execute(
                "UPDATE schedule_budget_reservations SET charged_usd=?,status=? "
                "WHERE schedule_id=? AND experiment_id=?",
                (charged, status, schedule_id, row["experiment_id"]),
            )
            total += charged
        # Migrations must account for occurrences admitted before reservations existed.
        for row in connection.execute(
            "SELECT e.id,e.state FROM experiments e JOIN schedule_occurrences o ON e.id=o.experiment_id "
            "WHERE o.schedule_id=? AND NOT EXISTS (SELECT 1 FROM schedule_budget_reservations r "
            "WHERE r.schedule_id=? AND r.experiment_id=e.id)",
            (schedule_id, schedule_id),
        ).fetchall():
            state = json.loads(row["state"])
            uncertain = bool(state.get("budget_uncertain"))
            finalized = state["status"] not in {"queued", "running"}
            reserved = float(state["budget_usd"])
            committed = float(state.get("budget_committed_usd", 0))
            charged = max(reserved, committed) if uncertain or not finalized else committed
            connection.execute(
                "INSERT INTO schedule_budget_reservations VALUES(?,?,?,?,?)",
                (
                    schedule_id,
                    row["id"],
                    reserved,
                    charged,
                    "uncertain" if uncertain else "settled" if finalized else "reserved",
                ),
            )
            total += charged
        return total

    def budget(self, identifier: str) -> dict[str, Any]:
        with self.store._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            charged = self._budget_usage(connection, identifier)
            cap = row["lifetime_budget_usd"]
            reservations = [
                dict(value)
                for value in connection.execute(
                    "SELECT experiment_id,reserved_usd,charged_usd,status FROM schedule_budget_reservations WHERE schedule_id=?",
                    (identifier,),
                )
            ]
        return {
            "schedule_id": identifier,
            "lifetime_budget_usd": cap,
            "committed_usd": charged,
            "remaining_usd": max(0.0, cap - charged) if cap is not None else None,
            "reservations": reservations,
            "scope": (
                "Includes the initial source run and all occurrences. "
                if any(
                    value["experiment_id"] == row["source_experiment_id"] for value in reservations
                )
                else "Occurrences only; original source run is outside this cap. "
            )
            + "Uncertain billing retains the full reservation.",
        }

    def set_budget(self, identifier: str, lifetime_budget_usd: float) -> dict[str, Any]:
        if (
            isinstance(lifetime_budget_usd, bool)
            or not isinstance(lifetime_budget_usd, (int, float))
            or not math.isfinite(lifetime_budget_usd)
            or lifetime_budget_usd < 0
        ):
            raise ValueError("Lifetime budget must be finite and nonnegative")
        with self.store._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            charged = self._budget_usage(connection, identifier)
            if lifetime_budget_usd + 1e-9 < charged:
                raise ValueError("Lifetime budget cannot be less than charged and reserved spend")
            connection.execute(
                "UPDATE evaluation_schedules SET lifetime_budget_usd=? WHERE id=?",
                (lifetime_budget_usd, identifier),
            )
            self.store._event(
                connection,
                row["source_experiment_id"],
                "schedule.budget_changed",
                {
                    "schedule_id": identifier,
                    "previous_cap_usd": row["lifetime_budget_usd"],
                    "lifetime_budget_usd": lifetime_budget_usd,
                    "committed_usd": charged,
                },
            )
        return self.budget(identifier)

    def digests(self, identifier: str) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            result = []
            for row in connection.execute(
                "SELECT data,hash FROM schedule_digests WHERE schedule_id=? ORDER BY rowid DESC",
                (identifier,),
            ):
                value = json.loads(row["data"])
                if content_hash(value) != row["hash"]:
                    raise ValueError("Schedule digest integrity check failed")
                result.append(value)
            return result

    def digest(self, identifier: str, days: int = 7) -> dict[str, Any]:
        """Freeze the current window, with task transitions and accounting coverage."""
        if type(days) is not int or not 1 <= days <= 366:
            raise ValueError("Digest window must be 1–366 days")
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=days)
        with self.store._connect(write=True) as connection:
            schedule = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (identifier,)
            ).fetchone()
            if schedule is None:
                raise KeyError(identifier)
            occurrences = connection.execute(
                "SELECT experiment_id,created_at FROM schedule_occurrences "
                "WHERE schedule_id=? AND experiment_id IS NOT NULL ORDER BY scheduled_for DESC LIMIT 502",
                (identifier,),
            ).fetchall()
            occurrences = list(reversed(occurrences))
            selected = [
                row
                for row in occurrences
                if datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) >= cutoff
            ]
            if len(selected) > 500:
                raise ValueError("Digest window contains more than 500 runs; choose fewer days")
            before = [
                row
                for row in occurrences
                if datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) < cutoff
            ]
            baseline_id = (
                before[-1]["experiment_id"] if before else schedule["source_experiment_id"]
            )

            def read(experiment_id: str) -> dict[str, Any]:
                snapshot, state = self.store._read_experiment(connection, experiment_id)
                trials = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT data FROM trials WHERE experiment_id=? ORDER BY position",
                        (experiment_id,),
                    )
                ]
                return {**state, "trials": trials, "snapshot": snapshot}

            baseline = read(baseline_id)
            runs = [read(row["experiment_id"]) for row in selected]

            def outcomes(run: dict[str, Any]) -> dict[str, str]:
                grouped: dict[str, builtins.list[dict[str, Any]]] = {}
                for trial in run["trials"]:
                    grouped.setdefault(trial["system_id"] + "/" + trial["scenario_id"], []).append(
                        trial
                    )
                return {
                    key: "incomplete"
                    if any(trial["status"] != "completed" for trial in trials)
                    else "passed"
                    if all(trial.get("passed") is True for trial in trials)
                    else "failed"
                    for key, trials in grouped.items()
                }

            previous = outcomes(baseline)
            transitions = []
            costs = []
            for run in runs:
                current = outcomes(run)
                comparable = run["collection_hash"] == baseline["collection_hash"]
                newly_failing = sorted(
                    key
                    for key in current
                    if comparable and previous.get(key) == "passed" and current[key] == "failed"
                )
                newly_passing = sorted(
                    key
                    for key in current
                    if comparable and previous.get(key) == "failed" and current[key] == "passed"
                )
                transitions.append(
                    {
                        "experiment_id": run["id"],
                        "status": run["status"],
                        "snapshot_hash": run["snapshot_hash"],
                        "newly_failing": newly_failing,
                        "newly_passing": newly_passing,
                        "incomplete": sorted(
                            key for key, value in current.items() if value == "incomplete"
                        ),
                        "evidence_command": "everval experiments show " + run["id"],
                    }
                )
                known = sum(
                    float(trial["cost_usd"])
                    if trial.get("cost_usd") is not None
                    else float((trial.get("provider_metadata") or {}).get("known_cost_usd", 0) or 0)
                    for trial in run["trials"]
                )
                complete = bool(run["trials"]) and all(
                    trial.get("cost_usd") is not None for trial in run["trials"]
                )
                costs.append(
                    {
                        "experiment_id": run["id"],
                        "known_cost_usd": known,
                        "cost_usd": known if complete else None,
                        "cost_complete": complete,
                        "budget_uncertain": run.get("budget_uncertain", False),
                    }
                )
                # Incomplete observations do not erase the last valid pass/fail state.
                previous.update(
                    {key: result for key, result in current.items() if result != "incomplete"}
                )
            charged = self._budget_usage(connection, identifier)
            value = {
                "id": new_id(),
                "schedule_id": identifier,
                "name": schedule["name"],
                "created_at": now.isoformat(),
                "window_start": cutoff.isoformat(),
                "window_days": days,
                "baseline_experiment_id": baseline_id,
                "baseline_snapshot_hash": baseline["snapshot_hash"],
                "occurrences": transitions,
                "costs": costs,
                "run_count": len(runs),
                "known_cost_usd": sum(value["known_cost_usd"] for value in costs),
                "cost_complete": bool(costs) and all(value["cost_complete"] for value in costs),
                "lifetime_budget_usd": schedule["lifetime_budget_usd"],
                "committed_usd": charged,
                "interpretation": "Transitions compare graded task outcomes, not causal attribution. Incomplete runs stay visible; repeat outcomes are grouped per task and system. Frozen at creation; later completions do not rewrite this digest.",
            }
            connection.execute(
                "INSERT INTO schedule_digests VALUES(?,?,?,?)",
                (value["id"], identifier, canonical_json(value), content_hash(value)),
            )
            self.store._event(
                connection,
                schedule["source_experiment_id"],
                "schedule.digest_created",
                {
                    "schedule_id": identifier,
                    "digest_id": value["id"],
                    "hash": content_hash(value),
                },
            )
        return value
