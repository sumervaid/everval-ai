"""Local identities, revocable bearer credentials, and explicit API capabilities."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, Request
from starlette.routing import Match

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.models import new_id, utc_now
from cvals.store import Store

Role = Literal["admin", "operator", "reviewer", "viewer"]
ROLES = {"admin", "operator", "reviewer", "viewer"}
CAPABILITIES: dict[str, set[str]] = {
    "workspace.read": {"admin", "operator"},
    "workspace.write": {"admin", "operator"},
    "summary.read": {"admin", "operator", "viewer"},
    "reviews.assigned": {"admin", "operator", "reviewer"},
    "team.admin": {"admin"},
    "identity.self": ROLES,
}
_request_actor: ContextVar[str | None] = ContextVar("everval_request_actor", default=None)


def audit_actor() -> str | None:
    return _request_actor.get()


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    role: str
    mode: str
    token_id: str | None = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "mode": self.mode}

    def permits(self, capability: str) -> bool:
        return self.role in CAPABILITIES.get(capability, set())


def register_capability(app: FastAPI, method: str, path: str, capability: str) -> None:
    """Explicitly authorize a FastAPI route template; unknown routes stay denied."""
    if capability not in CAPABILITIES or not path.startswith("/api/"):
        raise ValueError("Unknown capability or non-API route")
    if not hasattr(app.state, "capabilities"):
        app.state.capabilities = {}
    key = (method.upper(), path)
    old = app.state.capabilities.get(key)
    if old and old != capability:
        raise ValueError("A route capability cannot be silently replaced")
    app.state.capabilities[key] = capability


def route_capability(app: FastAPI, request: Request) -> str | None:
    # Match the actual route order, rather than a second, looser URL matcher.
    for branch in app.router.routes:
        # Recent FastAPI versions retain included routers as lazy branches.
        # Effective contexts preserve their prefixes and actual route order.
        contexts = getattr(branch, "effective_route_contexts", None)
        routes = contexts() if contexts is not None else (branch,)
        for route in routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                path = getattr(route, "path", "") or getattr(
                    getattr(route, "starlette_route", None), "path", ""
                )
                value = app.state.capabilities.get((request.method, path))
                return str(value) if value is not None else None
    return None


def principal(request: Request) -> Principal:
    value = getattr(request.state, "principal", None)
    if not isinstance(value, Principal):
        raise PermissionError("Authentication is required")
    return value


class IdentityStore:
    def __init__(self, settings: Settings):
        self.store = Store(settings)
        self.store.init()
        with self.store._connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS team_settings (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL)""")
            db.execute("INSERT OR IGNORE INTO team_settings VALUES(1,0)")
            db.execute("""CREATE TABLE IF NOT EXISTS team_users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                active INTEGER NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS team_tokens (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES team_users(id),
                label TEXT NOT NULL, salt TEXT NOT NULL, digest TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS team_tokens_user ON team_tokens(user_id)")

    def enabled(self) -> bool:
        with self.store._connect() as db:
            return bool(
                db.execute("SELECT enabled FROM team_settings WHERE singleton=1").fetchone()[0]
            )

    @staticmethod
    def _name(value: str, limit: int = 200) -> str:
        value = value.strip()
        if not value or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError("Names must be nonempty plain text within the length limit")
        return value

    @staticmethod
    def _user(row: Any) -> dict[str, Any]:
        return {**dict(row), "active": bool(row["active"])}

    @staticmethod
    def _issue(db: Any, user_id: str, label: str, days: int, actor: str) -> dict[str, Any]:
        label = IdentityStore._name(label)
        if isinstance(days, bool) or not 1 <= days <= 365:
            raise ValueError("Token lifetime must be between 1 and 365 days")
        user = db.execute("SELECT * FROM team_users WHERE id=? AND active=1", (user_id,)).fetchone()
        if not user:
            raise KeyError(user_id)
        token_id, secret = new_id(), secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        created = utc_now()
        expiry = (datetime.now(UTC) + timedelta(days=days)).isoformat()
        db.execute(
            "INSERT INTO team_tokens VALUES(?,?,?,?,?,?,?,NULL)",
            (token_id, user_id, label, salt, digest, created, expiry),
        )
        append_audit(
            db, "identity.token_issued", token_id, {"user_id": user_id, "expires_at": expiry}, actor
        )
        return {
            "id": token_id,
            "user_id": user_id,
            "label": label,
            "created_at": created,
            "expires_at": expiry,
            "token": f"ev1_{token_id}_{secret}",
        }

    def bootstrap(self, name: str) -> dict[str, Any]:
        name = self._name(name)
        with self.store._connect(write=True) as db:
            if db.execute("SELECT COUNT(*) FROM team_users").fetchone()[0]:
                raise ValueError("The team is already provisioned; use local token recovery")
            user_id = new_id()
            db.execute(
                "INSERT INTO team_users VALUES(?,?,?,1,?)", (user_id, name, "admin", utc_now())
            )
            append_audit(
                db, "identity.user_created", user_id, {"name": name, "role": "admin"}, user_id
            )
            token = self._issue(db, user_id, "Initial administrator", 90, user_id)
            db.execute("UPDATE team_settings SET enabled=1 WHERE singleton=1")
            append_audit(db, "identity.team_enabled", "workspace", {"admin_id": user_id}, user_id)
            return {"user": {"id": user_id, "name": name, "role": "admin", "active": True}, **token}

    def users(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            return [
                self._user(row)
                for row in db.execute("SELECT * FROM team_users ORDER BY created_at,id")
            ]

    def create_user(self, name: str, role: str, actor: str) -> dict[str, Any]:
        name = self._name(name)
        if role not in ROLES:
            raise ValueError("Unknown team role")
        with self.store._connect(write=True) as db:
            if not db.execute("SELECT enabled FROM team_settings WHERE singleton=1").fetchone()[0]:
                raise ValueError("Provision the initial administrator first")
            if db.execute("SELECT COUNT(*) FROM team_users").fetchone()[0] >= 500:
                raise ValueError("This local workspace supports at most 500 identities")
            value: dict[str, Any] = {
                "id": new_id(),
                "name": name,
                "role": role,
                "active": True,
                "created_at": utc_now(),
            }
            db.execute(
                "INSERT INTO team_users VALUES(?,?,?,1,?)",
                (value["id"], name, role, value["created_at"]),
            )
            append_audit(
                db, "identity.user_created", value["id"], {"name": name, "role": role}, actor
            )
            return value

    def issue_token(
        self, user_id: str, label: str, days: int = 30, *, actor: str
    ) -> dict[str, Any]:
        with self.store._connect(write=True) as db:
            return self._issue(db, user_id, label, days, actor)

    @staticmethod
    def _has_admin(db: Any) -> bool:
        return bool(
            db.execute(
                """SELECT 1 FROM team_users u JOIN team_tokens t ON t.user_id=u.id
               WHERE u.role='admin' AND u.active=1 AND t.revoked_at IS NULL AND t.expires_at>?
               LIMIT 1""",
                (utc_now(),),
            ).fetchone()
        )

    def set_active(self, user_id: str, active: bool, actor: str) -> dict[str, Any]:
        with self.store._connect(write=True) as db:
            if not db.execute(
                "UPDATE team_users SET active=? WHERE id=?", (int(active), user_id)
            ).rowcount:
                raise KeyError(user_id)
            if not self._has_admin(db):
                raise ValueError(
                    "Retain an active administrator token before disabling this identity"
                )
            append_audit(db, "identity.user_updated", user_id, {"active": active}, actor)
            return self._user(
                db.execute("SELECT * FROM team_users WHERE id=?", (user_id,)).fetchone()
            )

    def tokens(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,user_id,label,created_at,expires_at,revoked_at FROM team_tokens ORDER BY created_at DESC"
                )
            ]

    def revoke(self, token_id: str, actor: str) -> None:
        with self.store._connect(write=True) as db:
            if not db.execute(
                "UPDATE team_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=?",
                (utc_now(), token_id),
            ).rowcount:
                raise KeyError(token_id)
            if not self._has_admin(db):
                raise ValueError("Issue another administrator token before revoking the last one")
            append_audit(db, "identity.token_revoked", token_id, {}, actor)

    def active(self, who: Principal) -> bool:
        """Recheck long-lived streams without retaining or rehashing the raw token."""
        if who.mode != "team" or who.token_id is None:
            return False
        with self.store._connect() as db:
            return bool(
                db.execute(
                    """SELECT 1 FROM team_tokens t JOIN team_users u ON u.id=t.user_id
                   WHERE t.id=? AND u.id=? AND u.role=? AND u.active=1
                   AND t.revoked_at IS NULL AND t.expires_at>?""",
                    (who.token_id, who.id, who.role, utc_now()),
                ).fetchone()
            )

    def authenticate(self, token: str) -> Principal | None:
        match = re.fullmatch(r"ev1_([a-f0-9]{32})_([A-Za-z0-9_-]{43})", token)
        if not match:
            return None
        token_id, secret = match.groups()
        with self.store._connect() as db:
            row = db.execute(
                """SELECT t.*,u.name,u.role,u.active FROM team_tokens t
                   JOIN team_users u ON u.id=t.user_id WHERE t.id=?""",
                (token_id,),
            ).fetchone()
        if not row or not row["active"] or row["revoked_at"] or row["expires_at"] <= utc_now():
            return None
        digest = hashlib.scrypt(
            secret.encode(), salt=bytes.fromhex(row["salt"]), n=16384, r=8, p=1
        ).hex()
        if not hmac.compare_digest(digest, row["digest"]):
            return None
        return Principal(row["user_id"], row["name"], row["role"], "team", token_id)
