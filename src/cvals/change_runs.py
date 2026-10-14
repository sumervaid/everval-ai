"""Deduplicated local evaluation triggers tied to source and system revisions."""

from __future__ import annotations

import builtins
import json
import re
from typing import Any
from urllib.parse import quote

from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.github import git_command
from cvals.models import ExperimentCreate, canonical_json, content_hash, new_id, utc_now
from cvals.projects import Projects
from cvals.store import Store


def _configuration(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object of at most 64 KiB")

    def inspect(item: Any, depth: int = 0) -> None:
        if depth > 12:
            raise ValueError("Configuration nesting exceeds 12 levels")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 200:
                    raise ValueError("Configuration keys must be bounded strings")
                if re.search(
                    r"(?i)(secret|password|credential|api.?key|access.?token)", key
                ) and not key.endswith("_env"):
                    raise ValueError(
                        "Configuration may contain credential environment references, never secret values"
                    )
                if key.endswith("_env") and (
                    not isinstance(child, str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", child)
                ):
                    raise ValueError("Credential references must be environment variable names")
                inspect(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                inspect(child, depth + 1)
        elif not isinstance(item, (str, int, float, bool, type(None))):
            raise ValueError("Configuration must contain JSON values")

    inspect(value)
    # Validate depth before serializing and disallow non-portable numeric values.
    try:
        encoded = canonical_json(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("Configuration must contain finite JSON values") from exc
    if len(encoded.encode()) > 64 * 1024:
        raise ValueError("Configuration must be a JSON object of at most 64 KiB")
    return value


class ChangeRuns:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        self.projects = Projects(settings)
        self.audit = AuditLog(settings)
        with self.store._connect(write=True) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS change_baselines (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS change_baseline_versions (
                    baseline_id TEXT NOT NULL REFERENCES change_baselines(id),
                    version INTEGER NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL,
                    PRIMARY KEY(baseline_id,version)
                );
                CREATE TABLE IF NOT EXISTS change_evaluations (
                    id TEXT PRIMARY KEY, baseline_id TEXT NOT NULL REFERENCES change_baselines(id),
                    fingerprint TEXT NOT NULL, experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    data TEXT NOT NULL, hash TEXT NOT NULL,
                    UNIQUE(baseline_id,fingerprint)
                );
            """)

    @staticmethod
    def _read(row: Any) -> dict[str, Any]:
        if row is None:
            raise KeyError("Change baseline or evaluation not found")
        value: dict[str, Any] = json.loads(row["data"])
        if content_hash(value) != row["hash"]:
            raise ValueError("Change record integrity check failed")
        return value

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            return self._read(
                connection.execute(
                    "SELECT data,hash FROM change_baselines WHERE id=?", (identifier,)
                ).fetchone()
            )

    def list(self) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._read(row)
                for row in connection.execute(
                    "SELECT data,hash FROM change_baselines ORDER BY rowid DESC"
                )
            ]

    def register(
        self,
        name: str,
        project_id: str,
        experiment_id: str,
        config: dict[str, Any] | None = None,
        enabled: bool = False,
    ) -> dict[str, Any]:
        if not name.strip() or len(name) > 200 or type(enabled) is not bool:
            raise ValueError("Provide a name of 1–200 characters and an explicit enabled state")
        project = self.projects.get(project_id)
        config = _configuration(config or {})
        with self.store._connect(write=True) as connection:
            snapshot, state = self.store._read_experiment(connection, experiment_id)
            if state["status"] in {"queued", "running"}:
                raise ValueError("Finish or cancel the baseline experiment before linking changes")
            if state.get("budget_uncertain"):
                raise ValueError("Resolve uncertain baseline billing before linking changes")
            if any(system["kind"] == "replay" for system in snapshot["systems"]):
                raise ValueError("Imported replays cannot produce new change-linked evidence")
            value = {
                "id": new_id(),
                "name": name.strip(),
                "project_id": project_id,
                "source_experiment_id": experiment_id,
                "source_snapshot_hash": state["snapshot_hash"],
                "source_commit": project["head_sha"],
                "config": config,
                "config_hash": content_hash(config),
                "version": 1,
                "enabled": enabled,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "execution_policy": "Frozen baseline collection; current saved system configurations are frozen per trigger; repository commit is provenance, not a host build command",
                "poll_policy": "Explicit local invocation; read-only GitHub commit metadata; no status posts",
            }
            connection.execute(
                "INSERT INTO change_baselines VALUES(?,?,?)",
                (value["id"], canonical_json(value), content_hash(value)),
            )
            connection.execute(
                "INSERT INTO change_baseline_versions VALUES(?,?,?,?)",
                (value["id"], 1, canonical_json(value), content_hash(value)),
            )
            self.audit.record(
                "changes.registered",
                value["id"],
                {
                    "project_id": project_id,
                    "source_experiment_id": experiment_id,
                    "enabled": enabled,
                },
                connection=connection,
            )
        return value

    def revise(
        self, identifier: str, *, config: dict[str, Any] | None = None, enabled: bool | None = None
    ) -> dict[str, Any]:
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if config is not None:
            config = _configuration(config)
        with self.store._connect(write=True) as connection:
            value = self._read(
                connection.execute(
                    "SELECT data,hash FROM change_baselines WHERE id=?", (identifier,)
                ).fetchone()
            )
            if config is not None:
                value["config"] = config
                value["config_hash"] = content_hash(config)
            if enabled is not None:
                value["enabled"] = enabled
            value["version"] += 1
            value["updated_at"] = utc_now()
            connection.execute(
                "UPDATE change_baselines SET data=?,hash=? WHERE id=?",
                (canonical_json(value), content_hash(value), identifier),
            )
            connection.execute(
                "INSERT INTO change_baseline_versions VALUES(?,?,?,?)",
                (identifier, value["version"], canonical_json(value), content_hash(value)),
            )
            self.audit.record(
                "changes.revised",
                identifier,
                {
                    "version": value["version"],
                    "config_hash": value["config_hash"],
                    "enabled": value["enabled"],
                },
                connection=connection,
            )
        return value

    def evaluations(self, identifier: str) -> builtins.list[dict[str, Any]]:
        self.get(identifier)
        with self.store._connect() as connection:
            return [
                self._read(row)
                for row in connection.execute(
                    "SELECT data,hash FROM change_evaluations WHERE baseline_id=? ORDER BY rowid DESC",
                    (identifier,),
                )
            ]

    def trigger(
        self, identifier: str, commit: str | None = None, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        baseline = self.get(identifier)
        if not baseline["enabled"]:
            raise ValueError("Enable the reviewed baseline before admitting change-linked runs")
        project = self.projects.get(baseline["project_id"])
        if commit is None:
            if project["source_kind"] != "local":
                raise ValueError(
                    "Supply an exact commit or explicitly poll the connected GitHub repository"
                )
            from pathlib import Path

            commit = (
                git_command(Path(project["repository_path"]), "rev-parse", "--verify", "HEAD")
                .decode()
                .strip()
            )
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise ValueError("A full lowercase Git commit identity is required")
        if project["source_kind"] == "local":
            from pathlib import Path

            object_type = (
                git_command(Path(project["repository_path"]), "cat-file", "-t", commit)
                .decode()
                .strip()
            )
            if object_type != "commit":
                raise ValueError("The supplied object must be an existing local commit")
            commit_verification = "Commit exists in the connected local repository"
        else:
            response = self.projects.github._call(
                [
                    "api",
                    "--method",
                    "GET",
                    f"repos/{project['owner']}/{project['name']}/commits/{commit}",
                ]
            )
            if not isinstance(response, dict) or response.get("sha") != commit:
                raise ValueError("GitHub did not confirm the supplied repository commit")
            commit_verification = "Exact commit confirmed by connected repository GitHub metadata"
        selected = _configuration(config) if config is not None else baseline["config"]
        with self.store._connect(write=True) as connection:
            # Re-read under the admission transaction to respect concurrent disable/revision.
            baseline = self._read(
                connection.execute(
                    "SELECT data,hash FROM change_baselines WHERE id=?", (identifier,)
                ).fetchone()
            )
            if not baseline["enabled"]:
                raise ValueError("Change baseline is disabled")
            if config is None:
                selected = baseline["config"]
            snapshot, state = self.store._read_experiment(
                connection, baseline["source_experiment_id"]
            )
            if state.get("budget_uncertain"):
                raise ValueError("Baseline billing is uncertain")
            systems = [
                self.store._get(connection, "systems", system["id"])
                for system in snapshot["systems"]
            ]
            if any(system["kind"] == "replay" for system in systems):
                raise ValueError("Imported replays cannot produce new change-linked evidence")
            context = {
                "commit": commit,
                "config_hash": content_hash(selected),
                "system_hashes": {system["id"]: content_hash(system) for system in systems},
                "source_snapshot_hash": baseline["source_snapshot_hash"],
            }
            fingerprint = content_hash(context)
            previous = connection.execute(
                "SELECT data,hash FROM change_evaluations WHERE baseline_id=? AND fingerprint=?",
                (identifier, fingerprint),
            ).fetchone()
            if previous:
                return {**self._read(previous), "deduplicated": True}
            for row in connection.execute(
                "SELECT e.state FROM experiments e JOIN change_evaluations c ON e.id=c.experiment_id WHERE c.baseline_id=?",
                (identifier,),
            ):
                previous_state = json.loads(row["state"])
                if previous_state.get("budget_uncertain"):
                    raise ValueError("A prior change-linked run has uncertain billing")
                if previous_state["status"] in {"queued", "running"}:
                    raise ValueError("This baseline already has an outstanding evaluation")
            request = ExperimentCreate(
                name=baseline["name"][:160] + " · " + commit[:12],
                collection_id=state["collection_id"],
                system_ids=[item["id"] for item in systems],
                repetitions=state["repetitions"],
                budget_usd=state["budget_usd"],
            )
            experiment_id = self.store._insert_experiment(
                connection,
                request,
                snapshot["collection"],
                systems,
                baseline["source_experiment_id"],
            )
            value = {
                "id": new_id(),
                "baseline_id": identifier,
                "baseline_version": baseline["version"],
                "fingerprint": fingerprint,
                "experiment_id": experiment_id,
                "created_at": utc_now(),
                "config": selected,
                **context,
                "commit_verification": commit_verification,
                "deployment_verification": "A repository commit does not prove that the configured endpoint deployed it; pin or verify the agent adapter revision independently",
            }
            connection.execute(
                "INSERT INTO change_evaluations VALUES(?,?,?,?,?,?)",
                (
                    value["id"],
                    identifier,
                    fingerprint,
                    experiment_id,
                    canonical_json(value),
                    content_hash(value),
                ),
            )
            self.audit.record(
                "changes.triggered",
                value["id"],
                {
                    "baseline_id": identifier,
                    "experiment_id": experiment_id,
                    "fingerprint": fingerprint,
                },
                connection=connection,
            )
        return {**value, "deduplicated": False}

    def poll(self, identifier: str) -> dict[str, Any]:
        baseline = self.get(identifier)
        if not baseline["enabled"]:
            raise ValueError("Enable the reviewed baseline before polling")
        project = self.projects.get(baseline["project_id"])
        if project["source_kind"] != "github":
            return self.trigger(identifier)
        response = self.projects.github._call(
            [
                "api",
                "--method",
                "GET",
                f"repos/{project['owner']}/{project['name']}/commits/{quote(project['default_branch'], safe='')}",
            ]
        )
        if not isinstance(response, dict) or not isinstance(response.get("sha"), str):
            raise ValueError("GitHub returned no commit identity")
        return self.trigger(identifier, commit=response["sha"])
