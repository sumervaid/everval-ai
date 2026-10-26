"""Locked blind studies under a trusted local custodian.

This workflow blinds reviewers and summary readers. It does not hide prompts
from a hosted inference provider or from the workspace's filesystem owner.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.models import Manifest, canonical_json, content_hash, new_id, utc_now
from cvals.review_queues import QueueRequest, ReviewQueues

BOUNDARY = (
    "Trusted-custodian review of completed outputs. Reviewers and summary readers "
    "do not receive model identities before reveal. Administrators, operators, "
    "the filesystem owner, and inference providers are outside this blinding boundary. "
    "Task, rubric, and response wording may reveal identity. Hardware-backed confidential "
    "execution and proof of no previous exposure are not provided."
)


class StudyRequest(Manifest):
    name: str = Field(min_length=1, max_length=200)
    experiment_id: str = Field(min_length=1, max_length=128)
    rubric_id: str = Field(min_length=1, max_length=128)
    reviewer_ids: list[str] = Field(min_length=2, max_length=50)
    system_ids: list[str] = Field(min_length=2, max_length=2)
    mode: Literal["trusted_custodian"] = "trusted_custodian"

    @model_validator(mode="after")
    def distinct(self) -> StudyRequest:
        if not self.name.strip():
            raise ValueError("Give the study a name")
        if len(set(self.reviewer_ids)) != len(self.reviewer_ids):
            raise ValueError("Use distinct reviewer identities")
        if len(set(self.system_ids)) != 2:
            raise ValueError("Choose two distinct systems")
        return self


def _evidence(db: Any, record: dict[str, Any]) -> dict[str, Any]:
    protocol = record["protocol"]
    items = [
        row["id"]
        for row in db.execute(
            "SELECT id FROM review_queue_items WHERE queue_id=? ORDER BY position",
            (record["queue_id"],),
        )
    ]
    expected = {(item, reviewer) for item in items for reviewer in protocol["reviewer_ids"]}
    assigned = {
        (row["item_id"], row["user_id"])
        for row in db.execute(
            "SELECT a.item_id,a.user_id FROM review_assignments a "
            "JOIN review_queue_items i ON i.id=a.item_id WHERE i.queue_id=? AND a.active=1",
            (record["queue_id"],),
        )
    }
    if not items or expected != assigned:
        raise ValueError("The frozen study assignments changed; results remain sealed")
    # Read revisions together; a full cohort can contain 50,000 assignments.
    latest = []
    for row in db.execute(
        "SELECT j.id,j.item_id,j.user_id,j.data FROM review_judgments j JOIN ("
        "SELECT MAX(j.rowid) AS newest FROM review_judgments j "
        "JOIN review_queue_items i ON i.id=j.item_id WHERE i.queue_id=? "
        "GROUP BY j.item_id,j.user_id) revisions ON j.rowid=revisions.newest "
        "ORDER BY j.item_id,j.user_id",
        (record["queue_id"],),
    ):
        if (row["item_id"], row["user_id"]) in expected:
            latest.append({"id": row["id"], "hash": content_hash(json.loads(row["data"]))})
    return {
        "items": len(items),
        "assigned_reviews": len(expected),
        "completed_reviews": len(latest),
        "missing_reviews": len(expected) - len(latest),
        "judgments": latest,
    }


def study_summary(db: Any, record: dict[str, Any]) -> dict[str, Any]:
    evidence = _evidence(db, record)
    protocol, state = record["protocol"], record["state"]
    return {
        "id": record["id"],
        "queue_id": record["queue_id"],
        "name": protocol["name"]
        if state["status"] == "revealed"
        else "Blind study · " + record["id"][:8],
        "mode": protocol["mode"],
        "status": state["status"],
        "created_at": protocol["created_at"],
        "locked_at": state.get("locked_at"),
        "revealed_at": state.get("revealed_at"),
        **{
            key: evidence[key]
            for key in ("items", "assigned_reviews", "completed_reviews", "missing_reviews")
        },
        "evidence_hash": content_hash(
            {
                "protocol": record["protocol_hash"],
                "state": record["state_hash"],
                "evidence": evidence,
            }
        ),
        "boundary": BOUNDARY,
        "retrospective": True,
        "confidential_execution": False,
    }


class BlindStudies:
    def __init__(self, settings: Settings):
        self.queues = ReviewQueues(settings)
        self.store = self.queues.store
        with self.store._connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS blind_studies (
                id TEXT PRIMARY KEY,
                queue_id TEXT NOT NULL UNIQUE REFERENCES review_queues(id),
                protocol TEXT NOT NULL, protocol_hash TEXT NOT NULL,
                state TEXT NOT NULL, state_hash TEXT NOT NULL
            )""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS blind_studies_protocol_immutable
                BEFORE UPDATE OF id,queue_id,protocol,protocol_hash ON blind_studies
                BEGIN SELECT RAISE(ABORT, 'Study protocol is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS blind_studies_no_delete
                BEFORE DELETE ON blind_studies
                BEGIN SELECT RAISE(ABORT, 'Study evidence is retained'); END""")

    @staticmethod
    def _read(db: Any, identifier: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT q.data FROM blind_studies s JOIN review_queues q ON q.id=s.queue_id "
            "WHERE s.id=?",
            (identifier,),
        ).fetchone()
        if not row:
            raise KeyError(identifier)
        record = ReviewQueues._study(db, json.loads(row[0]))
        if record is None or record["id"] != identifier:
            raise ValueError("Study linkage is inconsistent")
        return record

    @staticmethod
    def _operator(db: Any, actor: str) -> None:
        if actor == "local-console":
            return
        row = db.execute("SELECT role,active FROM team_users WHERE id=?", (actor,)).fetchone()
        if not row or not row["active"] or row["role"] not in {"admin", "operator"}:
            raise ValueError("An active custodian identity is required")

    @classmethod
    def _coordinator(cls, db: Any, record: dict[str, Any], actor: str) -> None:
        cls._operator(db, actor)
        if actor != "local-console" and actor != record["protocol"]["coordinator_id"]:
            raise ValueError("Only the study's custodian can lock or reveal it")

    @staticmethod
    def _independent_reviewers(db: Any, reviewer_ids: list[str]) -> None:
        for identifier in reviewer_ids:
            row = db.execute(
                "SELECT role,active FROM team_users WHERE id=?", (identifier,)
            ).fetchone()
            if not row or not row["active"] or row["role"] != "reviewer":
                raise ValueError("Blind studies require active reviewer-only identities")

    def create(self, request: StudyRequest, actor: str) -> dict[str, Any]:
        request = StudyRequest.model_validate(request)
        with self.store._connect(write=True) as db:
            self._operator(db, actor)
            if not db.execute("SELECT enabled FROM team_settings WHERE singleton=1").fetchone()[0]:
                raise ValueError("Enable team identities before creating a blind study")
            self._independent_reviewers(db, request.reviewer_ids)
            source = db.execute(
                "SELECT status FROM experiments WHERE id=?", (request.experiment_id,)
            ).fetchone()
            if source is None:
                raise KeyError(request.experiment_id)
            if source["status"] != "completed":
                raise ValueError("Blind studies require a completed source experiment")
            if actor in request.reviewer_ids:
                raise ValueError("The custodian cannot review their study")
            if db.execute("SELECT COUNT(*) FROM blind_studies").fetchone()[0] >= 500:
                raise ValueError("This workspace supports at most 500 blind studies")
            # Detect recorded prior exposure; actual human independence remains organizational.
            for row in db.execute(
                "SELECT DISTINCT q.data FROM review_queues q "
                "JOIN review_queue_items i ON i.queue_id=q.id "
                "JOIN review_assignments a ON a.item_id=i.id "
                "WHERE a.user_id IN (" + ",".join("?" for _ in request.reviewer_ids) + ")",
                request.reviewer_ids,
            ):
                queue = json.loads(row[0])
                if queue["experiment_id"] == request.experiment_id and not queue["blind"]:
                    raise ValueError(
                        "A selected reviewer had an identified queue for this experiment; use unexposed reviewers"
                    )
            for row in db.execute("SELECT id FROM blind_studies"):
                previous = self._read(db, row["id"])
                if (
                    previous["protocol"]["experiment_id"] == request.experiment_id
                    and previous["state"]["status"] == "revealed"
                ):
                    raise ValueError(
                        "This experiment already has a revealed study; use a fresh source experiment"
                    )
            identifier = new_id()
            queue = self.queues._create(
                db,
                QueueRequest(
                    name=request.name,
                    experiment_id=request.experiment_id,
                    rubric_id=request.rubric_id,
                    reviewer_ids=request.reviewer_ids,
                    system_ids=request.system_ids,
                    blind=True,
                ),
                actor,
                study_id=identifier,
            )
            snapshot, _ = self.store._read_experiment(db, request.experiment_id)
            rubric = db.execute(
                "SELECT hash FROM review_rubrics WHERE id=?", (request.rubric_id,)
            ).fetchone()
            protocol = {
                **request.model_dump(mode="json"),
                "id": identifier,
                "queue_id": queue["id"],
                "coordinator_id": actor,
                "created_at": utc_now(),
                "source_snapshot_hash": content_hash(snapshot),
                "rubric_hash": rubric["hash"],
                "design": "retrospective review of completed outputs",
                "boundary": BOUNDARY,
            }
            state: dict[str, Any] = {
                "status": "collecting",
                "locked_at": None,
                "revealed_at": None,
            }
            db.execute(
                "INSERT INTO blind_studies VALUES(?,?,?,?,?,?)",
                (
                    identifier,
                    queue["id"],
                    canonical_json(protocol),
                    content_hash(protocol),
                    canonical_json(state),
                    content_hash(state),
                ),
            )
            append_audit(
                db,
                "blind_study.created",
                identifier,
                {
                    "queue_id": queue["id"],
                    "protocol_hash": content_hash(protocol),
                    "mode": request.mode,
                },
                actor,
            )
            return study_summary(db, self._read(db, identifier))

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            ids = [
                row["id"]
                for row in db.execute("SELECT id FROM blind_studies ORDER BY rowid DESC LIMIT 500")
            ]
            return [study_summary(db, self._read(db, identifier)) for identifier in ids]

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as db:
            return study_summary(db, self._read(db, identifier))

    @staticmethod
    def _save_state(db: Any, identifier: str, state: dict[str, Any]) -> None:
        db.execute(
            "UPDATE blind_studies SET state=?,state_hash=? WHERE id=?",
            (canonical_json(state), content_hash(state), identifier),
        )

    def lock(self, identifier: str, actor: str, expected_hash: str) -> dict[str, Any]:
        with self.store._connect(write=True) as db:
            record = self._read(db, identifier)
            self._coordinator(db, record, actor)
            summary = study_summary(db, record)
            if record["state"]["status"] != "collecting":
                return summary
            if summary["evidence_hash"] != expected_hash:
                raise ValueError("Study evidence changed. Refresh before locking judgments.")
            self._independent_reviewers(db, record["protocol"]["reviewer_ids"])
            if summary["missing_reviews"]:
                raise ValueError("Every frozen assignment must have a judgment before locking")
            report = self.queues._brief(db, record["queue_id"], respect_study=False)
            state = {
                **record["state"],
                "status": "locked",
                "locked_at": utc_now(),
                "locked_by": actor,
                "locked_report": report,
                "locked_evidence_hash": summary["evidence_hash"],
                "locked_judgments": _evidence(db, record)["judgments"],
            }
            self._save_state(db, identifier, state)
            append_audit(
                db,
                "blind_study.locked",
                identifier,
                {
                    "evidence_hash": summary["evidence_hash"],
                    "report_hash": content_hash(report),
                    "completed_reviews": summary["completed_reviews"],
                },
                actor,
            )
            return study_summary(db, self._read(db, identifier))

    def reveal(
        self, identifier: str, actor: str, expected_hash: str, rationale: str
    ) -> dict[str, Any]:
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 10000:
            raise ValueError("Give a nonempty reveal rationale of at most 10,000 characters")
        with self.store._connect(write=True) as db:
            record = self._read(db, identifier)
            self._coordinator(db, record, actor)
            summary = study_summary(db, record)
            state = record["state"]
            if state["status"] == "revealed":
                if state["reveal_rationale"] != rationale.strip():
                    raise ValueError("The reveal rationale is immutable")
                return summary
            if state["status"] != "locked":
                raise ValueError("Lock every required judgment before revealing model identities")
            if summary["evidence_hash"] != expected_hash:
                raise ValueError("Study evidence changed. Refresh before revealing identities.")
            for row in db.execute("SELECT id FROM blind_studies WHERE id<>?", (identifier,)):
                other = self._read(db, row["id"])
                if (
                    other["protocol"]["experiment_id"] == record["protocol"]["experiment_id"]
                    and other["state"]["status"] == "collecting"
                ):
                    raise ValueError(
                        "Lock all studies sharing this source experiment before revealing identities"
                    )
            if _evidence(db, record)["judgments"] != state["locked_judgments"]:
                raise ValueError("Locked study evidence changed; identities remain sealed")
            state = {
                **state,
                "status": "revealed",
                "revealed_at": utc_now(),
                "revealed_by": actor,
                "reveal_rationale": rationale.strip(),
            }
            self._save_state(db, identifier, state)
            append_audit(
                db,
                "blind_study.revealed",
                identifier,
                {
                    "locked_evidence_hash": state["locked_evidence_hash"],
                    "rationale": rationale.strip(),
                },
                actor,
            )
            return study_summary(db, self._read(db, identifier))

    def report(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as db:
            record = self._read(db, identifier)
            if record["state"]["status"] != "revealed":
                raise ValueError(
                    "Results are sealed until judgments are locked and the study is revealed"
                )
            if _evidence(db, record)["judgments"] != record["state"]["locked_judgments"]:
                raise ValueError("Locked study evidence changed")
            return {
                "study": study_summary(db, record),
                "protocol": {
                    "mode": record["protocol"]["mode"],
                    "design": record["protocol"]["design"],
                    "boundary": BOUNDARY,
                    "rubric_hash": record["protocol"]["rubric_hash"],
                    "source_snapshot_hash": record["protocol"]["source_snapshot_hash"],
                },
                "review": record["state"]["locked_report"],
                "reveal": {
                    key: record["state"][key]
                    for key in ("revealed_at", "revealed_by", "reveal_rationale")
                },
            }

    def require_revealed_experiment(self, experiment_id: str) -> None:
        with self.store._connect() as db:
            for row in db.execute("SELECT id FROM blind_studies"):
                record = self._read(db, row["id"])
                if (
                    record["protocol"]["experiment_id"] == experiment_id
                    and record["state"]["status"] != "revealed"
                ):
                    raise ValueError(
                        "This experiment belongs to an unrevealed blind study; its identified brief is sealed"
                    )
