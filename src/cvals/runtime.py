"""Local worker lifecycle and workspace-scoped tmux sessions."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import IO

from cvals.config import Settings


def _lock_path(settings: Settings) -> Path:
    settings.prepare()
    return settings.data_dir / "worker.lock"


def worker_lock(settings: Settings) -> IO[str] | None:
    path = _lock_path(settings)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(descriptor, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()) + "\n")
    handle.flush()
    return handle


def worker_running(settings: Settings) -> bool:
    # Health polling neither initializes storage nor rewrites a lock file.
    try:
        descriptor = os.open(settings.data_dir / "worker.lock", os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(descriptor)


def ensure_worker(settings: Settings) -> dict[str, object]:
    settings.prepare()
    if worker_running(settings):
        return {"running": True, "started": False}
    log = settings.data_dir / "worker.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    environment = os.environ.copy()
    environment["CVALS_DATA_DIR"] = str(settings.data_dir)
    environment["EVERVAL_DATA_DIR"] = str(settings.data_dir)
    if settings.docker_host:
        environment["CVALS_DOCKER_HOST"] = settings.docker_host
        environment["EVERVAL_DOCKER_HOST"] = settings.docker_host
    environment["CVALS_SANDBOX_IMAGE"] = settings.sandbox_image
    environment["EVERVAL_SANDBOX_IMAGE"] = settings.sandbox_image
    environment["CVALS_ALLOW_REMOTE_PROVIDERS"] = str(settings.allow_remote_providers).lower()
    environment["EVERVAL_ALLOW_REMOTE_PROVIDERS"] = str(settings.allow_remote_providers).lower()
    environment["EVERVAL_INTERNAL_PROVIDER_ORIGINS"] = ",".join(settings.internal_provider_origins)
    environment["EVERVAL_PROVIDER_CA_BUNDLE"] = (
        str(settings.provider_ca_bundle) if settings.provider_ca_bundle else ""
    )
    with os.fdopen(descriptor, "a") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "cvals", "worker"],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=environment,
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if worker_running(settings):
            return {"running": True, "started": True, "log": str(log)}
        if process.poll() is not None:
            break
        time.sleep(0.1)
    raise ValueError(f"Worker did not start; inspect {log}")


def tmux_session(settings: Settings, *, attach: bool = True) -> str:
    executable = shutil.which("tmux")
    if not executable:
        raise ValueError("tmux is not installed; install it, or run everval tui directly")
    ensure_worker(settings)
    name = "everval-" + hashlib.sha256(str(settings.data_dir.resolve()).encode()).hexdigest()[:8]

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run([executable, *args], check=check, text=True, capture_output=True)

    if run("has-session", "-t", name, check=False).returncode:
        environment = [
            "-e",
            "CVALS_DATA_DIR=" + str(settings.data_dir),
            "-e",
            "EVERVAL_DATA_DIR=" + str(settings.data_dir),
            "-e",
            "CVALS_ALLOW_REMOTE_PROVIDERS=" + str(settings.allow_remote_providers).lower(),
            "-e",
            "CVALS_SANDBOX_IMAGE=" + settings.sandbox_image,
            "-e",
            "EVERVAL_SANDBOX_IMAGE=" + settings.sandbox_image,
            "-e",
            "EVERVAL_ALLOW_REMOTE_PROVIDERS=" + str(settings.allow_remote_providers).lower(),
            "-e",
            "EVERVAL_INTERNAL_PROVIDER_ORIGINS=" + ",".join(settings.internal_provider_origins),
            "-e",
            "EVERVAL_PROVIDER_CA_BUNDLE="
            + (str(settings.provider_ca_bundle) if settings.provider_ca_bundle else ""),
        ]
        if settings.docker_host:
            environment += [
                "-e",
                "CVALS_DOCKER_HOST=" + settings.docker_host,
                "-e",
                "EVERVAL_DOCKER_HOST=" + settings.docker_host,
            ]
        command = shlex.join([sys.executable, "-m", "cvals", "tui", "--no-worker"])
        run(
            "new-session",
            "-d",
            "-s",
            name,
            "-n",
            "workbench",
            "-x",
            "140",
            "-y",
            "42",
            *environment,
            command,
        )
        run(
            "new-window",
            "-d",
            "-t",
            name,
            "-n",
            "activity",
            shlex.join(["tail", "-n", "80", "-F", str(settings.data_dir / "worker.log")]),
        )
        options = {
            "status-style": "bg=#111c22,fg=#92a6ac",
            "status-left": " #[fg=#6fe6c8,bold] EVERVAL AI #[default] │ ",
            "status-left-length": "30",
            "status-right": " #[fg=#92a6ac]#{session_name} │ %H:%M ",
            "window-status-current-style": "fg=#edf3ef,bg=#24413f,bold",
            "window-status-format": " #I #W ",
            "window-status-current-format": " #I #W ",
            "status-interval": "10",
            "remain-on-exit": "on",
            "mouse": "on",
        }
        for key, value in options.items():
            run("set-option", "-t", name, key, value)
    if attach:
        action = "switch-client" if os.environ.get("TMUX") else "attach-session"
        subprocess.run([executable, action, "-t", name], check=True)
    return name
