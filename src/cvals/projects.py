"""Durable repository projects, candidate ledgers, and immutable source snapshots."""

from __future__ import annotations

import builtins
import fcntl
import hashlib
import json
import os
import re
import shutil
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from cvals.github import MAX_MIRROR_BYTES, GitHub, git_command, github_source
from cvals.models import canonical_json, content_hash, utc_now
from cvals.store import Store

if TYPE_CHECKING:
    from cvals.config import Settings

_SHA = re.compile(r"[0-9a-f]{40,64}")
_ID = re.compile(r"[0-9a-f]{24}")
_SECRET_NAMES = {
    ".env",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "kubeconfig",
}


def _identifier(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _is_test(name: str) -> bool:
    path = PurePosixPath(name)
    from cvals.node_runtime import is_test

    return (
        is_test(str(path))
        or path.name.startswith("test_")
        or any(part in {"tests", "test"} for part in path.parts)
    )


def _safe_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and len(name) <= 512
        and not path.is_absolute()
        and "\\" not in name
        and not any(part in {"", ".", "..", ".git"} for part in name.split("/"))
        and not any(ord(char) < 32 for char in name)
    )


def _problem(pull: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    cutoff = pull.get("mergedAt")
    issues = (pull.get("closingIssuesReferences") or {}).get("nodes") or []
    records = []
    for issue in issues:
        created = issue.get("createdAt")
        if created and cutoff and created < cutoff:
            records.append(
                {
                    "kind": "github_issue",
                    "number": issue["number"],
                    "url": issue["url"],
                    "title": issue.get("title", "")[:1000],
                    "body": (issue.get("body") or "")[:40000],
                    "source_created_at": created,
                    "source_updated_at": issue.get("updatedAt"),
                    "possibly_edited_after_cutoff": not issue.get("updatedAt")
                    or issue["updatedAt"] > cutoff,
                }
            )
    if not records:
        records = [
            {
                "kind": "pull_request_description",
                "number": pull["number"],
                "url": pull["url"],
                "title": pull.get("title", "")[:1000],
                "body": (pull.get("body") or "")[:40000],
                "source_created_at": pull.get("createdAt"),
                "source_updated_at": pull.get("updatedAt"),
                "possibly_edited_after_cutoff": not pull.get("updatedAt")
                or not cutoff
                or pull["updatedAt"] > cutoff,
            }
        ]
    statement = "\n\n".join(f"{item['title']}\n\n{item['body']}".strip() for item in records)[
        :100000
    ]
    return statement, {
        "source": records[0]["kind"],
        "sources": records,
        "cutoff": cutoff,
        "source_created_at": records[0]["source_created_at"],
        "source_updated_at": records[0]["source_updated_at"],
        "retrieved_at": utc_now(),
        "review_required": True,
        "historical_body_version_verified": False,
        "hindsight_risk": any(record["possibly_edited_after_cutoff"] for record in records),
        "linked_issue_count": (pull.get("closingIssuesReferences") or {}).get("totalCount", 0),
        "linked_issue_limit": 10,
        "notice": "Current GitHub text is not a historical body snapshot. Review instructions for hindsight and solution leakage.",
    }


def _patch_lines(patch: str) -> builtins.list[str] | None:
    """Compare full hunk text while preserving whitespace; no patch-id heuristics."""
    lines = []
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("@@ "):
            match = re.fullmatch(r"(@@ -[0-9]+(?:,[0-9]+)? \+[0-9]+(?:,[0-9]+)? @@)(?: .*)?", line)
            if match is None:
                return None
            lines.append(match[1])
            in_hunk = True
        elif in_hunk:
            if line.startswith((" ", "+", "-", "\\ No newline")):
                lines.append(line)
            else:
                return None
    return lines or None


class Projects:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        self.github = GitHub()
        from cvals.audit import AuditLog

        self.audit = AuditLog(settings)
        self.root = settings.data_dir / "projects"
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("Managed project storage may not be a symbolic link")
        with self.store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS repository_projects (id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS repository_candidates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES repository_projects(id), data TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS repository_candidates_project ON repository_candidates(project_id)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS repository_candidate_versions (candidate_id TEXT NOT NULL REFERENCES repository_candidates(id), revision TEXT NOT NULL, data TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(candidate_id,revision))"
            )

    @contextmanager
    def _lock(self, project_id: str) -> Iterator[Path]:
        if not _ID.fullmatch(project_id):
            raise ValueError("Invalid project identifier")
        directory = self.root / project_id
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError("Managed project directory may not be a symbolic link")
        lock = directory / "project.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise ValueError("This project has an operation in progress; wait and retry") from exc
        try:
            yield directory
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _audit(self, action: str, resource: str, details: dict[str, Any], connection: Any) -> None:
        self.audit.record(action, resource, details, connection=connection)

    def diagnostics(self) -> dict[str, Any]:
        from cvals.sandbox import sandbox_status

        return {
            "github": self.github.diagnostics(),
            "git_installed": bool(shutil.which("git")),
            "sandbox": sandbox_status(self.settings),
            "data_dir": str(self.settings.data_dir),
        }

    def list(self) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT data FROM repository_projects ORDER BY rowid DESC"
                )
            ]

    def get(self, project_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT data FROM repository_projects WHERE id=?", (project_id,)
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return dict(json.loads(row[0]))

    def candidates(self, project_id: str) -> builtins.list[dict[str, Any]]:
        self.get(project_id)
        with self.store._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT data FROM repository_candidates WHERE project_id=? ORDER BY rowid DESC",
                    (project_id,),
                )
            ]

    def candidate(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT data FROM repository_candidates WHERE project_id=? AND id=?",
                (project_id, candidate_id),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return dict(json.loads(row[0]))

    def _save_project(self, project: dict[str, Any], source_key: str) -> None:
        with self.store._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO repository_projects(id,source_key,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (project["id"], source_key, canonical_json(project)),
            )
            self._audit(
                "project.connected",
                project["id"],
                {"source_kind": project["source_kind"], "source": project["source"]},
                connection,
            )

    def connect(self, source: str) -> dict[str, Any]:
        """Register a local repository or fetch a bounded GitHub bare mirror."""
        if not isinstance(source, str) or not source.strip() or len(source) > 2000:
            raise ValueError("Supply a GitHub repository or an absolute local repository path")
        source = source.strip()
        local = Path(source).expanduser()
        if local.is_absolute():
            if local.is_symlink():
                raise ValueError("Local repository paths may not be symbolic links")
            try:
                repository = local.resolve(strict=True)
            except OSError as exc:
                raise ValueError("Local repository path does not exist") from exc
            if not repository.is_dir():
                raise ValueError("Local repository path must be a directory")
            source_key = f"local:{repository}"
            project_id = _identifier(source_key)
            with self._lock(project_id):
                bare = (
                    git_command(repository, "rev-parse", "--is-bare-repository").strip() == b"true"
                )
                head = git_command(repository, "rev-parse", "--verify", "HEAD").decode().strip()
                try:
                    branch = (
                        git_command(repository, "symbolic-ref", "--short", "HEAD").decode().strip()
                    )
                except ValueError:
                    branch = None
                project = self._project(
                    project_id,
                    "local",
                    str(repository),
                    repository,
                    bare,
                    None,
                    repository.name,
                    branch,
                    head,
                    {},
                )
                self._save_project(project, source_key)
                return project
        owner, name = github_source(source)
        metadata = self.github.repository(owner, name)
        owner, name = metadata["owner"], metadata["name"]
        source_key = f"github:{owner.lower()}/{name.lower()}"
        project_id = _identifier(source_key)
        if metadata["size_kib"] * 1024 > MAX_MIRROR_BYTES:
            raise ValueError(
                "Repository exceeds the initial 256 MiB mirror admission ceiling; use a smaller reviewed local repository"
            )
        if shutil.disk_usage(self.root).free < 2 * MAX_MIRROR_BYTES:
            raise ValueError(
                "At least 512 MiB of free workspace storage is required before mirroring"
            )
        with self._lock(project_id) as directory:
            mirror = directory / "repository.git"
            fresh = not mirror.exists()
            if mirror.is_symlink():
                raise ValueError("Managed mirror may not be a symbolic link")
            try:
                if fresh:
                    mirror.mkdir(mode=0o700)
                    git_command(mirror, "init", "--bare", "--quiet")
                branch = metadata["default_branch"]
                git_command(mirror, "check-ref-format", f"refs/heads/{branch}")
                remote = f"https://github.com/{owner}/{name}.git"
                git_command(
                    mirror,
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "--depth=2",
                    remote,
                    f"+refs/heads/{branch}:refs/heads/{branch}",
                    timeout=90,
                    storage_root=mirror,
                    network=True,
                )
                git_command(mirror, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
                head = git_command(mirror, "rev-parse", "--verify", "HEAD").decode().strip()
                project = self._project(
                    project_id,
                    "github",
                    metadata["url"],
                    mirror,
                    True,
                    owner,
                    name,
                    branch,
                    head,
                    metadata,
                )
                self._save_project(project, source_key)
                return project
            except BaseException:
                if fresh:
                    shutil.rmtree(mirror)
                raise

    def _project(
        self,
        identifier: str,
        kind: str,
        source: str,
        repository: Path,
        bare: bool,
        owner: str | None,
        name: str,
        branch: str | None,
        head: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            created = self.get(identifier)["created_at"]
        except KeyError:
            created = utc_now()
        return {
            "id": identifier,
            "source_kind": kind,
            "source": source,
            "owner": owner,
            "name": name,
            "repository_path": str(repository),
            "is_bare": bare,
            "default_branch": branch,
            "head_sha": head,
            "status": "connected",
            "created_at": created,
            "updated_at": utc_now(),
            "metadata": {**metadata, **self._detect(repository, head)},
            "diagnostics": {
                "repository_execution": False,
                "snapshot_strategy": "Git blobs without checkout",
                "credential_storage": "GitHub CLI only; no credential in project records",
                "github": self.github.diagnostics() if kind == "github" else None,
            },
        }

    def _detect(self, repository: Path, revision: str) -> dict[str, Any]:
        names = [
            item.decode("utf-8", "replace")
            for item in git_command(
                repository, "ls-tree", "-r", "--name-only", "-z", revision
            ).split(b"\0")
            if item
        ]
        languages: Counter[str] = Counter()
        extensions = {
            ".py": "Python",
            ".js": "JavaScript",
            ".mjs": "JavaScript",
            ".cjs": "JavaScript",
            ".jsx": "JavaScript",
            ".ts": "TypeScript",
            ".mts": "TypeScript",
            ".cts": "TypeScript",
            ".tsx": "TypeScript",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".rb": "Ruby",
        }
        for name in names:
            if language := extensions.get(PurePosixPath(name).suffix):
                languages[language] += 1
        configs = [
            name
            for name in names
            if PurePosixPath(name).name
            in {
                "pyproject.toml",
                "pytest.ini",
                "tox.ini",
                "setup.cfg",
                "requirements.txt",
                "uv.lock",
                "poetry.lock",
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "Cargo.toml",
                "go.mod",
                "Dockerfile",
            }
        ]
        tests = [name for name in names if _is_test(name)]
        runners = set()
        if any(PurePosixPath(name).name in {"pytest.ini", "conftest.py"} for name in names):
            runners.add("pytest")
        previews = [
            name
            for name in configs
            if PurePosixPath(name).name
            in {"pyproject.toml", "setup.cfg", "requirements.txt", "tox.ini", "package.json"}
        ] + tests[:5]
        for name in previews[:10]:
            spec = f"{revision}:{name}"
            if int(git_command(repository, "cat-file", "-s", spec).strip()) > 65536:
                continue
            text = git_command(
                repository, "cat-file", "blob", spec, limit_bytes=65536 + 1024
            ).decode("utf-8", "replace")
            if PurePosixPath(name).name == "package.json":
                try:
                    package = json.loads(text)
                except ValueError:
                    package = {}
                if isinstance(package, dict):
                    for section in ("dependencies", "devDependencies"):
                        dependencies = package.get(section, {})
                        if isinstance(dependencies, dict):
                            runners.update(set(dependencies) & {"jest", "vitest"})
            if "node:test" in text:
                runners.add("node-test")
            if "pytest" in text:
                runners.add("pytest")
            if "import unittest" in text or "from unittest import" in text:
                runners.add("unittest")
        return {
            "languages": dict(sorted(languages.items())),
            "tracked_files": len(names),
            "test_files": len(tests),
            "environment": {
                "configuration_files": configs[:100],
                "detected_python": "Python" in languages,
                "detected_node": bool(set(languages) & {"JavaScript", "TypeScript"}),
                "test_runner": sorted(runners) or ["requires_review"],
                "dependency_installation": "not_performed",
                "notice": "File-based environment detection; no repository setup, tests, or builds executed",
            },
        }

    def _ensure_commit(self, project: dict[str, Any], revision: str) -> None:
        if not _SHA.fullmatch(revision):
            raise ValueError("Historical revisions must be full immutable commit hashes")
        repository = Path(project["repository_path"])
        try:
            git_command(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")
            return
        except ValueError:
            if project["source_kind"] != "github":
                raise
        remote = f"https://github.com/{project['owner']}/{project['name']}.git"
        git_command(
            repository,
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-recurse-submodules",
            "--depth=2",
            remote,
            revision,
            timeout=90,
            storage_root=repository,
            network=True,
        )
        git_command(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")

    def _parents(self, project: dict[str, Any], revision: str) -> builtins.list[str]:
        self._ensure_commit(project, revision)
        raw = git_command(Path(project["repository_path"]), "cat-file", "-p", revision).decode(
            "utf-8", "replace"
        )
        return [
            line[7:] for line in raw.split("\n\n", 1)[0].splitlines() if line.startswith("parent ")
        ]

    def _changed(self, project: dict[str, Any], base: str, reference: str) -> builtins.list[str]:
        self._ensure_commit(project, base)
        raw = git_command(
            Path(project["repository_path"]),
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--no-ext-diff",
            "--no-renames",
            "-r",
            "-z",
            base,
            reference,
        )
        names = [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]
        if any(not _safe_path(name) for name in names):
            raise ValueError("Candidate contains unsafe or nonportable filenames")
        return names

    def _github_candidate(self, project: dict[str, Any], pull: dict[str, Any]) -> dict[str, Any]:
        number = pull["number"]
        statement, provenance = _problem(pull)
        reference = (pull.get("mergeCommit") or {}).get("oid")
        record = {
            "id": _identifier(f"{project['id']}:pr:{number}"),
            "project_id": project["id"],
            "source_kind": "github",
            "number": number,
            "title": pull["title"][:1000],
            "url": pull["url"],
            "base_commit": None,
            "reference_commit": reference,
            "issue_statement": statement,
            "problem_provenance": provenance,
            "repository_path": project["repository_path"],
            "status": "quarantined",
            "reasons": [],
            "changed_files": [],
            "merged_at": pull.get("mergedAt"),
            "metadata": {
                "source": "github_merged_pull_request",
                "head_commit": pull.get("headRefOid"),
                "api_base_oid": pull.get("baseRefOid"),
                "base_branch": pull.get("baseRefName"),
                "pr_commits": (pull.get("commits") or {}).get("totalCount", 0),
            },
        }
        try:
            if not pull.get("mergedAt") or not reference:
                raise ValueError("GitHub does not expose a complete merged pull request record")
            parents = self._parents(project, reference)
            record["metadata"]["reference_parents"] = parents
            if not parents or len(parents) > 2:
                raise ValueError("Integration commit does not have one or two supported parents")
            base = parents[0]
            record["base_commit"] = base
            changed = self._changed(project, base, reference)
            record["changed_files"] = changed
            details = self.github.pull_files(project["owner"], project["name"], number)
            record["metadata"]["github_files"] = [
                {key: value for key, value in item.items() if key != "patch"} for item in details
            ]
            if any(
                not _safe_path(item["path"])
                or (item.get("previous_path") and not _safe_path(item["previous_path"]))
                for item in details
            ):
                raise ValueError("GitHub file metadata contains an unsafe path")
            if len(parents) == 2:
                if parents[1] != pull.get("headRefOid"):
                    raise ValueError(
                        "Merge second parent differs from the recorded PR head; review integration lineage"
                    )
                record["metadata"]["integration"] = "two_parent_merge"
            elif record["metadata"]["pr_commits"] == 1:
                record["metadata"]["integration"] = "single_commit_integration"
            else:
                # Rebase-and-merge reports the last rebased commit. Its parent
                # is unsafe as the whole PR base unless the full change matches.
                if set(changed) != {item["path"] for item in details} or not details:
                    raise ValueError(
                        "Single-parent multi-commit integration is ambiguous between squash and rebase"
                    )
                for item in details:
                    if not isinstance(item.get("patch"), str) or item["status"] == "renamed":
                        raise ValueError(
                            "Whole-PR patch equivalence is unavailable; squash/rebase requires manual review"
                        )
                    actual = git_command(
                        Path(project["repository_path"]),
                        "diff",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-renames",
                        "--unified=3",
                        base,
                        reference,
                        "--",
                        item["path"],
                    ).decode("utf-8", "strict")
                    expected_lines, actual_lines = _patch_lines(item["patch"]), _patch_lines(actual)
                    if expected_lines is None or expected_lines != actual_lines:
                        raise ValueError(
                            "Last integration commit differs from the whole PR patch; rebase ancestry is ambiguous"
                        )
                record["metadata"]["integration"] = "whole_pr_patch_equivalent_single_parent"
                record["metadata"]["patch_equivalence"] = (
                    "Exact hunk text, preserving whitespace; all PR paths reconciled"
                )
            if not changed:
                raise ValueError("Integration has no changed files")
            record["status"] = "discovered"
            record["reasons"] = [
                "Integration lineage established; problem review, environment preparation, and isolated validation remain required"
            ]
        except (ValueError, UnicodeDecodeError) as exc:
            record["reasons"] = [str(exc)]
        return record

    def _local_candidates(
        self, project: dict[str, Any], limit: int, offset: int = 0
    ) -> builtins.list[dict[str, Any]]:
        repository = Path(project["repository_path"])
        raw = git_command(
            repository,
            "log",
            "--no-show-signature",
            f"-{limit}",
            f"--skip={offset}",
            "--format=%H%x00%P%x00%cI%x00%s%x00",
        ).decode("utf-8", "replace")
        values = raw.split("\0")
        records = []
        for offset in range(0, len(values) - 3, 4):
            commit, parents_text, timestamp, subject = values[offset : offset + 4]
            commit = commit.strip()
            parents = parents_text.split()
            reasons = (
                []
                if len(parents) == 1
                else [
                    "Local history requires a single-parent commit; roots and local merges need manual review"
                ]
            )
            changed: builtins.list[str] = []
            if not reasons:
                try:
                    changed = self._changed(project, parents[0], commit)
                except ValueError as exc:
                    reasons.append(str(exc))
            records.append(
                {
                    "id": _identifier(f"{project['id']}:commit:{commit}"),
                    "project_id": project["id"],
                    "source_kind": "local",
                    "number": None,
                    "title": subject,
                    "url": None,
                    "base_commit": parents[0] if len(parents) == 1 else None,
                    "reference_commit": commit,
                    "issue_statement": "",
                    "problem_provenance": {
                        "source": "local_commit",
                        "cutoff": timestamp,
                        "source_created_at": None,
                        "source_updated_at": None,
                        "original_subject": subject,
                        "review_required": True,
                        "hindsight_risk": True,
                        "historical_body_version_verified": False,
                        "notice": "A local commit is not a GitHub pull request. Supply and review its original problem statement.",
                    },
                    "repository_path": str(repository),
                    "status": "quarantined" if reasons else "discovered",
                    "reasons": reasons
                    or [
                        "Local commit pair extracted; a reviewed problem statement and isolated validation are required"
                    ],
                    "changed_files": changed,
                    "merged_at": None,
                    "metadata": {
                        "source": "local_git_commit",
                        "committed_at": timestamp,
                        "reference_parents": parents,
                    },
                }
            )
        return records

    def discover(
        self, project_id: str, limit: int = 20, *, cursor: str | None = None
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Discovery limit must be between 1 and 100")
        project = self.get(project_id)
        with self._lock(project_id):
            if project["source_kind"] == "github":
                listing = self.github.merged_pulls(project["owner"], project["name"], limit, cursor)
                records = [self._github_candidate(project, pull) for pull in listing["nodes"]]
                available, has_more = listing["total"], listing["has_more"]
                next_cursor = listing.get("next_cursor")
            else:
                head = (
                    git_command(Path(project["repository_path"]), "rev-parse", "--verify", "HEAD")
                    .decode()
                    .strip()
                )
                offset = 0
                if cursor is not None:
                    match = re.fullmatch(r"local:([0-9a-f]{40,64}):([0-9]{1,8})", cursor)
                    if not match or match[1] != head:
                        raise ValueError(
                            "Local history cursor is invalid or HEAD changed; refresh from the first page"
                        )
                    offset = int(match[2])
                page = self._local_candidates(project, limit + 1, offset)
                records = page[:limit]
                available, has_more = None, len(page) > limit
                next_cursor = f"local:{head}:{offset + limit}" if has_more else None
            counts = {"seen": len(records), "added": 0, "updated": 0, "unchanged": 0}
            with self.store._connect(write=True) as connection:
                for record in records:
                    # Retrieval time is evidence, not a change to the source.
                    hashable = json.loads(canonical_json(record))
                    hashable.get("problem_provenance", {}).pop("retrieved_at", None)
                    revision = content_hash(hashable)
                    old = connection.execute(
                        "SELECT data FROM repository_candidates WHERE id=?", (record["id"],)
                    ).fetchone()
                    previous = json.loads(old[0]) if old else None
                    now = utc_now()
                    record["created_at"] = previous["created_at"] if previous else now
                    record["updated_at"] = now
                    record["metadata_revision"] = revision
                    if previous and previous["metadata_revision"] == revision:
                        counts["unchanged"] += 1
                    else:
                        counts["updated" if previous else "added"] += 1
                    connection.execute(
                        "INSERT INTO repository_candidates(id,project_id,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (record["id"], project_id, canonical_json(record)),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO repository_candidate_versions(candidate_id,revision,data,recorded_at) VALUES(?,?,?,?)",
                        (record["id"], revision, canonical_json(record), now),
                    )
                project["updated_at"] = utc_now()
                project["last_discovery"] = {
                    "at": project["updated_at"],
                    "limit": limit,
                    "cursor": cursor,
                    "next_cursor": next_cursor,
                    "available_merged_pull_requests": available,
                    "has_more": has_more,
                    "source_kind": project["source_kind"],
                    **counts,
                }
                connection.execute(
                    "UPDATE repository_projects SET data=? WHERE id=?",
                    (canonical_json(project), project_id),
                )
                self._audit("project.discovery", project_id, counts, connection)
        ledger = self.candidates(project_id)
        statuses = Counter(record["status"] for record in ledger)
        exclusions = Counter(
            reason
            for record in ledger
            if record["status"] == "quarantined"
            for reason in record["reasons"]
        )
        counts.update(
            {
                "discovered": statuses["discovered"],
                "quarantined": statuses["quarantined"],
                "total_stored": len(ledger),
            }
        )
        return {
            "project": project,
            "candidates": ledger,
            "counts": counts,
            "coverage": {
                "available_merged_pull_requests": available,
                "source_kind": project["source_kind"],
                "has_more": has_more,
                "next_cursor": next_cursor,
                "selected_limit": limit,
                "exclusion_reasons": dict(exclusions),
                "languages": project["metadata"]["languages"],
                "notice": "Bounded discovery is not exhaustive or representative. Existing candidates remain in the ledger on incremental refresh.",
            },
        }

    def snapshot(
        self,
        project_id: str,
        revision: str,
        *,
        include_tests: bool = False,
        max_bytes: int = 4 * 1024 * 1024,
        max_files: int = 128,
    ) -> dict[str, Any]:
        if (
            type(max_bytes) is not int
            or not 1 <= max_bytes <= 32 * 1024 * 1024
            or type(max_files) is not int
            or not 1 <= max_files <= 2048
        ):
            raise ValueError("Snapshot limits support at most 32 MiB and 2048 files")
        project = self.get(project_id)
        with self._lock(project_id):
            self._ensure_commit(project, revision)
            repository = Path(project["repository_path"])
            entries = git_command(repository, "ls-tree", "-r", "-z", revision).split(b"\0")
            files: dict[str, str] = {}
            modes: dict[str, str] = {}
            omitted: builtins.list[dict[str, str]] = []
            total = 0
            for entry in entries:
                if not entry:
                    continue
                metadata, raw_path = entry.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split()
                name = raw_path.decode("utf-8", "replace")
                reason = None
                if raw_path.decode("utf-8", "replace").encode(
                    "utf-8"
                ) != raw_path or not _safe_path(name):
                    reason = "unsafe or non-UTF-8 filename"
                elif mode not in {"100644", "100755"} or kind != "blob":
                    reason = "symlink, submodule, or unsupported object type"
                elif any(part.startswith("__cvals") for part in PurePosixPath(name).parts):
                    reason = "reserved verifier path excluded"
                elif (
                    PurePosixPath(name).name in _SECRET_NAMES
                    or PurePosixPath(name).name.startswith(".env.")
                    or PurePosixPath(name).suffix in {".pem", ".key"}
                    or any(part in {".ssh", ".aws", ".kube"} for part in PurePosixPath(name).parts)
                ):
                    reason = "potential credential material excluded"
                elif not include_tests and _is_test(name):
                    reason = "test material excluded from solver snapshot"
                if reason:
                    omitted.append({"path": name, "reason": reason})
                    continue
                size = int(git_command(repository, "cat-file", "-s", object_id).strip())
                if size > 1024 * 1024 or total + size > max_bytes or len(files) >= max_files:
                    omitted.append(
                        {"path": name, "reason": "snapshot file, count, or aggregate size ceiling"}
                    )
                    continue
                data = git_command(
                    repository, "cat-file", "blob", object_id, limit_bytes=1024 * 1024 + 1024
                )
                try:
                    text = data.decode("utf-8")
                    if "\0" in text:
                        raise UnicodeDecodeError("utf8", data, 0, 1, "NUL in text")
                except UnicodeDecodeError:
                    omitted.append({"path": name, "reason": "binary or non-UTF-8 content"})
                    continue
                files[name] = text
                modes[name] = mode
                total += len(data)
            return {
                "commit": revision,
                "files": files,
                "omitted": omitted,
                "bytes": total,
                "complete": not omitted,
                "includes_git_history": False,
                "includes_tests": include_tests,
                "file_modes": modes,
                "snapshot_hash": content_hash({"revision": revision, "files": files}),
                "notice": "Read-only Git blobs. No host code execution. Review omissions before environment admission.",
            }
