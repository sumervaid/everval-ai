"""Transactional local storage, immutable run manifests, and durable worker leases."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from cvals.models import (
    Collection,
    ExperimentCreate,
    System,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
)
from cvals.providers import validate_provider
from cvals.reports import build_report

if TYPE_CHECKING:
    from cvals.config import Settings

TERMINAL = {"completed", "error", "cancelled", "interrupted", "budget_exceeded"}
EXPERIMENT_TERMINAL = {"completed", "cancelled", "failed"}
LEASE_SECONDS = 30.0
MAX_TRIALS = 10000


class LeaseLost(RuntimeError):
    """A stale worker must stop without modifying another worker's results."""


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def init(self) -> None:
        self.settings.prepare()
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError(
                    "Unsupported workspace database schema; use a compatible application version"
                )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS collections (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS systems (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
                    state TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trials (
                    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    position INTEGER NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL,
                    UNIQUE(experiment_id, position)
                );
                CREATE INDEX IF NOT EXISTS trials_experiment ON trials(experiment_id, position);
                CREATE INDEX IF NOT EXISTS experiments_queue ON experiments(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_experiment ON events(experiment_id, id);
                CREATE TABLE IF NOT EXISTS worker_lease (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT NOT NULL,
                    experiment_id TEXT NOT NULL, expires_at REAL NOT NULL
                );
                PRAGMA user_version = 1;
            """)
            from cvals.audit import initialize_schema

            initialize_schema(connection)
            from cvals.spend_limits import initialize_schema as initialize_spending

            initialize_spending(connection)
        os.chmod(self.settings.database_path, 0o600)

    @staticmethod
    def _event(
        connection: sqlite3.Connection, experiment_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        from cvals.audit import append_audit

        connection.execute(
            "INSERT INTO events(experiment_id,created_at,kind,data) VALUES(?,?,?,?)",
            (experiment_id, utc_now(), kind, canonical_json(data)),
        )
        append_audit(connection, kind, experiment_id, data)

    @staticmethod
    def _get(connection: sqlite3.Connection, table: str, key: str) -> dict[str, Any]:
        if table not in {"collections", "systems", "trials"}:
            raise ValueError("Unsupported storage table")
        row = connection.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return dict(json.loads(row["data"]))

    def list_collections(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(json.loads(row[0]))
                for row in connection.execute(
                    "SELECT data FROM collections ORDER BY rowid DESC LIMIT 500"
                )
            ]

    def get_collection(self, id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._get(connection, "collections", id)

    def save_collection(self, collection: Collection) -> dict[str, Any]:
        collection = Collection.model_validate(collection)
        value = collection.model_dump(mode="json")
        with self._connect(write=True) as connection:
            old = connection.execute(
                "SELECT data FROM collections WHERE id=?", (collection.id,)
            ).fetchone()
            if old:
                previous = json.loads(old[0])
                if previous.get("benchmark_release_id") and previous != value:
                    raise ValueError(
                        "Released benchmark collections are immutable; create a new release"
                    )
                # Preserve existing hashes when optional schema defaults were absent.
                for optional in ("benchmark_release_id", "benchmark_partition"):
                    if optional not in previous and value.get(optional) is None:
                        value.pop(optional, None)
                # Stable starter installation may reconstruct its timestamp.
                value["created_at"] = previous["created_at"]
                if previous["version"] == value["version"] and previous != value:
                    raise ValueError("Changing a collection requires a new version")
            if old and previous == value:
                return value
            connection.execute(
                "INSERT INTO collections(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (collection.id, canonical_json(value)),
            )
            from cvals.audit import append_audit

            append_audit(
                connection,
                "collection.saved",
                collection.id,
                {
                    "name": value["name"],
                    "version": value["version"],
                    "hash": content_hash(value),
                    "previous_hash": content_hash(previous) if old else None,
                },
            )
        return value

    def delete_collection(self, id: str) -> None:
        with self._connect(write=True) as connection:
            existing = self._get(connection, "collections", id)
            if existing.get("benchmark_release_id"):
                raise ValueError("Released benchmark collections are immutable")
            if not connection.execute("DELETE FROM collections WHERE id=?", (id,)).rowcount:
                raise KeyError(id)
            from cvals.audit import append_audit

            append_audit(connection, "collection.deleted", id, {"hash": content_hash(existing)})

    @staticmethod
    def _system_public(value: dict[str, Any]) -> dict[str, Any]:
        return {
            **value,
            "configured": value["kind"] != "http"
            or not value["api_key_env"]
            or bool(os.getenv(value["api_key_env"])),
        }

    def list_systems(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                self._system_public(json.loads(row[0]))
                for row in connection.execute("SELECT data FROM systems ORDER BY rowid LIMIT 500")
            ]

    def get_system(self, id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._system_public(self._get(connection, "systems", id))

    def save_system(self, system: System) -> dict[str, Any]:
        value = system.model_dump(mode="json")
        with self._connect(write=True) as connection:
            old = connection.execute("SELECT data FROM systems WHERE id=?", (system.id,)).fetchone()
            previous = json.loads(old["data"]) if old else None
            if previous == value:
                return self._system_public(value)
            connection.execute(
                "INSERT INTO systems(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (system.id, canonical_json(value)),
            )
            from cvals.audit import append_audit

            append_audit(
                connection,
                "system.saved",
                system.id,
                {
                    "name": system.name,
                    "kind": system.kind,
                    "hash": content_hash(value),
                    "previous_hash": content_hash(previous) if previous else None,
                },
            )
        return self._system_public(value)

    def delete_system(self, id: str) -> None:
        with self._connect(write=True) as connection:
            existing = self._get(connection, "systems", id)
            if not connection.execute("DELETE FROM systems WHERE id=?", (id,)).rowcount:
                raise KeyError(id)
            from cvals.audit import append_audit

            append_audit(connection, "system.deleted", id, {"hash": content_hash(existing)})

    def ensure_fixture_systems(self) -> list[dict[str, Any]]:
        fixtures = [
            System(id="fixture-reference", name="Reference fixture · control", kind="reference"),
            System(id="fixture-incomplete", name="Empty submission · control", kind="incomplete"),
        ]
        with self._connect(write=True) as connection:
            for system in fixtures:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO systems(id,data) VALUES(?,?)",
                    (system.id, system.model_dump_json()),
                ).rowcount
                if inserted:
                    from cvals.audit import append_audit

                    append_audit(
                        connection,
                        "system.fixture_installed",
                        system.id,
                        {
                            "name": system.name,
                            "kind": system.kind,
                            "hash": content_hash(system.model_dump(mode="json")),
                        },
                    )
        return [self.get_system(system.id) for system in fixtures]

    def _insert_experiment(
        self,
        connection: sqlite3.Connection,
        request: ExperimentCreate,
        collection: dict[str, Any],
        systems: list[dict[str, Any]],
        parent_id: str | None = None,
    ) -> str:
        from cvals.benchmarks import assert_collection_admitted
        from cvals.spend_limits import project_scope

        assert_collection_admitted(connection, collection)
        total = len(collection["scenarios"]) * len(systems) * request.repetitions
        if total > MAX_TRIALS:
            raise ValueError(f"Experiment exceeds the {MAX_TRIALS:,} trial limit")
        for system in systems:
            if system["kind"] == "replay":
                if request.repetitions != 1:
                    raise ValueError(
                        "Recorded submissions cannot be repeated as independent trials"
                    )
                if system["collection_hash"] != content_hash(collection):
                    raise ValueError(
                        "Recorded submissions do not match this collection fingerprint"
                    )
                unknown_ids = set(system["submissions"]) - {
                    s["id"] for s in collection["scenarios"]
                }
                if unknown_ids:
                    raise ValueError("Recorded submissions contain unknown scenario IDs")
            validate_provider(System.model_validate(system), self.settings, request.budget_usd)
        id = new_id()
        now = utc_now()
        snapshot = {
            "schema_version": 1,
            "collection": collection,
            "systems": systems,
            "repetitions": request.repetitions,
            "budget_usd": request.budget_usd,
            "harness_revision": "everval/0.2.0",
            "grader_revision": "deterministic/1",
            "sandbox_image_requested": self.settings.sandbox_image,
            "protocol": "all scheduled trials; no automatic provider retries",
            "budget_scope": project_scope(connection, collection),
        }
        if len(canonical_json(snapshot).encode()) > 128 * 1024 * 1024:
            raise ValueError("Experiment snapshot exceeds the 128 MiB limit")
        state = {
            "id": id,
            "name": request.name,
            "collection_id": collection["id"],
            "collection_version": collection["version"],
            "collection_hash": content_hash(collection),
            "system_ids": [s["id"] for s in systems],
            "systems": systems,
            "repetitions": request.repetitions,
            "budget_usd": request.budget_usd,
            "budget_committed_usd": 0.0,
            "budget_uncertain": False,
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "total_trials": total,
            "completed_trials": 0,
            "cancel_requested": False,
            "report": None,
            "parent_experiment_id": parent_id,
            "schema_version": 1,
            "snapshot_hash": content_hash(snapshot),
        }
        connection.execute(
            "INSERT INTO experiments VALUES(?,?,?,?,?,?)",
            (
                id,
                canonical_json(snapshot),
                content_hash(snapshot),
                canonical_json(state),
                "queued",
                now,
            ),
        )
        position = 0
        for repetition in range(1, request.repetitions + 1):
            for scenario in collection["scenarios"]:
                # Rotate system order to reduce systematic ordering effects.
                offset = (position // len(systems)) % len(systems)
                for system in systems[offset:] + systems[:offset]:
                    trial = {
                        "id": new_id(),
                        "experiment_id": id,
                        "scenario_id": scenario["id"],
                        "scenario_name": scenario["name"],
                        "domain": scenario["domain"],
                        "system_id": system["id"],
                        "system_name": system["name"],
                        "repetition": repetition,
                        "status": "queued",
                        "score": None,
                        "passed": None,
                        "duration_ms": None,
                        "cost_usd": None,
                        "input_tokens": None,
                        "output_tokens": None,
                        "output": None,
                        "checks": [],
                        "error": None,
                        "started_at": None,
                        "finished_at": None,
                        "attempts": [],
                        "reserved_cost_usd": 0.0,
                    }
                    connection.execute(
                        "INSERT INTO trials VALUES(?,?,?,?,?)",
                        (trial["id"], id, position, canonical_json(trial), "queued"),
                    )
                    position += 1
        self._event(
            connection,
            id,
            "experiment.queued",
            {
                "total_trials": total,
                "snapshot_hash": state["snapshot_hash"],
                "collection_hash": state["collection_hash"],
                "system_ids": state["system_ids"],
                "budget_usd": state["budget_usd"],
                "parent_experiment_id": parent_id,
            },
        )
        return id

    def create_experiment(self, request: ExperimentCreate) -> dict[str, Any]:
        with self._connect(write=True) as connection:
            collection = self._get(connection, "collections", request.collection_id)
            systems = [self._get(connection, "systems", id) for id in request.system_ids]
            id = self._insert_experiment(connection, request, collection, systems)
        return self.get_experiment(id)

    def import_submissions(
        self,
        collection_id: str,
        name: str,
        collection_hash: str,
        submissions: dict[str, Any],
        provenance: dict[str, Any] | None = None,
        system_name: str | None = None,
    ) -> dict[str, Any]:
        system = System(
            id="replay-" + new_id(),
            name=system_name or name,
            kind="replay",
            collection_hash=collection_hash,
            submissions=submissions,
            provenance=provenance or {},
        )
        with self._connect(write=True) as connection:
            collection = self._get(connection, "collections", collection_id)
            request = ExperimentCreate(
                name=name, collection_id=collection_id, system_ids=[system.id]
            )
            id = self._insert_experiment(
                connection, request, collection, [system.model_dump(mode="json")]
            )
            connection.execute(
                "INSERT INTO systems(id,data) VALUES(?,?)", (system.id, system.model_dump_json())
            )
            self._event(
                connection,
                id,
                "submissions.imported",
                {"supplied": len(submissions), "collection_hash": collection_hash},
            )
        return self.get_experiment(id)

    def rerun_experiment(self, id: str) -> dict[str, Any]:
        with self._connect(write=True) as connection:
            snapshot, state = self._read_experiment(connection, id)
            request = ExperimentCreate(
                name=state["name"][:190] + " · rerun",
                collection_id=state["collection_id"],
                system_ids=state["system_ids"],
                repetitions=state["repetitions"],
                budget_usd=state["budget_usd"],
            )
            new = self._insert_experiment(
                connection, request, snapshot["collection"], snapshot["systems"], id
            )
        return self.get_experiment(new)

    @staticmethod
    def _read_experiment(
        connection: sqlite3.Connection, id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        row = connection.execute(
            "SELECT snapshot,snapshot_hash,state FROM experiments WHERE id=?", (id,)
        ).fetchone()
        if row is None:
            raise KeyError(id)
        snapshot = json.loads(row["snapshot"])
        if content_hash(snapshot) != row["snapshot_hash"]:
            raise ValueError("Experiment snapshot integrity check failed")
        if snapshot.get("schema_version") != 1:
            raise ValueError("Unsupported experiment snapshot schema")
        return snapshot, json.loads(row["state"])

    @staticmethod
    def _save_state(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE experiments SET state=?,status=? WHERE id=?",
            (canonical_json(state), state["status"], state["id"]),
        )

    @staticmethod
    def _save_trial(connection: sqlite3.Connection, trial: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE trials SET data=?,status=? WHERE id=?",
            (canonical_json(trial), trial["status"], trial["id"]),
        )

    def list_experiments(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT state FROM experiments ORDER BY created_at DESC LIMIT 500"
                )
            ]

    def get_experiment(self, id: str) -> dict[str, Any]:
        with self._connect() as connection:
            snapshot, state = self._read_experiment(connection, id)
            state["trials"] = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT data FROM trials WHERE experiment_id=? ORDER BY position", (id,)
                )
            ]
            state["snapshot"] = snapshot
        state["report"] = build_report(state)
        return state

    def execution_state(self, id: str) -> dict[str, Any]:
        with self._connect() as connection:
            _, state = self._read_experiment(connection, id)
            return state

    def cancel_experiment(self, id: str) -> dict[str, Any]:
        with self._connect(write=True) as connection:
            _, state = self._read_experiment(connection, id)
            if state["status"] in EXPERIMENT_TERMINAL:
                return self.get_experiment(id)
            state["cancel_requested"] = True
            now = utc_now()
            for row in connection.execute(
                "SELECT data FROM trials WHERE experiment_id=? AND status='queued'", (id,)
            ).fetchall():
                trial = json.loads(row[0])
                trial.update(
                    status="cancelled",
                    error="Cancelled before execution",
                    finished_at=now,
                    cost_usd=0.0,
                )
                self._save_trial(connection, trial)
            if state["status"] == "queued":
                state.update(status="cancelled", finished_at=now)
            state["completed_trials"] = connection.execute(
                "SELECT COUNT(*) FROM trials WHERE experiment_id=? AND status NOT IN ('queued','running')",
                (id,),
            ).fetchone()[0]
            self._save_state(connection, state)
            self._event(connection, id, "experiment.cancel_requested", {})
        return self.get_experiment(id)

    def events(self, experiment_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone():
                raise KeyError(experiment_id)
            rows = connection.execute(
                "SELECT * FROM events WHERE experiment_id=? AND id>? ORDER BY id LIMIT 500",
                (experiment_id, max(0, after)),
            )
            return [
                {
                    "id": r["id"],
                    "experiment_id": r["experiment_id"],
                    "created_at": r["created_at"],
                    "type": r["kind"],
                    "data": json.loads(r["data"]),
                }
                for r in rows
            ]

    def overview(self) -> dict[str, Any]:
        collections = self.list_collections()
        experiments = self.list_experiments()
        measured = [
            s
            for e in experiments
            for s in (e.get("report") or {}).get("systems", [])
            if not s.get("fixture_control")
        ]
        n = sum(s["scheduled"] for s in measured)
        complete_cost = bool(measured) and all(s["cost_complete"] for s in measured)
        return {
            "collections": len(collections),
            "scenarios": sum(len(c["scenarios"]) for c in collections),
            "experiments": len(experiments),
            "completed_experiments": sum(e["status"] == "completed" for e in experiments),
            "pass_rate": sum(s["passed"] for s in measured) / n if n else None,
            "total_cost_usd": sum(s["cost_usd"] for s in measured) if complete_cost else None,
            "recent_experiments": experiments[:6],
        }

    @staticmethod
    def _assert_lease(connection: sqlite3.Connection, id: str, owner: str) -> None:
        row = connection.execute("SELECT * FROM worker_lease WHERE singleton=1").fetchone()
        if (
            row is None
            or row["owner"] != owner
            or row["experiment_id"] != id
            or row["expires_at"] <= time.time()
        ):
            raise LeaseLost("Worker lease is no longer valid")

    def claim(self, owner: str, experiment_id: str | None = None) -> str | None:
        with self._connect(write=True) as connection:
            lease = connection.execute("SELECT * FROM worker_lease WHERE singleton=1").fetchone()
            if lease is not None:
                return None
            query = "SELECT id FROM experiments WHERE status='queued'"
            params: tuple[Any, ...] = ()
            if experiment_id is not None:
                query += " AND id=?"
                params = (experiment_id,)
            row = connection.execute(query + " ORDER BY created_at LIMIT 1", params).fetchone()
            if row is None:
                return None
            id = row["id"]
            _, state = self._read_experiment(connection, id)
            connection.execute(
                "INSERT INTO worker_lease VALUES(1,?,?,?)", (owner, id, time.time() + LEASE_SECONDS)
            )
            state.update(status="running", started_at=state["started_at"] or utc_now())
            self._save_state(connection, state)
            self._event(connection, id, "experiment.running", {})
            return str(id)

    def heartbeat(self, id: str, owner: str) -> None:
        with self._connect(write=True) as connection:
            self._assert_lease(connection, id, owner)
            connection.execute(
                "UPDATE worker_lease SET expires_at=? WHERE singleton=1",
                (time.time() + LEASE_SECONDS,),
            )

    def start_trial(self, id: str, owner: str, trial_id: str, reservation: float) -> dict[str, Any]:
        with self._connect(write=True) as connection:
            self._assert_lease(connection, id, owner)
            snapshot, state = self._read_experiment(connection, id)
            trial = self._get(connection, "trials", trial_id)
            if trial["experiment_id"] != id or trial["status"] != "queued":
                raise ValueError("Trial is not available for execution")
            if state["cancel_requested"]:
                raise ValueError("Experiment cancellation requested")
            from cvals.benchmarks import assert_collection_admitted

            try:
                assert_collection_admitted(connection, snapshot["collection"])
            except (ValueError, KeyError) as exc:
                trial.update(
                    status="error",
                    error=f"Benchmark admission failed: {exc}",
                    finished_at=utc_now(),
                    cost_usd=0.0,
                )
                state["completed_trials"] += 1
                self._save_trial(connection, trial)
                self._save_state(connection, state)
                self._event(connection, id, "trial.admission_blocked", {"trial_id": trial_id})
                return trial
            if state["budget_committed_usd"] + reservation > state["budget_usd"] + 1e-12:
                trial.update(
                    status="budget_exceeded",
                    error="Insufficient remaining budget for conservative reservation",
                    finished_at=utc_now(),
                    cost_usd=0.0,
                )
                state["completed_trials"] += 1
                self._save_trial(connection, trial)
                self._save_state(connection, state)
                self._event(connection, id, "trial.budget_exceeded", {"trial_id": trial_id})
                return trial
            system = next(s for s in snapshot["systems"] if s["id"] == trial["system_id"])
            if system["kind"] == "http":
                from cvals.spend_limits import admit

                blocked = admit(
                    connection, id, trial_id, snapshot.get("budget_scope", "workspace"), reservation
                )
                if blocked:
                    trial.update(
                        status="budget_exceeded", error=blocked, finished_at=utc_now(), cost_usd=0.0
                    )
                    state["completed_trials"] += 1
                    self._save_trial(connection, trial)
                    self._save_state(connection, state)
                    self._event(
                        connection,
                        id,
                        "trial.cumulative_budget_exceeded",
                        {"trial_id": trial_id, "reason": blocked},
                    )
                    return trial
            now = utc_now()
            trial.update(status="running", started_at=now, reserved_cost_usd=reservation)
            trial["attempts"].append(
                {
                    "number": len(trial["attempts"]) + 1,
                    "started_at": now,
                    "finished_at": None,
                    "status": "running",
                    "cost_usd": None,
                    "reserved_cost_usd": reservation,
                }
            )
            state["budget_committed_usd"] += reservation
            self._save_trial(connection, trial)
            self._save_state(connection, state)
            self._event(connection, id, "trial.running", {"trial_id": trial_id})
            return trial

    def finish_trial(self, id: str, owner: str, trial_id: str, result: dict[str, Any]) -> None:
        with self._connect(write=True) as connection:
            self._assert_lease(connection, id, owner)
            snapshot, state = self._read_experiment(connection, id)
            trial = self._get(connection, "trials", trial_id)
            if trial["experiment_id"] != id or trial["status"] != "running":
                raise ValueError("Only the active trial can be finalized")
            if result.get("status") not in TERMINAL:
                raise ValueError("Invalid final trial status")
            trial.update(result, finished_at=utc_now())
            trial["attempts"][-1].update(
                {
                    key: trial.get(key)
                    for key in (
                        "status",
                        "finished_at",
                        "cost_usd",
                        "error",
                        "duration_ms",
                        "input_tokens",
                        "output_tokens",
                    )
                }
            )
            live_provider = (
                next(
                    system["kind"]
                    for system in snapshot["systems"]
                    if system["id"] == trial["system_id"]
                )
                == "http"
            )
            if live_provider:
                from cvals.spend_limits import settle

                settle(connection, trial)
                if trial["cost_usd"] is not None:
                    state["budget_committed_usd"] += trial["cost_usd"] - trial["reserved_cost_usd"]
                elif trial["reserved_cost_usd"] > 0:
                    state["budget_uncertain"] = True
                if trial.get("provider_metadata", {}).get("budget_uncertain"):
                    state["budget_uncertain"] = True
                if state["budget_committed_usd"] > state["budget_usd"] + 1e-12:
                    state["budget_uncertain"] = True
            state["completed_trials"] += 1
            self._save_trial(connection, trial)
            self._save_state(connection, state)
            self._event(
                connection,
                id,
                "trial." + trial["status"],
                {"trial_id": trial_id, "passed": trial.get("passed")},
            )

    def skip_trial(
        self, id: str, owner: str, trial_id: str, reason: str, status: str = "error"
    ) -> None:
        with self._connect(write=True) as connection:
            self._assert_lease(connection, id, owner)
            _, state = self._read_experiment(connection, id)
            trial = self._get(connection, "trials", trial_id)
            if trial["experiment_id"] != id or trial["status"] != "queued":
                return
            trial.update(status=status, finished_at=utc_now(), error=reason, cost_usd=0.0)
            state["completed_trials"] += 1
            self._save_trial(connection, trial)
            self._save_state(connection, state)
            self._event(connection, id, "trial." + status, {"trial_id": trial_id, "error": reason})

    def finish_experiment(self, id: str, owner: str) -> None:
        with self._connect(write=True) as connection:
            self._assert_lease(connection, id, owner)
            _, state = self._read_experiment(connection, id)
            trials = [
                json.loads(r[0])
                for r in connection.execute(
                    "SELECT data FROM trials WHERE experiment_id=? ORDER BY position", (id,)
                )
            ]
            if any(t["status"] not in TERMINAL for t in trials):
                raise ValueError("Experiment has unfinished trials")
            status = (
                "cancelled"
                if state["cancel_requested"]
                else "failed"
                if any(t["status"] != "completed" for t in trials)
                else "completed"
            )
            state.update(status=status, finished_at=utc_now(), completed_trials=len(trials))
            state["report"] = build_report({**state, "trials": trials})
            self._save_state(connection, state)
            connection.execute("DELETE FROM worker_lease WHERE singleton=1 AND owner=?", (owner,))
            self._event(connection, id, "experiment." + status, {})

    def recover_expired(self) -> int:
        with self._connect(write=True) as connection:
            lease = connection.execute("SELECT * FROM worker_lease WHERE singleton=1").fetchone()
            if lease is not None and lease["expires_at"] > time.time():
                return 0
            rows = connection.execute(
                "SELECT id FROM experiments WHERE status='running'"
            ).fetchall()
            count = 0
            for row in rows:
                id = row["id"]
                _, state = self._read_experiment(connection, id)
                for trial_row in connection.execute(
                    "SELECT data FROM trials WHERE experiment_id=? AND status='running'", (id,)
                ).fetchall():
                    trial = json.loads(trial_row[0])
                    trial.update(
                        status="interrupted",
                        finished_at=utc_now(),
                        error="Worker lease expired; outcome and billing may be unknown. No automatic retry.",
                    )
                    if trial["attempts"]:
                        trial["attempts"][-1].update(
                            status="interrupted",
                            finished_at=trial["finished_at"],
                            error=trial["error"],
                        )
                    if trial["reserved_cost_usd"] > 0:
                        state["budget_uncertain"] = True
                    self._save_trial(connection, trial)
                state["completed_trials"] = connection.execute(
                    "SELECT COUNT(*) FROM trials WHERE experiment_id=? AND status NOT IN ('queued','running')",
                    (id,),
                ).fetchone()[0]
                state["status"] = "queued"
                self._save_state(connection, state)
                self._event(
                    connection, id, "experiment.recovered", {"ambiguous_attempts_retried": False}
                )
                count += 1
            connection.execute("DELETE FROM worker_lease")
            return count
