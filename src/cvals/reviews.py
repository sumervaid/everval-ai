"""Append-only human judgments, separate from automated evaluation outcomes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from cvals.models import Manifest, content_hash, new_id, utc_now
from cvals.store import TERMINAL, Store

if TYPE_CHECKING:
    from cvals.config import Settings


class _ReviewInput(Manifest):
    reviewer: str = Field(min_length=1, max_length=200)
    dimension: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=10000)

    @field_validator("reviewer", "dimension", "rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reviewer, dimension, and rationale must not be blank")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def numeric_score(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Review score must be a number between zero and one")
        return value


class ReviewStore:
    def __init__(self, settings: Settings):
        self.workspace = Store(settings)
        self.workspace.init()
        self.init()

    def init(self) -> None:
        with self.workspace._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS human_reviews (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    trial_id TEXT NOT NULL REFERENCES trials(id),
                    reviewer TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS human_reviews_experiment
                    ON human_reviews(experiment_id, created_at, id);
                CREATE TRIGGER IF NOT EXISTS human_reviews_no_update
                    BEFORE UPDATE ON human_reviews
                    BEGIN SELECT RAISE(ABORT, 'Human reviews are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS human_reviews_no_delete
                    BEFORE DELETE ON human_reviews
                    BEGIN SELECT RAISE(ABORT, 'Human reviews are append-only'); END;
            """)

    def list(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.workspace._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone():
                raise KeyError(experiment_id)
            rows = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT data FROM human_reviews WHERE experiment_id=? ORDER BY rowid",
                    (experiment_id,),
                )
            ]
        latest: dict[tuple[str, str, str], str] = {}
        for review in rows:
            latest[(review["trial_id"], review["reviewer"], review["dimension"])] = review["id"]
        for review in rows:
            review["latest"] = (
                latest[(review["trial_id"], review["reviewer"], review["dimension"])]
                == review["id"]
            )
        return rows

    def add(
        self,
        experiment_id: str,
        trial_id: str,
        reviewer: str,
        dimension: str,
        score: float,
        rationale: str,
    ) -> dict[str, Any]:
        validated = _ReviewInput(
            reviewer=reviewer, dimension=dimension, score=score, rationale=rationale
        )
        with self.workspace._connect(write=True) as connection:
            trial = self.workspace._get(connection, "trials", trial_id)
            if trial["experiment_id"] != experiment_id:
                raise ValueError("The trial does not belong to this experiment")
            if trial["status"] not in TERMINAL:
                raise ValueError("Only finalized trials can receive a human review")
            count = connection.execute(
                "SELECT COUNT(*) FROM human_reviews WHERE experiment_id=?", (experiment_id,)
            ).fetchone()[0]
            if count >= 10000:
                raise ValueError("The 10,000-review limit for this experiment has been reached")
            previous = connection.execute(
                "SELECT id FROM human_reviews WHERE trial_id=? AND reviewer=? AND dimension=? ORDER BY rowid DESC LIMIT 1",
                (trial_id, validated.reviewer, validated.dimension),
            ).fetchone()
            value = {
                "id": new_id(),
                "experiment_id": experiment_id,
                "trial_id": trial_id,
                **validated.model_dump(),
                "created_at": utc_now(),
                "identity_assurance": "self-reported label",
                "evidence_hash": content_hash(
                    {
                        "output": trial["output"],
                        "checks": trial["checks"],
                        "status": trial["status"],
                    }
                ),
                "supersedes": previous["id"] if previous else None,
            }
            connection.execute(
                "INSERT INTO human_reviews VALUES(?,?,?,?,?,?,?)",
                (
                    value["id"],
                    experiment_id,
                    trial_id,
                    validated.reviewer,
                    validated.dimension,
                    value["created_at"],
                    json.dumps(value, allow_nan=False),
                ),
            )
            self.workspace._event(
                connection,
                experiment_id,
                "review.added",
                {"review_id": value["id"], "trial_id": trial_id, "dimension": validated.dimension},
            )
        return {**value, "latest": True}
