"""Durable repository jobs, independent of a connected terminal."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.models import canonical_json, new_id, utc_now
from cvals.store import Store

KINDS = {"discover", "validate", "prepare"}


def _validate_payload(kind: str, payload: dict[str, Any]) -> None:
    allowed = {
        "discover": {"project_id", "limit", "cursor"},
        "validate": {"project_id", "candidate_id"},
        "prepare": {
            "project_id",
            "packages",
            "revision",
            "base_image",
            "refresh",
            "language",
            "runner",
            "artifacts",
        },
    }
    if not isinstance(payload, dict) or set(payload) - allowed[kind]:
        raise ValueError("Job payload contains unsupported fields")
    required = ["project_id", "candidate_id"] if kind == "validate" else ["project_id"]
    for name in required:
        if not isinstance(payload.get(name), str) or not 1 <= len(payload[name]) <= 128:
            raise ValueError(f"Job requires a bounded {name}")
    if kind == "discover":
        limit = payload.get("limit", 20)
        cursor = payload.get("cursor")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Discovery limit must be between 1 and 100")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > 1024
            or any(ord(char) < 32 or ord(char) > 126 for char in cursor)
        ):
            raise ValueError("Invalid discovery cursor")
    if kind == "prepare":
        from cvals.environments import validate_requirements

        language, runner = payload.get("language", "python"), payload.get("runner", "node-test")
        if (
            not isinstance(language, str)
            or not isinstance(runner, str)
            or language not in {"python", "javascript", "typescript"}
            or runner not in {"node-test", "jest", "vitest"}
        ):
            raise ValueError("Unsupported preparation language or Node test runner")
        if payload.get("packages") is not None:
            if language != "python":
                raise ValueError(
                    "Node preparation uses the committed npm lockfile, not a package override"
                )
            validate_requirements(payload["packages"])
        artifacts = payload.get("artifacts")
        if artifacts is not None and (
            not isinstance(artifacts, list)
            or len(artifacts) > 200
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in artifacts
            )
            or len(set(artifacts)) != len(artifacts)
        ):
            raise ValueError(
                "Private dependency references must be at most 200 unique SHA-256 artifact IDs"
            )
        if not isinstance(payload.get("revision"), str) or not re.fullmatch(
            r"[0-9a-f]{40,64}", payload["revision"]
        ):
            raise ValueError("Queued preparation requires an immutable source commit")
        if not isinstance(payload.get("base_image"), str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", payload["base_image"]
        ):
            raise ValueError("Queued preparation requires a pinned local image digest")
        if type(payload.get("refresh", False)) is not bool:
            raise ValueError("Preparation refresh must be boolean")


class Jobs:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        self.audit = AuditLog(settings)
        with self.store._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS workbench_jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL, result TEXT, error TEXT,
                    owner TEXT, lease_until REAL
                );
                CREATE INDEX IF NOT EXISTS workbench_jobs_pending ON workbench_jobs(status,created_at);
            """)

    def enqueue(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError("Unsupported repository job")
        _validate_payload(kind, payload)
        encoded = canonical_json(payload)
        if len(encoded.encode()) > 16384:
            raise ValueError("Job payload exceeds 16 KiB")
        identifier = new_id()
        now = utc_now()
        with self.store._connect(write=True) as connection:
            active = connection.execute(
                "SELECT count(*) FROM workbench_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= 100:
                raise ValueError("The workspace already has 100 pending jobs")
            connection.execute(
                "INSERT INTO workbench_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (identifier, kind, "queued", now, now, encoded, None, None, None, None),
            )
            self.audit.record(
                "job.queued", identifier, {"kind": kind, "payload": payload}, connection=connection
            )
        return self.get(identifier)

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["result"] = json.loads(item["result"]) if item.get("result") is not None else None
        item["has_result"] = bool(item.get("has_result", item["result"] is not None))
        item.pop("owner", None)
        item.pop("lease_until", None)
        return item

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workbench_jobs WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise KeyError(identifier)
        return self._public(row)

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._public(row)
                for row in connection.execute(
                    "SELECT id,kind,status,created_at,updated_at,payload,error,(result IS NOT NULL) AS has_result FROM workbench_jobs ORDER BY created_at DESC LIMIT 200"
                )
            ]

    def wait(self, identifier: str, timeout: float = 1800) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(identifier)
            if job["status"] == "completed":
                return {**job["result"], "job_id": identifier}
            if job["status"] == "error":
                raise ValueError(job["error"])
            time.sleep(0.2)
        raise ValueError(
            f"Job {identifier} is still queued or running; inspect jobs for its durable result"
        )

    def run_pending_once(self) -> bool:
        owner = new_id()
        # Idle polling must not acquire a writer lock or materialize job results.
        with self.store._connect() as connection:
            pending = connection.execute(
                "SELECT 1 FROM workbench_jobs WHERE status='queued' OR (status='running' AND (lease_until IS NULL OR lease_until<?)) LIMIT 1",
                (time.time(),),
            ).fetchone()
        if pending is None:
            return False
        with self.store._connect(write=True) as connection:
            expired = list(
                connection.execute(
                    "SELECT id FROM workbench_jobs WHERE status='running' AND (lease_until IS NULL OR lease_until<?)",
                    (time.time(),),
                )
            )
            for row in expired:
                connection.execute(
                    "UPDATE workbench_jobs SET status='error',error=?,updated_at=?,owner=NULL,lease_until=NULL WHERE id=?",
                    ("Worker interrupted; retry the job explicitly", utc_now(), row[0]),
                )
                self.audit.record("job.interrupted", row[0], {}, connection=connection)
            if connection.execute(
                "SELECT 1 FROM workbench_jobs WHERE status='running' LIMIT 1"
            ).fetchone():
                return False
            row = connection.execute(
                "SELECT * FROM workbench_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            identifier = row["id"]
            connection.execute(
                "UPDATE workbench_jobs SET status='running',owner=?,lease_until=?,updated_at=? WHERE id=?",
                (owner, time.time() + 30, utc_now(), identifier),
            )
            payload = json.loads(row["payload"])
            kind = row["kind"]
            self.audit.record("job.started", identifier, {"kind": kind}, connection=connection)
        stop = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(5):
                try:
                    with self.store._connect(write=True) as connection:
                        cursor = connection.execute(
                            "UPDATE workbench_jobs SET lease_until=?,updated_at=? WHERE id=? AND owner=? AND status='running'",
                            (time.time() + 30, utc_now(), identifier, owner),
                        )
                        if cursor.rowcount != 1:
                            lost.set()
                            return
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True, name="everval-job-lease")
        thread.start()
        try:
            if kind == "discover":
                from cvals.projects import Projects

                result = Projects(self.settings).discover(**payload)
            elif kind == "validate":
                from cvals.benchmarks import Benchmarks

                result = Benchmarks(self.settings).validate(**payload)
            else:
                from cvals.environments import Environments

                result = Environments(self.settings).prepare(**payload)
                from cvals.benchmarks import Benchmarks

                language = payload.get("language", "python")
                result["profile"] = Benchmarks(self.settings).configure(
                    payload["project_id"],
                    result["image_digest"],
                    runtime="python-pytest"
                    if language == "python"
                    else payload.get("runner", "node-test"),
                    language=language,
                )
            encoded = canonical_json(result)
            status, error = "completed", None
        except (ValueError, KeyError) as exc:
            encoded, status, error = None, "error", str(exc)[:2000]
        except Exception as exc:
            encoded, status, error = (
                None,
                "error",
                f"Job failed ({type(exc).__name__}); inspect local configuration",
            )
        finally:
            stop.set()
            thread.join(timeout=2)
        if lost.is_set():
            return True
        with self.store._connect(write=True) as connection:
            updated = connection.execute(
                "UPDATE workbench_jobs SET status=?,result=?,error=?,updated_at=?,lease_until=NULL WHERE id=? AND owner=? AND status='running'",
                (status, encoded, error, utc_now(), identifier, owner),
            )
            if updated.rowcount:
                self.audit.record(
                    "job." + status,
                    identifier,
                    {"kind": kind, "error": error},
                    connection=connection,
                )
        return True
