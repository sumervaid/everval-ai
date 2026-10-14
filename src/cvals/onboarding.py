"""Resumable guidance that derives progress from durable evidence."""

from __future__ import annotations

import builtins
import json
import shutil
from typing import Any

from cvals.audit import AuditLog
from cvals.benchmarks import Benchmarks, _candidate_hash, grader_hash
from cvals.config import Settings
from cvals.models import canonical_json, content_hash, new_id, utc_now
from cvals.projects import Projects
from cvals.sandbox import sandbox_status
from cvals.store import Store

SELECTIONS = {
    "project_id",
    "candidate_id",
    "release_id",
    "collection_id",
    "system_id",
    "experiment_id",
}


class Onboarding:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        self.projects = Projects(settings)
        self.benchmarks = Benchmarks(settings)
        self.audit = AuditLog(settings)
        with self.store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS guided_runs "
                "(id TEXT PRIMARY KEY, data TEXT NOT NULL, hash TEXT NOT NULL)"
            )

    def start(
        self, name: str = "First repository evaluation", source: str | None = None
    ) -> dict[str, Any]:
        if not name.strip() or len(name) > 200:
            raise ValueError("Guide name must contain 1–200 characters")
        if source and (
            len(source) > 2000 or any(char in source for char in "\n\r\0") or "@" in source
        ):
            raise ValueError("Use a local path or a credential-free GitHub source")
        value: dict[str, Any] = {
            "id": new_id(),
            "name": name.strip(),
            "source": source,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            **dict.fromkeys(SELECTIONS),
        }
        with self.store._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO guided_runs VALUES(?,?,?)",
                (value["id"], canonical_json(value), content_hash(value)),
            )
            self.audit.record(
                "onboarding.started", value["id"], {"name": value["name"]}, connection=connection
            )
        return value

    def list(self) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._read(row)
                for row in connection.execute(
                    "SELECT data,hash FROM guided_runs ORDER BY rowid DESC"
                )
            ]

    @staticmethod
    def _read(row: Any) -> dict[str, Any]:
        if row is None:
            raise KeyError("Guided run not found")
        value: dict[str, Any] = json.loads(row["data"])
        if content_hash(value) != row["hash"]:
            raise ValueError("Guided run integrity check failed")
        return value

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            return self._read(
                connection.execute(
                    "SELECT data,hash FROM guided_runs WHERE id=?", (identifier,)
                ).fetchone()
            )

    def select(self, identifier: str, **choices: str | None) -> dict[str, Any]:
        if not choices or set(choices) - SELECTIONS:
            raise ValueError("Choose project, candidate, release, collection, system or experiment")
        with self.store._connect(write=True) as connection:
            value = self._read(
                connection.execute(
                    "SELECT data,hash FROM guided_runs WHERE id=?", (identifier,)
                ).fetchone()
            )
            if "project_id" in choices and choices["project_id"] != value["project_id"]:
                for key in {"candidate_id", "release_id", "collection_id", "experiment_id"}:
                    value[key] = None
            if "candidate_id" in choices and choices["candidate_id"] != value["candidate_id"]:
                for key in {"release_id", "collection_id", "experiment_id"}:
                    value[key] = None
            if "release_id" in choices and choices["release_id"] != value["release_id"]:
                value["collection_id"] = value["experiment_id"] = None
            if any(
                key in choices and choices[key] != value[key]
                for key in ("system_id", "collection_id")
            ):
                value["experiment_id"] = None
            value.update(choices)
            # Read directly under this transaction, avoiding nested service writes.
            if value["project_id"]:
                row = connection.execute(
                    "SELECT data FROM repository_projects WHERE id=?", (value["project_id"],)
                ).fetchone()
                if row is None:
                    raise ValueError("Selected project does not exist")
            if value["candidate_id"]:
                row = connection.execute(
                    "SELECT project_id FROM repository_candidates WHERE id=?",
                    (value["candidate_id"],),
                ).fetchone()
                if row is None or row["project_id"] != value["project_id"]:
                    raise ValueError("Select a candidate belonging to this project")
            if value["release_id"]:
                row = connection.execute(
                    "SELECT data FROM benchmark_releases WHERE id=?", (value["release_id"],)
                ).fetchone()
                release = json.loads(row["data"]) if row else {}
                if release.get("project_id") != value["project_id"]:
                    raise ValueError("Select a release belonging to this project")
                if (
                    value["collection_id"]
                    and value["collection_id"] not in release["collection_ids"].values()
                ):
                    raise ValueError("Choose a partition of the selected release")
            elif value["collection_id"]:
                raise ValueError("Choose a release before choosing its partition")
            if value["system_id"]:
                system = self.store._get(connection, "systems", value["system_id"])
                if system["kind"] != "http":
                    raise ValueError(
                        "The first real run requires a live model or external agent; controls and replays are separate"
                    )
            if value["experiment_id"]:
                _, state = self.store._read_experiment(connection, value["experiment_id"])
                if (
                    state["collection_id"] != value["collection_id"]
                    or value["system_id"] not in state["system_ids"]
                ):
                    raise ValueError("Select an experiment with this release partition and system")
            value["updated_at"] = utc_now()
            connection.execute(
                "UPDATE guided_runs SET data=?,hash=? WHERE id=?",
                (canonical_json(value), content_hash(value), identifier),
            )
            self.audit.record("onboarding.selection", identifier, choices, connection=connection)
        return value

    def recommendations(self, project_id: str) -> builtins.list[dict[str, Any]]:
        records = []
        for candidate in self.projects.candidates(project_id):
            if candidate["status"] != "discovered":
                continue
            changed = candidate.get("changed_files", [])
            paths = [item if isinstance(item, str) else item.get("path", "") for item in changed]
            tests = sum(
                "/test" in "/" + path
                or path.startswith("test")
                or ".test." in path
                or ".spec." in path
                for path in paths
            )
            reasons = []
            score = 0
            if tests:
                score += 3
                reasons.append(
                    "Change includes test files; control quality still requires validation"
                )
            if candidate.get("issue_statement", "").strip():
                score += 2
                reasons.append("Source problem text is available for independent review")
            if 1 <= len(paths) <= 12:
                score += 1
                reasons.append("Bounded change surface may make reconstruction easier")
            records.append(
                {
                    "candidate_id": candidate["id"],
                    "title": candidate.get("title"),
                    "score": score,
                    "reasons": reasons,
                    "automatic_approval": False,
                }
            )
        return sorted(records, key=lambda value: (-value["score"], value["candidate_id"]))[:20]

    def status(self, identifier: str) -> dict[str, Any]:
        value = self.get(identifier)
        steps = []

        def step(key: str, title: str, done: bool, detail: str, evidence: Any = None) -> None:
            steps.append(
                {
                    "id": key,
                    "title": title,
                    "status": "complete" if done else "pending",
                    "detail": detail,
                    "evidence": evidence,
                }
            )

        runtime = sandbox_status(self.settings)
        available = bool(runtime.get("available"))
        # Current sandbox implementations report Docker separately from image readiness.
        if "available" not in runtime:
            available = bool(runtime.get("docker_available", runtime.get("ready", False)))
        step(
            "preflight",
            "Check local execution",
            available and bool(shutil.which("git")),
            "Git and the configured Docker runtime must be available before repository validation.",
            runtime,
        )
        project = self.projects.get(value["project_id"]) if value["project_id"] else None
        step(
            "connect",
            "Connect a repository",
            bool(project),
            "Choose authorized GitHub history or a local Git repository.",
            value["project_id"],
        )
        candidates = self.projects.candidates(project["id"]) if project else []
        step(
            "discover",
            "Discover and choose a candidate",
            bool(value["candidate_id"]),
            f"{len(candidates)} candidates retained. Recommendations are suggestions, never approvals.",
            value["candidate_id"],
        )
        candidate = next((item for item in candidates if item["id"] == value["candidate_id"]), None)
        review = (
            self.benchmarks._latest("benchmark_reviews", project["id"], candidate["id"])
            if project and candidate
            else None
        )
        reviewed = bool(
            review and candidate and review["candidate_hash"] == _candidate_hash(candidate)
        )
        step(
            "review",
            "Review the independent task statement",
            reviewed,
            "Explain observable pre-fix behavior without exposing the historical solution.",
            (review or {}).get("id"),
        )
        profile = self.benchmarks.profile(project["id"]) if project else None
        step(
            "prepare",
            "Prepare or pin a runtime",
            bool(profile),
            "Review dependencies and pin the container image used for full-repository validation.",
            (profile or {}).get("id"),
        )
        validation = (
            self.benchmarks._latest("benchmark_validations", project["id"], candidate["id"])
            if project and candidate
            else None
        )
        valid = bool(
            validation
            and candidate
            and review
            and validation["status"] == "valid"
            and validation["candidate_hash"] == _candidate_hash(candidate)
            and (validation.get("review") or {}).get("id") == review["id"]
            and validation.get("grader_hash") == grader_hash()
        )
        step(
            "validate",
            "Validate historical repair and regressions",
            valid,
            "; ".join((validation or {}).get("reasons", []))
            or "Repeated clean controls must establish task validity.",
            (validation or {}).get("id"),
        )
        release = self.benchmarks.get(value["release_id"]) if value["release_id"] else None
        step(
            "freeze",
            "Freeze a release and choose a partition",
            bool(release and value["collection_id"]),
            "Choose development, holdout or all explicitly; preserve holdout independence.",
            value["release_id"],
        )
        system = self.store.get_system(value["system_id"]) if value["system_id"] else None
        step(
            "system",
            "Select a live system",
            bool(system and system["kind"] == "http"),
            "Set endpoint, credential environment reference, prices and harness. Fixture controls do not measure performance.",
            value["system_id"],
        )
        experiment = (
            self.store.get_experiment(value["experiment_id"]) if value["experiment_id"] else None
        )
        step(
            "run",
            "Run and inspect evidence",
            bool(experiment and experiment["status"] == "completed"),
            f"Current experiment: {experiment['status']}."
            if experiment
            else "Declare the experiment budget, then inspect every outcome.",
            value["experiment_id"],
        )
        return {
            **value,
            "steps": steps,
            "complete": all(item["status"] == "complete" for item in steps),
            "next_step": next((item["id"] for item in steps if item["status"] != "complete"), None),
            "recommendations": self.recommendations(project["id"]) if project else [],
            "experiment_status": (experiment or {}).get("status"),
        }
