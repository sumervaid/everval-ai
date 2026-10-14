"""Regression cases discovered during independent boundary review."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario


@pytest.fixture
def reviewed_client(tmp_path):
    application = create_app(Settings(data_dir=tmp_path / "workspace"), start_worker=False)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


def test_terminal_event_stream_drains_every_recorded_event(reviewed_client):
    store = reviewed_client.app.state.store
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="events",
            name="Events",
            scenarios=[
                Scenario(
                    name="Task",
                    domain="data",
                    instruction="Return one",
                    checks=[Check(name="One", kind="numeric", path="value", expected=1)],
                    reference_output={"value": 1},
                )
            ],
        )
    )
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Event history", collection_id="events", system_ids=["fixture-reference"]
        )
    )
    Engine(store, reviewed_client.app.state.settings).run_experiment(experiment["id"])
    with store._connect(write=True) as connection:
        for sequence in range(600):
            store._event(connection, experiment["id"], "review.test", {"sequence": sequence})
        total = connection.execute(
            "SELECT COUNT(*) FROM events WHERE experiment_id=?", (experiment["id"],)
        ).fetchone()[0]
    response = reviewed_client.get(f"/api/experiments/{experiment['id']}/events")
    assert response.status_code == 200
    assert response.text.count("\nid: ") + response.text.startswith("id: ") == total
    assert response.text.endswith("event: complete\ndata: {}\n\n")


def test_cli_traceback_does_not_expose_workspace_token(tmp_path):
    canary = "REVIEW_ONLY_FAKE_TOKEN_739182"
    environment = {
        **os.environ,
        "CVALS_DATA_DIR": str(tmp_path / "workspace"),
        "CVALS_TOKEN": canary,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cvals.cli import app; app()",
            "experiments",
            "rerun",
            "missing",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert canary not in result.stdout + result.stderr


def test_malformed_origin_is_a_client_error(reviewed_client):
    response = reviewed_client.post("/api/starters", headers={"Origin": "http://[invalid"})
    assert 400 <= response.status_code < 500


def test_origin_scheme_is_part_of_the_origin(reviewed_client):
    response = reviewed_client.post("/api/starters", headers={"Origin": "https://testserver"})
    assert response.status_code == 403


def test_non_ascii_authorization_is_rejected_without_server_error(tmp_path):
    application = create_app(
        Settings(data_dir=tmp_path / "workspace", token="test-token"), start_worker=False
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/api/collections", headers=[(b"Authorization", b"Bearer \xff")])
        assert response.status_code == 401


def test_source_parser_does_not_block_health_requests(reviewed_client, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    release = threading.Event()

    def slow_parser(*args):
        entered.set()
        release.wait(3)
        return {"kind": "document", "text": "Preview"}

    monkeypatch.setattr("cvals.ingestion.ingest_source", slow_parser)
    with ThreadPoolExecutor(max_workers=2) as pool:
        source = pool.submit(
            reviewed_client.post,
            "/api/sources",
            files={"file": ("source.txt", "Text", "text/plain")},
        )
        try:
            assert entered.wait(2)
            health = pool.submit(reviewed_client.get, "/api/health")
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert source.result(timeout=2).status_code == 200


def completed_experiment(client):
    store = client.app.state.store
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="integration",
            name="Integration",
            scenarios=[
                Scenario(
                    id="one",
                    name="One",
                    domain="data",
                    instruction="Return one",
                    checks=[Check(id="one", name="One", kind="numeric", path="value", expected=1)],
                    reference_output={"value": 1},
                )
            ],
        )
    )
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Review target", collection_id="integration", system_ids=["fixture-reference"]
        )
    )
    return Engine(store, client.app.state.settings).run_experiment(experiment["id"])


def test_review_and_schedule_api_preserve_automated_evidence(reviewed_client):
    experiment = completed_experiment(reviewed_client)
    id = experiment["id"]
    body = {
        "trial_id": experiment["trials"][0]["id"],
        "reviewer": "Reviewer",
        "dimension": "clarity",
        "score": 0.7,
        "rationale": "The explanation is incomplete.",
    }
    first = reviewed_client.post(f"/api/experiments/{id}/reviews", json=body)
    assert first.status_code == 201, first.text
    second = reviewed_client.post(
        f"/api/experiments/{id}/reviews",
        json={**body, "score": 0.9, "rationale": "Revised after review."},
    )
    assert second.status_code == 201
    history = reviewed_client.get(f"/api/experiments/{id}/reviews").json()
    assert len(history) == 2 and not history[0]["latest"] and history[1]["latest"]
    current = reviewed_client.get(f"/api/experiments/{id}").json()
    assert current["report"] == experiment["report"]
    assert len(current["human_reviews"]) == 2
    exported = reviewed_client.get(f"/api/experiments/{id}/export").json()
    assert exported["human_reviews"] == history
    schedule = reviewed_client.post(
        "/api/schedules", json={"name": "Nightly", "experiment_id": id, "interval_minutes": 1440}
    )
    assert schedule.status_code == 201, schedule.text
    schedule_id = schedule.json()["id"]
    disabled = reviewed_client.patch(f"/api/schedules/{schedule_id}", json={"enabled": False})
    assert disabled.status_code == 200 and not disabled.json()["enabled"]
    assert reviewed_client.get("/api/schedules").json()[0]["experiment_id"] == id


def test_review_api_rejects_boolean_scores(reviewed_client):
    experiment = completed_experiment(reviewed_client)
    response = reviewed_client.post(
        f"/api/experiments/{experiment['id']}/reviews",
        json={
            "trial_id": experiment["trials"][0]["id"],
            "reviewer": "Reviewer",
            "dimension": "clarity",
            "score": True,
            "rationale": "Boolean is not a score.",
        },
    )
    assert response.status_code == 422


def test_event_resume_and_cursor_validation(reviewed_client):
    experiment = completed_experiment(reviewed_client)
    events = reviewed_client.app.state.store.events(experiment["id"])
    after = events[1]["id"]
    response = reviewed_client.get(
        f"/api/experiments/{experiment['id']}/events", headers={"Last-Event-ID": str(after)}
    )
    assert response.status_code == 200
    assert response.text.count("\nid: ") + response.text.startswith("id: ") == len(events) - 2
    assert (
        reviewed_client.get(f"/api/experiments/{experiment['id']}/events?after={2**80}").status_code
        == 422
    )
    assert (
        reviewed_client.get(
            f"/api/experiments/{experiment['id']}/events", headers={"Last-Event-ID": "invalid"}
        ).status_code
        == 400
    )


def test_yaml_nesting_is_rejected_before_materialization():
    from cvals.api import _load_collection

    payload = "tasks: " + "[" * 65 + "1" + "]" * 65
    with pytest.raises(ValueError, match="nesting"):
        _load_collection(payload.encode(), "nested.yaml")


def test_backup_restore_preserves_snapshots_reviews_and_schedules(
    reviewed_client, tmp_path, monkeypatch
):
    import shutil
    import sqlite3

    from typer.testing import CliRunner

    from cvals.cli import app
    from cvals.reviews import ReviewStore
    from cvals.schedules import ScheduleStore
    from cvals.store import Store

    experiment = completed_experiment(reviewed_client)
    settings = reviewed_client.app.state.settings
    monkeypatch.setenv("CVALS_DATA_DIR", str(settings.data_dir))
    # Keep WAL open so the backup must include committed pages beyond the main file.
    keeper = sqlite3.connect(settings.database_path)
    try:
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        reviews = ReviewStore(settings)
        reviews.add(
            experiment["id"],
            experiment["trials"][0]["id"],
            "Reviewer",
            "clarity",
            0.8,
            "Usable with a short explanation.",
        )
        schedules = reviewed_client.app.state.worker.schedules
        schedules.create("Restore check", experiment["id"], 1440, enabled=False)
        destination = tmp_path / "backup.sqlite3"
        result = CliRunner().invoke(app, ["backup", "--output", str(destination)])
        assert result.exit_code == 0, result.output
        restored_settings = Settings(data_dir=tmp_path / "restored")
        restored_settings.data_dir.mkdir(mode=0o700)
        shutil.copyfile(destination, restored_settings.database_path)
        restored = Store(restored_settings)
        restored.init()
        evidence = restored.get_experiment(experiment["id"])
        assert evidence["snapshot"] == experiment["snapshot"]
        assert evidence["report"] == experiment["report"]
        assert ReviewStore(restored_settings).list(experiment["id"]) == reviews.list(
            experiment["id"]
        )
        assert ScheduleStore(restored_settings, restored).list() == schedules.list()
    finally:
        keeper.close()
