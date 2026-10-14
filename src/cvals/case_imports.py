"""Redacted production examples that become tests only after explicit human approval."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import PurePath
from typing import Any, Literal

from pydantic import Field, field_validator

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.domains import validate_collection
from cvals.models import (
    Check,
    Collection,
    Manifest,
    Scenario,
    canonical_json,
    content_hash,
    new_id,
    utc_now,
)
from cvals.store import Store

DEFAULT_FIELDS = [
    "password",
    "token",
    "api_key",
    "authorization",
    "email",
    "customer_email",
    "secret",
]


class CaseInput(Manifest):
    name: str = Field(min_length=1, max_length=200)
    domain: Literal["documents", "data", "workflow"]
    instruction: str = Field(min_length=1, max_length=100000)
    inputs: dict[str, Any]
    observed_output: dict[str, Any] | None = None
    source_id: str = Field(default="", max_length=500)


class Approval(Manifest):
    expected_behavior: str = Field(min_length=1, max_length=10000)
    rationale: str = Field(min_length=1, max_length=10000)
    reference_output: dict[str, Any]
    checks: list[Check] = Field(min_length=1, max_length=100)
    negative_controls: list[dict[str, Any]] = Field(
        default_factory=lambda: [dict[str, Any]()], min_length=1, max_length=20
    )

    @field_validator("expected_behavior", "rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Approved behavior and approval rationale are required")
        return value.strip()

    @field_validator("checks")
    @classmethod
    def no_execution(cls, checks: list[Check]) -> list[Check]:
        if any(check.kind == "python_tests" for check in checks):
            raise ValueError(
                "Production-case approval supports document, data, and workflow checks only"
            )
        return checks


class CaseImports:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        with self.store._connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS case_imports (
                id TEXT PRIMARY KEY, data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS production_cases (
                id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES case_imports(id),
                data TEXT NOT NULL, status TEXT NOT NULL, collection_id TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS case_approvals (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL UNIQUE REFERENCES production_cases(id),
                data TEXT NOT NULL)""")
            for operation in ("UPDATE", "DELETE"):
                db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS case_approvals_no_{operation.lower()} BEFORE {operation} ON case_approvals BEGIN SELECT RAISE(ABORT, 'Approvals are immutable'); END"
                )

    @staticmethod
    def _redact(value: Any, fields: set[str], literals: list[str]) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                safe_key = CaseImports._redact(key, set(), literals)
                if safe_key in redacted:
                    raise ValueError("Redaction would merge distinct input field names")
                redacted[safe_key] = (
                    "[REDACTED]"
                    if key.casefold() in fields
                    else CaseImports._redact(item, fields, literals)
                )
            return redacted
        if isinstance(value, list):
            return [CaseImports._redact(item, fields, literals) for item in value]
        if isinstance(value, str):
            for literal in sorted(literals, key=len, reverse=True):
                value = value.replace(literal, "[REDACTED]")
        return value

    def stage(
        self,
        raw: bytes,
        filename: str,
        actor: str,
        *,
        redacted_fields: list[str] | None = None,
        redacted_literals: list[str] | None = None,
    ) -> dict[str, Any]:
        if not raw or len(raw) > min(self.settings.max_request_bytes, 16 * 1024 * 1024):
            raise ValueError("Choose a nonempty file within the workspace upload limit")
        fields = redacted_fields if redacted_fields is not None else DEFAULT_FIELDS
        literals = redacted_literals or []
        if (
            len(fields) > 100
            or len(literals) > 100
            or any(not isinstance(v, str) or not v or len(v) > 1000 for v in fields + literals)
        ):
            raise ValueError(
                "Redaction supports at most 100 nonempty field names and 100 literals, each at most 1000 characters"
            )
        try:
            text = raw.decode("utf-8-sig")
            suffix = PurePath(filename).suffix.lower()
            if suffix == ".csv":
                reader = csv.DictReader(io.StringIO(text))
                if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                    raise ValueError("CSV needs unique column names")
                rows = []
                for row in reader:
                    if None in row:
                        raise ValueError("CSV row has more cells than its header")
                    for field in ("inputs", "observed_output"):
                        if row.get(field):
                            row[field] = json.loads(row[field])
                        elif field == "observed_output":
                            row[field] = None
                    rows.append(row)
            elif suffix == ".jsonl":
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            elif suffix == ".json":
                body = json.loads(text)
                rows = body["cases"] if isinstance(body, dict) and set(body) == {"cases"} else body
            else:
                raise ValueError("Choose CSV, JSONL, or JSON")
        except (UnicodeDecodeError, json.JSONDecodeError, csv.Error, RecursionError) as exc:
            raise ValueError("The production examples file could not be parsed") from exc
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ValueError("An import must contain between 1 and 500 cases")
        import_id = new_id()
        safe_name = "".join(c for c in PurePath(filename).name if ord(c) >= 32)[:180]
        info = {
            "id": import_id,
            "filename": self._redact(safe_name, set(), literals),
            "source_hash": hashlib.sha256(raw).hexdigest(),
            "created_at": utc_now(),
            "actor": actor,
            "count": len(rows),
            "redacted_fields": fields,
            "literal_redaction_count": len(literals),
            "privacy_note": "Only redacted cases are retained. Field and literal redaction does not guarantee removal of every sensitive value; inspect each staged case.",
        }
        cases = []
        for index, row in enumerate(rows):
            redacted = self._redact(row, {field.casefold() for field in fields}, literals)
            parsed_case = CaseInput.model_validate(redacted)
            value = {
                **parsed_case.model_dump(mode="json"),
                "id": new_id(),
                "import_id": import_id,
                "provenance": {
                    "source_hash": info["source_hash"],
                    "row": index + 1,
                    "source_id": parsed_case.source_id,
                    "import_id": import_id,
                },
            }
            cases.append(value)
        with self.store._connect(write=True) as db:
            db.execute("INSERT INTO case_imports VALUES(?,?)", (import_id, canonical_json(info)))
            for item in cases:
                db.execute(
                    "INSERT INTO production_cases VALUES(?,?,?,'staged',NULL)",
                    (item["id"], import_id, canonical_json(item)),
                )
            append_audit(
                db,
                "cases.staged",
                import_id,
                {
                    "source_hash": info["source_hash"],
                    "count": len(cases),
                    "redacted_fields": fields,
                    "literal_redaction_count": len(literals),
                },
                actor,
            )
        return {**info, "cases": [{**item, "status": "staged", "approval": None} for item in cases]}

    def imports(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM case_imports ORDER BY rowid DESC LIMIT 500")
            ]

    def get(self, import_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT data FROM case_imports WHERE id=?", (import_id,)).fetchone()
            if not row:
                raise KeyError(import_id)
            cases = []
            for item in db.execute(
                "SELECT * FROM production_cases WHERE import_id=? ORDER BY rowid", (import_id,)
            ):
                approval = db.execute(
                    "SELECT data FROM case_approvals WHERE case_id=?", (item["id"],)
                ).fetchone()
                cases.append(
                    {
                        **json.loads(item["data"]),
                        "status": item["status"],
                        "collection_id": item["collection_id"],
                        "approval": json.loads(approval[0]) if approval else None,
                    }
                )
            return {**json.loads(row[0]), "cases": cases}

    def approve(self, case_id: str, approval: Approval, actor: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT data,status FROM production_cases WHERE id=?", (case_id,)
            ).fetchone()
            if not row:
                raise KeyError(case_id)
            if row["status"] != "staged":
                raise ValueError("Approved cases are immutable; stage a corrected case separately")
            case = json.loads(row["data"])
        scenario = Scenario(
            name=case["name"],
            domain=case["domain"],
            instruction=case["instruction"],
            inputs=case["inputs"],
            description=approval.expected_behavior,
            reference_output=approval.reference_output,
            checks=approval.checks,
            provenance={
                **case["provenance"],
                "case_id": case_id,
                "expected_behavior": approval.expected_behavior,
                "approved_by": actor,
                "approval_rationale": approval.rationale,
                "negative_controls": approval.negative_controls,
            },
        )
        validation = validate_collection(
            Collection(name="Production case approval", scenarios=[scenario]), self.settings
        )
        if not validation["valid"]:
            raise ValueError(
                "Reference and negative controls did not validate: " + canonical_json(validation)
            )
        value = {
            "id": new_id(),
            "case_id": case_id,
            "actor": actor,
            "created_at": utc_now(),
            **approval.model_dump(mode="json"),
            "scenario": scenario.model_dump(mode="json"),
            "validation": validation,
        }
        with self.store._connect(write=True) as db:
            changed = db.execute(
                "UPDATE production_cases SET status='approved' WHERE id=? AND status='staged'",
                (case_id,),
            ).rowcount
            if not changed:
                raise ValueError("This case was already approved")
            db.execute(
                "INSERT INTO case_approvals VALUES(?,?,?)",
                (value["id"], case_id, canonical_json(value)),
            )
            append_audit(
                db,
                "cases.approved",
                case_id,
                {"approval_id": value["id"], "hash": content_hash(value)},
                actor,
            )
        return value

    def publish(self, import_id: str, case_ids: list[str], name: str, actor: str) -> dict[str, Any]:
        if not case_ids or len(set(case_ids)) != len(case_ids) or len(case_ids) > 500:
            raise ValueError("Choose distinct approved cases")
        with self.store._connect(write=True) as db:
            scenarios = []
            for case_id in case_ids:
                row = db.execute(
                    """SELECT c.status,a.data FROM production_cases c JOIN case_approvals a ON a.case_id=c.id
                       WHERE c.id=? AND c.import_id=?""",
                    (case_id, import_id),
                ).fetchone()
                if not row or row["status"] != "approved":
                    raise ValueError(
                        "Only approved, unpublished cases from this import can be published"
                    )
                scenarios.append(Scenario.model_validate(json.loads(row["data"])["scenario"]))
            collection = Collection(
                name=name,
                description="Human-approved production cases; observed outputs are not ground truth.",
                scenarios=scenarios,
            )
            validation = validate_collection(collection, self.settings)
            if not validation["valid"]:
                raise ValueError("The approved cases no longer satisfy validation")
            value = collection.model_dump(mode="json")
            db.execute(
                "INSERT INTO collections VALUES(?,?)", (collection.id, canonical_json(value))
            )
            for case_id in case_ids:
                db.execute(
                    "UPDATE production_cases SET status='published',collection_id=? WHERE id=?",
                    (collection.id, case_id),
                )
            append_audit(
                db,
                "collection.saved",
                collection.id,
                {"name": name, "hash": content_hash(value), "source": "approved_production_cases"},
                actor,
            )
            append_audit(
                db,
                "cases.published",
                import_id,
                {"collection_id": collection.id, "case_ids": case_ids, "hash": content_hash(value)},
                actor,
            )
            return {"collection": value, "validation": validation}
