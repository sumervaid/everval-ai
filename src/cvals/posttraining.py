"""Frozen checkpoint lineage, budgeted evaluation and evidence-bound decisions.

Checkpoint digests and training provenance are declarations. This service does not
inspect model weights or attest what a remote endpoint actually deployed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from cvals import __version__
from cvals.audit import append_audit
from cvals.comparison import evidence_issues
from cvals.config import Settings
from cvals.models import ExperimentCreate, canonical_json, content_hash, new_id, utc_now
from cvals.posttraining_models import (
    CheckpointCreate,
    InvestigationCreate,
    MixCreate,
    MixValidation,
    QualificationCreate,
    TrainingRunCreate,
)
from cvals.reports import build_report
from cvals.store import EXPERIMENT_TERMINAL, Store

TABLES = {"training_runs", "training_checkpoints", "training_links", "training_records"}
CONTRACT_KEYS = ("harness_revision", "grader_revision", "sandbox_image_requested", "protocol")
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def _digest_record(row: sqlite3.Row | None, identifier: str) -> dict[str, Any]:
    if row is None:
        raise KeyError(identifier)
    value = json.loads(row["data"])
    if not isinstance(value, dict) or content_hash(value) != row["hash"]:
        raise ValueError("Training record integrity check failed")
    return dict(value)


def _read(db: sqlite3.Connection, table: str, identifier: str) -> dict[str, Any]:
    if table not in TABLES:
        raise ValueError("Unsupported training table")
    return _digest_record(
        db.execute(f"SELECT data,hash FROM {table} WHERE id=?", (identifier,)).fetchone(),
        identifier,
    )


def _experiment(db: sqlite3.Connection, identifier: str) -> dict[str, Any]:
    snapshot, state = Store._read_experiment(db, identifier)
    state["snapshot"] = snapshot
    state["trials"] = [
        json.loads(row[0])
        for row in db.execute(
            "SELECT data FROM trials WHERE experiment_id=? ORDER BY position", (identifier,)
        )
    ]
    return state


def _evidence_hash(experiment: dict[str, Any]) -> str:
    return content_hash(
        {
            "snapshot_hash": experiment["snapshot_hash"],
            "status": experiment["status"],
            "trials": experiment["trials"],
        }
    )


def _lag(created: str, finished: str | None) -> float:
    start = datetime.fromisoformat(created)
    end = datetime.fromisoformat(finished) if finished else datetime.now(UTC)
    return max(0.0, (end - start).total_seconds())


def _summary_run(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key != "collections"}


def _training_budget_health(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Retain full ceilings and fail closed after unknown or excess live charges."""
    allocated = []
    commitments = []
    uncertain = []
    overrun = []
    for row in db.execute(
        "SELECT l.data,l.hash,e.state,coalesce((SELECT sum(committed) FROM spend_ledger "
        "WHERE experiment_id=e.id),0) AS ledger_committed FROM training_links l "
        "JOIN experiments e ON e.id=l.experiment_id WHERE l.run_id=?",
        (run_id,),
    ):
        link = _digest_record(row, "allocation")
        state = json.loads(row["state"])
        ceiling = link["budget_usd"]
        committed = state.get("budget_committed_usd")
        if (
            isinstance(committed, bool)
            or not isinstance(committed, (int, float))
            or not math.isfinite(committed)
            or committed < 0
        ):
            raise ValueError("Training experiment accounting is invalid")
        ledger = row["ledger_committed"]
        if not isinstance(ledger, (int, float)) or not math.isfinite(ledger) or ledger < 0:
            raise ValueError("Training ledger accounting is invalid")
        committed = max(committed, ledger)
        allocated.append(ceiling)
        commitments.append(max(ceiling, committed))
        if state.get("budget_uncertain"):
            uncertain.append(link["experiment_id"])
        if committed > ceiling + 1e-12:
            overrun.append(link["experiment_id"])
    reasons = []
    if uncertain:
        reasons.append(
            "A linked evaluation has uncertain provider accounting; further live admission is blocked."
        )
    if overrun:
        reasons.append(
            "A linked evaluation exceeded its frozen allocation; further live admission is blocked."
        )
    return {
        "allocated_usd": math.fsum(allocated),
        "conservative_commitment_usd": math.fsum(commitments),
        "uncertain_experiment_ids": uncertain,
        "overrun_experiment_ids": overrun,
        "admission_blocked": bool(reasons),
        "blocking_reasons": reasons,
    }


def training_budget_block(db: sqlite3.Connection, experiment_id: str) -> str | None:
    """Store HTTP-admission hook, within the caller's existing transaction.

    A previously queued checkpoint cannot bypass accounting uncertainty discovered
    while an earlier checkpoint was executing. Legacy workspaces are unaffected.
    """
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_links'"
    ).fetchone():
        return None
    row = db.execute(
        "SELECT run_id FROM training_links WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if row is None:
        return None
    health = _training_budget_health(db, row["run_id"])
    return " ".join(health["blocking_reasons"]) if health["admission_blocked"] else None


class PostTraining:
    def __init__(self, settings: Settings, store: Store | None = None):
        self.settings = settings
        self.store = store or Store(settings)
        self._tick_cursor = 0
        self.store.init()
        with self.store._connect(write=True) as db:
            db.execute(
                "CREATE INDEX IF NOT EXISTS spend_ledger_experiment ON spend_ledger(experiment_id)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS training_runs(
                id TEXT PRIMARY KEY, training_run_id TEXT NOT NULL UNIQUE,
                data TEXT NOT NULL, hash TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS training_checkpoints(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES training_runs(id),
                step INTEGER NOT NULL, digest TEXT NOT NULL,
                data TEXT NOT NULL, hash TEXT NOT NULL,
                UNIQUE(run_id,step), UNIQUE(run_id,digest))""")
            db.execute("""CREATE TABLE IF NOT EXISTS training_links(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES training_runs(id),
                checkpoint_id TEXT NOT NULL REFERENCES training_checkpoints(id),
                domain TEXT NOT NULL, experiment_id TEXT NOT NULL UNIQUE REFERENCES experiments(id),
                data TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(checkpoint_id,domain))""")
            db.execute("""CREATE TABLE IF NOT EXISTS training_records(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES training_runs(id),
                kind TEXT NOT NULL, record_key TEXT NOT NULL,
                data TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(run_id,kind,record_key))""")
            for table in TABLES:
                for operation in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                        f"BEFORE {operation} ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'Training declarations and evidence are immutable'); END"
                    )

    @staticmethod
    def _record(
        db: sqlite3.Connection,
        run_id: str,
        kind: str,
        key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        value = {"id": new_id(), "run_id": run_id, "kind": kind, "created_at": utc_now(), **payload}
        if len(canonical_json(value).encode()) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Training record exceeds 16 MiB")
        db.execute(
            "INSERT INTO training_records VALUES(?,?,?,?,?,?)",
            (value["id"], run_id, kind, key, canonical_json(value), content_hash(value)),
        )
        append_audit(
            db, "training." + kind, value["id"], {"run_id": run_id, "hash": content_hash(value)}
        )
        return value

    @staticmethod
    def _existing(
        db: sqlite3.Connection,
        run_id: str,
        kind: str,
        key: str,
    ) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT data,hash FROM training_records WHERE run_id=? AND kind=? AND record_key=?",
            (run_id, kind, key),
        ).fetchone()
        return _digest_record(row, key) if row else None

    @staticmethod
    def _records(db: sqlite3.Connection, run_id: str, kind: str) -> list[dict[str, Any]]:
        return [
            _digest_record(row, kind)
            for row in db.execute(
                "SELECT data,hash FROM training_records WHERE run_id=? AND kind=? ORDER BY rowid",
                (run_id, kind),
            )
        ]

    def create_run(self, request: TrainingRunCreate | dict[str, Any]) -> dict[str, Any]:
        request = TrainingRunCreate.model_validate(request)
        declaration = request.model_dump(mode="json")
        with self.store._connect(write=True) as db:
            previous = db.execute(
                "SELECT data,hash FROM training_runs WHERE training_run_id=?",
                (request.training_run_id,),
            ).fetchone()
            if previous:
                value = _digest_record(previous, request.training_run_id)
                if value["declaration_hash"] != content_hash(declaration):
                    raise ValueError(
                        "Training run identity is already bound to a different protocol"
                    )
                return _summary_run(value)
            from cvals.benchmarks import assert_collection_admitted
            from cvals.optimization import assert_holdout_available

            collections = {}
            for domain in request.protocol.domains:
                collection = Store._get(db, "collections", domain.collection_id)
                if collection.get("benchmark_partition") in {"holdout", "all"}:
                    raise ValueError(
                        "Continuous monitoring requires development collections, not holdouts"
                    )
                assert_collection_admitted(db, collection)
                assert_holdout_available(db, collection)
                if set(domain.task_families) != {s["id"] for s in collection["scenarios"]}:
                    raise ValueError("Task families must cover every frozen scenario exactly")
                if len(collection["scenarios"]) * request.protocol.repetitions > 10000:
                    raise ValueError("Domain exceeds the 10000 trial limit")
                collections[domain.name] = collection
            value = {
                "id": new_id(),
                "created_at": utc_now(),
                **declaration,
                "declaration_hash": content_hash(declaration),
                "collections": collections,
                "collection_hashes": {name: content_hash(c) for name, c in collections.items()},
                "weight_verification": "declared; not independently verified",
                "analysis_version": "paired-family-hoeffding/1",
                "execution_contract": {
                    "harness_revision": f"everval/{__version__}",
                    "grader_revision": "deterministic/1",
                    "sandbox_image_requested": self.settings.sandbox_image,
                    "protocol": "all scheduled trials; no automatic provider retries",
                },
                "status": "registered",
            }
            if len(canonical_json(value).encode()) > MAX_SNAPSHOT_BYTES:
                raise ValueError("Frozen training protocol exceeds 16 MiB")
            db.execute(
                "INSERT INTO training_runs VALUES(?,?,?,?)",
                (value["id"], request.training_run_id, canonical_json(value), content_hash(value)),
            )
            append_audit(
                db,
                "training.run_created",
                value["id"],
                {
                    "declaration_hash": value["declaration_hash"],
                    "provenance_kind": value["provenance_kind"],
                },
            )
            return _summary_run(value)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            return _summary_run(_read(db, "training_runs", run_id))

    def list_runs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1000000
        ):
            raise ValueError("Run pagination requires limit 1–100 and offset 0–1000000")
        with self.store._connect() as db:
            values = []
            for row in db.execute(
                "SELECT data,hash FROM training_runs ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ):
                run = _digest_record(row, "run")
                count = db.execute(
                    "SELECT COUNT(*) FROM training_checkpoints WHERE run_id=?", (run["id"],)
                ).fetchone()[0]
                values.append(
                    {
                        **{
                            key: run[key]
                            for key in (
                                "id",
                                "name",
                                "training_run_id",
                                "provenance_kind",
                                "created_at",
                                "status",
                            )
                        },
                        "checkpoint_count": count,
                    }
                )
            return values

    def register_checkpoint(
        self,
        run_id: str,
        request: CheckpointCreate | dict[str, Any],
    ) -> dict[str, Any]:
        request = CheckpointCreate.model_validate(request)
        declaration = request.model_dump(mode="json")
        with self.store._connect(write=True) as db:
            run = _read(db, "training_runs", run_id)
            if request.step not in [
                run["protocol"]["baseline_step"],
                *run["protocol"]["comparison_steps"],
            ]:
                raise ValueError("Checkpoint step is outside the frozen comparison grid")
            previous = db.execute(
                "SELECT data,hash FROM training_checkpoints WHERE run_id=? AND step=?",
                (run_id, request.step),
            ).fetchone()
            if previous:
                value = _digest_record(previous, str(request.step))
                if value["declaration_hash"] != content_hash(declaration):
                    raise ValueError("Checkpoint step is already bound to a different declaration")
                return self._public_checkpoint(value)
            if db.execute(
                "SELECT 1 FROM training_checkpoints WHERE run_id=? AND digest=?",
                (run_id, request.weight_sha256),
            ).fetchone():
                raise ValueError("Weight digest is already registered at another step")
            system = Store._get(db, "systems", request.system_id)
            if content_hash(system) != request.expected_system_hash:
                raise ValueError(
                    "System configuration changed or expected_system_hash is incorrect"
                )
            if system["kind"] == "replay":
                raise ValueError("Replayed submissions are not independent checkpoint evaluations")
            if run["provenance_kind"] == "declared_training":
                if system["kind"] != "http":
                    raise ValueError("Declared training checkpoints require a live HTTP system")
                revision = (
                    system["adapter_revision"]
                    if system["harness"] == "external-agent"
                    else system["model"]
                )
                if request.revision != revision:
                    raise ValueError(
                        "Checkpoint revision must match the frozen system deployment contract"
                    )
            value = {
                "id": new_id(),
                "run_id": run_id,
                "created_at": utc_now(),
                **declaration,
                "declaration_hash": content_hash(declaration),
                "system": system,
                "system_hash": content_hash(system),
                "weight_verification": "declared; not independently verified",
            }
            db.execute(
                "INSERT INTO training_checkpoints VALUES(?,?,?,?,?,?)",
                (
                    value["id"],
                    run_id,
                    request.step,
                    request.weight_sha256,
                    canonical_json(value),
                    content_hash(value),
                ),
            )
            append_audit(
                db,
                "training.checkpoint_registered",
                value["id"],
                {"run_id": run_id, "step": request.step, "system_hash": value["system_hash"]},
            )
            return self._public_checkpoint(value)

    @staticmethod
    def _public_checkpoint(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if key != "system"}

    @staticmethod
    def _checkpoint(db: sqlite3.Connection, run_id: str, checkpoint_id: str) -> dict[str, Any]:
        value = _read(db, "training_checkpoints", checkpoint_id)
        if value["run_id"] != run_id:
            raise ValueError("Checkpoint belongs to another training run")
        return value

    @staticmethod
    def _links(db: sqlite3.Connection, checkpoint_id: str) -> list[dict[str, Any]]:
        return [
            _digest_record(row, "link")
            for row in db.execute(
                "SELECT data,hash FROM training_links WHERE checkpoint_id=? ORDER BY rowid",
                (checkpoint_id,),
            )
        ]

    def queue_checkpoint(self, run_id: str, checkpoint_id: str) -> dict[str, Any]:
        with self.store._connect(write=True) as db:
            run = _read(db, "training_runs", run_id)
            checkpoint = self._checkpoint(db, run_id, checkpoint_id)
            previous = self._links(db, checkpoint_id)
            if previous:
                if len(previous) != len(run["protocol"]["domains"]):
                    raise ValueError("Checkpoint queue integrity check failed")
                return {"checkpoint_id": checkpoint_id, "experiments": previous, "idempotent": True}
            protocol = run["protocol"]
            allocation = protocol["budget_per_domain_usd"] * len(protocol["domains"])
            health = _training_budget_health(db, run_id)
            if health["admission_blocked"] and checkpoint["system"]["kind"] == "http":
                raise ValueError(" ".join(health["blocking_reasons"]))
            allocated = health["allocated_usd"]
            if allocated + allocation > protocol["lifetime_budget_usd"] + 1e-10:
                raise ValueError(
                    "Training lifetime budget is fully allocated; protocol is immutable"
                )
            values = []
            for domain in protocol["domains"]:
                collection = run["collections"][domain["name"]]
                request = ExperimentCreate(
                    name=f"{run['name'][:100]} · step {checkpoint['step']} · {domain['name'][:50]}",
                    collection_id=collection["id"],
                    system_ids=[checkpoint["system_id"]],
                    repetitions=protocol["repetitions"],
                    budget_usd=protocol["budget_per_domain_usd"],
                )
                experiment_id = self.store._insert_experiment(
                    db, request, collection, [checkpoint["system"]]
                )
                snapshot, _ = Store._read_experiment(db, experiment_id)
                if any(
                    snapshot.get(key) != run["execution_contract"][key] for key in CONTRACT_KEYS
                ):
                    raise ValueError(
                        "Runtime execution contract changed after protocol registration"
                    )
                value = {
                    "id": new_id(),
                    "run_id": run_id,
                    "checkpoint_id": checkpoint_id,
                    "domain": domain["name"],
                    "experiment_id": experiment_id,
                    "snapshot_hash": content_hash(snapshot),
                    "budget_usd": request.budget_usd,
                    "created_at": utc_now(),
                    "contract": {key: snapshot[key] for key in CONTRACT_KEYS},
                }
                db.execute(
                    "INSERT INTO training_links VALUES(?,?,?,?,?,?,?)",
                    (
                        value["id"],
                        run_id,
                        checkpoint_id,
                        domain["name"],
                        experiment_id,
                        canonical_json(value),
                        content_hash(value),
                    ),
                )
                values.append(value)
            append_audit(
                db,
                "training.checkpoint_queued",
                checkpoint_id,
                {
                    "run_id": run_id,
                    "allocated_usd": allocation,
                    "experiment_ids": [v["experiment_id"] for v in values],
                },
            )
            return {"checkpoint_id": checkpoint_id, "experiments": values, "idempotent": False}

    @staticmethod
    def _linked_experiment(
        db: sqlite3.Connection,
        run: dict[str, Any],
        checkpoint: dict[str, Any],
        link: dict[str, Any],
    ) -> dict[str, Any]:
        experiment = _experiment(db, link["experiment_id"])
        snapshot = experiment["snapshot"]
        if (
            experiment["snapshot_hash"] != link["snapshot_hash"]
            or snapshot["collection"] != run["collections"][link["domain"]]
            or snapshot["systems"] != [checkpoint["system"]]
            or any(snapshot.get(key) != link["contract"][key] for key in CONTRACT_KEYS)
        ):
            raise ValueError("Linked experiment lineage or execution contract changed")
        issues = evidence_issues(experiment)
        if issues:
            raise ValueError("Linked experiment evidence is invalid: " + "; ".join(issues))
        return experiment

    def analyze(self, run_id: str, candidate_checkpoint_id: str) -> dict[str, Any]:
        from cvals.posttraining_analysis import analyze_regression

        with self.store._connect(write=True) as db:
            run = _read(db, "training_runs", run_id)
            protocol = run["protocol"]
            candidate = self._checkpoint(db, run_id, candidate_checkpoint_id)
            if candidate["step"] not in protocol["comparison_steps"]:
                raise ValueError("Only a predeclared candidate step can be compared")
            baseline_row = db.execute(
                "SELECT data,hash FROM training_checkpoints WHERE run_id=? AND step=?",
                (run_id, protocol["baseline_step"]),
            ).fetchone()
            baseline = _digest_record(baseline_row, "baseline checkpoint")
            links = {
                cp["id"]: {x["domain"]: x for x in self._links(db, cp["id"])}
                for cp in (baseline, candidate)
            }
            experiments = {}
            for cp in (baseline, candidate):
                if len(links[cp["id"]]) != len(protocol["domains"]):
                    raise ValueError("Queue baseline and candidate across all frozen domains first")
                for domain in protocol["domains"]:
                    experiment = self._linked_experiment(
                        db, run, cp, links[cp["id"]][domain["name"]]
                    )
                    if experiment["status"] not in EXPERIMENT_TERMINAL:
                        raise ValueError(
                            "Wait for all baseline and candidate evaluations to finish"
                        )
                    experiments[(cp["id"], domain["name"])] = experiment
            evidence = {e["id"]: _evidence_hash(e) for e in experiments.values()}
            existing = self._existing(db, run_id, "comparison", candidate_checkpoint_id)
            if existing:
                if existing["evidence_hashes"] != evidence:
                    raise ValueError(
                        "Evidence changed after this decision; retained comparison is stale"
                    )
                return existing
            looks = len(self._records(db, run_id, "comparison"))
            if looks >= protocol["max_looks"]:
                raise ValueError("The predeclared testing budget is exhausted")
            results = []
            qualifications = {
                record["domain"]: record for record in self._records(db, run_id, "qualification")
            }
            for domain in protocol["domains"]:
                left = experiments[(baseline["id"], domain["name"])]
                right = experiments[(candidate["id"], domain["name"])]
                expected = [
                    {
                        "scenario_id": scenario["id"],
                        "repetition": repetition,
                        "cluster_id": domain["task_families"][scenario["id"]],
                    }
                    for scenario in run["collections"][domain["name"]]["scenarios"]
                    for repetition in range(1, protocol["repetitions"] + 1)
                ]

                def rows(
                    experiment: dict[str, Any], families: dict[str, str] = domain["task_families"]
                ) -> list[dict[str, Any]]:
                    return [
                        {
                            "scenario_id": t["scenario_id"],
                            "repetition": t["repetition"],
                            "cluster_id": families[t["scenario_id"]],
                            "status": t["status"],
                            "score": float(t["passed"]) if t["status"] == "completed" else None,
                        }
                        for t in experiment["trials"]
                    ]

                result = analyze_regression(
                    baseline=rows(left),
                    candidate=rows(right),
                    expected_pairs=expected,
                    alpha=protocol["alpha"],
                    planned_looks=protocol["max_looks"],
                    planned_domains=len(protocol["domains"]),
                    min_clusters=protocol["min_clusters"],
                    practical_threshold=domain["practical_threshold"],
                    look_index=looks + 1,
                )
                incompatible = [
                    key for key in CONTRACT_KEYS if left["snapshot"][key] != right["snapshot"][key]
                ]
                images = [
                    sorted(
                        {
                            c["actual"]["image_digest"]
                            for t in e["trials"]
                            for c in t.get("checks", [])
                            if isinstance(c.get("actual"), dict)
                            and isinstance(c["actual"].get("image_digest"), str)
                        }
                    )
                    for e in (left, right)
                ]
                allowed_changes = {"id", "name", "model", "adapter_revision", "provenance"}
                if run["provenance_kind"] == "synthetic_control":
                    allowed_changes.add("kind")
                if {k: v for k, v in baseline["system"].items() if k not in allowed_changes} != {
                    k: v for k, v in candidate["system"].items() if k not in allowed_changes
                }:
                    incompatible.append("inference_configuration")
                if images[0] != images[1]:
                    incompatible.append("observed_verifier_images")
                qualification = qualifications.get(domain["name"])
                result["qualification"] = {
                    "record_id": qualification["id"] if qualification else None,
                    "record_hash": content_hash(qualification) if qualification else None,
                    "status": qualification["result"]["status"]
                    if qualification
                    else "not_supplied",
                    "automatic_trust": False,
                    "origin": "user supplied evidence; independent scientific review remains necessary",
                }
                result["measurement_review_required"] = True
                result["inference_scope"] = protocol.get("inference_scope", "exploratory")
                result.update(
                    domain=domain["name"],
                    baseline_experiment_id=left["id"],
                    candidate_experiment_id=right["id"],
                    execution_contract_issues=incompatible,
                    synthetic_control=run["provenance_kind"] == "synthetic_control",
                )
                if incompatible:
                    result["decision"] = "invalid"
                    result["valid"] = False
                    result["reasons"].append(
                        "Execution conditions differ; do not attribute the change to training."
                    )
                results.append(result)
            return self._record(
                db,
                run_id,
                "comparison",
                candidate_checkpoint_id,
                {
                    "baseline_checkpoint_id": baseline["id"],
                    "candidate_checkpoint_id": candidate["id"],
                    "baseline_step": baseline["step"],
                    "candidate_step": candidate["step"],
                    "look_index": looks + 1,
                    "max_looks": protocol["max_looks"],
                    "family_alpha": protocol["alpha"],
                    "inference_scope": protocol.get("inference_scope", "exploratory"),
                    "independence_statement": protocol.get("independence_statement", ""),
                    "allocated_alpha": protocol["alpha"]
                    / protocol["max_looks"]
                    / len(protocol["domains"]),
                    "evidence_hashes": evidence,
                    "domains": results,
                    "synthetic_control": run["provenance_kind"] == "synthetic_control",
                    "analysis_version": run["analysis_version"],
                    "limitations": [
                        "Independent representative task families are an assumption, not established by grouping.",
                        "Checkpoint and deployment provenance are declarations, not independent weight attestation.",
                        "Repeated-look control is scoped to this frozen run; independent runs are separate families.",
                        "Checkpoint tuning or selection on these same tasks can invalidate inferential coverage; exploratory monitoring is the default.",
                    ],
                },
            )

    def investigate(
        self,
        run_id: str,
        request: InvestigationCreate | dict[str, Any],
    ) -> dict[str, Any]:
        request = InvestigationCreate.model_validate(request)
        payload = request.model_dump(mode="json")
        with self.store._connect(write=True) as db:
            _read(db, "training_runs", run_id)
            comparison = _read(db, "training_records", request.comparison_id)
            if comparison["run_id"] != run_id or comparison["kind"] != "comparison":
                raise ValueError("Investigation must reference this run's comparison")
            stale = self._stale(db, comparison)
            if stale:
                raise ValueError("Comparison evidence is stale; do not attach a new recommendation")
            regrades = []
            if request.regrade_record_ids:
                from cvals.research import ResearchRecords

                for identifier in request.regrade_record_ids:
                    record = ResearchRecords.read(db, identifier)
                    if record.get("experiment_id") not in comparison["evidence_hashes"]:
                        raise ValueError("Controlled regrade must reference comparison evidence")
                    if (
                        record.get("kind") != "repeated_grading"
                        or record.get("status") != "completed"
                    ):
                        raise ValueError("Only completed repeat-regrade evidence can be linked")
                    regrades.append({"id": identifier, "hash": content_hash(record)})
            key = content_hash(payload)
            previous = self._existing(db, run_id, "investigation", key)
            if previous:
                return previous
            return self._record(
                db,
                run_id,
                "investigation",
                key,
                {
                    **payload,
                    "comparison_hash": content_hash(comparison),
                    "evidence_hashes": comparison["evidence_hashes"],
                    "regrades": regrades,
                    "attribution": "Hypotheses and recommendations are author judgments; causality is not established.",
                },
            )

    @staticmethod
    def _stale(db: sqlite3.Connection, comparison: dict[str, Any]) -> bool:
        return any(
            _evidence_hash(_experiment(db, key)) != value
            for key, value in comparison["evidence_hashes"].items()
        )

    def qualify(
        self,
        run_id: str,
        request: QualificationCreate | dict[str, Any],
    ) -> dict[str, Any]:
        from cvals.posttraining_qualification import QualificationEvidence, qualify_evaluation

        request = QualificationCreate.model_validate(request)
        evidence = QualificationEvidence.model_validate(request.evidence).model_dump(mode="json")
        result = qualify_evaluation(evidence)
        with self.store._connect(write=True) as db:
            run = _read(db, "training_runs", run_id)
            if request.domain not in run["collections"]:
                raise ValueError("Qualification domain is not in the frozen protocol")
            if evidence["task_version"] != run["collections"][request.domain]["version"]:
                raise ValueError("Qualification task_version must match the frozen collection")
            if evidence["grader_version"] != run["execution_contract"]["grader_revision"]:
                raise ValueError(
                    "Qualification grader_version must match the frozen execution contract"
                )
            payload = {
                "domain": request.domain,
                "collection_hash": run["collection_hashes"][request.domain],
                "evidence": evidence,
                "result": result,
                "evidence_origin": "user supplied; external judgments and controls are not independently verified",
            }
            key = content_hash(payload)
            previous = self._existing(db, run_id, "qualification", key)
            return previous or self._record(db, run_id, "qualification", key, payload)

    def select_mix(self, run_id: str, request: MixCreate | dict[str, Any]) -> dict[str, Any]:
        from cvals.posttraining_mix import select_mix

        request = MixCreate.model_validate(request)
        result = select_mix(request.development, request.config)
        development_checkpoints = set(result["development_checkpoints"])
        development_families = set(result["development_task_families"])
        development_evals = result["domains"]
        manifest = [row.model_dump(mode="json") for row in request.validation_manifest]
        keys = [(r["checkpoint_id"], r["task_family"], r["eval_id"]) for r in manifest]
        if len(keys) != len(set(keys)):
            raise ValueError("Validation manifest contains duplicate identities")
        if any(
            r["checkpoint_id"] in development_checkpoints
            or r["task_family"] in development_families
            for r in manifest
        ):
            raise ValueError(
                "Validation checkpoints and families must be withheld from development"
            )
        if any(development_evals.get(r["eval_id"]) != r["domain"] for r in manifest):
            raise ValueError("Validation evaluations and domains must match development")
        if {r["eval_id"] for r in manifest} != set(development_evals):
            raise ValueError("Validation manifest must include every frozen full-suite evaluation")
        families: dict[str, set[str]] = defaultdict(set)
        for row in manifest:
            families[row["eval_id"]].add(row["task_family"])
        expected_grid = {
            (cp, family, evaluation)
            for cp in {row["checkpoint_id"] for row in manifest}
            for evaluation, names in families.items()
            for family in names
        }
        if set(keys) != expected_grid:
            raise ValueError(
                "Validation manifest must freeze a complete checkpoint/evaluation/family grid"
            )
        payload = request.model_dump(mode="json")
        with self.store._connect(write=True) as db:
            _read(db, "training_runs", run_id)
            key = content_hash(payload)
            previous = self._existing(db, run_id, "mix_selection", key)
            if previous:
                return previous
            validation_checkpoints = {row["checkpoint_id"] for row in manifest}
            validation_families = {row["task_family"] for row in manifest}
            for earlier in self._records(db, run_id, "mix_selection"):
                prior_checkpoints = {row["checkpoint_id"] for row in earlier["validation_manifest"]}
                prior_families = {row["task_family"] for row in earlier["validation_manifest"]}
                if (development_checkpoints | validation_checkpoints) & prior_checkpoints or (
                    development_families | validation_families
                ) & prior_families:
                    raise ValueError(
                        "Validation checkpoints and families are reserved by another frozen study"
                    )
                if validation_checkpoints & set(
                    earlier["selection"]["development_checkpoints"]
                ) or (validation_families & set(earlier["selection"]["development_task_families"])):
                    raise ValueError(
                        "Previously observed development data cannot become a withheld validation set"
                    )
            return self._record(
                db,
                run_id,
                "mix_selection",
                key,
                {
                    **payload,
                    "selection": result,
                    "evidence_origin": "user supplied development observations; not independently measured by this endpoint",
                },
            )

    def validate_mix(
        self,
        run_id: str,
        selection_id: str,
        request: MixValidation | dict[str, Any],
    ) -> dict[str, Any]:
        from cvals.posttraining_mix import evaluate_mix

        request = MixValidation.model_validate(request)
        with self.store._connect(write=True) as db:
            selection = _read(db, "training_records", selection_id)
            if selection["run_id"] != run_id or selection["kind"] != "mix_selection":
                raise ValueError("Mix selection must belong to this run")
            previous = self._existing(db, run_id, "mix_validation", selection_id)
            if previous:
                if previous["validation_hash"] != content_hash(request.validation):
                    raise ValueError("This selection's validation is already frozen")
                return previous
            expected = {
                (r["checkpoint_id"], r["task_family"], r["eval_id"], r["domain"])
                for r in selection["validation_manifest"]
            }
            observed = {
                (r.get("checkpoint_id"), r.get("task_family"), r.get("eval_id"), r.get("domain"))
                for r in request.validation
            }
            if expected != observed or len(observed) != len(request.validation):
                raise ValueError("Validation rows must cover the complete frozen withheld manifest")
            result = evaluate_mix(selection["selection"], request.validation)
            return self._record(
                db,
                run_id,
                "mix_validation",
                selection_id,
                {
                    "selection_id": selection_id,
                    "selection_hash": content_hash(selection),
                    "validation_hash": content_hash(request.validation),
                    "validation": request.validation,
                    "result": result,
                },
            )

    def tick(self) -> list[dict[str, Any]]:
        """Inspect at most 32 pending candidates and analyze at most four per tick.

        The cursor rotates over pending candidates, so an unqueued early step
        cannot starve later ready work. No inference or experiment admission occurs.
        """
        outcomes: list[dict[str, Any]] = []
        with self.store._connect() as db:
            query = (
                "SELECT c.rowid,c.id,c.run_id FROM training_checkpoints c "
                "WHERE c.rowid {operator} ? AND NOT EXISTS("
                "SELECT 1 FROM training_records r WHERE r.run_id=c.run_id "
                "AND r.kind='comparison' AND r.record_key=c.id) ORDER BY c.rowid LIMIT ?"
            )
            selected = db.execute(query.format(operator=">"), (self._tick_cursor, 32)).fetchall()
            if len(selected) < 32:
                selected += db.execute(
                    query.format(operator="<="), (self._tick_cursor, 32 - len(selected))
                ).fetchall()
        if not selected:
            return outcomes
        self._tick_cursor = selected[-1][0]
        ready = []
        with self.store._connect() as db:
            runs = {}
            for row in selected:
                _, checkpoint_id, run_id = row
                if run_id not in runs:
                    runs[run_id] = _read(db, "training_runs", run_id)
                run = runs[run_id]
                baseline = db.execute(
                    "SELECT id FROM training_checkpoints WHERE run_id=? AND step=?",
                    (run_id, run["protocol"]["baseline_step"]),
                ).fetchone()
                if baseline is None or baseline[0] == checkpoint_id:
                    continue
                links = self._links(db, baseline[0]) + self._links(db, checkpoint_id)
                if len(links) != 2 * len(run["protocol"]["domains"]):
                    continue
                statuses = [
                    db.execute(
                        "SELECT status FROM experiments WHERE id=?", (link["experiment_id"],)
                    ).fetchone()[0]
                    for link in links
                ]
                if all(status in EXPERIMENT_TERMINAL for status in statuses):
                    ready.append((run_id, checkpoint_id))
                    if len(ready) == 4:
                        break
        for run_id, checkpoint_id in ready:
            try:
                outcomes.append(self.analyze(run_id, checkpoint_id))
            except (ValueError, KeyError) as exc:
                outcomes.append(
                    {"run_id": run_id, "checkpoint_id": checkpoint_id, "error": str(exc)[:1000]}
                )
        return outcomes

    def dashboard(self, run_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            # A single read transaction keeps queue states and evidence internally consistent.
            db.execute("BEGIN")
            run = _read(db, "training_runs", run_id)
            checkpoints = []
            allocated = 0.0
            for row in db.execute(
                "SELECT data,hash FROM training_checkpoints WHERE run_id=? ORDER BY step", (run_id,)
            ):
                cp = _digest_record(row, "checkpoint")
                links = {x["domain"]: x for x in self._links(db, cp["id"])}
                metrics = []
                for domain in run["protocol"]["domains"]:
                    expected = (
                        len(run["collections"][domain["name"]]["scenarios"])
                        * run["protocol"]["repetitions"]
                    )
                    metric = {
                        "domain": domain["name"],
                        "experiment_id": None,
                        "status": "not_queued",
                        "value": None,
                        "lower": None,
                        "upper": None,
                        "n": 0,
                        "expected_n": expected,
                        "scheduled_pass_rate": None,
                        "known_cost_usd": 0.0,
                        "cost_complete": False,
                        "unknown_cost_count": expected,
                        "errors": 0,
                        "missing": expected,
                        "lag_seconds": _lag(cp["created_at"], None),
                        "interval_method": "task-family Hoeffding 95% (descriptive; not alert threshold)",
                    }
                    link = links.get(domain["name"])
                    if link:
                        allocated += link["budget_usd"]
                        experiment = self._linked_experiment(db, run, cp, link)
                        report = build_report(experiment)["systems"][0]
                        groups: dict[str, list[float]] = defaultdict(list)
                        complete = report["completed"] == expected
                        for trial in experiment["trials"]:
                            if trial["status"] == "completed":
                                groups[domain["task_families"][trial["scenario_id"]]].append(
                                    float(trial["passed"])
                                )
                        values = [mean(values) for values in groups.values()]
                        value = mean(values) if complete and values else None
                        radius = math.sqrt(math.log(40) / (2 * len(values))) if values else 0.0
                        metric.update(
                            experiment_id=experiment["id"],
                            status=experiment["status"],
                            n=report["completed"],
                            value=value,
                            lower=max(0.0, value - radius) if value is not None else None,
                            upper=min(1.0, value + radius) if value is not None else None,
                            scheduled_pass_rate=report["pass_rate"],
                            known_cost_usd=report["known_cost_usd"],
                            cost_complete=report["cost_complete"],
                            unknown_cost_count=sum(
                                t.get("cost_usd") is None for t in experiment["trials"]
                            ),
                            errors=report["errors"],
                            missing=expected - report["completed"],
                            lag_seconds=_lag(cp["created_at"], experiment["finished_at"]),
                            snapshot_hash=experiment["snapshot_hash"],
                        )
                    metrics.append(metric)
                statuses = {m["status"] for m in metrics}
                status = (
                    next(iter(statuses))
                    if len(statuses) == 1
                    else "running"
                    if statuses & {"queued", "running"}
                    else "partial"
                )
                checkpoints.append(
                    {
                        **self._public_checkpoint(cp),
                        "status": status,
                        "metrics": metrics,
                        "known_cost_usd": math.fsum(m["known_cost_usd"] for m in metrics),
                        "unknown_cost_count": sum(m["unknown_cost_count"] for m in metrics),
                        "lag_seconds": max(m["lag_seconds"] for m in metrics),
                    }
                )
            comparisons = self._records(db, run_id, "comparison")
            for comparison in comparisons:
                comparison["stale"] = self._stale(db, comparison)
            investigations = self._records(db, run_id, "investigation")
            for investigation in investigations:
                investigation["stale"] = self._stale(db, investigation)
                if investigation.get("regrades"):
                    from cvals.research import ResearchRecords

                    investigation["stale"] = investigation["stale"] or any(
                        content_hash(ResearchRecords.read(db, entry["id"])) != entry["hash"]
                        for entry in investigation["regrades"]
                    )
            alerts = [
                {
                    "comparison_id": comparison["id"],
                    "candidate_step": comparison["candidate_step"],
                    "domain": result["domain"],
                    "decision": result.get("decision"),
                    "measurement_review_required": True,
                    "inference_scope": comparison.get("inference_scope", "exploratory"),
                    "qualification": result.get(
                        "qualification",
                        {"record_id": None, "status": "not_supplied", "automatic_trust": False},
                    ),
                    "synthetic_control": comparison["synthetic_control"],
                }
                for comparison in comparisons
                if not comparison["stale"]
                for result in comparison["domains"]
                if result.get("decision") == "regression"
            ]
            return {
                "schema_version": 1,
                "generated_at": utc_now(),
                "run": _summary_run(run),
                "checkpoints": checkpoints,
                "comparisons": comparisons,
                "alerts": alerts,
                "investigations": investigations,
                "qualifications": self._records(db, run_id, "qualification"),
                "mix_studies": self._records(db, run_id, "mix_selection"),
                "mix_validations": self._records(db, run_id, "mix_validation"),
                "budget": {
                    **_training_budget_health(db, run_id),
                    "allocated_usd": allocated,
                    "lifetime_budget_usd": run["protocol"]["lifetime_budget_usd"],
                    "remaining_allocation_usd": max(
                        0, run["protocol"]["lifetime_budget_usd"] - allocated
                    ),
                    "known_cost_usd": math.fsum(cp["known_cost_usd"] for cp in checkpoints),
                    "unknown_cost_count": sum(cp["unknown_cost_count"] for cp in checkpoints),
                    "allocation_policy": "Full experiment ceilings remain allocated; no automatic recycling.",
                },
            }
