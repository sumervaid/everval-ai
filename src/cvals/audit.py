"""Transactional workspace audit records with a verifiable hash chain.

The chain detects edits against a retained digest; a machine administrator can
rewrite the database and chain together. Export digests to an independent store
when organizational policy requires an external trust anchor.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import sqlite3
from typing import Any

from cvals.config import Settings
from cvals.models import canonical_json, utc_now
from cvals.store import Store

GENESIS = "0" * 64
_SECRET_KEYS = {"password", "passphrase", "secret", "api_key", "access_token", "authorization"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if str(k).lower() in _SECRET_KEYS else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Initialize on an existing connection without committing its transaction."""
    connection.execute("""CREATE TABLE IF NOT EXISTS workspace_audit (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, resource TEXT NOT NULL,
        details TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL UNIQUE
    )""")
    for operation in ("UPDATE", "DELETE"):
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS workspace_audit_no_{operation.lower()} "
            f"BEFORE {operation} ON workspace_audit BEGIN "
            "SELECT RAISE(ABORT, 'Audit records are append-only'); END"
        )


def append_audit(
    connection: sqlite3.Connection,
    action: str,
    resource: str,
    details: dict[str, Any],
    actor: str | None = None,
) -> dict[str, Any]:
    """Append within the caller's transaction; business changes fail with audit."""
    if not connection.in_transaction:
        raise ValueError("Audit append requires an active write transaction")
    if not action or len(action) > 200 or len(resource) > 1000:
        raise ValueError("Audit action/resource exceeds its bounds")
    actor = actor or getpass.getuser()
    if len(actor) > 200:
        raise ValueError("Audit actor exceeds 200 characters")
    safe = canonical_json(_redact(details))
    if len(safe.encode()) > 128 * 1024:
        raise ValueError("Audit details exceed 128 KiB")
    previous = connection.execute(
        "SELECT hash FROM workspace_audit ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    body = {
        "timestamp": utc_now(),
        "actor": actor,
        "action": action,
        "resource": resource,
        "details": json.loads(safe),
        "previous_hash": previous[0] if previous else GENESIS,
    }
    digest = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    cursor = connection.execute(
        "INSERT INTO workspace_audit(timestamp,actor,action,resource,details,previous_hash,hash) VALUES(?,?,?,?,?,?,?)",
        (body["timestamp"], actor, action, resource, safe, body["previous_hash"], digest),
    )
    return {"sequence": cursor.lastrowid, **body, "hash": digest}


class AuditLog:
    def __init__(self, settings: Settings):
        self.store = Store(settings)
        self.store.init()

    def record(
        self,
        action: str,
        resource: str,
        details: dict[str, Any],
        actor: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        if connection is not None:
            return append_audit(connection, action, resource, details, actor)
        with self.store._connect(write=True) as transaction:
            return append_audit(transaction, action, resource, details, actor)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["details"] = json.loads(record["details"])
        return record

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 10000:
            raise ValueError("Audit limit must be between 1 and 10000")
        with self.store._connect() as connection:
            return [
                self._public(row)
                for row in connection.execute(
                    "SELECT * FROM workspace_audit ORDER BY sequence DESC LIMIT ?", (limit,)
                )
            ]

    def verify(self, expected_head: str | None = None) -> dict[str, Any]:
        previous = GENESIS
        count = 0
        errors = []
        with self.store._connect() as connection:
            for row in connection.execute("SELECT * FROM workspace_audit ORDER BY sequence"):
                item = self._public(row)
                count += 1
                sequence = item.pop("sequence")
                digest = item.pop("hash")
                actual = hashlib.sha256(canonical_json(item).encode()).hexdigest()
                if item["previous_hash"] != previous or digest != actual or sequence != count:
                    errors.append({"sequence": sequence, "error": "Audit chain mismatch"})
                previous = digest
        if expected_head is not None and previous != expected_head:
            errors.append({"error": "Audit head does not match the retained digest"})
        return {
            "valid": not errors,
            "count": count,
            "head": previous,
            "errors": errors,
            "trust": "local hash chain; independent digest retention required for tamper evidence",
        }
