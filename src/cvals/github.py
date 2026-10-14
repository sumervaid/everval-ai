"""Read-only GitHub metadata and bounded Git transport for project onboarding."""

from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cvals.domains._process import run_bounded

MAX_MIRROR_BYTES = 256 * 1024 * 1024
MAX_API_BYTES = 5 * 1024 * 1024
MAX_GIT_OUTPUT = 4 * 1024 * 1024


def github_source(source: str) -> tuple[str, str]:
    value = source.strip()
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.netloc != "github.com" or url.query or url.fragment or url.username or url.password:
            raise ValueError(
                "Use an https://github.com/OWNER/REPO URL without credentials, query, or fragment"
            )
        value = url.path.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if (
        len(parts) != 2
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", parts[0])
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", parts[1])
        or parts[1] in {".", ".."}
    ):
        raise ValueError(
            "Use OWNER/REPO, https://github.com/OWNER/REPO, or an absolute local repository path"
        )
    return parts[0], parts[1]


def tool_environment(*, credentials: bool = False) -> dict[str, str]:
    allowed = {"PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    if credentials:
        allowed |= {"GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ASKPASS": "/usr/bin/false",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_PAGER": "cat",
        }
    )
    return env


def directory_bytes(path: Path) -> int:
    size = 0
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            item = Path(root) / name
            if item.is_symlink():
                raise ValueError("Managed Git storage contains an unexpected symbolic link")
        for name in files:
            try:
                size += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                continue
    return size


def git_command(
    path: Path,
    *args: str,
    timeout: float = 20,
    storage_root: Path | None = None,
    network: bool = False,
    limit_bytes: int = MAX_GIT_OUTPUT,
) -> bytes:
    """Run Git plumbing; no repository hooks, filters, checkout, or builds."""
    if not shutil.which("git"):
        raise ValueError("Git is required; install Git on the workspace machine")
    command = [
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
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always" if network else "protocol.https.allow=never",
        "-c",
        "fetch.recurseSubmodules=false",
        "-c",
        "submodule.recurse=false",
        "-c",
        "http.followRedirects=false",
        "-c",
        "credential.helper=",
        "-c",
        "gc.auto=0",
        "-c",
        "maintenance.auto=false",
        "-c",
        "pack.threads=1",
        "-c",
        "pack.windowMemory=32m",
    ]
    if network:
        gh = shutil.which("gh")
        if not gh:
            raise ValueError(
                "GitHub CLI is required for authenticated Git transport; install gh and run gh auth login"
            )
        command += ["-c", f"credential.helper=!{shlex.quote(gh)} auth git-credential"]
    command += ["-C", str(path), *args]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=tool_environment(credentials=network),
        start_new_session=True,
        umask=0o077,
    )
    assert process.stdout is not None and process.stderr is not None
    output = bytearray()
    errors = bytearray()
    deadline = time.monotonic() + timeout
    next_storage_check = 0.0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                now = time.monotonic()
                if now >= deadline:
                    raise ValueError(
                        "Git operation exceeded its time limit; the transfer was stopped"
                    )
                if storage_root is not None and now >= next_storage_check:
                    if directory_bytes(storage_root) > MAX_MIRROR_BYTES:
                        raise ValueError(
                            "Git mirror exceeded the 256 MiB storage ceiling; the transfer was stopped"
                        )
                    next_storage_check = now + 0.1
                for key, _ in selector.select(min(0.1, deadline - now)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        key.data.extend(chunk)
                        if len(output) + len(errors) > limit_bytes:
                            raise ValueError("Git output exceeded the bounded metadata limit")
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Git operation exceeded its time limit") from exc
        if storage_root is not None and directory_bytes(storage_root) > MAX_MIRROR_BYTES:
            raise ValueError("Git mirror exceeds the 256 MiB storage ceiling")
        if process.returncode:
            detail = errors.decode("utf-8", "replace").lower()
            if "authentication" in detail or "could not read username" in detail:
                raise ValueError(
                    "GitHub Git authentication failed; run gh auth login on the workspace machine"
                )
            if "not our ref" in detail or "unadvertised" in detail:
                raise ValueError("GitHub no longer exposes the requested historical commit")
            raise ValueError("Git could not read the requested repository or revision")
        return bytes(output)
    except BaseException:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()


_MERGED_QUERY = """query($owner:String!, $name:String!, $limit:Int!, $after:String) {
  repository(owner:$owner,name:$name) {
    pullRequests(states:MERGED,first:$limit,after:$after,orderBy:{field:UPDATED_AT,direction:DESC}) {
      totalCount pageInfo {hasNextPage endCursor}
      nodes { number title body url createdAt updatedAt mergedAt baseRefName baseRefOid headRefOid
        commits {totalCount}
        mergeCommit {oid parents(first:3) {nodes {oid}}}
        closingIssuesReferences(first:10) {totalCount nodes {number title body url createdAt updatedAt}}
      }
    }
  }
}"""


class GitHub:
    """Read-only GitHub CLI calls; never persist credentials or raw API payloads."""

    def _call(self, args: list[str]) -> Any:
        if not shutil.which("gh"):
            raise ValueError("GitHub CLI is unavailable; install gh, then run gh auth login")
        try:
            result = run_bounded(
                ["gh", *args],
                env=tool_environment(credentials=True),
                timeout=30,
                limit_bytes=MAX_API_BYTES,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("GitHub CLI did not complete before its deadline") from exc
        if result.returncode:
            message = result.stderr.decode("utf-8", "replace").lower()
            if "rate limit" in message:
                raise ValueError(
                    "GitHub rate limit reached; wait for the limit to reset and refresh discovery"
                )
            if "auth login" in message or "authentication" in message or "401" in message:
                raise ValueError(
                    "GitHub authentication is unavailable; run gh auth login on the workspace machine"
                )
            if "404" in message or "not found" in message or "could not resolve" in message:
                raise ValueError(
                    "Repository is unavailable to the current GitHub account; verify its name and access"
                )
            raise ValueError(
                "GitHub metadata request failed; verify gh auth status and repository access"
            )
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError("GitHub returned malformed metadata") from exc
        if isinstance(value, dict) and value.get("errors"):
            raise ValueError("GitHub could not complete the repository metadata query")
        return value

    def repository(self, owner: str, name: str) -> dict[str, Any]:
        value = self._call(
            ["api", "--hostname", "github.com", "--method", "GET", f"repos/{owner}/{name}"]
        )
        canonical_owner, canonical_name = github_source(value["full_name"])
        license_value = value.get("license") or {}
        return {
            "owner": canonical_owner,
            "name": canonical_name,
            "url": f"https://github.com/{canonical_owner}/{canonical_name}",
            "description": (value.get("description") or "")[:4000],
            "private": bool(value.get("private")),
            "default_branch": value["default_branch"],
            "size_kib": int(value.get("size", 0)),
            "archived": bool(value.get("archived")),
            "language": value.get("language"),
            "license": {"spdx_id": license_value.get("spdx_id"), "name": license_value.get("name")},
        }

    def merged_pulls(
        self, owner: str, name: str, limit: int, cursor: str | None = None
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Discovery limit must be between 1 and 100")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > 1024
            or any(ord(char) < 32 or ord(char) > 126 for char in cursor)
        ):
            raise ValueError("Invalid GitHub discovery cursor")
        args = [
            "api",
            "graphql",
            "--hostname",
            "github.com",
            "-f",
            f"query={_MERGED_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"limit={limit}",
        ]
        if cursor is not None:
            args += ["-f", f"after={cursor}"]
        value = self._call(args)
        try:
            result = value["data"]["repository"]["pullRequests"]
            return {
                "nodes": result["nodes"],
                "total": result["totalCount"],
                "has_more": result["pageInfo"]["hasNextPage"],
                "next_cursor": result["pageInfo"]["endCursor"]
                if result["pageInfo"]["hasNextPage"]
                else None,
            }
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "GitHub did not return merged pull requests for this repository"
            ) from exc

    def pull_files(self, owner: str, name: str, number: int) -> list[dict[str, Any]]:
        value = self._call(
            [
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                f"repos/{owner}/{name}/pulls/{number}/files?per_page=100&page=1",
            ]
        )
        if not isinstance(value, list) or len(value) >= 100:
            raise ValueError(
                "Pull request has 100 or more changed files; bounded automatic extraction requires manual review"
            )
        return [
            {
                "path": item["filename"],
                "previous_path": item.get("previous_filename"),
                "status": item["status"],
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
                "patch": item.get("patch"),
            }
            for item in value
        ]

    def diagnostics(self) -> dict[str, Any]:
        status = {
            "gh_installed": bool(shutil.which("gh")),
            "git_installed": bool(shutil.which("git")),
            "authenticated": False,
            "message": "",
        }
        if not status["gh_installed"]:
            status["message"] = "Install GitHub CLI and run gh auth login on this machine"
            return status
        try:
            result = run_bounded(
                ["gh", "auth", "status", "--hostname", "github.com"],
                env=tool_environment(credentials=True),
                timeout=10,
                limit_bytes=16384,
            )
            status["authenticated"] = result.returncode == 0
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
        status["message"] = (
            "GitHub access is configured"
            if status["authenticated"]
            else "Run gh auth login on this machine; no token is stored in project data"
        )
        return status
