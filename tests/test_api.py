import json

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.engine import Engine


def manifest(name="Reconciliation"):
    return {
        "name": name,
        "domain": "data",
        "scenarios": [
            {
                "name": "Invoice total",
                "domain": "data",
                "instruction": "Add invoice amounts.",
                "inputs": {"amounts": [20, 22]},
                "checks": [{"name": "Total", "kind": "numeric", "path": "total", "expected": 42}],
                "reference_output": {"total": 42},
            }
        ],
    }


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "workspace"), start_worker=False)
    with TestClient(app) as client:
        yield client


def test_collection_run_export_and_frozen_rerun(client):
    created = client.post("/api/collections", json=manifest())
    assert created.status_code == 201, created.text
    collection = created.json()
    control = client.post(
        "/api/systems", json={"name": "Reference control", "kind": "reference"}
    ).json()
    body = {
        "name": "Ledger check",
        "collection_id": collection["id"],
        "system_ids": [control["id"]],
    }
    response = client.post("/api/experiments", json=body)
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    engine = Engine(client.app.state.store, client.app.state.settings)
    engine.run_experiment(run_id)
    result = client.get(f"/api/experiments/{run_id}").json()
    assert result["status"] == "completed", result
    assert result["trials"][0]["passed"] is True
    for format in ("json", "csv", "html"):
        exported = client.get(f"/api/experiments/{run_id}/export?format={format}")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
    replay = client.post(f"/api/experiments/{run_id}/rerun").json()
    assert replay["id"] != run_id
    assert replay["collection_hash"] == result["collection_hash"]
    assert client.get("/api/overview").json()["experiments"] == 2


def test_origin_and_host_boundaries(client):
    assert (
        client.post(
            "/api/collections", json=manifest(), headers={"Origin": "https://untrusted.test"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/collections", json=manifest(), headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert client.get("/api/health", headers={"Host": "attacker.test"}).status_code == 400


def test_token_required(tmp_path):
    with TestClient(
        create_app(Settings(data_dir=tmp_path, token="test-token"), start_worker=False)
    ) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/collections").status_code == 401
        assert (
            client.get(
                "/api/collections", headers={"Authorization": "Bearer test-token"}
            ).status_code
            == 200
        )


def test_request_limit_including_chunked(tmp_path):
    with TestClient(
        create_app(Settings(data_dir=tmp_path, max_request_bytes=512), start_worker=False)
    ) as client:
        assert (
            client.post(
                "/api/import", content=b"x" * 513, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )

        def chunks():
            for _ in range(10):
                yield b"x" * 100

        assert (
            client.post(
                "/api/import", content=chunks(), headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )


def test_import_and_malformed_upload(client):
    response = client.post(
        "/api/import",
        files={"file": ("collection.json", json.dumps(manifest()), "application/json")},
    )
    assert response.status_code == 201, response.text
    assert (
        client.post(
            "/api/import", files={"file": ("a.yaml", "a: &x [1]\nb: *x", "application/yaml")}
        ).status_code
        == 400
    )
    assert client.post("/api/import", json=["bad"]).status_code == 400
    assert (
        client.post(
            "/api/import", content=b"invalid", headers={"Content-Type": "application/json"}
        ).status_code
        == 400
    )


def test_source_preview(client):
    response = client.post(
        "/api/sources", files={"file": ("totals.csv", "name,total\nA,42\n", "text/csv")}
    )
    assert response.status_code == 200, response.text


def test_unknown_record_is_404(client):
    assert client.get("/api/collections/missing").status_code == 404
    assert client.get("/api/experiments/missing").status_code == 404


def test_schema_is_local_and_hardened(client):
    response = client.get("/api/schema")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Everval AI"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_no_remote_model_admitted_by_default(client):
    collection = client.post("/api/collections", json=manifest()).json()
    system = client.post(
        "/api/systems",
        json={
            "name": "Remote",
            "kind": "http",
            "model": "model",
            "base_url": "https://example.com/v1",
            "input_cost_per_million": 1,
            "output_cost_per_million": 1,
        },
    ).json()
    response = client.post(
        "/api/experiments",
        json={
            "name": "Not admitted",
            "collection_id": collection["id"],
            "system_ids": [system["id"]],
            "budget_usd": 1,
        },
    )
    assert response.status_code == 400, response.text
