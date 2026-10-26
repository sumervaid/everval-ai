"""Conversation admission, API role boundaries and durable report delivery."""

from pathlib import Path

from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, Scenario


def setup_client(tmp_path: Path):
    app = create_app(Settings(tmp_path), start_worker=False)
    app.state.store.ensure_fixture_systems()
    app.state.store.save_collection(
        Collection(
            id="support-tickets",
            name="Support tickets",
            scenarios=[
                Scenario(
                    name="Card delivery",
                    domain="data",
                    instruction='Return {"category": "card_delivery"} for this installation control.',
                    checks=[
                        Check(
                            name="Route", kind="equals", path="category", expected="card_delivery"
                        )
                    ],
                    reference_output={"category": "card_delivery"},
                )
            ],
        )
    )
    return app


def test_conversation_api_executes_and_delivers_local_report(tmp_path: Path) -> None:
    from cvals.conversation_execution import ExecutionService

    app = setup_client(tmp_path)
    with TestClient(app) as client:
        catalog = client.get("/api/conversations/catalog").json()
        assert catalog["collections"][0]["name"] == "Support tickets"
        session = client.post("/api/conversations").json()
        base = "/api/conversations/" + session["id"]
        empty = client.get(base + "/reports")
        assert empty.status_code == 200 and empty.json() == []
        assert client.get("/api/conversations/missing/reports").status_code == 404
        response = client.post(
            base + "/messages",
            json={
                "message": 'Compare fixture-reference on "Support tickets" every 2 weeks and save HTML and CSV reports. Budget $0 per run and $0 total.'
            },
        )
        assert response.status_code == 200, response.text
        current = response.json()
        if not current.get("experiment_id"):
            response = client.post(base + "/messages", json={"message": "start"})
            assert response.status_code == 200, response.text
            current = response.json()
        assert current.get("experiment_id"), current
        experiment_id = current["experiment_id"]
        assert len(app.state.store.list_experiments()) == 1
        # A client retry never admits another initial run.
        client.post(base + "/messages", json={"message": "start"})
        assert len(app.state.store.list_experiments()) == 1
        Engine(app.state.store, app.state.settings).run_experiment(experiment_id)
        ExecutionService(app.state.settings, app.state.store).tick()
        files = client.get(base + "/reports").json()
        assert {item["format"] for item in files} == {"html", "csv"}
        for item in files:
            report = client.get(base + "/reports/" + item["id"])
            assert report.status_code == 200
            assert report.headers["content-disposition"].startswith("attachment;")
            assert b"card" in report.content.lower()
        client.post(base + "/messages", json={"message": "pause"})
        saved = client.get(base).json()
        assert saved["status"] == "paused"
        assert len(saved["messages"]) >= 4
        assert client.get(base + "/reports/missing").status_code == 404


def test_chat_routes_reject_viewer_and_cross_origin_mutation(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    identities = IdentityStore(settings)
    identities.bootstrap("Admin")
    viewer = identities.create_user("Viewer", "viewer", "admin")
    token = identities.issue_token(viewer["id"], "Test", actor="admin")["token"]
    with TestClient(create_app(settings, start_worker=False)) as client:
        headers = {"Authorization": "Bearer " + token}
        for suffix in ("", "/catalog", "/private", "/private/reports", "/private/reports/file"):
            assert client.get("/api/conversations" + suffix, headers=headers).status_code == 403
        assert client.post("/api/conversations", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/conversations/private/messages", json={"message": "start"}, headers=headers
            ).status_code
            == 403
        )
    with TestClient(create_app(Settings(tmp_path / "local"), start_worker=False)) as client:
        assert (
            client.post(
                "/api/conversations", headers={"Origin": "https://unrelated.example"}
            ).status_code
            == 403
        )
        session = client.post("/api/conversations").json()
        url = "/api/conversations/" + session["id"] + "/messages"
        assert client.post(url, json={"message": "x" * 8001}).status_code == 422
        assert client.post(url, json={"message": "status", "extra": True}).status_code == 422
        assert client.post(url, json={"message": ""}).status_code == 422
