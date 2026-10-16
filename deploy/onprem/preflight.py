"""Read-only startup checks for the supplied single-host systemd deployment.

This script does not initialize storage, enable authentication, contact a model,
or repair permissions. Run it as the service account through ExecStartPre.
"""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
import sqlite3
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class PreflightError(ValueError):
    """An operator must correct the deployment before starting it."""


@dataclass(frozen=True)
class Layout:
    root: Path = Path("/")

    def path(self, relative: str) -> Path:
        return self.root / relative


def _node(path: Path, *, owner: int, directory: bool, private: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise PreflightError(f"Expected a real {'directory' if directory else 'file'}: {path}")
    if info.st_uid != owner:
        raise PreflightError(f"Unexpected owner: {path}")
    mode = stat.S_IMODE(info.st_mode)
    if mode & (0o077 if private else 0o022):
        raise PreflightError(f"Permissions are too broad: {path}")
    if private and mode & (0o700 if directory else 0o600) != (0o700 if directory else 0o600):
        raise PreflightError(f"Owner permissions are insufficient: {path}")


def _ancestors(path: Path, root: Path, owner: int) -> None:
    if not path.is_relative_to(root):
        raise PreflightError("A protected path escaped the deployment root")
    for parent in (path, *path.parents):
        _node(parent, owner=owner, directory=True)
        if parent == root:
            return
    raise PreflightError("Protected path has no trusted root")


def _release(layout: Layout, trusted_uid: int) -> None:
    releases = layout.path("opt/everval/releases")
    _ancestors(releases, layout.root, trusted_uid)
    current = layout.path("opt/everval/current")
    info = current.lstat()
    if not stat.S_ISLNK(info.st_mode) or info.st_uid != trusted_uid:
        raise PreflightError("The current release must be a root-owned symlink")
    release = current.resolve(strict=True)
    if release.parent != releases:
        raise PreflightError("The current release must be a direct child of releases")
    _node(release, owner=trusted_uid, directory=True)
    required = ("venv/bin/python", "venv/bin/everval", "deploy/onprem/preflight.py")
    for relative in required:
        if not (release / relative).is_file():
            raise PreflightError(f"Release is incomplete: {relative}")
    # A writable module anywhere in the installed release could bypass the
    # preflight. Check the immutable tree, including symlink destinations.
    for number, path in enumerate(release.rglob("*"), start=1):
        if number > 100_000:
            raise PreflightError("Release tree exceeds the inspection limit")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != trusted_uid:
                raise PreflightError(f"Unexpected symlink owner: {path}")
            target = path.resolve(strict=True)
            if target.is_dir() and not target.is_relative_to(release):
                raise PreflightError("Release directory symlinks must stay inside the release")
            _ancestors(target if target.is_dir() else target.parent, layout.root, trusted_uid)
            _node(target, owner=trusted_uid, directory=target.is_dir())
        else:
            _node(path, owner=trusted_uid, directory=stat.S_ISDIR(info.st_mode))


def _team_enabled(database: Path, now: datetime) -> None:
    # mode=ro refuses to create a missing database. Do not call Store.init or
    # IdentityStore here: service startup must never bootstrap a fresh workspace.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT enabled FROM team_settings WHERE singleton=1").fetchone()
        if row != (1,):
            raise PreflightError("Team authentication is disabled; bootstrap it locally first")
        expiries = db.execute(
            "SELECT t.expires_at FROM team_tokens t JOIN team_users u ON u.id=t.user_id "
            "WHERE u.role='admin' AND u.active=1 AND t.revoked_at IS NULL"
        ).fetchall()
    for (expiry,) in expiries:
        try:
            value = datetime.fromisoformat(expiry)
        except (TypeError, ValueError):
            continue
        if value.tzinfo is not None and value > now:
            return
    raise PreflightError("No active administrator has an unexpired token; recover access locally")


def check_layout(
    layout: Layout,
    *,
    service_uid: int,
    service_gid: int,
    environment: Mapping[str, str],
    trusted_uid: int = 0,
    now: datetime | None = None,
) -> None:
    """Inspect explicit paths; trusted_uid is injectable only for portable tests."""
    if service_uid == 0:
        raise PreflightError("The application must not run as root")
    data = layout.path("var/lib/everval")
    cache = layout.path("var/cache/everval")
    config = layout.path("etc/everval")
    if environment.get("EVERVAL_DATA_DIR") != str(data):
        raise PreflightError("EVERVAL_DATA_DIR must select the initialized deployment workspace")
    if environment.get("XDG_CACHE_HOME") != str(cache):
        raise PreflightError("XDG_CACHE_HOME must select the deployment cache")
    if environment.get("EVERVAL_ALLOW_REMOTE_PROVIDERS", "").lower() not in {"false", "0"}:
        raise PreflightError("Public provider access must be explicitly disabled")
    if environment.get("CVALS_ALLOW_REMOTE_PROVIDERS", "").lower() in {"1", "true"}:
        raise PreflightError("Remove the conflicting legacy public-provider setting")
    if environment.get("EVERVAL_TOKEN") or environment.get("CVALS_TOKEN"):
        raise PreflightError(
            "Remove legacy shared tokens; this deployment requires personal tokens"
        )
    _release(layout, trusted_uid)
    for parent in (data.parent, cache.parent, config.parent):
        _ancestors(parent, layout.root, trusted_uid)
    _node(config, owner=trusted_uid, directory=True)
    if config.stat().st_gid != service_gid or stat.S_IMODE(config.stat().st_mode) != 0o750:
        raise PreflightError("Configuration directory must be root:everval mode 0750")
    shared = config / "everval.env"
    _node(shared, owner=trusted_uid, directory=False)
    if shared.stat().st_gid != service_gid or stat.S_IMODE(shared.stat().st_mode) != 0o640:
        raise PreflightError("Shared configuration must be root:everval mode 0640")
    secrets = config / "provider-secrets.env"
    if secrets.exists() or secrets.is_symlink():
        _node(secrets, owner=trusted_uid, directory=False, private=True)
        if stat.S_IMODE(secrets.stat().st_mode) != 0o600:
            raise PreflightError("Provider secrets must be root-owned mode 0600")
    for directory in (data, cache, data / "artifacts"):
        _node(directory, owner=service_uid, directory=True, private=True)
    projects = data / "projects"
    if projects.exists() or projects.is_symlink():
        _node(projects, owner=service_uid, directory=True, private=True)
    database = data / "workspace.sqlite3"
    _node(database, owner=service_uid, directory=False, private=True)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _node(sidecar, owner=service_uid, directory=False, private=True)
    if shutil.disk_usage(data).free < 1024**3:
        raise PreflightError("Keep at least 1 GiB free before starting this deployment")
    _team_enabled(database, now or datetime.now(UTC))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("api", "worker"), required=True)
    args = parser.parse_args()
    try:
        account = pwd.getpwnam("everval")
        if os.geteuid() != account.pw_uid or os.getegid() != account.pw_gid:
            raise PreflightError("Run preflight as the everval service account")
        check_layout(
            Layout(),
            service_uid=account.pw_uid,
            service_gid=account.pw_gid,
            environment=os.environ,
        )
    except (PreflightError, OSError, sqlite3.Error, KeyError) as exc:
        print(f"Everval {args.component} preflight failed: {exc}", file=sys.stderr)
        return 1
    print(f"Everval {args.component} preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
