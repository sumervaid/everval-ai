"""Fetch immutable native-runtime artifacts, checking bytes before installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HOSTS = {"repo.anaconda.com", "files.pythonhosted.org"}
MAX_BYTES = 1024 * 1024 * 1024


def artifact(item: dict[str, Any], kind: str) -> tuple[str, int]:
    name = item["fn"] if kind == "conda" else item["filename"]
    size = item["size"] if kind == "conda" else item["size_bytes"]
    url = urllib.parse.urlsplit(item["url"])
    if (
        not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]+", name)
        or name in {".", ".."}
        or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        or type(size) is not int
        or not 1 <= size <= MAX_BYTES
        or url.scheme != "https"
        or url.hostname not in HOSTS
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError("Unsafe or incomplete locked artifact")
    return name, size


def fetch_one(item: dict[str, Any], kind: str, output: Path) -> Path:
    name, size = artifact(item, kind)
    output.mkdir(parents=True, exist_ok=True)
    target = output / name
    if target.is_symlink():
        raise ValueError("Locked artifact must not be a symlink")
    if target.exists():
        if (
            target.stat().st_size != size
            or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise ValueError("Existing artifact differs from the lock")
        return target
    temporary = target.with_suffix(target.suffix + ".partial")
    owned_partial = False
    try:
        with urllib.request.urlopen(item["url"], timeout=60) as response:
            if urllib.parse.urlsplit(response.geturl()).hostname not in HOSTS:
                raise ValueError("Artifact redirect left the declared public source hosts")
            with temporary.open("xb") as stream:
                owned_partial = True
                digest = hashlib.sha256()
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise ValueError("Artifact exceeds its locked byte count")
                    digest.update(chunk)
                    stream.write(chunk)
        if count != size or digest.hexdigest() != item["sha256"]:
            raise ValueError("Artifact bytes differ from the lock")
        temporary.replace(target)
    finally:
        if owned_partial:
            temporary.unlink(missing_ok=True)
    return target


def fetch(kind: str, output: Path, root: Path | None = None) -> None:
    root = root or Path(__file__).resolve().parent
    lock = "conda-packages.lock.json" if kind == "conda" else "wheels.lock.json"
    manifest = json.loads((root / "manifest-sha256.json").read_text())
    for name in (lock, "runtime-pip.txt", "provenance.json", "fetch_locked.py"):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != manifest[name]:
            raise ValueError("Published preparation source or lock changed")
    value = json.loads((root / lock).read_text())
    rows = value["artifacts"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("Expected a bounded artifact list")
    names = [artifact(item, kind)[0] for item in rows]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate locked artifact filename")
    paths = [fetch_one(item, kind, output) for item in rows]
    if kind == "conda":
        lines = ["@EXPLICIT"]
        for item, path in zip(rows, paths, strict=True):
            if not re.fullmatch(r"[0-9a-f]{32}", item["md5"]):
                raise ValueError("Conda explicit entry requires the retained MD5")
            lines.append(path.resolve().as_uri() + "#" + item["md5"])
        (output / "local-explicit.txt").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("conda", "wheels"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.kind, args.output)


if __name__ == "__main__":
    main()
