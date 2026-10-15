"""Opt-in, durable screening and escalation on the existing evaluation queue.

An occurrence reserves both stages before starting. Queue admission, occurrence
identity, reservations, and watch checkpoints share one SQLite transaction.
The worker observes local metadata; network discovery uses the repository queue.
"""

from __future__ import annotations

import builtins
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from cvals.audit import append_audit
from cvals.change_runs import _configuration
from cvals.config import Settings
from cvals.github import git_command
from cvals.jobs import Jobs
from cvals.models import Collection, ExperimentCreate, canonical_json, content_hash, new_id, utc_now
from cvals.projects import Projects
from cvals.store import Store

_ACTIVE = {"screening", "full"}


def _money(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("Budget must be a finite nonnegative USD amount")
    return float(value)


class Autopilot:
    def __init__(self, settings: Settings, store: Store | None = None):
        self.settings = settings
        self.store = store or Store(settings)
        self.store.init()
        self.projects = Projects(settings)
        self.jobs = Jobs(settings)
        with self.store._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS autopilot_policies (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    next_check_epoch REAL NOT NULL, next_due_epoch REAL NOT NULL,
                    last_fingerprint TEXT NOT NULL, last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS autopilot_due ON autopilot_policies(enabled,next_check_epoch);
                CREATE TABLE IF NOT EXISTS autopilot_versions (
                    policy_id TEXT NOT NULL REFERENCES autopilot_policies(id),
                    version INTEGER NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL,
                    PRIMARY KEY(policy_id,version)
                );
                CREATE TABLE IF NOT EXISTS autopilot_occurrences (
                    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES autopilot_policies(id),
                    fingerprint TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL,
                    status TEXT NOT NULL, screen_id TEXT NOT NULL REFERENCES experiments(id),
                    full_id TEXT REFERENCES experiments(id), created_epoch REAL NOT NULL,
                    UNIQUE(policy_id,fingerprint)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS autopilot_one_active
                    ON autopilot_occurrences(policy_id) WHERE status IN ('screening','full');
                CREATE TABLE IF NOT EXISTS autopilot_investigations (
                    occurrence_id TEXT PRIMARY KEY REFERENCES autopilot_occurrences(id),
                    data TEXT NOT NULL, hash TEXT NOT NULL, status TEXT NOT NULL,
                    lease_until REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS autopilot_discoveries (
                    policy_id TEXT NOT NULL REFERENCES autopilot_policies(id),
                    due_epoch REAL NOT NULL, job_id TEXT NOT NULL REFERENCES workbench_jobs(id),
                    PRIMARY KEY(policy_id,due_epoch)
                );
            """)

    @staticmethod
    def _read(row: Any) -> dict[str, Any]:
        if row is None:
            raise KeyError("Autopilot policy or occurrence not found")
        value: dict[str, Any] = json.loads(row["data"])
        if content_hash(value) != row["hash"]:
            raise ValueError("Autopilot record integrity check failed")
        return value

    def _policy(self, connection: sqlite3.Connection, identifier: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM autopilot_policies WHERE id=?", (identifier,)
        ).fetchone()
        value = self._read(row)
        return {
            **value,
            "enabled": bool(row["enabled"]),
            "next_check_epoch": row["next_check_epoch"],
            "next_due_epoch": row["next_due_epoch"],
            "last_fingerprint": row["last_fingerprint"],
            "last_error": row["last_error"],
        }

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            return self._policy(connection, identifier)

    def list(self) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._policy(connection, row[0])
                for row in connection.execute(
                    "SELECT id FROM autopilot_policies ORDER BY rowid DESC LIMIT 100"
                )
            ]

    @staticmethod
    def _development(collection: dict[str, Any]) -> None:
        if collection.get("benchmark_partition") in {"holdout", "all"}:
            raise ValueError(
                "Autopilot excludes holdout and combined partitions; select development only"
            )
        if any(
            s.get("provenance", {}).get("partition") == "holdout" or "holdout" in s.get("tags", [])
            for s in collection["scenarios"]
        ):
            raise ValueError("Autopilot cannot use scenarios marked holdout")

    def _sources(
        self, connection: sqlite3.Connection, full_id: str, screen_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        full, full_state = self.store._read_experiment(connection, full_id)
        screen, screen_state = self.store._read_experiment(connection, screen_id)
        for snapshot, state in ((full, full_state), (screen, screen_state)):
            self._development(snapshot["collection"])
            if state["status"] != "completed" or state.get("budget_uncertain"):
                raise ValueError("Source experiments must be completed with known accounting")
            if connection.execute(
                "SELECT 1 FROM trials WHERE experiment_id=? AND (status!='completed' OR json_extract(data,'$.cost_usd') IS NULL) LIMIT 1",
                (state["id"],),
            ).fetchone():
                raise ValueError(
                    "Source experiments must have completed grading and known cost for every trial"
                )
            if any(s["kind"] == "replay" for s in snapshot["systems"]):
                raise ValueError("Recorded replays cannot produce autonomous evidence")
        full_systems = {s["id"]: content_hash(s) for s in full["systems"]}
        if (
            full_systems != {s["id"]: content_hash(s) for s in screen["systems"]}
            or full["repetitions"] != screen["repetitions"]
        ):
            raise ValueError("Sources require identical frozen systems and repetition counts")
        members = {s["id"]: content_hash(s) for s in full["collection"]["scenarios"]}
        subset = screen["collection"]["scenarios"]
        if not 1 <= len(subset) < len(members) or any(
            members.get(s["id"]) != content_hash(s) for s in subset
        ):
            raise ValueError(
                "Screening must be a strict nonempty subset of unchanged full-cohort scenarios"
            )
        return full, full_state, screen, screen_state

    def _observation(self, project_id: str | None) -> dict[str, Any]:
        if not project_id:
            return {"source": "saved system configuration", "commit": None}
        project = self.projects.get(project_id)
        if project["source_kind"] == "local":
            commit = (
                git_command(
                    Path(project["repository_path"]),
                    "rev-parse",
                    "--verify",
                    "HEAD^{commit}",
                    timeout=2,
                    limit_bytes=256,
                )
                .decode()
                .strip()
            )
            return {"source": "local Git HEAD", "project_id": project_id, "commit": commit}
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM repository_candidates WHERE project_id=? ORDER BY id LIMIT 5001",
                (project_id,),
            ).fetchall()
        if len(rows) > 5000:
            raise ValueError("Project watch exceeds 5000 candidates; narrow the project")
        revisions = []
        for row in rows:
            value = json.loads(row["data"])
            revisions.append((value["id"], value.get("metadata_revision")))
        return {
            "source": "locally retained GitHub candidate ledger",
            "project_id": project_id,
            "commit": project["head_sha"],
            "candidates_hash": content_hash(revisions),
            "last_discovery_at": (project.get("last_discovery") or {}).get("at"),
        }

    @staticmethod
    def _fingerprint(
        observation: dict[str, Any], config: dict[str, Any], systems: builtins.list[dict[str, Any]]
    ) -> str:
        stable = {key: value for key, value in observation.items() if key != "last_discovery_at"}
        return content_hash(
            {
                "observation": stable,
                "config": config,
                "systems": {s["id"]: content_hash(s) for s in systems},
            }
        )

    def create(
        self,
        name: str,
        experiment_id: str,
        screening_experiment_id: str,
        *,
        lifetime_budget_usd: float,
        interval_minutes: int = 60,
        trigger: Literal["periodic", "change", "both"] = "periodic",
        project_id: str | None = None,
        enabled: bool = False,
        discover: bool = False,
        config: dict[str, Any] | None = None,
        escalate: Literal["always", "regression", "change"] = "regression",
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Provide a policy name of 1–200 characters")
        if type(interval_minutes) is not int or not 5 <= interval_minutes <= 525600:
            raise ValueError("Interval must be an integer from 5 to 525600 minutes")
        if trigger not in {"periodic", "change", "both"} or escalate not in {
            "always",
            "regression",
            "change",
        }:
            raise ValueError("Unsupported trigger or escalation rule")
        if type(enabled) is not bool or type(discover) is not bool:
            raise ValueError("Enabled and discovery must be explicit booleans")
        if discover and not project_id:
            raise ValueError("Automatic candidate discovery requires a connected project")
        cap, configuration = (
            _money(lifetime_budget_usd),
            _configuration(config if config is not None else {}),
        )
        observation = self._observation(project_id)
        now = time.time()
        with self.store._connect(write=True) as connection:
            full, full_state, screen, screen_state = self._sources(
                connection, experiment_id, screening_experiment_id
            )
            reserve = full_state["budget_usd"] + screen_state["budget_usd"]
            if cap + 1e-9 < reserve:
                raise ValueError("Lifetime budget must reserve both screening and full stages")
            if connection.execute("SELECT COUNT(*) FROM autopilot_policies").fetchone()[0] >= 100:
                raise ValueError("The workspace supports at most 100 Autopilot policies")
            value = {
                "id": new_id(),
                "name": name.strip(),
                "source_experiment_id": experiment_id,
                "screening_experiment_id": screening_experiment_id,
                "source_snapshot_hash": full_state["snapshot_hash"],
                "screening_snapshot_hash": screen_state["snapshot_hash"],
                "screening_scenario_ids": [s["id"] for s in screen["collection"]["scenarios"]],
                "full_scenarios": len(full["collection"]["scenarios"]),
                "screen_budget_usd": screen_state["budget_usd"],
                "full_budget_usd": full_state["budget_usd"],
                "lifetime_budget_usd": cap,
                "interval_minutes": interval_minutes,
                "trigger": trigger,
                "project_id": project_id,
                "discover": discover,
                "config": configuration,
                "escalate": escalate,
                "version": 1,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            fingerprint = self._fingerprint(observation, configuration, full["systems"])
            connection.execute(
                "INSERT INTO autopilot_policies VALUES(?,?,?,?,?,?,?,?)",
                (
                    value["id"],
                    canonical_json(value),
                    content_hash(value),
                    int(enabled),
                    now,
                    now + interval_minutes * 60,
                    fingerprint,
                    None,
                ),
            )
            connection.execute(
                "INSERT INTO autopilot_versions VALUES(?,?,?,?)",
                (value["id"], 1, canonical_json(value), content_hash(value)),
            )
            append_audit(
                connection,
                "autopilot.created",
                value["id"],
                {"enabled": enabled, "policy_hash": content_hash(value)},
            )
        return self.get(value["id"])

    def configure(
        self,
        identifier: str,
        *,
        enabled: bool | None = None,
        config: dict[str, Any] | None = None,
        lifetime_budget_usd: float | None = None,
    ) -> dict[str, Any]:
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("Enabled must be a boolean")
        if config is not None:
            config = _configuration(config)
        if lifetime_budget_usd is not None:
            lifetime_budget_usd = _money(lifetime_budget_usd)
        with self.store._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM autopilot_policies WHERE id=?", (identifier,)
            ).fetchone()
            value = self._read(row)
            usage = self._usage(connection, identifier)
            if enabled and usage["uncertain"]:
                raise ValueError("Uncertain billing prevents resuming Autopilot")
            if lifetime_budget_usd is not None:
                if lifetime_budget_usd + 1e-9 < usage["committed_usd"]:
                    raise ValueError("Budget cannot be less than retained charges and reservations")
                value["lifetime_budget_usd"] = lifetime_budget_usd
            if config is not None:
                value["config"] = config
            value["version"] += 1
            value["updated_at"] = utc_now()
            connection.execute(
                "UPDATE autopilot_policies SET data=?,hash=?,enabled=?,next_check_epoch=?,last_error=NULL WHERE id=?",
                (
                    canonical_json(value),
                    content_hash(value),
                    int(enabled) if enabled is not None else row["enabled"],
                    time.time(),
                    identifier,
                ),
            )
            connection.execute(
                "INSERT INTO autopilot_versions VALUES(?,?,?,?)",
                (identifier, value["version"], canonical_json(value), content_hash(value)),
            )
            append_audit(
                connection,
                "autopilot.configured",
                identifier,
                {
                    "version": value["version"],
                    "policy_hash": content_hash(value),
                    "enabled": enabled,
                },
            )
        return self.get(identifier)

    def history(self, identifier: str) -> builtins.list[dict[str, Any]]:
        self.get(identifier)
        with self.store._connect() as connection:
            return [
                self._read(row)
                for row in connection.execute(
                    "SELECT data,hash FROM autopilot_occurrences WHERE policy_id=? ORDER BY created_epoch DESC LIMIT 200",
                    (identifier,),
                )
            ]

    def _usage(self, connection: sqlite3.Connection, identifier: str) -> dict[str, Any]:
        committed, known, uncertain = 0.0, 0.0, False
        reservations = []
        for row in connection.execute(
            "SELECT * FROM autopilot_occurrences WHERE policy_id=?", (identifier,)
        ).fetchall():
            value = self._read(row)
            stage_charges, stage_known = 0.0, 0.0
            occurrence_uncertain = False
            for stage in ("screen", "full"):
                experiment_id = row[stage + "_id"]
                reserve = value[stage + "_budget_usd"]
                if experiment_id is None:
                    stage_charges += reserve if row["status"] in _ACTIVE else 0.0
                    continue
                state = json.loads(
                    connection.execute(
                        "SELECT state FROM experiments WHERE id=?", (experiment_id,)
                    ).fetchone()[0]
                )
                billed = float(state.get("budget_committed_usd", 0))
                missing = bool(state.get("budget_uncertain")) or bool(
                    connection.execute(
                        "SELECT 1 FROM trials WHERE experiment_id=? AND status NOT IN ('queued','running') AND json_extract(data,'$.cost_usd') IS NULL LIMIT 1",
                        (experiment_id,),
                    ).fetchone()
                )
                active = state["status"] in {"queued", "running"}
                stage_charges += max(reserve, billed) if missing or active else billed
                if missing or active:
                    observed = connection.execute(
                        "SELECT COALESCE(SUM(COALESCE(json_extract(data,'$.cost_usd'),json_extract(data,'$.provider_metadata.known_cost_usd'),0)),0) FROM trials WHERE experiment_id=?",
                        (experiment_id,),
                    ).fetchone()[0]
                    stage_known += float(observed)
                else:
                    stage_known += billed
                occurrence_uncertain |= missing
            # Unknown accounting retains the original combined allowance.
            if occurrence_uncertain:
                stage_charges = max(
                    stage_charges, value["screen_budget_usd"] + value["full_budget_usd"]
                )
            committed += stage_charges
            known += stage_known
            uncertain |= occurrence_uncertain
            reservations.append(
                {
                    "occurrence_id": row["id"],
                    "reserved_or_charged_usd": stage_charges,
                    "accounted_usd": stage_known,
                    "uncertain": occurrence_uncertain,
                }
            )
        return {
            "committed_usd": committed,
            "accounted_usd": known,
            "uncertain": uncertain,
            "reservations": reservations,
        }

    def budget(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            policy = self._policy(connection, identifier)
            usage = self._usage(connection, identifier)
        return {
            "policy_id": identifier,
            **usage,
            "lifetime_budget_usd": policy["lifetime_budget_usd"],
            "remaining_usd": max(0.0, policy["lifetime_budget_usd"] - usage["committed_usd"]),
            "scope": "Autopilot occurrences only; source experiments excluded. Configured provider accounting, not a coding-session invoice.",
        }

    def projection(self, identifier: str, days: int = 30) -> dict[str, Any]:
        if type(days) is not int or not 1 <= days <= 366:
            raise ValueError("Projection window must be 1–366 days")
        policy, budget = self.get(identifier), self.budget(identifier)
        per_cycle = policy["screen_budget_usd"] + policy["full_budget_usd"]
        periodic = math.ceil(days * 1440 / policy["interval_minutes"])
        return {
            "policy_id": identifier,
            "days": days,
            "screen_cap_usd": policy["screen_budget_usd"],
            "full_cap_usd": policy["full_budget_usd"],
            "cycle_reservation_usd": per_cycle,
            "periodic_cycles": periodic if policy["trigger"] != "change" else None,
            "periodic_ceiling_usd": periodic * per_cycle if policy["trigger"] != "change" else None,
            "remaining_cap_usd": budget["remaining_usd"],
            "fully_reserved_cycles_remaining": math.floor(
                (budget["remaining_usd"] + 1e-9) / per_cycle
            )
            if per_cycle
            else None,
            "assumptions": "Budget ceilings, not predicted invoices. Every projected cycle escalates; settled unused reservations can fund more cycles. Change-trigger counts are unknown. Workspace/project caps also apply at request admission.",
        }

    def screen(self, experiment_id: str, size: int = 5) -> dict[str, Any]:
        """Explicitly queue a screening baseline for a curated, unreleased collection."""
        with self.store._connect() as connection:
            snapshot, state = self.store._read_experiment(connection, experiment_id)
        collection = snapshot["collection"]
        self._development(collection)
        if state["status"] != "completed" or state.get("budget_uncertain"):
            raise ValueError("Complete the source with known accounting before creating screening")
        if collection.get("benchmark_release_id"):
            raise ValueError(
                "Historical screening needs a separately validated development release; released collections cannot be sliced"
            )
        scenarios = collection["scenarios"]
        if type(size) is not int or not 1 <= size < len(scenarios):
            raise ValueError("Screen size must be positive and smaller than the full cohort")
        subset = Collection.model_validate(
            {
                **collection,
                "id": "screen-" + new_id(),
                "name": collection["name"][:170] + " · screening",
                "created_at": utc_now(),
                "scenarios": sorted(scenarios, key=lambda s: content_hash(s))[0:size],
            }
        )
        # The normal admission boundary still rejects unvalidated historical tasks.
        from cvals.benchmarks import assert_collection_admitted

        with self.store._connect() as connection:
            assert_collection_admitted(connection, subset.model_dump(mode="json"))
        saved = self.store.save_collection(subset)
        with self.store._connect(write=True) as connection:
            identifier = self.store._insert_experiment(
                connection,
                ExperimentCreate(
                    name=state["name"][:170] + " · screening baseline",
                    collection_id=saved["id"],
                    system_ids=state["system_ids"],
                    repetitions=state["repetitions"],
                    budget_usd=state["budget_usd"],
                ),
                saved,
                snapshot["systems"],
                experiment_id,
            )
        return self.store.get_experiment(identifier)

    @staticmethod
    def _save_occurrence(connection: sqlite3.Connection, value: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE autopilot_occurrences SET data=?,hash=?,status=?,full_id=? WHERE id=?",
            (
                canonical_json(value),
                content_hash(value),
                value["status"],
                value.get("full_experiment_id"),
                value["id"],
            ),
        )

    def _halt(self, connection: sqlite3.Connection, policy_id: str, error: str) -> None:
        # Errors are operator-safe messages, never raw provider response bodies.
        connection.execute(
            "UPDATE autopilot_policies SET enabled=0,last_error=? WHERE id=?",
            (error[:2000], policy_id),
        )
        for row in connection.execute(
            "SELECT data,hash FROM autopilot_occurrences WHERE policy_id=? AND status IN ('screening','full')",
            (policy_id,),
        ).fetchall():
            occurrence = self._read(row)
            occurrence.update(status="halted", reason=error[:2000], finished_at=utc_now())
            self._save_occurrence(connection, occurrence)
        append_audit(connection, "autopilot.halted", policy_id, {"error": error[:2000]})

    def _outcomes(self, connection: sqlite3.Connection, identifier: str) -> dict[str, bool]:
        values = {}
        for row in connection.execute(
            "SELECT data FROM trials WHERE experiment_id=? ORDER BY position", (identifier,)
        ):
            trial = json.loads(row[0])
            if trial["status"] != "completed":
                raise ValueError(
                    "Incomplete or infrastructure-error trials cannot justify escalation"
                )
            values[f"{trial['system_id']}/{trial['scenario_id']}/{trial['repetition']}"] = (
                trial.get("passed") is True
            )
        return values

    def _reconcile(
        self, connection: sqlite3.Connection, policy: dict[str, Any]
    ) -> builtins.list[str]:
        row = connection.execute(
            "SELECT * FROM autopilot_occurrences WHERE policy_id=? AND status IN ('screening','full')",
            (policy["id"],),
        ).fetchone()
        if row is None:
            return []
        occurrence = self._read(row)
        stage_id = row["full_id"] or row["screen_id"]
        snapshot, state = self.store._read_experiment(connection, stage_id)
        if state.get("budget_uncertain"):
            raise ValueError("An Autopilot stage has uncertain billing; reservations retained")
        if state["status"] in {"queued", "running"}:
            return []
        if state["status"] != "completed":
            occurrence.update(
                status="cancelled" if state["status"] == "cancelled" else "halted",
                finished_at=utc_now(),
                reason="Stage did not complete",
            )
            self._save_occurrence(connection, occurrence)
            self._halt(
                connection,
                policy["id"],
                "Stage did not complete; inspect retained evidence before resuming",
            )
            return []
        current = self._outcomes(connection, stage_id)
        if row["full_id"]:
            occurrence.update(status="completed", finished_at=utc_now())
            self._save_occurrence(connection, occurrence)
            append_audit(
                connection,
                "autopilot.completed",
                occurrence["id"],
                {"full_experiment_id": stage_id},
            )
            return []
        if not policy["enabled"]:
            return []
        baseline = self._outcomes(connection, occurrence["screening_source_id"])
        if set(current) != set(baseline):
            raise ValueError("Screening evidence is not comparable to its frozen baseline")
        regressions = sorted(key for key, passed in current.items() if baseline[key] and not passed)
        occurrence["screening_regressions"] = regressions
        rule = occurrence["escalate"]
        escalate = (
            rule == "always" or bool(regressions) or (rule == "change" and occurrence["changed"])
        )
        if not escalate:
            occurrence.update(
                status="screened_out",
                finished_at=utc_now(),
                reason="Screening did not meet the configured escalation rule",
            )
            self._save_occurrence(connection, occurrence)
            append_audit(
                connection,
                "autopilot.screened_out",
                occurrence["id"],
                {"screen_experiment_id": stage_id},
            )
            return []
        full, full_state = self.store._read_experiment(connection, occurrence["full_source_id"])
        experiment_id = self.store._insert_experiment(
            connection,
            ExperimentCreate(
                name=policy["name"][:170] + " · full",
                collection_id=full_state["collection_id"],
                system_ids=state["system_ids"],
                repetitions=state["repetitions"],
                budget_usd=occurrence["full_budget_usd"],
            ),
            full["collection"],
            snapshot["systems"],
            occurrence["full_source_id"],
        )
        occurrence.update(status="full", full_experiment_id=experiment_id)
        self._save_occurrence(connection, occurrence)
        append_audit(
            connection,
            "autopilot.escalated",
            occurrence["id"],
            {"experiment_id": experiment_id, "regressions": regressions},
        )
        return [experiment_id]

    def _discovery(
        self, connection: sqlite3.Connection, policy: dict[str, Any], now: float
    ) -> None:
        if not policy["discover"]:
            return
        last = connection.execute(
            "SELECT d.due_epoch,j.status FROM autopilot_discoveries d JOIN workbench_jobs j ON j.id=d.job_id WHERE d.policy_id=? ORDER BY d.due_epoch DESC LIMIT 1",
            (policy["id"],),
        ).fetchone()
        if last and (
            last["status"] in {"queued", "running"}
            or now < last["due_epoch"] + policy["interval_minutes"] * 60
        ):
            return
        if connection.execute(
            "SELECT 1 FROM workbench_jobs WHERE kind='discover' AND status IN ('queued','running') AND json_extract(payload,'$.project_id')=?",
            (policy["project_id"],),
        ).fetchone():
            return
        if (
            connection.execute(
                "SELECT COUNT(*) FROM workbench_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            >= 100
        ):
            raise ValueError("Repository queue is full; candidate discovery was not admitted")
        identifier, created = new_id(), utc_now()
        payload = {"project_id": policy["project_id"], "limit": 20}
        connection.execute(
            "INSERT INTO workbench_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                "discover",
                "queued",
                created,
                created,
                canonical_json(payload),
                None,
                None,
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO autopilot_discoveries VALUES(?,?,?)", (policy["id"], now, identifier)
        )
        append_audit(
            connection,
            "job.queued",
            identifier,
            {"kind": "discover", "payload": payload, "autopilot_policy_id": policy["id"]},
        )

    def tick(self, now: float | None = None) -> builtins.list[str]:
        """Bounded worker heartbeat. No provider requests or network discovery here."""
        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("Heartbeat time must be finite")
        with self.store._connect() as connection:
            identifiers = [
                r[0]
                for r in connection.execute(
                    "SELECT id FROM autopilot_policies WHERE next_check_epoch<=? AND (enabled=1 OR EXISTS (SELECT 1 FROM autopilot_occurrences o WHERE o.policy_id=autopilot_policies.id AND o.status IN ('screening','full'))) ORDER BY next_check_epoch,id LIMIT 10",
                    (now,),
                )
            ]
        created = []
        for identifier in identifiers:
            observed_error = None
            try:
                previous = self.get(identifier)
                observation = self._observation(previous["project_id"])
            except (KeyError, ValueError, OSError):
                observed_error = (
                    "Project observation failed; inspect the connected repository before resuming"
                )
                observation = {}
            with self.store._connect(write=True) as connection:
                policy = self._policy(connection, identifier)
                if policy["next_check_epoch"] > now:
                    continue
                connection.execute(
                    "UPDATE autopilot_policies SET next_check_epoch=? WHERE id=?",
                    (now + 60, identifier),
                )
                connection.execute("SAVEPOINT autopilot_admission")
                try:
                    if observed_error:
                        raise ValueError(observed_error)
                    usage = self._usage(connection, identifier)
                    if usage["uncertain"]:
                        raise ValueError(
                            "A prior Autopilot run has uncertain billing; reservations retained"
                        )
                    had_active = connection.execute(
                        "SELECT 1 FROM autopilot_occurrences WHERE policy_id=? AND status IN ('screening','full')",
                        (identifier,),
                    ).fetchone()
                    staged = self._reconcile(connection, policy)
                    # Settle one existing occurrence before attempting a new admission.
                    # An exhausted next-cycle cap must not roll back retained completion.
                    policy = self._policy(connection, identifier)
                    if not policy["enabled"] or had_active:
                        connection.execute("RELEASE autopilot_admission")
                        created.extend(staged)
                        continue
                    # A halted occurrence can still own queued/running work. Its
                    # actual queue state, not its controller label, prevents overlap.
                    if connection.execute(
                        "SELECT 1 FROM autopilot_occurrences o JOIN experiments e ON e.id IN (o.screen_id,o.full_id) WHERE o.policy_id=? AND e.status IN ('queued','running') LIMIT 1",
                        (identifier,),
                    ).fetchone():
                        connection.execute("RELEASE autopilot_admission")
                        created.extend(staged)
                        continue
                    self._discovery(connection, policy, now)
                    full, full_state, screen, screen_state = self._sources(
                        connection,
                        policy["source_experiment_id"],
                        policy["screening_experiment_id"],
                    )
                    systems = [
                        self.store._get(connection, "systems", s["id"]) for s in full["systems"]
                    ]
                    if any(s["kind"] == "replay" for s in systems):
                        raise ValueError("Recorded replays cannot produce autonomous evidence")
                    fingerprint = self._fingerprint(observation, policy["config"], systems)
                    changed = fingerprint != policy["last_fingerprint"]
                    periodic = policy["trigger"] != "change" and now >= policy["next_due_epoch"]
                    change = policy["trigger"] != "periodic" and changed
                    if not periodic and not change:
                        connection.execute("RELEASE autopilot_admission")
                        created.extend(staged)
                        continue
                    occurrence_fingerprint = content_hash(
                        {
                            "context": fingerprint,
                            "version": policy["version"],
                            "due": policy["next_due_epoch"] if periodic else None,
                        }
                    )
                    if connection.execute(
                        "SELECT 1 FROM autopilot_occurrences WHERE policy_id=? AND fingerprint=?",
                        (identifier, occurrence_fingerprint),
                    ).fetchone():
                        connection.execute(
                            "UPDATE autopilot_policies SET last_fingerprint=?,next_due_epoch=? WHERE id=?",
                            (fingerprint, now + policy["interval_minutes"] * 60, identifier),
                        )
                        connection.execute("RELEASE autopilot_admission")
                        created.extend(staged)
                        continue
                    usage = self._usage(connection, identifier)
                    reserve = policy["screen_budget_usd"] + policy["full_budget_usd"]
                    if usage["committed_usd"] + reserve > policy["lifetime_budget_usd"] + 1e-9:
                        raise ValueError(
                            "Lifetime budget cannot reserve both stages; increase the cap explicitly before resuming"
                        )
                    experiment_id = self.store._insert_experiment(
                        connection,
                        ExperimentCreate(
                            name=policy["name"][:170] + " · screening",
                            collection_id=screen_state["collection_id"],
                            system_ids=[s["id"] for s in systems],
                            repetitions=screen_state["repetitions"],
                            budget_usd=policy["screen_budget_usd"],
                        ),
                        screen["collection"],
                        systems,
                        policy["screening_experiment_id"],
                    )
                    occurrence = {
                        "id": new_id(),
                        "policy_id": identifier,
                        "policy_version": policy["version"],
                        "fingerprint": occurrence_fingerprint,
                        "context_fingerprint": fingerprint,
                        "observation": observation,
                        "config_hash": content_hash(policy["config"]),
                        "system_hashes": {s["id"]: content_hash(s) for s in systems},
                        "expected_adapter_revisions": {
                            s["id"]: s["adapter_revision"]
                            for s in systems
                            if s.get("harness") == "external-agent"
                        },
                        "deployment_verification": "Adapter responses must match each frozen revision or the trial fails; Git provenance does not attest a deployment.",
                        "full_source_id": full_state["id"],
                        "screening_source_id": screen_state["id"],
                        "screen_budget_usd": policy["screen_budget_usd"],
                        "full_budget_usd": policy["full_budget_usd"],
                        "screen_experiment_id": experiment_id,
                        "full_experiment_id": None,
                        "changed": changed,
                        "trigger": "periodic" if periodic else "change",
                        "escalate": policy["escalate"],
                        "status": "screening",
                        "created_at": utc_now(),
                    }
                    connection.execute(
                        "INSERT INTO autopilot_occurrences VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            occurrence["id"],
                            identifier,
                            occurrence_fingerprint,
                            canonical_json(occurrence),
                            content_hash(occurrence),
                            "screening",
                            experiment_id,
                            None,
                            now,
                        ),
                    )
                    connection.execute(
                        "UPDATE autopilot_policies SET last_fingerprint=?,next_due_epoch=?,last_error=NULL WHERE id=?",
                        (fingerprint, now + policy["interval_minutes"] * 60, identifier),
                    )
                    append_audit(
                        connection,
                        "autopilot.screening_queued",
                        occurrence["id"],
                        {
                            "policy_id": identifier,
                            "experiment_id": experiment_id,
                            "reserved_usd": reserve,
                        },
                    )
                    staged.append(experiment_id)
                except (ValueError, KeyError) as exc:
                    connection.execute("ROLLBACK TO autopilot_admission")
                    connection.execute("RELEASE autopilot_admission")
                    self._halt(connection, identifier, str(exc))
                    continue
                connection.execute("RELEASE autopilot_admission")
                created.extend(staged)
        return created

    def investigations(self, identifier: str) -> builtins.list[dict[str, Any]]:
        self.get(identifier)
        with self.store._connect() as connection:
            return [
                self._read(row)
                for row in connection.execute(
                    "SELECT i.data,i.hash FROM autopilot_investigations i JOIN autopilot_occurrences o ON o.id=i.occurrence_id WHERE o.policy_id=? ORDER BY o.created_epoch DESC LIMIT 200",
                    (identifier,),
                )
            ]

    @staticmethod
    def _save_investigation(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE autopilot_investigations SET data=?,hash=?,status=? WHERE occurrence_id=?",
            (
                canonical_json(record),
                content_hash(record),
                record["status"],
                record["occurrence_id"],
            ),
        )

    def investigate_pending(self, now: float | None = None) -> dict[str, Any] | None:
        """Investigate one finalized cycle, at most three pure-grader failures.

        Claims commit before synchronous work. Interrupted claims remain visible and
        are never automatically repeated, so a worker restart cannot duplicate work.
        This repeats grading of recorded output, never a model or an adapter call.
        """
        from cvals.pure_checks import assert_pure_work
        from cvals.research import MAX_JSON_BYTES, PURE_CHECKS, ResearchLab

        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("Investigation time must be finite")
        with self.store._connect(write=True) as connection:
            expired = connection.execute(
                "SELECT data,hash FROM autopilot_investigations WHERE status='running' AND lease_until<? LIMIT 10",
                (now,),
            ).fetchall()
            for row in expired:
                interrupted = self._read(row)
                interrupted.update(
                    status="interrupted",
                    finished_at=utc_now(),
                    reason="Worker stopped before investigation completion; inspect retained research records before explicit retry",
                )
                self._save_investigation(connection, interrupted)
                append_audit(
                    connection,
                    "autopilot.investigation_interrupted",
                    interrupted["occurrence_id"],
                    {},
                )
            row = connection.execute(
                "SELECT o.* FROM autopilot_occurrences o WHERE o.status NOT IN ('screening','full') AND NOT EXISTS (SELECT 1 FROM experiments e WHERE e.id IN (o.screen_id,o.full_id) AND e.status IN ('queued','running')) AND NOT EXISTS (SELECT 1 FROM autopilot_investigations i WHERE i.occurrence_id=o.id) ORDER BY o.created_epoch LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            occurrence = self._read(row)
            selected: builtins.list[dict[str, Any]] = []
            skipped: builtins.list[dict[str, Any]] = []
            seen = set()
            for stage in ("full_id", "screen_id"):
                experiment_id = row[stage]
                if not experiment_id:
                    continue
                snapshot, state = self.store._read_experiment(connection, experiment_id)
                if state["status"] in {"queued", "running"}:
                    continue
                scenarios = {
                    scenario["id"]: scenario for scenario in snapshot["collection"]["scenarios"]
                }
                for trial_row in connection.execute(
                    "SELECT data FROM trials WHERE experiment_id=? AND status='completed' AND json_extract(data,'$.passed')=0 ORDER BY position LIMIT 20",
                    (experiment_id,),
                ):
                    trial = json.loads(trial_row[0])
                    scenario = scenarios[trial["scenario_id"]]
                    key = (trial["scenario_id"], trial["system_id"])
                    if (
                        key in seen
                        or len(selected) >= 3
                        or trial.get("output") is None
                        or any(check["kind"] not in PURE_CHECKS for check in scenario["checks"])
                        or len(canonical_json(scenario).encode()) > MAX_JSON_BYTES
                        or len(canonical_json(trial["output"]).encode()) > MAX_JSON_BYTES
                    ):
                        continue
                    try:
                        assert_pure_work(scenario, trial["output"])
                    except ValueError:
                        skipped.append(
                            {
                                "experiment_id": experiment_id,
                                "trial_id": trial["id"],
                                "reason": "Pure grading exceeds the supported structural or work bounds",
                            }
                        )
                        continue
                    seen.add(key)
                    selected.append(
                        {
                            "experiment_id": experiment_id,
                            "trial_id": trial["id"],
                            "scenario_id": trial["scenario_id"],
                            "system_id": trial["system_id"],
                        }
                    )
            record = {
                "occurrence_id": occurrence["id"],
                "policy_id": occurrence["policy_id"],
                "status": "running" if selected else "skipped",
                "selected": selected,
                "skipped": skipped,
                "results": [],
                "created_at": utc_now(),
                "interpretation": "Repeated grading and reduction of recorded output, not repeated model sampling or causal root-cause proof; at most three distinct pure-grader task/system failures.",
            }
            if not selected:
                record["reason"] = (
                    "No eligible recorded pure-grader failures in the bounded inspection window"
                )
            connection.execute(
                "INSERT INTO autopilot_investigations VALUES(?,?,?,?,?)",
                (
                    occurrence["id"],
                    canonical_json(record),
                    content_hash(record),
                    record["status"],
                    now + 300,
                ),
            )
            append_audit(
                connection,
                "autopilot.investigation_claimed",
                occurrence["id"],
                {"selected": selected},
            )
        if not selected:
            return record
        lab = ResearchLab(self.settings)
        for target in selected:
            result: dict[str, Any] = {**target}
            for kind in ("repeat_regrade", "minimize_output"):
                try:
                    evidence = (
                        lab.repeat_regrade(
                            target["experiment_id"], target["trial_id"], repetitions=2
                        )
                        if kind == "repeat_regrade"
                        else lab.minimize_output(
                            target["experiment_id"], target["trial_id"], max_evaluations=20
                        )
                    )
                    result[kind + "_id"] = evidence["id"]
                except (ValueError, KeyError, OSError):
                    result[kind + "_error"] = (
                        "Local research unavailable; inspect the retained experiment and research records"
                    )
                with self.store._connect(write=True) as connection:
                    current = self._read(
                        connection.execute(
                            "SELECT data,hash FROM autopilot_investigations WHERE occurrence_id=?",
                            (occurrence["id"],),
                        ).fetchone()
                    )
                    if current["status"] != "running":
                        return current
                    # Save the first result before starting the next operation, so
                    # a crash preserves links to every completed research record.
                    record["results"] = [
                        value
                        for value in record["results"]
                        if value["trial_id"] != target["trial_id"]
                    ] + [result]
                    self._save_investigation(connection, record)
        record.update(status="completed", finished_at=utc_now())
        with self.store._connect(write=True) as connection:
            current = self._read(
                connection.execute(
                    "SELECT data,hash FROM autopilot_investigations WHERE occurrence_id=?",
                    (occurrence["id"],),
                ).fetchone()
            )
            if current["status"] != "running":
                return current
            self._save_investigation(connection, record)
            append_audit(
                connection,
                "autopilot.investigation_completed",
                occurrence["id"],
                {"results": record["results"]},
            )
        return record
