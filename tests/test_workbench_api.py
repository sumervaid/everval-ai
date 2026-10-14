"""New repository endpoints inherit the existing local API boundaries."""

from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings


def test_repository_endpoints_require_workspace_token(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, token="test-key"), start_worker=False)
    with TestClient(app) as client:
        for endpoint in [
            "/api/projects",
            "/api/releases",
            "/api/jobs",
            "/api/audit",
            "/api/audit/verify",
        ]:
            assert client.get(endpoint).status_code == 401
            assert (
                client.get(endpoint, headers={"Authorization": "Bearer test-key"}).status_code
                == 200
            )
        assert (
            client.post(
                "/api/projects",
                json={"source": "owner/repo"},
                headers={"Authorization": "Bearer test-key", "Origin": "https://other.example"},
            ).status_code
            == 403
        )


def test_repository_requests_are_bounded_before_side_effects(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False)) as client:
        assert client.post("/api/projects", json={"source": "x" * 3000}).status_code == 422
        assert (
            client.post(
                "/api/projects", json={"source": "owner/repo", "token": "unwanted"}
            ).status_code
            == 422
        )
        assert client.post("/api/projects/missing/discover", json={"limit": 0}).status_code == 422
        assert client.get("/api/jobs/missing").status_code == 404
        assert client.get("/api/audit?limit=10001").status_code == 422
        assert client.get("/api/projects").json() == []


def test_preparation_request_freezes_resolved_inputs_before_queue(tmp_path, monkeypatch):
    from cvals.jobs import Jobs
    from cvals.workbench import Workbench

    recorded = []
    digest = "sha256:" + "b" * 64

    def plan(self, project_id, **kwargs):
        recorded.append((project_id, kwargs))
        return {"source_commit": "a" * 40, "base_image_digest": digest}

    monkeypatch.setattr(Workbench, "environment_plan", plan)
    monkeypatch.setattr("cvals.runtime.ensure_worker", lambda settings: {"running": True})
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, start_worker=False)) as client:
        result = client.post(
            "/api/projects/example/environment/prepare", json={"packages": ["pytest==8.4.2"]}
        )
    assert result.status_code == 202
    queued = Jobs(settings).get(result.json()["id"])
    assert queued["payload"] == {
        "project_id": "example",
        "packages": ["pytest==8.4.2"],
        "revision": "a" * 40,
        "base_image": digest,
    }
    assert recorded[0][1]["revision"] is None


def test_invalid_environment_declaration_never_starts_worker(tmp_path, monkeypatch):
    from cvals.workbench import Workbench

    started = []

    def reject(self, project_id, **kwargs):
        raise ValueError("URL dependencies are not allowed")

    monkeypatch.setattr(Workbench, "environment_plan", reject)
    monkeypatch.setattr("cvals.runtime.ensure_worker", lambda settings: started.append(True))
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False)) as client:
        response = client.post(
            "/api/projects/example/environment/prepare",
            json={"packages": ["name @ https://private.example/package"]},
        )
        assert response.status_code == 400
        assert client.get("/api/jobs").json() == []
    assert started == []
