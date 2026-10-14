"""Bounded local source previews and read-only Git history inspection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from cvals.domains._process import run_bounded

MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_RECORDS = 10_000
MAX_COLUMNS = 200
MAX_DEPTH = 32
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


def _bounded_json(value: Any, depth: int = 0) -> int:
    if depth > MAX_DEPTH:
        raise ValueError(f"JSON exceeds the nesting limit of {MAX_DEPTH}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, dict):
        if len(value) > MAX_RECORDS:
            raise ValueError("JSON object has too many fields")
        return 1 + sum(_bounded_json(item, depth + 1) for item in value.values())
    if isinstance(value, list):
        if len(value) > MAX_RECORDS:
            raise ValueError("JSON list has too many records")
        return 1 + sum(_bounded_json(item, depth + 1) for item in value)
    return 1


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _pdf(content: bytes) -> dict[str, Any]:
    worker = Path(__file__).with_name("domains") / "_pdf_worker.py"
    try:
        process = run_bounded(
            [sys.executable, "-I", str(worker)],
            payload=content,
            limit_bytes=12 * 1024 * 1024,
            timeout=12,
            env={key: value for key, value in os.environ.items() if key in {"PATH", "TMPDIR"}},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("PDF extraction exceeded the time limit or could not start") from exc
    if process.returncode:
        raise ValueError(
            "PDF extraction failed; file may be malformed, encrypted, too complex, or exceed limits"
        )
    try:
        result = json.loads(process.stdout)
    except ValueError as exc:
        raise ValueError("PDF extraction did not produce a usable preview") from exc
    if result.get("error"):
        raise ValueError(str(result["error"]))
    return dict(result)


def ingest_source(filename: str, content: bytes) -> dict[str, Any]:
    """Return a local preview; importing material does not manufacture a benchmark."""
    if not isinstance(filename, str) or not filename or "\x00" in filename or len(filename) > 255:
        raise ValueError("Supply a filename with a supported extension")
    if not isinstance(content, bytes) or not content or len(content) > MAX_SOURCE_BYTES:
        raise ValueError("Source files must contain between 1 byte and 5 MiB")
    suffix = Path(filename).suffix.lower()
    digest = hashlib.sha256(content).hexdigest()
    base: dict[str, Any] = {
        "id": f"source-{digest[:16]}",
        "filename": PurePosixPath(filename.replace("\\", "/")).name,
        "sha256": digest,
        "bytes": len(content),
        "notice": "Source preview only. Review objectives and independent success criteria before creating scenarios.",
    }
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("The file does not have a PDF header")
        return {**base, "kind": "document", **_pdf(content)}
    if suffix not in {".csv", ".tsv", ".json", ".txt", ".md"}:
        raise ValueError("Supported source types: CSV, TSV, JSON, TXT, MD, and text-based PDF")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Text sources must use UTF-8 encoding") from exc
    if "\x00" in text:
        raise ValueError("Text sources may not contain NUL bytes")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Extracted text exceeds {MAX_TEXT_CHARS} characters")
    if suffix in {".txt", ".md"}:
        return {
            **base,
            "kind": "document",
            "text": text,
            "sources": [{"id": base["id"], "text": text}],
        }
    if suffix == ".json":
        try:
            data = json.loads(text, object_pairs_hook=_unique_object)
            nodes = _bounded_json(data)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Malformed or excessively nested JSON") from exc
        if nodes > 100_000:
            raise ValueError("JSON contains too many values")
        if not isinstance(data, (dict, list)):
            raise ValueError("JSON sources must contain an object or list")
        return {
            **base,
            "kind": "structured",
            "data": data,
            "records": data if isinstance(data, list) else None,
            "record_count": len(data),
        }
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter="\t" if suffix == ".tsv" else ",", strict=True
        )
        header = next(reader, [])
        if not header or len(header) > MAX_COLUMNS or any(not key.strip() for key in header):
            raise ValueError("A table needs 1–200 named columns")
        if len(set(header)) != len(header):
            raise ValueError("Table column names must be unique")
        records: list[dict[str, str]] = []
        for number, row in enumerate(reader, start=2):
            if len(records) >= MAX_RECORDS:
                raise ValueError(f"Tables are limited to {MAX_RECORDS} records")
            if len(row) != len(header):
                raise ValueError(f"Row {number} has {len(row)} fields; expected {len(header)}")
            records.append(dict(zip(header, row, strict=True)))
    except csv.Error as exc:
        raise ValueError(f"Malformed table: {exc}") from exc
    return {
        **base,
        "kind": "table",
        "columns": header,
        "records": records,
        "record_count": len(records),
    }


def _repository(path: str) -> Path:
    location = Path(path).expanduser()
    if not location.is_absolute():
        raise ValueError("Repository paths must be absolute local paths")
    if location.is_symlink():
        raise ValueError("Repository paths may not be symbolic links")
    try:
        location = location.resolve(strict=True)
    except OSError as exc:
        raise ValueError("The repository path does not exist") from exc
    if not location.is_dir():
        raise ValueError("The repository path is not a directory")
    if not shutil.which("git"):
        raise ValueError("Git is required to inspect repository history")
    return location


def _git(path: Path, *args: str, limit_bytes: int = 2 * 1024 * 1024) -> bytes:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR"}}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
    )
    try:
        process = run_bounded(
            [
                "git",
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "log.showSignature=false",
                "-c",
                "diff.external=",
                "-c",
                "core.attributesfile=/dev/null",
                "-C",
                str(path),
                *args,
            ],
            limit_bytes=limit_bytes,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Git inspection failed or exceeded its time limit") from exc
    if process.returncode:
        raise ValueError(
            "The path is not a readable repository or the requested history is unavailable"
        )
    if len(process.stdout) > limit_bytes:
        raise ValueError("Repository metadata exceeds the preview size limit")
    return process.stdout


def inspect_repository(path: str, limit: int = 20) -> dict[str, Any]:
    """Inspect commit metadata without checkout, hooks, builds, or repository execution."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Candidate limit must be between 1 and 100")
    location = _repository(path)
    inside = _git(location, "rev-parse", "--is-inside-work-tree").strip()
    if inside != b"true":
        raise ValueError("Inspect a working repository; bare repositories are not supported")
    head = _git(location, "rev-parse", "--verify", "HEAD").decode().strip()
    raw = _git(
        location,
        "log",
        "--no-show-signature",
        f"-{limit}",
        "--format=%H%x00%P%x00%aI%x00%s%x00",
    ).decode("utf-8", "replace")
    chunks = raw.split("\x00")
    candidates = []
    for offset in range(0, len(chunks) - 3, 4):
        commit, parents_text, timestamp, subject = chunks[offset : offset + 4]
        commit = commit.strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            continue
        parents = parents_text.split()
        reason = (
            "Root commit has no reconstructable base"
            if not parents
            else ("Merge commit needs an explicit base review" if len(parents) > 1 else None)
        )
        names: list[str] = []
        if len(parents) == 1:
            names = [
                item.decode("utf-8", "replace")
                for item in _git(
                    location,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    "--no-ext-diff",
                    parents[0],
                    commit,
                ).split(b"\x00")
                if item
            ]
        candidates.append(
            {
                "commit": commit,
                "base_commit": parents[0] if len(parents) == 1 else None,
                "parents": parents,
                "timestamp": timestamp,
                "subject": subject,
                "changed_files": names,
                "test_files": [
                    name
                    for name in names
                    if (PurePosixPath(name).name.startswith("test_") or "/tests/" in f"/{name}")
                ],
                "review_status": "needs_review" if reason is None else "unsupported",
                "reason": reason,
            }
        )
    tree = _git(location, "ls-tree", "-r", "--name-only", "-z", "HEAD").split(b"\x00")
    names = [name.decode("utf-8", "replace") for name in tree if name]
    return {
        "path": str(location),
        "head": head,
        "candidates": candidates,
        "tracked_files": len(names),
        "languages": sorted(
            {
                language
                for extension, language in {
                    ".py": "Python",
                    ".ts": "TypeScript",
                    ".js": "JavaScript",
                    ".go": "Go",
                    ".rs": "Rust",
                }.items()
                if any(name.endswith(extension) for name in names)
            }
        ),
        "executed_repository_code": False,
        "notice": "Metadata candidates are unvalidated. Review licenses, task instructions, base/reference snapshots, and hidden tests.",
    }


def snapshot_repository(path: str, revision: str, *, include_tests: bool = False) -> dict[str, Any]:
    """Read UTF-8 source blobs from an exact commit, without materializing Git history."""
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError("Snapshot revision must be a full immutable commit hash")
    location = _repository(path)
    commit = _git(location, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    raw = _git(location, "ls-tree", "-r", "-z", commit)
    files: dict[str, str] = {}
    omitted: list[dict[str, str]] = []
    total = 0
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            omitted.append(
                {"path": raw_name.decode("utf-8", "replace"), "reason": "non-UTF-8 path"}
            )
            continue
        parts = PurePosixPath(name).parts
        if mode not in {"100644", "100755"} or kind != "blob":
            omitted.append({"path": name, "reason": "symlink, submodule, or unsupported file type"})
            continue
        if (
            PurePosixPath(name).is_absolute()
            or any(part in {"..", ".git"} for part in parts)
            or "\\" in name
            or any(part.startswith("__cvals") for part in parts)
        ):
            raise ValueError("Git tree contains an unsafe path")
        if not include_tests and (
            any(part in {"tests", "test"} for part in parts)
            or PurePosixPath(name).name.startswith("test_")
        ):
            omitted.append({"path": name, "reason": "test material excluded from agent snapshot"})
            continue
        size = int(_git(location, "cat-file", "-s", object_id).strip())
        if size > 512 * 1024 or total + size > MAX_SNAPSHOT_BYTES or len(files) >= 128:
            omitted.append({"path": name, "reason": "snapshot size or file count limit"})
            continue
        data = _git(location, "cat-file", "blob", object_id, limit_bytes=512 * 1024)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            omitted.append({"path": name, "reason": "binary content"})
            continue
        if "\x00" in text:
            omitted.append({"path": name, "reason": "binary content"})
            continue
        files[name] = text
        total += size
    return {
        "commit": commit,
        "files": files,
        "omitted": omitted,
        "bytes": total,
        "includes_git_history": False,
        "includes_tests": include_tests,
        "complete": not omitted,
        "notice": "Review every included file for answers, secrets, and licensing before using as agent input.",
    }
