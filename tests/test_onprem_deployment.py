"""Portable checks for the deployment preflight, without root or systemd."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cvals.config import Settings
from cvals.identity import IdentityStore

SCRIPT = Path(__file__).parents[1] / "deploy/onprem/preflight.py"
SPEC = importlib.util.spec_from_file_location("everval_deployment_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path.resolve()
    layout = preflight.Layout(root)
    release = root / "opt/everval/releases/reviewed-revision"
    for relative in (
        "venv/bin/python",
        "venv/bin/everval",
        "deploy/onprem/preflight.py",
        "venv/lib/site-packages/cvals/__init__.py",
    ):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test installation\n")
        path.chmod(0o755 if "/bin/" in relative else 0o644)
    (root / "opt/everval/current").symlink_to(release)
    config = layout.path("etc/everval")
    config.mkdir(parents=True)
    config.chmod(0o750)
    (config / "everval.env").write_text("EVERVAL_ALLOW_REMOTE_PROVIDERS=false\n")
    (config / "everval.env").chmod(0o640)
    data = layout.path("var/lib/everval")
    cache = layout.path("var/cache/everval")
    for path in (data, cache):
        path.mkdir(parents=True)
        path.chmod(0o700)
    IdentityStore(Settings(data)).bootstrap("Test administrator")
    database = data / "workspace.sqlite3"
    database.chmod(0o600)
    environment = {
        "EVERVAL_DATA_DIR": str(data),
        "XDG_CACHE_HOME": str(cache),
        "EVERVAL_ALLOW_REMOTE_PROVIDERS": "false",
    }
    return layout, environment


def check(deployment, **overrides):
    layout, environment = deployment
    arguments = {
        "service_uid": os.getuid(),
        "service_gid": os.getgid(),
        "environment": environment,
        "trusted_uid": os.getuid(),
        "now": datetime(2026, 1, 1, tzinfo=UTC),
    }
    arguments.update(overrides)
    preflight.check_layout(layout, **arguments)


def test_existing_private_workspace_passes_without_changing_database(deployment):
    layout, _ = deployment
    database = layout.path("var/lib/everval/workspace.sqlite3")
    original = database.read_bytes()
    check(deployment)
    assert database.read_bytes() == original


@pytest.mark.parametrize(
    "relative",
    [
        "var/lib/everval",
        "var/lib/everval/artifacts",
        "var/cache/everval",
        "var/lib/everval/workspace.sqlite3",
    ],
)
def test_group_readable_workspace_is_rejected(deployment, relative):
    layout, _ = deployment
    path = layout.path(relative)
    path.chmod(path.stat().st_mode | 0o040)
    with pytest.raises(preflight.PreflightError, match="Permissions are too broad"):
        check(deployment)


def test_missing_database_is_not_created(deployment):
    layout, _ = deployment
    database = layout.path("var/lib/everval/workspace.sqlite3")
    database.unlink()
    with pytest.raises(FileNotFoundError):
        check(deployment)
    assert not database.exists()


def test_disabled_team_auth_is_not_enabled_implicitly(deployment):
    layout, _ = deployment
    with sqlite3.connect(layout.path("var/lib/everval/workspace.sqlite3")) as db:
        db.execute("UPDATE team_settings SET enabled=0")
    with pytest.raises(preflight.PreflightError, match="Team authentication is disabled"):
        check(deployment)
    with sqlite3.connect(layout.path("var/lib/everval/workspace.sqlite3")) as db:
        assert db.execute("SELECT enabled FROM team_settings").fetchone() == (0,)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE team_users SET active=0",
        "UPDATE team_users SET role='viewer'",
        "UPDATE team_tokens SET revoked_at='2025-01-01T00:00:00+00:00'",
        "UPDATE team_tokens SET expires_at='2025-01-01T00:00:00+00:00'",
        "UPDATE team_tokens SET expires_at='2099-01-01T00:00:00'",
        "UPDATE team_tokens SET expires_at='malformed'",
    ],
)
def test_unusable_administrator_access_blocks_startup(deployment, statement):
    layout, _ = deployment
    with sqlite3.connect(layout.path("var/lib/everval/workspace.sqlite3")) as db:
        db.execute(statement)
    with pytest.raises(preflight.PreflightError, match="No active administrator"):
        check(deployment)


def test_release_module_cannot_be_group_writable(deployment):
    layout, _ = deployment
    module = layout.path(
        "opt/everval/releases/reviewed-revision/venv/lib/site-packages/cvals/__init__.py"
    )
    module.chmod(0o664)
    with pytest.raises(preflight.PreflightError, match="Permissions are too broad"):
        check(deployment)


def test_current_release_cannot_point_outside_release_directory(deployment):
    layout, _ = deployment
    current = layout.path("opt/everval/current")
    current.unlink()
    current.symlink_to(layout.path("var/lib/everval"))
    with pytest.raises(preflight.PreflightError, match="direct child of releases"):
        check(deployment)


def test_external_release_directory_symlink_is_rejected(deployment):
    layout, _ = deployment
    target = layout.path("opt/shared-modules")
    target.mkdir()
    layout.path("opt/everval/releases/reviewed-revision/modules").symlink_to(target)
    with pytest.raises(preflight.PreflightError, match="directory symlinks must stay inside"):
        check(deployment)


def test_internal_release_directory_symlink_is_checked_and_allowed(deployment):
    layout, _ = deployment
    release = layout.path("opt/everval/releases/reviewed-revision")
    (release / "venv/lib64").symlink_to(release / "venv/lib")
    check(deployment)


def test_symlinked_database_is_rejected(deployment):
    layout, _ = deployment
    database = layout.path("var/lib/everval/workspace.sqlite3")
    moved = database.with_name("moved.sqlite3")
    database.rename(moved)
    database.symlink_to(moved)
    with pytest.raises(preflight.PreflightError, match="Expected a real file"):
        check(deployment)


def test_secret_file_requires_exact_private_mode(deployment):
    layout, _ = deployment
    secret = layout.path("etc/everval/provider-secrets.env")
    secret.write_text("TEST_PROVIDER_CREDENTIAL=not-a-real-key\n")
    secret.chmod(0o600)
    check(deployment)
    secret.chmod(0o640)
    with pytest.raises(preflight.PreflightError, match="Permissions are too broad"):
        check(deployment)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EVERVAL_ALLOW_REMOTE_PROVIDERS", "true"),
        ("EVERVAL_ALLOW_REMOTE_PROVIDERS", ""),
        ("CVALS_ALLOW_REMOTE_PROVIDERS", "true"),
        ("EVERVAL_DATA_DIR", "/some/other/workspace"),
        ("XDG_CACHE_HOME", "/tmp/shared-cache"),
        ("EVERVAL_TOKEN", "legacy-shared-token"),
    ],
)
def test_unsafe_environment_is_rejected(deployment, key, value):
    _, environment = deployment
    environment[key] = value
    with pytest.raises(preflight.PreflightError):
        check(deployment)


def test_application_cannot_run_as_root(deployment):
    with pytest.raises(preflight.PreflightError, match="must not run as root"):
        check(deployment, service_uid=0)


def test_owner_check_does_not_accept_another_account(deployment):
    layout, _ = deployment
    with pytest.raises(preflight.PreflightError, match="Unexpected owner"):
        preflight._node(layout.path("var/lib/everval"), owner=os.getuid() + 1, directory=True)


def test_unit_templates_preserve_one_worker_and_loopback_api():
    root = SCRIPT.parent
    api = (root / "everval-api.service").read_text()
    worker = (root / "everval-worker.service").read_text()
    assert "serve --host 127.0.0.1 --port 8765 --no-worker" in api
    assert "ExecStart=/opt/everval/current/venv/bin/everval worker\n" in worker
    for unit in (api, worker):
        assert "User=everval\n" in unit
        assert "UMask=0077\n" in unit
        assert "ProtectSystem=strict\n" in unit
        assert "EnvironmentFile=-/etc/everval/provider-secrets.env\n" in unit
        assert "ExecStartPre=" in unit
        assert "ReadWritePaths=/var/lib/everval /var/cache/everval\n" in unit
