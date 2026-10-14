"""Runtime options stay explicit and pinned through CLI, API and durable jobs."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cvals.api import create_app
from cvals.benchmarks import Benchmarks
from cvals.cli import app
from cvals.config import Settings
from cvals.environments import Environments
from cvals.jobs import Jobs
from cvals.workbench import Workbench

IMAGE = "sha256:" + "b" * 64
COMMIT = "a" * 40
ARTIFACT = "c" * 64


def test_node_api_freezes_language_runner_and_artifact_references(
    tmp_path: Any, monkeypatch: Any
) -> None:
    plans = []

    def plan(self: Any, project_id: str, **kwargs: Any) -> dict[str, Any]:
        plans.append(kwargs)
        return {"source_commit": COMMIT, "base_image_digest": IMAGE}

    monkeypatch.setattr(Workbench, "environment_plan", plan)
    monkeypatch.setattr("cvals.runtime.ensure_worker", lambda settings: None)
    settings = Settings(tmp_path)
    with TestClient(create_app(settings, start_worker=False)) as client:
        response = client.post(
            "/api/projects/repository/environment/prepare",
            json={
                "language": "typescript",
                "runner": "vitest",
                "artifacts": [ARTIFACT],
            },
        )
        assert response.status_code == 202, response.text
        queued = Jobs(settings).get(response.json()["id"])
        assert queued["payload"] == {
            "project_id": "repository",
            "packages": None,
            "revision": COMMIT,
            "base_image": IMAGE,
            "language": "typescript",
            "runner": "vitest",
            "artifacts": [ARTIFACT],
        }
        assert plans[0]["language"] == "typescript"


@pytest.mark.parametrize(
    "body",
    [
        {"language": "shell"},
        {"runner": "npm test"},
        {"artifacts": ["../../credential"]},
        {"artifacts": ["c" * 63]},
        {"artifacts": [1]},
        {"token": "CANARY"},
    ],
)
def test_api_rejects_bad_runtime_inputs_before_worker(
    tmp_path: Any, monkeypatch: Any, body: dict[str, Any]
) -> None:
    started = []
    monkeypatch.setattr("cvals.runtime.ensure_worker", lambda settings: started.append(True))
    with TestClient(create_app(Settings(tmp_path), start_worker=False)) as client:
        assert (
            client.post("/api/projects/repository/environment/prepare", json=body).status_code
            == 422
        )
    assert not started


def test_durable_preparation_selects_matching_profile(tmp_path: Any, monkeypatch: Any) -> None:
    prepared = []
    configured = []

    def prepare(self: Any, **payload: Any) -> dict[str, Any]:
        prepared.append(payload)
        return {"image_digest": IMAGE, "language": payload["language"], "runner": payload["runner"]}

    def configure(self: Any, project_id: str, image: str, **kwargs: Any) -> dict[str, Any]:
        configured.append((project_id, image, kwargs))
        return {"runtime": kwargs["runtime"], "language": kwargs["language"]}

    monkeypatch.setattr(Environments, "prepare", prepare)
    monkeypatch.setattr(Benchmarks, "configure", configure)
    jobs = Jobs(Settings(tmp_path))
    queued = jobs.enqueue(
        "prepare",
        {
            "project_id": "p",
            "revision": COMMIT,
            "base_image": IMAGE,
            "language": "javascript",
            "runner": "jest",
            "artifacts": [ARTIFACT],
        },
    )
    assert jobs.run_pending_once()
    result = jobs.wait(queued["id"])
    assert prepared[0]["artifacts"] == [ARTIFACT]
    assert configured == [("p", IMAGE, {"runtime": "jest", "language": "javascript"})]
    assert result["profile"]["runtime"] == "jest"


@pytest.mark.parametrize(
    "values",
    [
        {"language": "rust"},
        {"runner": "shell"},
        {"artifacts": ["bad"]},
        {"artifacts": [ARTIFACT, ARTIFACT]},
        {"language": "typescript", "packages": []},
        {"artifacts": [{"secret": "CANARY"}]},
    ],
)
def test_job_admission_refuses_ambiguous_or_credential_payloads(
    tmp_path: Any, values: dict[str, Any]
) -> None:
    jobs = Jobs(Settings(tmp_path))
    with pytest.raises(ValueError):
        jobs.enqueue(
            "prepare", {"project_id": "p", "revision": COMMIT, "base_image": IMAGE, **values}
        )
    assert not jobs.list()


def test_workbench_pins_options_before_queueing(tmp_path: Any, monkeypatch: Any) -> None:
    workbench = Workbench(Settings(tmp_path))
    plans = []
    queues = []

    def plan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        plans.append(kwargs)
        return {"source_commit": COMMIT, "base_image_digest": IMAGE}

    monkeypatch.setattr(workbench, "environment_plan", plan)
    monkeypatch.setattr(
        workbench, "_job", lambda kind, payload: queues.append((kind, payload)) or payload
    )
    result = workbench.prepare_environment(
        "p", language="typescript", runner="node-test", artifacts=[ARTIFACT]
    )
    assert plans[0]["artifacts"] == [ARTIFACT]
    assert result["language"] == "typescript" and result["revision"] == COMMIT
    assert queues[0][0] == "prepare"


def test_cli_language_options_reach_shared_service(monkeypatch: Any) -> None:
    calls = []
    monkeypatch.setattr(
        "cvals.terminal_cli.call", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "environments",
            "prepare",
            "p",
            "--language",
            "typescript",
            "--runner",
            "vitest",
            "--artifact",
            ARTIFACT,
            "--base-image",
            "node:24-slim",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"language": "typescript", "runner": "vitest", "artifacts": [ARTIFACT]}
    assert calls[0][0] == ("prepare_environment", "p", None, None, "node:24-slim")
    result = runner.invoke(
        app, ["projects", "profile", "p", IMAGE, "--language", "javascript", "--runtime", "jest"]
    )
    assert result.exit_code == 0, result.output
    assert calls[1][1] == {"language": "javascript", "runtime": "jest"}
