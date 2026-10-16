"""Health recognizes the dedicated worker used in on-prem service deployments."""

from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.runtime import worker_lock


def test_health_tracks_dedicated_worker_lock_without_starting_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("cvals.api.docker_available", lambda _: False)
    settings = Settings(tmp_path / "workspace")
    with TestClient(create_app(settings, start_worker=False)) as client:
        before = client.get("/api/health").json()
        assert before["worker_running"] is False
        assert before["worker_mode"] == "stopped"
        lock = worker_lock(settings)
        assert lock is not None
        try:
            for endpoint in ("/api/health", "/api/settings"):
                result = client.get(endpoint).json()
                assert result["worker_running"] is True
                assert result["worker_mode"] == "dedicated"
        finally:
            lock.close()
        assert client.get("/api/health").json()["worker_running"] is False
        assert client.get("/api/settings").json()["worker_mode"] == "stopped"
