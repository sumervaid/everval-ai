from pathlib import Path

from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.identity import IdentityStore


def test_new_private_evidence_routes_require_operator(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    identities = IdentityStore(settings)
    identities.bootstrap("Admin")
    viewer = identities.create_user("Viewer", "viewer", "admin")
    token = identities.issue_token(viewer["id"], "Test", actor="admin")["token"]
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings, start_worker=False)) as client:
        for path in (
            "/api/autopilot",
            "/api/autopilot/private",
            "/api/research",
            "/api/research/private",
            "/api/optimization",
            "/api/optimization/private",
            "/api/budget/status",
            "/api/community/packs",
            "/api/community/packs/private",
        ):
            assert client.get(path, headers=headers).status_code == 403, path
        for path in (
            "/api/optimization",
            "/api/autopilot",
            "/api/collections/private/quality",
            "/api/community/packs",
            "/api/community/packs/private/approve",
            "/api/experiments/private/trials/private/investigate",
            "/api/experiments/private/trials/private/reduce",
        ):
            assert client.post(path, json={}, headers=headers).status_code == 403, path
        assert client.get("/api/community/catalog", headers=headers).status_code == 200


def test_local_readiness_and_request_validation(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(tmp_path), start_worker=False)) as client:
        for path in (
            "/api/autopilot",
            "/api/research",
            "/api/optimization",
            "/api/community/packs",
        ):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.json() == []
        status = client.get("/api/budget/status").json()
        assert status["known_provider_cost_usd"] == 0
        assert status["external_service_cost_usd"] is None
        assert (
            client.post(
                "/api/budget/projection", json={"experiment_id": "x", "runs": True}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/experiments/x/trials/y/investigate", json={"repetitions": 100}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/community/packs", content=b'{"duplicate":1,"duplicate":2}'
            ).status_code
            == 400
        )
