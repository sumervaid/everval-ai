"""Finite candidate selection with a reserved, single-use final holdout."""

from __future__ import annotations

import builtins
import json
import math
import sqlite3
from typing import Any

from cvals.config import Settings
from cvals.economics import analyze
from cvals.models import (
    ExperimentCreate,
    Scenario,
    System,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
)
from cvals.providers import reserve_cost
from cvals.research import ResearchRecords, task_identity
from cvals.store import Store

ACTIVE = {"development_queued", "holdout_queued"}


def _task_keys(collection: dict[str, Any]) -> set[str]:
    result = set()
    for scenario in collection["scenarios"]:
        result.add("id:" + scenario["id"])
        result.add("content:" + task_identity(scenario))
        provenance = scenario.get("provenance", {})
        for key in ("candidate_id", "case_id", "production_case_id"):
            if provenance.get(key):
                result.add(key + ":" + str(provenance[key]))
    return result


def assert_holdout_available(
    connection: sqlite3.Connection, collection: dict[str, Any], reservation_id: str | None = None
) -> None:
    """Store admission hook; a reserved task cannot leak through another collection."""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='optimization_holdouts'"
    ).fetchone()
    if not exists:
        if reservation_id is not None:
            raise ValueError("Unknown optimization holdout reservation")
        return
    keys = _task_keys(collection)
    authorized = reservation_id is None
    for row in connection.execute("SELECT * FROM optimization_holdouts"):
        reservation = json.loads(row["data"])
        if content_hash(reservation) != row["hash"]:
            raise ValueError("Holdout reservation integrity check failed")
        overlaps = keys & set(reservation["task_keys"])
        if not overlaps and reservation["collection_id"] != collection["id"]:
            continue
        if reservation_id != row["study_id"]:
            raise ValueError(
                "This collection contains a reserved or consumed optimization holdout task"
            )
        if (
            reservation["collection_hash"] != content_hash(collection)
            or row["consumed_experiment_id"] is not None
        ):
            raise ValueError("The final holdout is modified or has already been consumed")
        study = ResearchRecords.read(connection, str(reservation_id))
        if study["status"] != "development_queued":
            raise ValueError("The study is not ready for final holdout admission")
        authorized = True
    if not authorized:
        raise ValueError("The supplied holdout reservation does not match this collection")


def _experiment(connection: sqlite3.Connection, identifier: str) -> dict[str, Any]:
    snapshot, state = Store._read_experiment(connection, identifier)
    state["snapshot"] = snapshot
    state["trials"] = [
        json.loads(row[0])
        for row in connection.execute(
            "SELECT data FROM trials WHERE experiment_id=? ORDER BY position", (identifier,)
        )
    ]
    return state


def _finite(value: float, name: str, lower: float, upper: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not lower <= value <= upper
    ):
        raise ValueError(f"{name} must be finite and between {lower} and {upper}")


def _projection(
    development: dict[str, Any],
    holdout: dict[str, Any],
    systems: list[dict[str, Any]],
    budget_usd: float,
    repetitions: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for value in systems:
        system = System.model_validate(value)
        priced = (
            system.input_cost_per_million is not None and system.output_cost_per_million is not None
        )
        if system.kind != "http" or not priced:
            raise ValueError(
                "Projection requires live candidates with explicit input and output prices"
            )
        rows.append(
            {
                "system_id": system.id,
                "configuration_hash": content_hash(value),
                "pricing_version": system.pricing_version,
                "development_reservation_usd": math.fsum(
                    reserve_cost(Scenario.model_validate(s), system)
                    for s in development["scenarios"]
                )
                * repetitions,
                "holdout_reservation_usd": math.fsum(
                    reserve_cost(Scenario.model_validate(s), system) for s in holdout["scenarios"]
                )
                * repetitions,
            }
        )
    development_budget = budget_usd * 0.6
    holdout_budget = budget_usd - development_budget
    development_reservation = math.fsum(row["development_reservation_usd"] for row in rows)
    holdout_reservation = max(row["holdout_reservation_usd"] for row in rows)
    return {
        "systems": rows,
        "expected_provider_bill_usd": None,
        "development_budget_usd": development_budget,
        "holdout_budget_usd": holdout_budget,
        "development_reservation_usd": development_reservation,
        "largest_candidate_holdout_reservation_usd": holdout_reservation,
        "combined_reservation_estimate_usd": development_reservation + holdout_reservation,
        "all_development_attempts_fit": development_reservation <= development_budget,
        "any_selected_candidate_holdout_fits": holdout_reservation <= holdout_budget,
        "assumptions": [
            "Configured prices, input sizes and token/tool ceilings; these are admission bounds, not invoice forecasts.",
            "All candidates run on development; only the selected candidate runs on holdout.",
            "A 60/40 phase split is fixed before execution; unused allocation is not transferred.",
            "Actual provider charges, external tools, hosting and development-service billing are not predicted.",
        ],
    }


class Optimizer(ResearchRecords):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        with self.store._connect(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS optimization_holdouts(
                study_id TEXT PRIMARY KEY, data TEXT NOT NULL, hash TEXT NOT NULL,
                consumed_experiment_id TEXT
            )""")

    def project(
        self,
        development_collection_id: str,
        holdout_collection_id: str,
        system_ids: list[str],
        budget_usd: float,
        repetitions: int = 1,
    ) -> dict[str, Any]:
        _finite(budget_usd, "Study lifetime budget", 0, 100000)
        if type(repetitions) is not int or not 1 <= repetitions <= 10:
            raise ValueError("Projection supports 1–10 repetitions")
        if not 1 <= len(system_ids) <= 10 or len(system_ids) != len(set(system_ids)):
            raise ValueError("Select 1–10 distinct candidate systems")
        with self.store._connect() as connection:
            development = Store._get(connection, "collections", development_collection_id)
            holdout = Store._get(connection, "collections", holdout_collection_id)
            systems = [Store._get(connection, "systems", key) for key in system_ids]
            return _projection(development, holdout, systems, budget_usd, repetitions)

    def create(
        self,
        name: str,
        development_collection_id: str,
        holdout_collection_id: str,
        system_ids: list[str],
        budget_usd: float,
        min_pass_rate: float = 1.0,
        max_p95_ms: float = 60000,
        repetitions: int = 1,
    ) -> dict[str, Any]:
        if not name.strip() or len(name) > 160:
            raise ValueError("Study name must contain 1–160 characters")
        _finite(budget_usd, "Study lifetime budget", 0, 100000)
        _finite(min_pass_rate, "Minimum pass rate", 0, 1)
        _finite(max_p95_ms, "Maximum p95 duration", 1, 604800000)
        if type(repetitions) is not int or not 1 <= repetitions <= 10:
            raise ValueError("Optimization supports 1–10 repetitions")
        if not 1 <= len(system_ids) <= 10 or len(system_ids) != len(set(system_ids)):
            raise ValueError("Select 1–10 distinct, explicitly configured HTTP systems")
        with self.store._connect(write=True) as connection:
            development = Store._get(connection, "collections", development_collection_id)
            holdout = Store._get(connection, "collections", holdout_collection_id)
            if development.get("benchmark_partition") != "dev":
                raise ValueError("Development collection must declare benchmark_partition=dev")
            if any(
                scenario.get("provenance", {}).get("partition") == "holdout"
                or "holdout" in scenario.get("tags", [])
                for scenario in development["scenarios"]
            ):
                raise ValueError("Development scenarios must not be marked as holdout")
            if holdout.get("benchmark_partition") != "holdout":
                raise ValueError("Final collection must declare benchmark_partition=holdout")
            keys = _task_keys(holdout)
            if keys & _task_keys(development):
                raise ValueError(
                    "Development and holdout task identities or public content overlap"
                )
            assert_holdout_available(connection, development)
            assert_holdout_available(connection, holdout)
            # Check the complete history, including cancelled, replay and fixture experiments.
            for row in connection.execute("SELECT snapshot FROM experiments"):
                if keys & _task_keys(json.loads(row[0])["collection"]):
                    raise ValueError(
                        "Holdout tasks have already appeared in a workspace experiment"
                    )
            systems = [Store._get(connection, "systems", key) for key in system_ids]
            if any(system["kind"] != "http" for system in systems):
                raise ValueError(
                    "Optimizer candidates must be live HTTP systems; fixtures and replays are excluded"
                )
            identifier = new_id()
            projection = _projection(development, holdout, systems, budget_usd, repetitions)
            development_budget = projection["development_budget_usd"]
            holdout_budget = projection["holdout_budget_usd"]
            reservation = {
                "study_id": identifier,
                "collection_id": holdout_collection_id,
                "collection_hash": content_hash(holdout),
                "task_keys": sorted(keys),
                "created_at": utc_now(),
            }
            connection.execute(
                "INSERT INTO optimization_holdouts VALUES(?,?,?,NULL)",
                (identifier, canonical_json(reservation), content_hash(reservation)),
            )
            experiment_id = self.store._insert_experiment(
                connection,
                ExperimentCreate(
                    name=name + " · development",
                    collection_id=development_collection_id,
                    system_ids=system_ids,
                    repetitions=repetitions,
                    budget_usd=development_budget,
                ),
                development,
                systems,
            )
            value = {
                "id": identifier,
                "kind": "optimization",
                "name": name,
                "status": "development_queued",
                "created_at": utc_now(),
                "development_experiment_id": experiment_id,
                "holdout_experiment_id": None,
                "development_collection_hash": content_hash(development),
                "holdout": holdout,
                "holdout_collection_hash": content_hash(holdout),
                "systems": systems,
                "repetitions": repetitions,
                "budget_usd": budget_usd,
                "development_budget_usd": development_budget,
                "holdout_budget_usd": holdout_budget,
                "known_cost_usd": 0.0,
                "cost_complete": False,
                "constraints": {"min_pass_rate": min_pass_rate, "max_p95_ms": max_p95_ms},
                "objective": "lowest observed development cost per passing attempt among eligible candidates",
                "projection": projection,
                "selected_system_id": None,
                "proposal": None,
                "limitations": [
                    "Finite explicit candidates only; no prompt generation, model training, or automatic deployment.",
                    "Selection is exploratory; the single final holdout checks fixed observed thresholds, not statistical superiority.",
                    "Reservations prevent supported experiment APIs from reusing holdout task IDs and exact public task content; trusted filesystem administrators can inspect private files.",
                    "No semantic contamination detector or guarantee against external model pretraining exposure.",
                ],
            }
            return self._view(connection, self.append(connection, value))

    @staticmethod
    def _view(connection: sqlite3.Connection, value: dict[str, Any]) -> dict[str, Any]:
        """Reflect late trial settlement without changing frozen study decisions."""
        known = []
        complete = value["status"] not in ACTIVE
        for experiment_id in (value["development_experiment_id"], value["holdout_experiment_id"]):
            if experiment_id is None:
                continue
            experiment = _experiment(connection, experiment_id)
            complete &= not experiment.get("budget_uncertain") and experiment["status"] not in {
                "queued",
                "running",
            }
            for trial in experiment["trials"]:
                cost = trial.get("cost_usd")
                valid = type(cost) in {int, float} and math.isfinite(cost) and cost >= 0
                complete &= valid
                if valid:
                    known.append(float(cost))
                else:
                    partial = trial.get("provider_metadata", {}).get("known_cost_usd")
                    if type(partial) in {int, float} and math.isfinite(partial) and partial >= 0:
                        known.append(float(partial))
        return {
            **value,
            "known_cost_usd": math.fsum(known),
            "cost_complete": complete,
            "accounting_basis": "Current retained trial charges, including late settlement and partial known costs; unknown charges remain unknown",
        }

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            value = self.read(connection, identifier)
            if value["kind"] != "optimization":
                raise KeyError(identifier)
            return self._view(connection, value)

    def list(self) -> list[dict[str, Any]]:
        return [self.get(value["id"]) for value in self.list_records("optimization")]

    @staticmethod
    def _eligible(row: dict[str, Any], constraints: dict[str, Any]) -> bool:
        return bool(
            row["eligible_for_frontier"]
            and row["completed"] == row["scheduled"]
            and row["pass_rate"] >= constraints["min_pass_rate"]
            and row["p95_duration_ms"] <= constraints["max_p95_ms"]
        )

    def advance(self, identifier: str) -> dict[str, Any]:
        with self.store._connect(write=True) as connection:
            value = self.read(connection, identifier)
            if value["kind"] != "optimization":
                raise KeyError(identifier)
            if value["status"] not in ACTIVE:
                return self._view(connection, value)
            development_phase = value["status"] == "development_queued"
            experiment_id = (
                value["development_experiment_id"]
                if development_phase
                else value["holdout_experiment_id"]
            )
            experiment = _experiment(connection, experiment_id)
            if experiment["status"] in {"queued", "running"}:
                return self._view(connection, value)
            economics = analyze(experiment)
            known = sum(row["known_cost_usd"] for row in economics["systems"])
            if development_phase:
                value["known_cost_usd"] = known
                value["development_evidence"] = economics
            else:
                value["known_cost_usd"] = value["development_known_cost_usd"] + known
                value["holdout_evidence"] = economics
            value["cost_complete"] = not experiment.get("budget_uncertain") and all(
                row["cost_complete"] for row in economics["systems"]
            )
            if experiment["status"] != "completed":
                value.update(
                    status="cancelled" if experiment["status"] == "cancelled" else "blocked",
                    reason="The evaluation phase did not complete; no further evaluation or proposal is admitted",
                )
                return self._view(connection, self.append(connection, value))
            if (
                not value["cost_complete"]
                or economics["evidence_issues"]
                or value["known_cost_usd"] > value["budget_usd"] + 1e-9
            ):
                value.update(
                    status="blocked",
                    reason="Unknown accounting, invalid evidence, or spend above the declared lifetime budget; no further evaluation is admitted",
                )
                return self._view(connection, self.append(connection, value))
            phase_cap = (
                value["development_budget_usd"]
                if development_phase
                else value["holdout_budget_usd"]
            )
            if known > phase_cap + 1e-9:
                value.update(
                    status="blocked", reason="Recorded spend exceeds the reserved phase budget"
                )
                return self._view(connection, self.append(connection, value))
            eligible = [
                row for row in economics["systems"] if self._eligible(row, value["constraints"])
            ]
            if development_phase:
                if not eligible:
                    value.update(
                        status="rejected",
                        reason="No development candidate satisfies complete evidence, quality and latency constraints",
                    )
                    return self._view(connection, self.append(connection, value))
                winner = min(
                    eligible,
                    key=lambda row: (
                        row["cost_per_pass"],
                        -row["pass_rate"],
                        row["p95_duration_ms"],
                        row["system_id"],
                    ),
                )
                system = next(s for s in value["systems"] if s["id"] == winner["system_id"])
                holdout_experiment = self.store._insert_experiment(
                    connection,
                    ExperimentCreate(
                        name=value["name"] + " · final holdout",
                        collection_id=value["holdout"]["id"],
                        system_ids=[system["id"]],
                        repetitions=value["repetitions"],
                        budget_usd=value["holdout_budget_usd"],
                    ),
                    value["holdout"],
                    [system],
                    value["development_experiment_id"],
                    holdout_reservation_id=identifier,
                )
                connection.execute(
                    "UPDATE optimization_holdouts SET consumed_experiment_id=? WHERE study_id=? AND consumed_experiment_id IS NULL",
                    (holdout_experiment, identifier),
                )
                value.update(
                    status="holdout_queued",
                    selected_system_id=system["id"],
                    selected_configuration_hash=content_hash(system),
                    holdout_experiment_id=holdout_experiment,
                    development_known_cost_usd=known,
                    cost_complete=False,
                )
            elif not eligible:
                value.update(
                    status="rejected",
                    reason="Selected configuration did not satisfy the fixed final holdout constraints; no alternate candidate is retried",
                )
            else:
                system = next(s for s in value["systems"] if s["id"] == value["selected_system_id"])
                proposal = {
                    "id": new_id(),
                    "study_id": identifier,
                    "status": "review_required",
                    "created_at": utc_now(),
                    "system": system,
                    "configuration_hash": content_hash(system),
                    "constraints": value["constraints"],
                    "development_experiment_id": value["development_experiment_id"],
                    "holdout_experiment_id": value["holdout_experiment_id"],
                    "development_evidence_hash": content_hash(value["development_evidence"]),
                    "holdout_evidence_hash": content_hash(value["holdout_evidence"]),
                    "known_cost_usd": value["known_cost_usd"],
                    "budget_usd": value["budget_usd"],
                    "deployment_performed": False,
                    "interpretation": "This frozen candidate met observed thresholds on the reserved holdout; review workload coverage and uncertainty before deployment.",
                }
                value.update(status="ready_for_review", proposal=proposal)
            return self._view(connection, self.append(connection, value))

    def tick(self, limit: int = 10) -> builtins.list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Optimizer tick limit must be 1–50")
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT e.record_id,e.data FROM research_events e JOIN "
                "(SELECT record_id,MAX(sequence) AS sequence FROM research_events WHERE kind='optimization' GROUP BY record_id) latest "
                "ON e.record_id=latest.record_id AND e.sequence=latest.sequence ORDER BY e.rowid"
            ).fetchall()
        result: builtins.list[dict[str, Any]] = []
        for row in rows:
            if json.loads(row["data"])["status"] not in ACTIVE:
                continue
            if len(result) >= limit:
                break
            try:
                result.append(self.advance(row["record_id"]))
            except (ValueError, KeyError, OSError) as exc:
                # Retain a visible failed admission rather than retrying a broken contract forever.
                with self.store._connect(write=True) as connection:
                    value = self.read(connection, row["record_id"])
                    if value["status"] in ACTIVE:
                        value.update(status="blocked", reason=str(exc)[:2000])
                        result.append(self._view(connection, self.append(connection, value)))
        return result

    def cancel(self, identifier: str) -> dict[str, Any]:
        value = self.get(identifier)
        if value["status"] not in ACTIVE:
            return value
        active = value["holdout_experiment_id"] or value["development_experiment_id"]
        self.store.cancel_experiment(active)
        with self.store._connect(write=True) as connection:
            value = self.read(connection, identifier)
            if value["status"] in ACTIVE:
                # A concurrent advance may have admitted the final holdout; cancel that too.
                current = value["holdout_experiment_id"] or value["development_experiment_id"]
                if current != active:
                    raise ValueError("Study advanced during cancellation; retry cancellation")
                value.update(
                    status="cancelled",
                    reason="Operator cancelled; holdout reservation remains retained",
                )
                self.append(connection, self._view(connection, value))
        return self.get(identifier)
