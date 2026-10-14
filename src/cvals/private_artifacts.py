"""Inspected offline dependency imports. Credentials never enter this store."""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import urlsplit

from packaging.utils import parse_wheel_filename

from cvals.audit import AuditLog
from cvals.models import canonical_json, utc_now
from cvals.store import Store

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS_BYTES = 128 * 1024 * 1024


def registry_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or any(c.isspace() for c in value)
            or len(value) > 1000
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "Registry provenance must be an HTTPS URL without credentials or query parameters"
        ) from exc
    return value.rstrip("/")


def inspect_npm(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Package artifact exceeds 64 MiB")
    total = count = 0
    seen: set[str] = set()
    manifest = None
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive:
                count += 1
                total += member.size
                path = PurePosixPath(member.name)
                if (
                    count > 20000
                    or total > 256 * 1024 * 1024
                    or len(member.name) > 240
                    or str(path) != member.name.rstrip("/")
                    or any(ord(character) < 32 for character in member.name)
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.name
                    or not path.parts
                    or path.parts[0] != "package"
                    or member.name in seen
                    or not (member.isfile() or member.isdir())
                    or member.size < 0
                    or any(p in {".git", ".npmrc", ".env"} for p in path.parts)
                ):
                    raise ValueError(
                        "Npm archive contains unsafe paths, links, sensitive files or excessive content"
                    )
                seen.add(member.name)
                if member.name == "package/package.json":
                    if member.size > 64 * 1024:
                        raise ValueError("Npm manifest exceeds 64 KiB")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("Npm manifest is not a regular file")
                    manifest = json.loads(stream.read())
    except (tarfile.TarError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed npm package archive") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Npm archive must contain package/package.json")
    name, version = manifest.get("name"), manifest.get("version")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", name)
        or not isinstance(version, str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version)
    ):
        raise ValueError("Npm artifact needs a valid name and exact semantic version")
    return {
        "name": name,
        "version": version,
        "files": count,
        "expanded_bytes": total,
        "lifecycle_scripts_disabled": True,
    }


class PrivateArtifacts:
    def __init__(self, settings: Any):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        self.audit = AuditLog(settings)
        self.root = settings.data_dir / "dependency-artifacts"
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("Artifact storage cannot be a symbolic link")
        with self.store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS dependency_artifacts (id TEXT PRIMARY KEY,data TEXT NOT NULL)"
            )

    def import_file(self, path: str | Path, *, kind: str, registry: str) -> dict[str, Any]:
        registry = registry_url(registry)
        source = Path(path)
        if kind not in {"wheel", "npm"}:
            raise ValueError("Artifact kind must be wheel or npm")
        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
                    raise ValueError("Artifact must be a regular file of at most 64 MiB")
                payload = stream.read(MAX_ARTIFACT_BYTES + 1)
        except OSError as exc:
            raise ValueError(
                "Cannot read the local artifact as a regular, non-symlink file"
            ) from exc
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact exceeds 64 MiB")
        if kind == "wheel":
            from cvals.environments import _wheel_archive

            try:
                name, version, _, _ = parse_wheel_filename(source.name)
            except ValueError as exc:
                raise ValueError("Wheel filename is invalid") from exc
            transferred = io.BytesIO()
            with tarfile.open(fileobj=transferred, mode="w") as archive:
                item = tarfile.TarInfo(source.name)
                item.size = len(payload)
                archive.addfile(item, io.BytesIO(payload))
            _wheel_archive(transferred.getvalue())
            details = {"name": str(name), "version": str(version)}
            filename = source.name
        else:
            details = inspect_npm(payload)
            filename = hashlib.sha256(payload).hexdigest() + ".tgz"
        identity = hashlib.sha256(payload).hexdigest()
        target = self.root / identity
        if target.exists():
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != identity:
                raise ValueError("Artifact storage failed integrity verification")
        else:
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        record = {
            "id": identity,
            "sha256": identity,
            "kind": kind,
            "filename": filename,
            "registry": registry,
            "bytes": len(payload),
            "created_at": utc_now(),
            **details,
        }
        with self.store._connect(write=True) as connection:
            existing = connection.execute(
                "SELECT data FROM dependency_artifacts WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                previous = json.loads(existing[0])
                if previous["kind"] != kind or previous["registry"] != registry:
                    raise ValueError("Artifact already has different reviewed registry provenance")
                return cast(dict[str, Any], previous)
            connection.execute(
                "INSERT INTO dependency_artifacts(id,data) VALUES(?,?)",
                (identity, canonical_json(record)),
            )
            self.audit.record(
                "dependency_artifact.imported",
                identity,
                {
                    "kind": kind,
                    "registry": registry,
                    "name": details["name"],
                    "version": details["version"],
                },
                connection=connection,
            )
        return record

    def list(self) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT data FROM dependency_artifacts ORDER BY rowid DESC"
                )
            ]

    def get(self, identity: str) -> dict[str, Any]:
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("Artifact ID must be a full SHA-256 digest")
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT data FROM dependency_artifacts WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown dependency artifact")
        return cast(dict[str, Any], json.loads(row[0]))

    def read(self, identity: str) -> bytes:
        record = self.get(identity)
        descriptor = os.open(self.root / identity, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            payload = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != identity:
            raise ValueError("Dependency artifact failed integrity verification")
        return payload

    def resolve(
        self, identities: builtins.list[str] | None, *, kind: str
    ) -> builtins.list[dict[str, Any]]:
        if identities is None:
            return []
        if (
            not isinstance(identities, list)
            or len(identities) > 200
            or any(not isinstance(value, str) for value in identities)
            or len(set(identities)) != len(identities)
        ):
            raise ValueError("Select at most 200 unique dependency artifact IDs")
        records = [self.get(value) for value in identities]
        if (
            any(r["kind"] != kind for r in records)
            or sum(r["bytes"] for r in records) > MAX_ARTIFACTS_BYTES
        ):
            raise ValueError(
                "Artifacts have an incompatible kind or exceed the 128 MiB bundle limit"
            )
        return records
