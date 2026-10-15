"""Private, durable request accounting for an explicitly budgeted benchmark campaign.

No provider execution or credential access belongs here. A pending request is
already an uncertain financial obligation: even a process restart cannot free it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal, Inexact, Rounded, localcontext
from pathlib import Path
from typing import Any

ZERO = Decimal("0")
MILLION = Decimal("1000000")
MAX_CEILING = 1_000_000_000
MAX_USAGE = (1 << 63) - 1
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class BudgetError(ValueError):
    """Admission, accounting or frozen-configuration contract was refused."""


class UsageError(BudgetError):
    """Usage cannot settle this reservation; admissions remain blocked."""


def _label(value: object, name: str) -> str:
    if not isinstance(value, str) or _LABEL.fullmatch(value) is None:
        raise BudgetError(f"{name} must be a bounded identifier, not free-form text")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise BudgetError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _decimal(
    value: object, name: str, *, zero: bool = False, fractional_places: int = 18
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise BudgetError(f"{name} must be a finite Decimal")
    if value < 0 or (not zero and value == 0):
        raise BudgetError(f"{name} must be {'nonnegative' if zero else 'positive'}")
    if len(value.as_tuple().digits) > 36 or value.adjusted() > 18:
        raise BudgetError(f"{name} exceeds the supported precision or magnitude")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -fractional_places:
        raise BudgetError(f"{name} supports at most {fractional_places} fractional decimal places")
    return value


def _tokens(value: object, name: str, *, ceiling: bool = False) -> int:
    limit = MAX_CEILING if ceiling else MAX_USAGE
    if type(value) is not int or value < (1 if ceiling else 0) or value > limit:
        raise UsageError(f"{name} must be an integer in the supported token range")
    return value


def _text(value: Decimal) -> str:
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _exact() -> Iterator[None]:
    # Bounds above make 100 digits sufficient; traps prevent silent rounding if
    # a later extension breaks that assumption or changes the ambient context.
    with localcontext() as context:
        context.prec = 100
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        yield


@dataclass(frozen=True)
class BudgetConfig:
    campaign_id: str
    campaign_config_hash: str
    pricing_version: str
    opening_spend_usd: Decimal = Decimal("0")
    user_ceiling_usd: Decimal = Decimal("200")
    admission_ceiling_usd: Decimal = Decimal("190")
    attempt_cap_usd: Decimal = Decimal("6")
    input_rate_per_million: Decimal = Decimal("2.50")
    output_rate_per_million: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        _label(self.campaign_id, "campaign_id")
        _hash(self.campaign_config_hash, "campaign_config_hash")
        _label(self.pricing_version, "pricing_version")
        for field in fields(self):
            if field.name not in {"campaign_id", "campaign_config_hash", "pricing_version"}:
                _decimal(
                    getattr(self, field.name), field.name, zero=field.name == "opening_spend_usd"
                )
        with _exact():
            if not self.opening_spend_usd < self.admission_ceiling_usd <= self.user_ceiling_usd:
                raise BudgetError("Opening spend must be below admission and user ceilings")
            if self.attempt_cap_usd > self.admission_ceiling_usd - self.opening_spend_usd:
                raise BudgetError("Attempt cap exceeds the campaign's initial admission headroom")

    def as_dict(self) -> dict[str, str | int]:
        data: dict[str, str | int] = {"schema_version": 1}
        for field in fields(self):
            value = getattr(self, field.name)
            data[field.name] = _text(value) if isinstance(value, Decimal) else value
        return data

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        input_tokens = _tokens(input_tokens, "input_tokens")
        output_tokens = _tokens(output_tokens, "output_tokens")
        with _exact():
            return (
                Decimal(input_tokens) * self.input_rate_per_million
                + Decimal(output_tokens) * self.output_rate_per_million
            ) / MILLION


class BudgetLedger:
    def __init__(self, path: Path, config: BudgetConfig, *, create: bool = True):
        if not isinstance(config, BudgetConfig):
            raise BudgetError("A validated BudgetConfig is required")
        self.path = Path(path).expanduser().absolute()
        self._config = config
        self._config_json = _json(config.as_dict())
        self._config_digest = hashlib.sha256(self._config_json.encode()).hexdigest()
        if not create and not self.path.exists():
            raise BudgetError("Existing accounting ledger is required; refusing to create it")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        created = False
        try:
            if not create:
                raise FileExistsError
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            if not stat.S_ISREG(self.path.lstat().st_mode):
                raise BudgetError("Ledger must be a regular file, never a symlink") from None
        else:
            os.close(descriptor)
            created = True
        os.chmod(self.path, 0o600)
        identity = self.path.lstat()
        self._file_identity = (identity.st_dev, identity.st_ino)
        self._initialize(created)

    @property
    def config(self) -> BudgetConfig:
        return self._config

    @contextmanager
    def _connection(
        self, *, write: bool = False, check_config: bool = True
    ) -> Iterator[sqlite3.Connection]:
        try:
            identity = self.path.lstat()
        except FileNotFoundError:
            raise BudgetError("Accounting ledger disappeared; recovery is required") from None
        if (
            not stat.S_ISREG(identity.st_mode)
            or (identity.st_dev, identity.st_ino) != self._file_identity
        ):
            raise BudgetError("Accounting ledger was replaced; recovery is required")
        # Never let sqlite's default mode recreate a lost database between operations.
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=rw", uri=True, timeout=30, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if check_config:
                self._check_config(connection)
            with _exact():
                yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self, created: bool) -> None:
        with self._connection(write=True, check_config=False) as connection:
            if not created:
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not {"campaign", "attempts", "requests", "events"}.issubset(tables):
                    raise BudgetError("Existing ledger schema is incomplete; refusing to reset it")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS campaign "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "config TEXT NOT NULL, config_hash TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT config,config_hash FROM campaign WHERE singleton=1"
            ).fetchone()
            if row is None and not created:
                raise BudgetError("Existing ledger has no frozen campaign; refusing to reset it")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(id TEXT PRIMARY KEY, attempt_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id), "
                "request_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','settled','uncertain')), "
                "input_ceiling INTEGER NOT NULL, output_ceiling INTEGER NOT NULL, "
                "reserved_usd TEXT NOT NULL, known_cost_usd TEXT NOT NULL DEFAULT '0', "
                "input_tokens INTEGER, output_tokens INTEGER, reason TEXT, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "UNIQUE(attempt_id,request_hash))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
                "request_id TEXT, attempt_id TEXT, recorded_at TEXT NOT NULL, data TEXT NOT NULL)"
            )
            if row is None:
                connection.execute(
                    "INSERT INTO campaign VALUES(1,?,?)", (self._config_json, self._config_digest)
                )
                self._event(connection, "campaign_created", {"config_hash": self._config_digest})
            self._check_config(connection)

    def _check_config(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT config,config_hash FROM campaign WHERE singleton=1"
        ).fetchone()
        if (
            row is None
            or row["config"] != self._config_json
            or row["config_hash"] != self._config_digest
        ):
            raise BudgetError("Frozen campaign configuration differs; refusing to reset or resume")

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        kind: str,
        data: dict[str, Any],
        *,
        request_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(kind,request_id,attempt_id,recorded_at,data) VALUES(?,?,?,?,?)",
            (kind, request_id, attempt_id, _now(), _json(data)),
        )

    def register_attempt(self, attempt_id: str, attempt_hash: str) -> None:
        """Bind an identity to its public configuration hash; no prompt text is stored."""
        _label(attempt_id, "attempt_id")
        _hash(attempt_hash, "attempt_hash")
        with self._connection(write=True) as connection:
            existing = connection.execute(
                "SELECT attempt_hash FROM attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if existing is not None:
                if existing["attempt_hash"] != attempt_hash:
                    raise BudgetError("Attempt identity already has a different frozen hash")
                return
            if connection.execute(
                "SELECT id FROM attempts WHERE attempt_hash=?", (attempt_hash,)
            ).fetchone():
                raise BudgetError("Attempt hash already belongs to another identity")
            connection.execute(
                "INSERT INTO attempts VALUES(?,?,?)", (attempt_id, attempt_hash, _now())
            )
            self._event(
                connection,
                "attempt_registered",
                {"attempt_hash": attempt_hash},
                attempt_id=attempt_id,
            )

    @staticmethod
    def _totals(rows: list[sqlite3.Row]) -> dict[str, Decimal | int]:
        settled = ZERO
        lower_bound = ZERO
        reserved = ZERO
        remaining = ZERO
        pending = uncertain = 0
        for row in rows:
            known = Decimal(row["known_cost_usd"])
            bound = Decimal(row["reserved_usd"])
            if not known.is_finite() or known < 0 or not bound.is_finite() or bound <= 0:
                raise BudgetError("Stored accounting is invalid; refusing further admissions")
            if row["state"] not in {"pending", "settled", "uncertain"}:
                raise BudgetError("Stored request state is invalid")
            if row["state"] == "settled":
                settled += known
            else:
                lower_bound += known
                reserved += bound
                remaining += max(ZERO, bound - known)
                pending += row["state"] == "pending"
                uncertain += row["state"] == "uncertain"
        return {
            "settled_cost_usd": settled,
            "unsettled_known_lower_bound_usd": lower_bound,
            "reserved_ceiling_usd": reserved,
            "outstanding_reservations_usd": remaining,
            "known_request_cost_usd": settled + lower_bound,
            "committed_request_cost_usd": settled + lower_bound + remaining,
            "pending_requests": pending,
            "uncertain_requests": uncertain,
        }

    def reserve(
        self,
        attempt_id: str,
        request_hash: str,
        input_token_ceiling: int,
        output_token_ceiling: int,
    ) -> str:
        _label(attempt_id, "attempt_id")
        _hash(request_hash, "request_hash")
        _tokens(input_token_ceiling, "input_token_ceiling", ceiling=True)
        _tokens(output_token_ceiling, "output_token_ceiling", ceiling=True)
        reservation = self.config.cost(input_token_ceiling, output_token_ceiling)
        with self._connection(write=True) as connection:
            if (
                connection.execute("SELECT id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
                is None
            ):
                raise BudgetError("Register the attempt's public configuration hash first")
            if connection.execute(
                "SELECT id FROM requests WHERE attempt_id=? AND request_hash=?",
                (attempt_id, request_hash),
            ).fetchone():
                raise BudgetError("This request hash was already admitted for the attempt")
            rows = list(connection.execute("SELECT * FROM requests"))
            if any(row["state"] != "settled" for row in rows):
                raise BudgetError("Pending or uncertain usage blocks all further admissions")
            totals = self._totals(rows)
            committed = self.config.opening_spend_usd + Decimal(
                totals["committed_request_cost_usd"]
            )
            if committed + reservation > self.config.admission_ceiling_usd:
                raise BudgetError("Request would exceed the campaign admission ceiling")
            attempt = self._totals([row for row in rows if row["attempt_id"] == attempt_id])
            if (
                Decimal(attempt["committed_request_cost_usd"]) + reservation
                > self.config.attempt_cap_usd
            ):
                raise BudgetError("Request would exceed the attempt cap")
            identifier = uuid.uuid4().hex
            now = _now()
            connection.execute(
                "INSERT INTO requests(id,attempt_id,request_hash,state,input_ceiling,output_ceiling,"
                "reserved_usd,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?,?,?)",
                (
                    identifier,
                    attempt_id,
                    request_hash,
                    input_token_ceiling,
                    output_token_ceiling,
                    _text(reservation),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                "request_reserved",
                {
                    "request_hash": request_hash,
                    "reserved_usd": _text(reservation),
                    "input_token_ceiling": input_token_ceiling,
                    "output_token_ceiling": output_token_ceiling,
                },
                request_id=identifier,
                attempt_id=attempt_id,
            )
            return identifier

    @staticmethod
    def _request(connection: sqlite3.Connection, identifier: str) -> sqlite3.Row:
        _label(identifier, "request_id")
        row = connection.execute("SELECT * FROM requests WHERE id=?", (identifier,)).fetchone()
        if not isinstance(row, sqlite3.Row):
            raise BudgetError("Unknown request identity")
        return row

    def settle(self, identifier: str, input_tokens: int, output_tokens: int) -> Decimal:
        try:
            _tokens(input_tokens, "input_tokens")
            _tokens(output_tokens, "output_tokens")
        except UsageError as exc:
            self.uncertain(
                identifier,
                "invalid_usage",
                input_tokens=input_tokens
                if type(input_tokens) is int and 0 <= input_tokens <= MAX_USAGE
                else None,
                output_tokens=output_tokens
                if type(output_tokens) is int and 0 <= output_tokens <= MAX_USAGE
                else None,
            )
            raise UsageError("Invalid usage retained as uncertain; admissions are blocked") from exc
        cost = self.config.cost(input_tokens, output_tokens)
        exceeded = False
        with self._connection(write=True) as connection:
            row = self._request(connection, identifier)
            if row["state"] == "settled":
                if row["input_tokens"] == input_tokens and row["output_tokens"] == output_tokens:
                    return Decimal(row["known_cost_usd"])
                raise UsageError("Settled usage is immutable")
            if row["state"] == "uncertain":
                raise UsageError(
                    "Uncertain usage requires explicit review; it cannot silently settle"
                )
            exceeded = input_tokens > row["input_ceiling"] or output_tokens > row["output_ceiling"]
            state = "uncertain" if exceeded else "settled"
            reason = "usage_exceeds_ceiling" if exceeded else None
            connection.execute(
                "UPDATE requests SET state=?,known_cost_usd=?,input_tokens=?,output_tokens=?,reason=?,updated_at=? WHERE id=?",
                (state, _text(cost), input_tokens, output_tokens, reason, _now(), identifier),
            )
            self._event(
                connection,
                "request_" + state,
                {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "known_cost_usd": _text(cost),
                    "reason": reason,
                },
                request_id=identifier,
                attempt_id=row["attempt_id"],
            )
        if exceeded:
            raise UsageError(
                "Usage exceeded its reserved ceiling; reported charges remain a known lower bound"
            )
        return cost

    def uncertain(
        self,
        identifier: str,
        reason: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        known_cost_usd: Decimal | None = None,
    ) -> None:
        """Retain a reason code and any known lower bound; never clear a reservation."""
        _label(reason, "reason")
        if input_tokens is not None:
            _tokens(input_tokens, "input_tokens")
        if output_tokens is not None:
            _tokens(output_tokens, "output_tokens")
        if known_cost_usd is not None:
            _decimal(known_cost_usd, "known_cost_usd", zero=True, fractional_places=24)
        with self._connection(write=True) as connection:
            row = self._request(connection, identifier)
            if row["state"] == "settled":
                raise UsageError("A settled request cannot be rewritten as uncertain")
            inputs = max(v for v in (row["input_tokens"], input_tokens, -1) if v is not None)
            outputs = max(v for v in (row["output_tokens"], output_tokens, -1) if v is not None)
            lower_bound = max(
                Decimal(row["known_cost_usd"]),
                known_cost_usd or ZERO,
                self.config.cost(max(0, inputs), max(0, outputs)),
            )
            connection.execute(
                "UPDATE requests SET state='uncertain',known_cost_usd=?,input_tokens=?,output_tokens=?,"
                "reason=?,updated_at=? WHERE id=?",
                (
                    _text(lower_bound),
                    None if inputs < 0 else inputs,
                    None if outputs < 0 else outputs,
                    reason,
                    _now(),
                    identifier,
                ),
            )
            self._event(
                connection,
                "request_uncertain",
                {
                    "reason": reason,
                    "input_tokens": None if inputs < 0 else inputs,
                    "output_tokens": None if outputs < 0 else outputs,
                    "known_cost_usd": _text(lower_bound),
                },
                request_id=identifier,
                attempt_id=row["attempt_id"],
            )

    def status(self) -> dict[str, Any]:
        with self._connection() as connection:
            rows = list(connection.execute("SELECT * FROM requests ORDER BY created_at,id"))
            totals = self._totals(rows)
            known = self.config.opening_spend_usd + Decimal(totals["known_request_cost_usd"])
            committed = self.config.opening_spend_usd + Decimal(
                totals["committed_request_cost_usd"]
            )
            blocked_usage = bool(totals["pending_requests"] or totals["uncertain_requests"])
            attempts = []
            for attempt in connection.execute("SELECT * FROM attempts ORDER BY created_at,id"):
                own = self._totals([row for row in rows if row["attempt_id"] == attempt["id"]])
                attempts.append(
                    {
                        "attempt_id": attempt["id"],
                        "attempt_hash": attempt["attempt_hash"],
                        **{
                            key: _text(value) if isinstance(value, Decimal) else value
                            for key, value in own.items()
                        },
                    }
                )
            return {
                "schema_version": 1,
                "campaign": self.config.as_dict(),
                "ledger_config_hash": self._config_digest,
                "opening_spend_usd": _text(self.config.opening_spend_usd),
                "admission_ceiling_usd": _text(self.config.admission_ceiling_usd),
                "user_ceiling_usd": _text(self.config.user_ceiling_usd),
                "attempt_cap_usd": _text(self.config.attempt_cap_usd),
                **{
                    key: _text(value) if isinstance(value, Decimal) else value
                    for key, value in totals.items()
                },
                "known_spend_usd": _text(known),
                "committed_spend_usd": _text(committed),
                "admission_headroom_usd": _text(
                    max(ZERO, self.config.admission_ceiling_usd - committed)
                ),
                "user_ceiling_headroom_usd": _text(
                    max(ZERO, self.config.user_ceiling_usd - committed)
                ),
                "cost_complete": not blocked_usage,
                "admissions_blocked": blocked_usage
                or committed >= self.config.admission_ceiling_usd,
                "blocked_reason": "unsettled_usage"
                if blocked_usage
                else (
                    "admission_ceiling" if committed >= self.config.admission_ceiling_usd else None
                ),
                "attempts": attempts,
                "requests": [dict(row) for row in rows],
            }
