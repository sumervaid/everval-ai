"""Assigned human review over frozen public tasks, outputs, and rubrics."""

from __future__ import annotations

import json
import secrets
from collections import defaultdict
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.identity import IdentityStore, Principal
from cvals.models import Manifest, canonical_json, content_hash, new_id, utc_now
from cvals.store import EXPERIMENT_TERMINAL, TERMINAL, Store


class Dimension(Manifest):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    guidance: str = Field(min_length=1, max_length=2000)


class Rubric(Manifest):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=64)
    instructions: str = Field(min_length=1, max_length=5000)
    dimensions: list[Dimension] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_dimensions(self) -> Rubric:
        if len({item.id for item in self.dimensions}) != len(self.dimensions):
            raise ValueError("Rubric dimension IDs must be unique")
        return self


class QueueRequest(Manifest):
    name: str = Field(min_length=1, max_length=200)
    experiment_id: str = Field(min_length=1, max_length=128)
    rubric_id: str = Field(min_length=1, max_length=128)
    reviewer_ids: list[str] = Field(min_length=1, max_length=50)
    system_ids: list[str] = Field(min_length=1, max_length=2)
    blind: bool = True

    @model_validator(mode="after")
    def unique_choices(self) -> QueueRequest:
        if len(set(self.reviewer_ids)) != len(self.reviewer_ids) or len(
            set(self.system_ids)
        ) != len(self.system_ids):
            raise ValueError("Reviewers and systems must be unique")
        return self


class Judgment(Manifest):
    scores: dict[str, dict[str, float]]
    preference: Literal["A", "B", "tie"] | None = None
    disposition: Literal["acceptable", "unacceptable", "needs_discussion"]
    rationale: str = Field(min_length=1, max_length=10000)

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A review rationale is required")
        return value.strip()

    @field_validator("scores", mode="before")
    @classmethod
    def numeric_scores(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("Scores must be grouped by response label")
        for dimensions in value.values():
            if not isinstance(dimensions, dict) or any(
                isinstance(score, bool)
                or not isinstance(score, (float, int))
                or not 0 <= score <= 1
                for score in dimensions.values()
            ):
                raise ValueError("Scores must be numbers from zero to one")
        return value


class ReviewQueues:
    def __init__(self, settings: Settings):
        self.store = Store(settings)
        self.identities = IdentityStore(settings)
        with self.store._connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS review_rubrics (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                data TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(name,version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_queues (
                id TEXT PRIMARY KEY, data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_queue_items (
                id TEXT PRIMARY KEY, queue_id TEXT NOT NULL REFERENCES review_queues(id),
                position INTEGER NOT NULL, data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_assignments (
                item_id TEXT NOT NULL REFERENCES review_queue_items(id),
                user_id TEXT NOT NULL REFERENCES team_users(id), active INTEGER NOT NULL,
                PRIMARY KEY(item_id,user_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_judgments (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES review_queue_items(id),
                user_id TEXT NOT NULL REFERENCES team_users(id), data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_decisions (
                id TEXT PRIMARY KEY, queue_id TEXT NOT NULL REFERENCES review_queues(id),
                data TEXT NOT NULL)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS review_items_queue ON review_queue_items(queue_id,position)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS review_judgments_assignment ON review_judgments(item_id,user_id)"
            )
            for table in (
                "review_rubrics",
                "review_queues",
                "review_queue_items",
                "review_judgments",
                "review_decisions",
            ):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                        f"BEFORE {operation} ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'Review evidence is immutable'); END"
                    )

    def rubrics(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM review_rubrics ORDER BY rowid DESC")
            ]

    def create_rubric(self, rubric: Rubric, actor: str) -> dict[str, Any]:
        body = rubric.model_dump(mode="json")
        with self.store._connect(write=True) as db:
            previous = db.execute(
                "SELECT data FROM review_rubrics WHERE name=? AND version=?",
                (rubric.name, rubric.version),
            ).fetchone()
            if previous:
                value: dict[str, Any] = json.loads(previous[0])
                if value["hash"] == content_hash(body):
                    return value
                raise ValueError("Rubrics are immutable; create a new version")
            value = {**body, "id": new_id(), "hash": content_hash(body), "created_at": utc_now()}
            db.execute(
                "INSERT INTO review_rubrics VALUES(?,?,?,?,?)",
                (value["id"], rubric.name, rubric.version, canonical_json(value), value["hash"]),
            )
            append_audit(db, "review.rubric_created", value["id"], {"hash": value["hash"]}, actor)
            return value

    @staticmethod
    def _reviewers(db: Any, user_ids: list[str]) -> None:
        if not user_ids or len(set(user_ids)) != len(user_ids) or len(user_ids) > 50:
            raise ValueError("Select between one and fifty distinct reviewers")
        for user_id in user_ids:
            user = db.execute(
                "SELECT role,active FROM team_users WHERE id=?", (user_id,)
            ).fetchone()
            if (
                not user
                or not user["active"]
                or user["role"] not in {"reviewer", "operator", "admin"}
            ):
                raise ValueError(
                    "Assignments require active reviewer, operator, or administrator identities"
                )

    def create(self, request: QueueRequest, actor: str) -> dict[str, Any]:
        with self.store._connect(write=True) as db:
            return self._create(db, request, actor)

    def _create(
        self, db: Any, request: QueueRequest, actor: str, *, study_id: str | None = None
    ) -> dict[str, Any]:
        """Freeze a queue in the caller's transaction."""
        self._reviewers(db, request.reviewer_ids)
        self._protect_participants(db, request.experiment_id, request.reviewer_ids, request.blind)
        rubric_row = db.execute(
            "SELECT data FROM review_rubrics WHERE id=?", (request.rubric_id,)
        ).fetchone()
        if not rubric_row:
            raise KeyError(request.rubric_id)
        exp_row = db.execute(
            "SELECT snapshot,status FROM experiments WHERE id=?", (request.experiment_id,)
        ).fetchone()
        if not exp_row:
            raise KeyError(request.experiment_id)
        if exp_row["status"] not in EXPERIMENT_TERMINAL:
            raise ValueError("Finalize the experiment before freezing a review queue")
        snapshot, _ = self.store._read_experiment(db, request.experiment_id)
        systems = {item["id"]: item for item in snapshot["systems"]}
        if any(item not in systems for item in request.system_ids):
            raise ValueError("Every selected system must belong to the frozen experiment")
        scenarios = {item["id"]: item for item in snapshot["collection"]["scenarios"]}
        trials = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT data FROM trials WHERE experiment_id=? ORDER BY position",
                (request.experiment_id,),
            )
        ]
        groups: dict[tuple[str, int], dict[str, Any]] = defaultdict(dict)
        for trial in trials:
            if trial["system_id"] in request.system_ids:
                groups[(trial["scenario_id"], trial["repetition"])][trial["system_id"]] = trial
        if not groups or len(groups) > 1000:
            raise ValueError("A review queue requires between one and one thousand task attempts")
        queue = {
            "id": new_id(),
            **({"study_id": study_id} if study_id is not None else {}),
            "name": request.name,
            "experiment_id": request.experiment_id,
            "rubric": json.loads(rubric_row[0]),
            "blind": request.blind,
            "system_ids": request.system_ids,
            "systems": [
                {
                    "id": system_id,
                    "name": systems[system_id]["name"],
                    "fixture_control": systems[system_id]["kind"] in {"reference", "incomplete"},
                    "submission_replay": systems[system_id]["kind"] == "replay",
                }
                for system_id in request.system_ids
            ],
            "created_at": utc_now(),
            "created_by": actor,
        }
        db.execute("INSERT INTO review_queues VALUES(?,?)", (queue["id"], canonical_json(queue)))
        for position, ((scenario_id, _), pair) in enumerate(groups.items()):
            if set(pair) != set(request.system_ids) or any(
                t["status"] not in TERMINAL or t.get("output") is None for t in pair.values()
            ):
                raise ValueError(
                    "Every selected task attempt needs finalized output from each selected system; incomplete pairs cannot be hidden"
                )
            order = list(request.system_ids)
            if request.blind and len(order) == 2 and secrets.randbelow(2):
                order.reverse()
            scenario = scenarios[scenario_id]
            item = {
                "id": new_id(),
                "queue_id": queue["id"],
                "task": {key: scenario[key] for key in ("name", "domain", "instruction", "inputs")},
                "responses": {
                    chr(65 + index): {
                        "output": pair[system_id]["output"],
                        "trial_id": pair[system_id]["id"],
                        "system_id": system_id,
                        "system_name": systems[system_id]["name"],
                    }
                    for index, system_id in enumerate(order)
                },
            }
            item["evidence_hash"] = content_hash(item)
            db.execute(
                "INSERT INTO review_queue_items VALUES(?,?,?,?)",
                (item["id"], queue["id"], position, canonical_json(item)),
            )
            for user_id in request.reviewer_ids:
                db.execute("INSERT INTO review_assignments VALUES(?,?,1)", (item["id"], user_id))
        append_audit(
            db,
            "review.queue_created",
            queue["id"],
            {
                "items": len(groups),
                "rubric_hash": queue["rubric"]["hash"],
                "reviewer_ids": request.reviewer_ids,
                "blind": request.blind,
            },
            actor,
        )
        return {"id": queue["id"], "name": queue["name"], "items": len(groups)}

    @staticmethod
    def _study(db: Any, queue: dict[str, Any]) -> dict[str, Any] | None:
        identifier = queue.get("study_id")
        if identifier is None:
            return None
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='blind_studies'"
        ).fetchone():
            raise ValueError("Blind study linkage is missing; access is withheld")
        row = db.execute(
            "SELECT * FROM blind_studies WHERE id=? AND queue_id=?", (identifier, queue["id"])
        ).fetchone()
        if row is None:
            raise ValueError("Blind study linkage is missing; access is withheld")
        protocol, state = json.loads(row["protocol"]), json.loads(row["state"])
        if (
            content_hash(protocol) != row["protocol_hash"]
            or content_hash(state) != row["state_hash"]
        ):
            raise ValueError("Blind study integrity check failed")
        return {**dict(row), "protocol": protocol, "state": state}

    @staticmethod
    def _unrevealed_source(db: Any, experiment_id: str) -> bool:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='blind_studies'"
        ).fetchone():
            return False
        for row in db.execute("SELECT protocol,state,protocol_hash,state_hash FROM blind_studies"):
            protocol, state = json.loads(row["protocol"]), json.loads(row["state"])
            if (
                content_hash(protocol) != row["protocol_hash"]
                or content_hash(state) != row["state_hash"]
            ):
                raise ValueError("Blind study integrity check failed")
            if protocol["experiment_id"] == experiment_id and state["status"] != "revealed":
                return True
        return False

    @staticmethod
    def _study_reviewer(db: Any, study: dict[str, Any], who: Principal) -> None:
        user = db.execute("SELECT role,active FROM team_users WHERE id=?", (who.id,)).fetchone()
        if (
            who.mode != "team"
            or who.role != "reviewer"
            or not user
            or not user["active"]
            or user["role"] != "reviewer"
            or who.id not in study["protocol"]["reviewer_ids"]
        ):
            raise KeyError("Review item not found")

    @staticmethod
    def _protect_participants(
        db: Any, experiment_id: str, reviewer_ids: list[str], blind: bool
    ) -> None:
        """Prevent another queue exposing the same source to a study reviewer."""
        if (
            blind
            or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='blind_studies'"
            ).fetchone()
        ):
            return
        for row in db.execute("SELECT protocol,state,protocol_hash,state_hash FROM blind_studies"):
            protocol, state = json.loads(row["protocol"]), json.loads(row["state"])
            if (
                content_hash(protocol) != row["protocol_hash"]
                or content_hash(state) != row["state_hash"]
            ):
                raise ValueError("Blind study integrity check failed")
            if (
                state["status"] != "revealed"
                and protocol["experiment_id"] == experiment_id
                and set(reviewer_ids) & set(protocol["reviewer_ids"])
            ):
                raise ValueError(
                    "An unrevealed blind study protects these reviewers from identified source queues"
                )

    def assign(self, queue_id: str, user_ids: list[str], actor: str) -> None:
        with self.store._connect(write=True) as db:
            row = db.execute("SELECT data FROM review_queues WHERE id=?", (queue_id,)).fetchone()
            if not row:
                raise KeyError(queue_id)
            queue = json.loads(row[0])
            if self._study(db, queue):
                raise ValueError("Blind study assignments are frozen; create a new study")
            self._reviewers(db, user_ids)
            self._protect_participants(db, queue["experiment_id"], user_ids, queue["blind"])
            items = db.execute(
                "SELECT id FROM review_queue_items WHERE queue_id=?", (queue_id,)
            ).fetchall()
            if not items:
                raise KeyError(queue_id)
            for item in items:
                db.execute("UPDATE review_assignments SET active=0 WHERE item_id=?", (item[0],))
                for user_id in user_ids:
                    db.execute(
                        "INSERT INTO review_assignments VALUES(?,?,1) ON CONFLICT(item_id,user_id) DO UPDATE SET active=1",
                        (item[0], user_id),
                    )
            append_audit(
                db, "review.assignments_changed", queue_id, {"reviewer_ids": user_ids}, actor
            )

    @staticmethod
    def _assigned(db: Any, item_id: str, user_id: str) -> Any:
        row = db.execute(
            """SELECT i.data,q.data AS queue_data FROM review_queue_items i
               JOIN review_queues q ON q.id=i.queue_id
               JOIN review_assignments a ON a.item_id=i.id
               JOIN team_users u ON u.id=a.user_id
               WHERE i.id=? AND a.user_id=? AND a.active=1 AND u.active=1""",
            (item_id, user_id),
        ).fetchone()
        if not row:
            raise KeyError("Review item not found")
        return row

    def assigned(self, who: Principal) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT i.id,i.data,q.data AS queue_data FROM review_queue_items i
                   JOIN review_queues q ON q.id=i.queue_id JOIN review_assignments a ON a.item_id=i.id
                   WHERE a.user_id=? AND a.active=1 ORDER BY q.rowid DESC,i.position""",
                (who.id,),
            )
            result = []
            for row in rows:
                item, queue = json.loads(row["data"]), json.loads(row["queue_data"])
                study = self._study(db, queue)
                if study:
                    self._study_reviewer(db, study, who)
                reviewed = bool(
                    db.execute(
                        "SELECT 1 FROM review_judgments WHERE item_id=? AND user_id=? LIMIT 1",
                        (row["id"], who.id),
                    ).fetchone()
                )
                result.append(
                    {
                        "id": row["id"],
                        "queue_id": queue["id"],
                        "queue_name": "Blind review · " + queue["id"][:8]
                        if queue["blind"]
                        else queue["name"],
                        "name": item["task"]["name"],
                        "domain": item["task"]["domain"],
                        "blind": queue["blind"],
                        "reviewed": reviewed,
                        **(
                            {
                                "study_id": study["id"],
                                "study_status": study["state"]["status"],
                                "locked": study["state"]["status"] != "collecting",
                            }
                            if study
                            else {}
                        ),
                    }
                )
            return result

    def item(self, item_id: str, who: Principal) -> dict[str, Any]:
        with self.store._connect() as db:
            row = self._assigned(db, item_id, who.id)
            item, queue = json.loads(row["data"]), json.loads(row["queue_data"])
            study = self._study(db, queue)
            if study:
                self._study_reviewer(db, study, who)
            previous = db.execute(
                "SELECT data FROM review_judgments WHERE item_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1",
                (item_id, who.id),
            ).fetchone()
            return {
                "id": item["id"],
                "queue_id": queue["id"],
                "queue_name": "Blind review · " + queue["id"][:8]
                if queue["blind"]
                else queue["name"],
                "task": item["task"],
                "rubric": queue["rubric"],
                "blind": queue["blind"],
                "responses": {
                    label: {
                        "output": response["output"],
                        **({"name": response["system_name"]} if not queue["blind"] else {}),
                    }
                    for label, response in item["responses"].items()
                },
                "previous": json.loads(previous[0]) if previous else None,
                **(
                    {
                        "study_id": study["id"],
                        "study_status": study["state"]["status"],
                        "locked": study["state"]["status"] != "collecting",
                    }
                    if study
                    else {}
                ),
                "blinding_note": "System metadata, traces, checks, and identities are withheld. Response text itself may disclose its source.",
            }

    def judge(self, item_id: str, who: Principal, judgment: Judgment) -> dict[str, Any]:
        with self.store._connect(write=True) as db:
            row = self._assigned(db, item_id, who.id)
            item, queue = json.loads(row["data"]), json.loads(row["queue_data"])
            study = self._study(db, queue)
            if study:
                self._study_reviewer(db, study, who)
            if study and study["state"]["status"] != "collecting":
                raise ValueError("This study is locked; judgments can no longer change")
            dimensions = {dimension["id"] for dimension in queue["rubric"]["dimensions"]}
            labels = set(item["responses"])
            if set(judgment.scores) != labels or any(
                set(scores) != dimensions for scores in judgment.scores.values()
            ):
                raise ValueError("Score every response against every frozen rubric dimension")
            if (len(labels) == 2 and judgment.preference is None) or (
                len(labels) == 1 and judgment.preference is not None
            ):
                raise ValueError(
                    "Paired reviews require a preference; single-response reviews do not"
                )
            count = db.execute(
                "SELECT COUNT(*) FROM review_judgments WHERE item_id=? AND user_id=?",
                (item_id, who.id),
            ).fetchone()[0]
            if count >= 100:
                raise ValueError("This assignment has reached its 100-judgment history limit")
            previous = db.execute(
                "SELECT id FROM review_judgments WHERE item_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1",
                (item_id, who.id),
            ).fetchone()
            value = {
                **judgment.model_dump(mode="json"),
                "id": new_id(),
                "item_id": item_id,
                "reviewer_id": who.id,
                "reviewer_name": who.name,
                "created_at": utc_now(),
                "identity_assurance": "authenticated bearer token",
                "supersedes": previous[0] if previous else None,
                "evidence_hash": item["evidence_hash"],
                "rubric_hash": queue["rubric"]["hash"],
            }
            db.execute(
                "INSERT INTO review_judgments VALUES(?,?,?,?)",
                (value["id"], item_id, who.id, canonical_json(value)),
            )
            append_audit(
                db,
                "review.judgment_recorded",
                item_id,
                {"judgment_id": value["id"], "hash": content_hash(value)},
                who.id,
            )
            return value

    def queues(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            result = []
            for row in db.execute("SELECT data FROM review_queues ORDER BY rowid DESC"):
                queue = json.loads(row[0])
                study = self._study(db, queue)
                hidden = study is not None and study["state"]["status"] != "revealed"
                value = {
                    key: queue[key]
                    for key in ("id", "name", "blind", "created_at", "experiment_id")
                }
                if hidden and study:
                    value.update(name="Blind study · " + study["id"][:8], experiment_id="")
                elif study is None and self._unrevealed_source(db, queue["experiment_id"]):
                    value.update(name="Review queue · " + queue["id"][:8], experiment_id="")
                if study:
                    value.update(study_id=study["id"], study_status=study["state"]["status"])
                result.append(value)
            return result

    def brief(self, queue_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            return self._brief(db, queue_id)

    def _brief(self, db: Any, queue_id: str, *, respect_study: bool = True) -> dict[str, Any]:
        queue_row = db.execute("SELECT data FROM review_queues WHERE id=?", (queue_id,)).fetchone()
        if not queue_row:
            raise KeyError(queue_id)
        queue = json.loads(queue_row[0])
        study = self._study(db, queue)
        if respect_study and study is None and self._unrevealed_source(db, queue["experiment_id"]):
            raise ValueError(
                "This experiment belongs to an unrevealed blind study; its identified brief is sealed"
            )
        if respect_study and study and study["state"]["status"] != "revealed":
            from cvals.blind_studies import study_summary

            safe = study_summary(db, study)
            return {
                "queue_id": queue_id,
                "name": safe["name"],
                "study_id": safe["id"],
                "study_status": safe["status"],
                "blind": True,
                "rubric": {
                    "name": "Rubric withheld",
                    "version": "sealed",
                    "instructions": "",
                    "dimensions": [],
                },
                "systems": [],
                "fixture_controls_present": None,
                "submission_replays_present": None,
                "assigned_reviews": safe["assigned_reviews"],
                "completed_reviews": safe["completed_reviews"],
                "preferences": {},
                "dispositions": {},
                "judgment_ids": [],
                "decisions": [],
                "evidence_hash": safe["evidence_hash"],
                "interpretation": "Identities and outcomes remain sealed until every required judgment is locked and the study is revealed.",
            }
        systems = queue.get("systems")
        if systems is None:
            snapshot, _ = self.store._read_experiment(db, queue["experiment_id"])
            systems = [
                {
                    "id": system["id"],
                    "name": system["name"],
                    "fixture_control": system["kind"] in {"reference", "incomplete"},
                    "submission_replay": system["kind"] == "replay",
                }
                for system in snapshot["systems"]
                if system["id"] in queue["system_ids"]
            ]
        items = {
            row["id"]: json.loads(row["data"])
            for row in db.execute(
                "SELECT id,data FROM review_queue_items WHERE queue_id=?", (queue_id,)
            )
        }
        assignments = [
            (row[0], row[1])
            for row in db.execute(
                "SELECT a.item_id,a.user_id FROM review_assignments a JOIN review_queue_items i ON i.id=a.item_id WHERE i.queue_id=? AND a.active=1",
                (queue_id,),
            )
        ]
        latest: list[dict[str, Any]] = []
        for item_id, user_id in assignments:
            row = db.execute(
                "SELECT data FROM review_judgments WHERE item_id=? AND user_id=? ORDER BY rowid DESC LIMIT 1",
                (item_id, user_id),
            ).fetchone()
            if row:
                latest.append(json.loads(row[0]))
        preferences: dict[str, int] = defaultdict(int)
        dispositions: dict[str, int] = defaultdict(int)
        system_names = [system["name"] for system in systems]
        for judgment in latest:
            dispositions[judgment["disposition"]] += 1
            preference = judgment["preference"]
            if preference:
                label = (
                    "Tie"
                    if preference == "tie"
                    else items[judgment["item_id"]]["responses"][preference]["system_name"]
                )
                if preference != "tie" and (label == "Tie" or system_names.count(label) > 1):
                    response = items[judgment["item_id"]]["responses"][preference]
                    label += " · " + response["system_id"]
                preferences[label] += 1
        decisions = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT data FROM review_decisions WHERE queue_id=? ORDER BY rowid", (queue_id,)
            )
        ]
        value = {
            "queue_id": queue_id,
            "name": queue["name"],
            "rubric": queue["rubric"],
            "systems": systems,
            "fixture_controls_present": any(system["fixture_control"] for system in systems),
            "submission_replays_present": any(system["submission_replay"] for system in systems),
            "assigned_reviews": len(assignments),
            "completed_reviews": len(latest),
            "preferences": dict(preferences),
            "dispositions": dict(dispositions),
            "judgment_ids": [item["id"] for item in latest],
            "decisions": decisions,
            "interpretation": "Human judgments are separate from automated scores. Counts are descriptive, not independent model-performance estimates.",
        }

        if respect_study and study:
            value.update(study_id=study["id"], study_status=study["state"]["status"])
        value["evidence_hash"] = content_hash(
            {key: item for key, item in value.items() if key != "decisions"}
        )
        return value

    def decide(
        self, queue_id: str, outcome: str, rationale: str, actor: str, expected_hash: str
    ) -> dict[str, Any]:
        if (
            outcome not in {"adopt", "reject", "more_evidence"}
            or not rationale.strip()
            or len(rationale) > 10000
        ):
            raise ValueError("Choose adopt, reject, or more_evidence and give a rationale")
        with self.store._connect(write=True) as db:
            # Holding the writer lock makes assignment/judgment state stable
            # while the frozen decision and its audit record are committed.
            row = db.execute("SELECT data FROM review_queues WHERE id=?", (queue_id,)).fetchone()
            if not row:
                raise KeyError(queue_id)
            study = self._study(db, json.loads(row[0]))
            if study and study["state"]["status"] != "revealed":
                raise ValueError("Reveal the locked study before recording an identified decision")
            summary = self._brief(db, queue_id)
            if summary.pop("evidence_hash") != expected_hash:
                raise ValueError("Review evidence changed. Refresh the brief before deciding.")
            summary.pop("decisions")
            value = {
                "id": new_id(),
                "queue_id": queue_id,
                "outcome": outcome,
                "rationale": rationale.strip(),
                "actor": actor,
                "created_at": utc_now(),
                "evidence": summary,
                "evidence_hash": content_hash(summary),
            }
            db.execute(
                "INSERT INTO review_decisions VALUES(?,?,?)",
                (value["id"], queue_id, canonical_json(value)),
            )
            append_audit(
                db,
                "review.decision_recorded",
                queue_id,
                {"decision_id": value["id"], "hash": content_hash(value)},
                actor,
            )
        return value
