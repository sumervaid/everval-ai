"""Versioned data-only task packs with explicit review and local admission."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.domains import validate_collection
from cvals.models import Collection, Manifest, canonical_json, content_hash, new_id, utc_now
from cvals.pure_checks import assert_pure_work, assert_schema_bounded, count_json_nodes
from cvals.store import Store

MAX_PACK_BYTES = 4 * 1024 * 1024
PURE_CHECKS = {"equals", "contains", "numeric", "json_schema", "citation", "forbidden"}
LICENSES = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "CC-BY-4.0"}


class PackProvenance(Manifest):
    origin: str = Field(min_length=1, max_length=2000)
    authors: list[str] = Field(min_length=1, max_length=30)
    permission_basis: str = Field(min_length=1, max_length=4000)

    @field_validator("origin", "permission_basis")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provenance must be nonblank")
        return value.strip()

    @field_validator("authors")
    @classmethod
    def valid_authors(cls, value: list[str]) -> list[str]:
        if any(not name.strip() or len(name) > 200 for name in value):
            raise ValueError("Authors must be nonblank names of at most 200 characters")
        return value


class PackRuntime(Manifest):
    executor: Literal["offline-checks"] = "offline-checks"
    revision: Literal["1"] = "1"
    network: Literal["none"] = "none"
    code_execution: Literal[False] = False


class CommunityPack(Manifest):
    format: Literal["everval.community-pack"] = "everval.community-pack"
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", max_length=40
    )
    license: str
    provenance: PackProvenance
    runtime: PackRuntime = Field(default_factory=PackRuntime)
    purpose: Literal["installation-control", "benchmark"]
    collection: Collection
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def contract(self) -> Self:
        if self.license not in LICENSES:
            raise ValueError(
                "Use a supported explicit SPDX license: " + ", ".join(sorted(LICENSES))
            )
        if self.collection.benchmark_release_id or self.collection.benchmark_partition:
            raise ValueError("Portable packs cannot claim local repository release attestations")
        if len(self.collection.scenarios) > 100:
            raise ValueError("A community pack contains at most 100 scenarios")
        for scenario in self.collection.scenarios:
            if len(scenario.checks) > 20:
                raise ValueError("A community scenario contains at most 20 checks")
            if any(check.kind not in PURE_CHECKS for check in scenario.checks):
                raise ValueError("Executable and custom graders are forbidden in community packs")
            if scenario.reference_output is None:
                raise ValueError("Each packed scenario requires a private reference control")
            negatives = scenario.provenance.get("negative_controls")
            if (
                not isinstance(negatives, list)
                or not 1 <= len(negatives) <= 20
                or any(not isinstance(n, dict) for n in negatives)
            ):
                raise ValueError("Each packed scenario requires 1–20 negative control objects")
        value = self.model_dump(mode="json", exclude={"digest"})
        if content_hash(value) != self.digest:
            raise ValueError("Community pack content digest does not match")
        return self


def _bounded_tree(value: Any, depth: int = 0, counter: list[int] | None = None) -> None:
    count = counter if counter is not None else [0]
    count[0] += 1
    if depth > 40 or count[0] > 50000:
        raise ValueError("Pack structure exceeds the nesting or node limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Pack keys must be strings")
            _bounded_tree(item, depth + 1, count)
    elif isinstance(value, list):
        for item in value:
            _bounded_tree(item, depth + 1, count)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are forbidden")
        result[key] = value
    return result


def parse_pack(raw: bytes) -> CommunityPack:
    if not raw or len(raw) > MAX_PACK_BYTES:
        raise ValueError("Pack must be nonempty JSON within the 4 MiB limit")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        _bounded_tree(value)
        # Reject potentially recursive schemas before model validation.
        if isinstance(value, dict) and isinstance(value.get("collection"), dict):
            scenarios = value["collection"].get("scenarios", [])
            for scenario in scenarios if isinstance(scenarios, list) else []:
                if isinstance(scenario, dict):
                    checks = scenario.get("checks", [])
                    for check in checks if isinstance(checks, list) else []:
                        if isinstance(check, dict) and check.get("kind") == "json_schema":
                            assert_schema_bounded(check.get("expected"))
        pack = CommunityPack.model_validate(value)
        if len(canonical_json(pack.model_dump(mode="json")).encode()) > MAX_PACK_BYTES:
            raise ValueError("Expanded pack exceeds the 4 MiB limit")
        work = 0
        for scenario in pack.collection.scenarios:
            controls = [scenario.reference_output, *scenario.provenance["negative_controls"]]
            for control in controls:
                assert_pure_work(scenario, control)
            for check in scenario.checks:
                factor = count_json_nodes(check.expected) if check.kind == "json_schema" else 1
                work += factor * sum(count_json_nodes(control) for control in controls)
        if work > 2_000_000:
            raise ValueError("Pack control validation exceeds the bounded work budget")
        return pack
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Pack must be bounded UTF-8 JSON") from exc


def read_pack(path: Path) -> CommunityPack:
    with path.open("rb") as stream:
        return parse_pack(stream.read(MAX_PACK_BYTES + 1))


def make_pack(
    *,
    name: str,
    slug: str,
    collection: Collection,
    provenance: PackProvenance,
    purpose: Literal["installation-control", "benchmark"] = "benchmark",
    license: str = "Apache-2.0",
    version: str = "1.0.0",
) -> CommunityPack:
    value = {
        "format": "everval.community-pack",
        "schema_version": 1,
        "name": name,
        "slug": slug,
        "version": version,
        "license": license,
        "provenance": provenance.model_dump(mode="json"),
        "runtime": PackRuntime().model_dump(mode="json"),
        "purpose": purpose,
        "collection": collection.model_dump(mode="json"),
    }
    return parse_pack(canonical_json({**value, "digest": content_hash(value)}).encode())


def pack_summary(pack: CommunityPack) -> dict[str, Any]:
    return {
        "name": pack.name,
        "slug": pack.slug,
        "version": pack.version,
        "license": pack.license,
        "digest": pack.digest,
        "purpose": pack.purpose,
        "runtime": pack.runtime.model_dump(mode="json"),
        "provenance": pack.provenance.model_dump(mode="json"),
        "scenario_count": len(pack.collection.scenarios),
        "domains": sorted({s.domain for s in pack.collection.scenarios}),
        "trust": "Digest proves content identity, not publisher identity or task quality.",
    }


def write_pack(pack: CommunityPack, path: Path, *, include_hidden: bool = False) -> dict[str, Any]:
    if not include_hidden:
        raise ValueError(
            "Pack exports contain hidden checks, reference outputs, and controls; pass --include-hidden deliberately"
        )
    payload = (canonical_json(pack.model_dump(mode="json")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
    return {
        "path": str(path),
        "digest": pack.digest,
        "bytes": len(payload),
        "contains_hidden_material": True,
        "uploaded": False,
    }


class CommunityPacks:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        with self.store._connect(write=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS community_packs(id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, data TEXT NOT NULL, staged_at TEXT NOT NULL, actor TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS community_approvals(id TEXT PRIMARY KEY, pack_id TEXT NOT NULL UNIQUE REFERENCES community_packs(id), collection_id TEXT NOT NULL, data TEXT NOT NULL)"
            )
            for table in ("community_packs", "community_approvals"):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Pack evidence is immutable'); END"
                    )

    @staticmethod
    def catalog() -> dict[str, Any]:
        from cvals.demo import builtin_catalog

        return builtin_catalog()

    @staticmethod
    def _actor(actor: str) -> str:
        if not actor.strip() or len(actor) > 200:
            raise ValueError(
                "A nonblank local reviewer identity of at most 200 characters is required"
            )
        return actor.strip()

    def stage(self, raw: bytes, *, actor: str) -> dict[str, Any]:
        pack = parse_pack(raw)
        actor = self._actor(actor)
        with self.store._connect(write=True) as db:
            existing = db.execute(
                "SELECT id FROM community_packs WHERE digest=?", (pack.digest,)
            ).fetchone()
            if existing:
                identifier = str(existing[0])
            else:
                identifier = new_id()
                db.execute(
                    "INSERT INTO community_packs VALUES(?,?,?,?,?)",
                    (
                        identifier,
                        pack.digest,
                        canonical_json(pack.model_dump(mode="json")),
                        utc_now(),
                        actor,
                    ),
                )
                append_audit(
                    db,
                    "pack.staged",
                    identifier,
                    {"digest": pack.digest, "scenario_count": len(pack.collection.scenarios)},
                    actor,
                )
        return self.get(identifier)

    def _pack(self, identifier: str) -> CommunityPack:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT data FROM community_packs WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            return parse_pack(row[0].encode())

    def get(self, identifier: str, *, include_hidden: bool = False) -> dict[str, Any]:
        pack = self._pack(identifier)
        with self.store._connect() as db:
            row = db.execute(
                "SELECT staged_at,actor FROM community_packs WHERE id=?", (identifier,)
            ).fetchone()
            approval = db.execute(
                "SELECT data FROM community_approvals WHERE pack_id=?", (identifier,)
            ).fetchone()
        approval_data = json.loads(approval[0]) if approval else None
        public_approval = (
            {
                k: v
                for k, v in approval_data.items()
                if k not in {"validation", "expected_behaviors"}
            }
            if approval_data
            else None
        )
        result = {
            "id": identifier,
            **pack_summary(pack),
            "staged_at": row[0],
            "actor": row[1],
            "status": "admitted" if approval else "staged",
            "approval": approval_data if include_hidden else public_approval,
            "public_tasks": [s.agent_view() for s in pack.collection.scenarios],
        }
        if include_hidden:
            result["private_pack"] = pack.model_dump(mode="json")
        return result

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            ids = [
                str(row[0])
                for row in db.execute(
                    "SELECT id FROM community_packs ORDER BY rowid DESC LIMIT 500"
                )
            ]
        return [
            {k: v for k, v in self.get(identifier).items() if k != "public_tasks"}
            for identifier in ids
        ]

    def approve(
        self, identifier: str, *, actor: str, rationale: str, expected_behaviors: dict[str, str]
    ) -> dict[str, Any]:
        actor = self._actor(actor)
        if not rationale.strip() or len(rationale) > 10000:
            raise ValueError("Review rationale must contain 1–10000 characters")
        pack = self._pack(identifier)
        ids = {s.id for s in pack.collection.scenarios}
        if set(expected_behaviors) != ids or any(
            not isinstance(v, str) or not v.strip() or len(v) > 10000
            for v in expected_behaviors.values()
        ):
            raise ValueError(
                "Review must independently describe expected behavior for every scenario ID"
            )
        scenarios = []
        for original in pack.collection.scenarios:
            scenario = original.model_copy(deep=True)
            scenario.description = expected_behaviors[original.id].strip()
            scenario.provenance = {
                "origin": "community_pack",
                "pack_digest": pack.digest,
                "pack_id": identifier,
                "license": pack.license,
                "purpose": pack.purpose,
                "approved_by": actor,
                "negative_controls": original.provenance["negative_controls"],
            }
            scenarios.append(scenario)
        collection = Collection(
            name=pack.name,
            version=pack.version,
            domain=pack.collection.domain,
            scenarios=scenarios,
            description="Reviewed community pack; local controls do not certify representativeness.",
        )
        validation = validate_collection(collection, self.settings)
        if not validation["valid"]:
            raise ValueError(
                "Reference and negative controls failed validation: " + canonical_json(validation)
            )
        record = {
            "id": new_id(),
            "pack_id": identifier,
            "pack_digest": pack.digest,
            "collection_id": collection.id,
            "collection_hash": content_hash(collection.model_dump(mode="json")),
            "actor": actor,
            "identity_scope": "trusted local operator label",
            "rationale": rationale.strip(),
            "expected_behaviors": expected_behaviors,
            "created_at": utc_now(),
            "validation": validation,
        }
        with self.store._connect(write=True) as db:
            if db.execute(
                "SELECT 1 FROM community_approvals WHERE pack_id=?", (identifier,)
            ).fetchone():
                raise ValueError(
                    "This pack is already admitted; changed content requires a new digest"
                )
            db.execute(
                "INSERT INTO collections VALUES(?,?)",
                (collection.id, canonical_json(collection.model_dump(mode="json"))),
            )
            db.execute(
                "INSERT INTO community_approvals VALUES(?,?,?,?)",
                (record["id"], identifier, collection.id, canonical_json(record)),
            )
            append_audit(
                db,
                "pack.admitted",
                identifier,
                {
                    "approval_id": record["id"],
                    "collection_id": collection.id,
                    "digest": pack.digest,
                    "collection_hash": record["collection_hash"],
                },
                actor,
            )
        return record

    def export(
        self, identifier: str, path: Path, *, include_hidden: bool = False
    ) -> dict[str, Any]:
        return write_pack(self._pack(identifier), path, include_hidden=include_hidden)
