"""One application service shared by the terminal and automation interfaces."""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any

from cvals.audit import AuditLog
from cvals.benchmarks import Benchmarks
from cvals.config import Settings
from cvals.exporting import export_experiment
from cvals.jobs import Jobs
from cvals.models import ExperimentCreate, System, new_id
from cvals.projects import Projects
from cvals.reviews import ReviewStore
from cvals.runtime import ensure_worker, worker_running
from cvals.schedules import ScheduleStore
from cvals.store import Store


class Workbench:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.settings.prepare()
        self.store = Store(self.settings)
        self.store.init()
        self.store.ensure_fixture_systems()
        self._audit = AuditLog(self.settings)
        self._projects = Projects(self.settings)
        self._benchmarks = Benchmarks(self.settings)
        self._jobs = Jobs(self.settings)

    def overview(self) -> dict[str, Any]:
        projects = self.projects()
        with self.store._connect() as connection:
            candidate_count = connection.execute(
                "SELECT count(*) FROM repository_candidates"
            ).fetchone()[0]
        return {
            **self.store.overview(),
            "projects": len(projects),
            "releases": len(self.releases()),
            "systems": len(self.systems()),
            "recent_projects": projects[:8],
            "candidates": candidate_count,
            "jobs": self._jobs.list()[:10],
            "worker_running": worker_running(self.settings),
        }

    def projects(self) -> list[dict[str, Any]]:
        return self._projects.list()

    def connect(self, source: str) -> dict[str, Any]:
        return self._projects.connect(source)

    def _job(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_worker(self.settings)
        queued = self._jobs.enqueue(kind, payload)
        return self._jobs.wait(queued["id"])

    def discover(
        self, project_id: str, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        return self._job("discover", {"project_id": project_id, "limit": limit, "cursor": cursor})

    def candidates(self, project_id: str) -> list[dict[str, Any]]:
        records = self._projects.candidates(project_id)
        validations = self._benchmarks.validations(project_id)
        latest: dict[str, dict[str, Any]] = {}
        for value in validations:
            latest.setdefault(value["candidate_id"], value)
        return [{**item, "validation": latest.get(item["id"])} for item in records]

    def validate(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        return self._job("validate", {"project_id": project_id, "candidate_id": candidate_id})

    def review_candidate(
        self, project_id: str, candidate_id: str, statement: str, reviewer: str, rationale: str
    ) -> dict[str, Any]:
        return self._benchmarks.review(project_id, candidate_id, statement, reviewer, rationale)

    def add_project_profile(
        self,
        project_id: str,
        image: str,
        timeout_seconds: int = 60,
        plugins: list[str] | None = None,
        *,
        runtime: str = "python-pytest",
        language: str = "python",
    ) -> dict[str, Any]:
        return self._benchmarks.configure(
            project_id, image, timeout_seconds, plugins, runtime=runtime, language=language
        )

    def project_profile(self, project_id: str) -> dict[str, Any] | None:
        return self._benchmarks.profile(project_id)

    def environment_plan(
        self,
        project_id: str,
        packages: list[str] | None = None,
        revision: str | None = None,
        base_image: str | None = None,
        *,
        language: str = "python",
        runner: str = "node-test",
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        from cvals.environments import Environments

        return Environments(self.settings).plan(
            project_id,
            packages=packages,
            revision=revision,
            base_image=base_image,
            language=language,
            runner=runner,
            artifacts=artifacts,
        )

    def prepare_environment(
        self,
        project_id: str,
        packages: list[str] | None = None,
        revision: str | None = None,
        base_image: str | None = None,
        *,
        language: str = "python",
        runner: str = "node-test",
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        plan = self.environment_plan(
            project_id,
            packages,
            revision,
            base_image,
            language=language,
            runner=runner,
            artifacts=artifacts,
        )
        payload: dict[str, Any] = {
            "project_id": project_id,
            "packages": packages,
            "revision": plan["source_commit"],
            "base_image": plan["base_image_digest"],
        }
        if language != "python":
            payload.update(language=language, runner=runner)
        if artifacts:
            payload["artifacts"] = artifacts
        return self._job("prepare", payload)

    def release(self, project_id: str, name: str) -> dict[str, Any]:
        return self._benchmarks.release(project_id, name)

    def releases(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self._benchmarks.list(project_id)

    def collections(self) -> list[dict[str, Any]]:
        return self.store.list_collections()

    def experiments(self) -> list[dict[str, Any]]:
        return self.store.list_experiments()

    def systems(self) -> list[dict[str, Any]]:
        return self.store.list_systems()

    def add_system(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.store.save_system(System.model_validate(payload))
        return result

    def run(
        self,
        collection_id: str,
        system_ids: list[str],
        name: str,
        budget: float = 0,
        repetitions: int = 1,
    ) -> dict[str, Any]:
        ensure_worker(self.settings)
        request = ExperimentCreate(
            name=name,
            collection_id=collection_id,
            system_ids=system_ids,
            budget_usd=budget,
            repetitions=repetitions,
        )
        result = self.store.create_experiment(request)
        return result

    def experiment(self, identifier: str) -> dict[str, Any]:
        result = self.store.get_experiment(identifier)
        return {**result, "experiment": result}

    def cancel(self, identifier: str) -> dict[str, Any]:
        result = self.store.cancel_experiment(identifier)
        return result

    def export(self, identifier: str, format: str = "json") -> dict[str, Any]:
        experiment = self.store.get_experiment(identifier)
        experiment["human_reviews"] = self.reviews(identifier)
        from cvals.policies import PolicyStore

        experiment["gate_decisions"] = PolicyStore(self.settings).decisions(identifier)
        body, _ = export_experiment(experiment, format)
        if format not in {"json", "csv", "html"}:
            raise ValueError("Supported export formats: json, csv, html")
        path = self.settings.artifacts_dir / f"experiment-{identifier}-{new_id()}.{format}"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(body)
        self._audit.record("experiment.exported", identifier, {"path": str(path), "format": format})
        return {"path": str(path), "format": format}

    def diff(self, left: str, right: str) -> dict[str, Any]:
        from cvals.comparison import compare_experiments

        return compare_experiments(
            self.store.get_experiment(left), self.store.get_experiment(right)
        )

    def reviews(self, identifier: str) -> list[dict[str, Any]]:
        return ReviewStore(self.settings).list(identifier)

    def add_review(
        self,
        experiment_id: str,
        trial_id: str,
        reviewer: str,
        dimension: str,
        score: float,
        rationale: str,
    ) -> dict[str, Any]:
        result = ReviewStore(self.settings).add(
            experiment_id, trial_id, reviewer, dimension, score, rationale
        )
        return result

    def schedules(self) -> list[dict[str, Any]]:
        return ScheduleStore(self.settings, self.store).list()

    def add_schedule(
        self,
        experiment_id: str,
        name: str,
        interval_minutes: int,
        enabled: bool = True,
        lifetime_budget_usd: float | None = None,
    ) -> dict[str, Any]:
        result = ScheduleStore(self.settings, self.store).create(
            name,
            experiment_id,
            interval_minutes,
            enabled,
            lifetime_budget_usd=lifetime_budget_usd,
        )
        return result

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        result = ScheduleStore(self.settings, self.store).set_enabled(schedule_id, enabled)
        return result

    def diagnostics(self) -> dict[str, Any]:
        from cvals.api import docker_available

        return {
            "data_dir": str(self.settings.data_dir),
            "python": platform.python_version(),
            "storage_free_bytes": shutil.disk_usage(self.settings.data_dir).free,
            "docker_available": docker_available(self.settings),
            "tmux_available": bool(shutil.which("tmux")),
            "worker_running": worker_running(self.settings),
            "remote_providers_enabled": self.settings.allow_remote_providers,
            "audit": self._audit.verify(),
            "jobs": self._jobs.list()[:20],
        }

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._audit.list(limit)

    def gates(
        self,
        experiment_id: str,
        policy: dict[str, Any] | None = None,
        baseline_id: str | None = None,
        policy_id: str | None = None,
    ) -> dict[str, Any]:
        from cvals.policies import PolicyStore

        return PolicyStore(self.settings).evaluate(
            self.store.get_experiment(experiment_id),
            policy,
            self.store.get_experiment(baseline_id) if baseline_id else None,
            policy_id=policy_id,
        )

    def triage(self, experiment_id: str) -> dict[str, Any]:
        from cvals.diagnostics import Diagnostics

        return Diagnostics(self.settings).triage(experiment_id)

    def diagnose(self, experiment_id: str, trial_id: str) -> dict[str, Any]:
        from cvals.diagnostics import Diagnostics

        return Diagnostics(self.settings).diagnose(experiment_id, trial_id)

    def regrade(self, experiment_id: str, trial_id: str) -> dict[str, Any]:
        from cvals.diagnostics import Diagnostics

        return Diagnostics(self.settings).regrade(experiment_id, trial_id)

    def economics(self, experiment_id: str) -> dict[str, Any]:
        from cvals.economics import analyze

        return analyze(self.store.get_experiment(experiment_id))

    def decision_brief(self, experiment_id: str) -> dict[str, Any]:
        from cvals.economics import decision_brief
        from cvals.policies import PolicyStore

        return decision_brief(
            self.store.get_experiment(experiment_id),
            PolicyStore(self.settings).decisions(experiment_id),
        )
