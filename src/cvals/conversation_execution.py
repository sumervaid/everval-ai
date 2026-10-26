"""Atomic chat activation and private reports from the shared evaluation engine.

Conversation text is never executable. Only admitted collection and system IDs
reach the scheduler; report destinations are fixed beneath the local workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from cvals.config import Settings
from cvals.exporting import export_experiment
from cvals.models import ExperimentCreate, Manifest, canonical_json, content_hash, utc_now
from cvals.reports import build_report
from cvals.schedules import ScheduleStore
from cvals.store import Store

FORMATS = {"html": ".html", "markdown": ".md", "csv": ".csv", "json": ".json"}


class ExecutionPlan(Manifest):
    name: str = Field(min_length=1, max_length=200)
    collection_id: str = Field(min_length=1, max_length=128)
    system_ids: list[str] = Field(min_length=1, max_length=20)
    interval_minutes: int = Field(ge=5, le=525600, strict=True)
    report_formats: list[Literal["html", "markdown", "csv", "json"]] = Field(
        min_length=1, max_length=4
    )
    budget_usd: float = Field(ge=0, le=100000, strict=True)
    lifetime_budget_usd: float = Field(ge=0, le=1000000, strict=True)
    repetitions: int = Field(default=1, ge=1, le=30, strict=True)

    @field_validator("name", "collection_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A name and collection are required")
        return value.strip()

    @field_validator("system_ids", "report_formats")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Choose each model or report format only once")
        return value

    @model_validator(mode="after")
    def covers_initial_run(self) -> ExecutionPlan:
        if self.lifetime_budget_usd < self.budget_usd:
            raise ValueError("The total budget must cover the first run")
        return self


def _markdown(experiment: dict[str, Any]) -> str:
    import html

    def safe(value: Any) -> str:
        text = html.escape(str(value if value is not None else "Unavailable"))
        text = " ".join(text.splitlines())
        return re.sub(r"([\\\x60*_{}\[\]()#+.!|>\-])", r"\\\1", text)

    report = build_report(experiment)
    lines = [
        "# " + safe(experiment["name"]),
        "",
        "Everval benchmark report",
        "",
        "Status: " + safe(experiment["status"]),
        "",
        "Collection fingerprint: " + safe(experiment.get("collection_hash")),
        "",
        "| Model / system | Passed / scheduled | Pass rate | Recorded cost | Cost complete | Median response time |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for system in report["systems"]:
        rate = system["pass_rate"]
        cost = system["cost_usd"]
        duration = system["median_duration_ms"]
        cells = [
            system["name"],
            f"{system['passed']} / {system['scheduled']}",
            f"{rate:.1%}" if rate is not None else "Unavailable",
            f"${cost:.6f}" if cost is not None else "Unknown",
            "Yes" if system["cost_complete"] else "No",
            f"{duration:.1f} ms" if duration is not None else "Unavailable",
        ]
        lines.append("| " + " | ".join(safe(value) for value in cells) + " |")
    lines.extend(["", "## Interpretation", ""])
    lines.extend("- " + safe(warning) for warning in report["warnings"])
    lines.extend(
        [
            "",
            "All scheduled trials remain in the denominator. "
            "Recorded provider cost excludes hardware, hosting, and development costs.",
            "",
        ]
    )
    return "\n".join(lines)


class ExecutionService:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.schedules = ScheduleStore(settings, store)
        with self.store._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_executions (
                    conversation_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL, plan_hash TEXT NOT NULL,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    schedule_id TEXT NOT NULL REFERENCES evaluation_schedules(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_reports (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversation_executions(conversation_id),
                    experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    format TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id,experiment_id,format)
                );
                CREATE TABLE IF NOT EXISTS conversation_report_errors (
                    conversation_id TEXT NOT NULL REFERENCES conversation_executions(conversation_id),
                    experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    error TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id,experiment_id)
                );
                """
            )

    @staticmethod
    def _read(connection: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM conversation_executions WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        if content_hash(json.loads(row["plan"])) != row["plan_hash"]:
            raise ValueError("Conversation execution integrity check failed")
        return cast(sqlite3.Row, row)

    def activate(self, conversation_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128:
            raise ValueError("Conversation identifier must contain 1–128 characters")
        validated = ExecutionPlan.model_validate(plan)
        value = validated.model_dump(mode="json")
        fingerprint = content_hash(value)
        with self.store._connect(write=True) as connection:
            existing = connection.execute(
                "SELECT 1 FROM conversation_executions WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if existing:
                previous = self._read(connection, conversation_id)
                if previous["plan_hash"] != fingerprint:
                    raise ValueError(
                        "This conversation already has a frozen benchmark. Start a new conversation to change it."
                    )
            else:
                collection = self.store._get(connection, "collections", validated.collection_id)
                systems = [
                    self.store._get(connection, "systems", identifier)
                    for identifier in validated.system_ids
                ]
                if any(system["kind"] == "replay" for system in systems):
                    raise ValueError("Recorded submissions cannot create fresh recurring evidence")
                request = ExperimentCreate(
                    name=validated.name,
                    collection_id=validated.collection_id,
                    system_ids=validated.system_ids,
                    repetitions=validated.repetitions,
                    budget_usd=validated.budget_usd,
                )
                experiment_id = self.store._insert_experiment(
                    connection, request, collection, systems
                )
                schedule = self.schedules._create(
                    connection,
                    validated.name,
                    experiment_id,
                    validated.interval_minutes,
                    True,
                    validated.lifetime_budget_usd,
                    include_source_in_budget=True,
                )
                connection.execute(
                    "INSERT INTO conversation_executions VALUES(?,?,?,?,?,?)",
                    (
                        conversation_id,
                        canonical_json(value),
                        fingerprint,
                        experiment_id,
                        schedule["id"],
                        utc_now(),
                    ),
                )
                self.store._event(
                    connection,
                    experiment_id,
                    "conversation.activated",
                    {
                        "conversation_id": conversation_id,
                        "plan_hash": fingerprint,
                        "schedule_id": schedule["id"],
                    },
                )
        return self.get(conversation_id)

    def _directory(self, conversation_id: str, *, create: bool = False) -> Path:
        root = self.settings.artifacts_dir.resolve()
        key = hashlib.sha256(conversation_id.encode()).hexdigest()
        directory = root / "conversations" / key
        # Refuse symlink redirection within the report namespace.
        for path in (root / "conversations", directory):
            if path.is_symlink():
                raise ValueError("Report directories must not be symbolic links")
            if create:
                path.mkdir(mode=0o700, exist_ok=True)
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Report directory is outside the workspace")
        return directory

    def get(self, conversation_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = self._read(connection, conversation_id)
            schedule = connection.execute(
                "SELECT * FROM evaluation_schedules WHERE id=?", (row["schedule_id"],)
            ).fetchone()
            errors = [
                dict(value)
                for value in connection.execute(
                    "SELECT experiment_id,error,updated_at FROM conversation_report_errors "
                    "WHERE conversation_id=? ORDER BY updated_at",
                    (conversation_id,),
                )
            ]
        return {
            "experiment_id": row["experiment_id"],
            "schedule_id": row["schedule_id"],
            "status": "active" if schedule["enabled"] else "paused",
            "schedule": ScheduleStore._public(schedule),
            "report_directory": str(
                self.settings.artifacts_dir.resolve()
                / "conversations"
                / hashlib.sha256(conversation_id.encode()).hexdigest()
            ),
            "reports": self.reports(conversation_id),
            "report_errors": errors,
            "budget": self.schedules.budget(row["schedule_id"]),
        }

    def pause(self, conversation_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = self._read(connection, conversation_id)
        self.schedules.set_enabled(row["schedule_id"], False)
        return self.get(conversation_id)

    def resume(self, conversation_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = self._read(connection, conversation_id)
        self.schedules.set_enabled(row["schedule_id"], True)
        return self.get(conversation_id)

    def reports(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            self._read(connection, conversation_id)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM conversation_reports WHERE conversation_id=? "
                    "ORDER BY created_at,id",
                    (conversation_id,),
                )
            ]

    def resolve_report(self, conversation_id: str, report_id: str) -> Path:
        with self.store._connect() as connection:
            self._read(connection, conversation_id)
            row = connection.execute(
                "SELECT * FROM conversation_reports WHERE id=? AND conversation_id=?",
                (report_id, conversation_id),
            ).fetchone()
        if row is None:
            raise KeyError(report_id)
        path = Path(row["path"])
        expected = self._directory(conversation_id) / (row["id"] + FORMATS[row["format"]])
        if (
            path != expected
            or path.is_symlink()
            or not path.resolve().is_relative_to(self.settings.artifacts_dir.resolve())
        ):
            raise ValueError("Report path failed the workspace boundary check")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != row["sha256"]:
            raise ValueError("Report content has changed since it was generated")
        return path

    def _export(
        self, conversation_id: str, experiment: dict[str, Any], format: str
    ) -> dict[str, Any] | None:
        identifier = content_hash([conversation_id, experiment["id"], format])
        text = (
            _markdown(experiment)
            if format == "markdown"
            else export_experiment(experiment, format)[0]
        )
        data = text.encode("utf-8")
        directory = self._directory(conversation_id, create=True)
        path = directory / (identifier + FORMATS[format])
        temporary: str | None = None
        try:
            with self.store._connect(write=True) as connection:
                if connection.execute(
                    "SELECT 1 FROM conversation_reports WHERE id=?", (identifier,)
                ).fetchone():
                    return None
                # A crash before the receipt commits is repaired by rewriting the
                # same deterministic path on the next worker pass.
                with tempfile.NamedTemporaryFile(
                    dir=directory, prefix=".report-", delete=False
                ) as output:
                    temporary = output.name
                    os.chmod(output.name, 0o600)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                temporary = None
                receipt = {
                    "id": identifier,
                    "conversation_id": conversation_id,
                    "experiment_id": experiment["id"],
                    "format": format,
                    "path": str(path),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "created_at": utc_now(),
                }
                connection.execute(
                    "INSERT INTO conversation_reports VALUES(?,?,?,?,?,?,?)",
                    tuple(
                        receipt[key]
                        for key in (
                            "id",
                            "conversation_id",
                            "experiment_id",
                            "format",
                            "path",
                            "sha256",
                            "created_at",
                        )
                    ),
                )
                self.store._event(
                    connection,
                    experiment["id"],
                    "conversation.report_saved",
                    {
                        "conversation_id": conversation_id,
                        "report_id": identifier,
                        "format": format,
                        "sha256": receipt["sha256"],
                    },
                )
            return receipt
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def tick(self) -> list[dict[str, Any]]:
        """Export completed runs, including failures, without another inference call."""
        created: list[dict[str, Any]] = []
        with self.store._connect() as connection:
            executions = list(connection.execute("SELECT * FROM conversation_executions"))
        for execution in executions:
            conversation_id = execution["conversation_id"]
            if content_hash(json.loads(execution["plan"])) != execution["plan_hash"]:
                raise ValueError("Conversation execution integrity check failed")
            plan = ExecutionPlan.model_validate_json(execution["plan"])
            with self.store._connect() as connection:
                pending = connection.execute(
                    "SELECT e.id FROM experiments e WHERE e.status IN ('completed','failed','cancelled') "
                    "AND (e.id=? OR EXISTS(SELECT 1 FROM schedule_occurrences o "
                    "WHERE o.experiment_id=e.id AND o.schedule_id=?)) "
                    "AND (SELECT COUNT(*) FROM conversation_reports r WHERE r.conversation_id=? "
                    "AND r.experiment_id=e.id) < ? ORDER BY e.created_at LIMIT 25",
                    (
                        execution["experiment_id"],
                        execution["schedule_id"],
                        conversation_id,
                        len(plan.report_formats),
                    ),
                ).fetchall()
            for pending_row in pending:
                identifier = pending_row["id"]
                try:
                    experiment = self.store.get_experiment(identifier)
                    present = {
                        row["format"]
                        for row in self.reports(conversation_id)
                        if row["experiment_id"] == identifier
                    }
                    for format in plan.report_formats:
                        if format not in present:
                            receipt = self._export(conversation_id, experiment, format)
                            if receipt is not None:
                                created.append(receipt)
                    with self.store._connect(write=True) as connection:
                        connection.execute(
                            "DELETE FROM conversation_report_errors WHERE conversation_id=? AND experiment_id=?",
                            (conversation_id, identifier),
                        )
                except (OSError, ValueError, KeyError) as exc:
                    with self.store._connect(write=True) as connection:
                        connection.execute(
                            "INSERT INTO conversation_report_errors VALUES(?,?,?,?) "
                            "ON CONFLICT(conversation_id,experiment_id) DO UPDATE "
                            "SET error=excluded.error,updated_at=excluded.updated_at",
                            (conversation_id, identifier, str(exc)[:1000], utc_now()),
                        )
        return created
