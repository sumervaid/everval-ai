"""Durable, bounded recurring evaluations of frozen experiment snapshots.

A due occurrence, its experiment, and the next due time commit atomically. A
crash rolls back all three or leaves a recoverable queued experiment. Missed
intervals coalesce into one run and each schedule allows one outstanding run.
"""

from __future__ import annotations

import builtins
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from cvals.models import ExperimentCreate, Manifest, System, new_id, utc_now
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
            """)

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
        self, name: str, experiment_id: str, interval_minutes: int, enabled: bool = True
    ) -> dict[str, Any]:
        validated = _ScheduleInput(name=name, interval_minutes=interval_minutes, enabled=enabled)
        with self.store._connect(write=True) as connection:
            snapshot, state = self.store._read_experiment(connection, experiment_id)
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
                "INSERT INTO evaluation_schedules VALUES(?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            self.store._event(
                connection,
                experiment_id,
                "schedule.created",
                {"schedule_id": id, "interval_minutes": validated.interval_minutes},
            )
            row = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (id,)
            ).fetchone()
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
            if enabled:
                snapshot, state = self.store._read_experiment(
                    connection, row["source_experiment_id"]
                )
                self._admit(snapshot, state)
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
