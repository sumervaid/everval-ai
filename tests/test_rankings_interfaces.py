"""Rankings expose aggregate evidence within existing workspace permissions."""

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cvals.api import create_app
from cvals.cli import app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.store import Store


def saved_control(settings):
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    collection = store.save_collection(
        Collection(
            name="Interface benchmark",
            domain="data",
            scenarios=[
                Scenario(
                    name="Private fixture",
                    domain="data",
                    instruction="instruction-canary",
                    inputs={"private_input": "input-canary"},
                    checks=[
                        Check(
                            name="Expected", kind="equals", path="answer", expected="oracle-canary"
                        )
                    ],
                    reference_output={"answer": "oracle-canary"},
                )
            ],
        )
    )
    reference = next(s["id"] for s in store.list_systems() if s["kind"] == "reference")
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Control evidence",
            collection_id=collection["id"],
            system_ids=[reference],
        )
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    return store, result


def test_rankings_api_roles_validation_and_read_only_summary(tmp_path):
    settings = Settings(tmp_path / "workspace")
    store, experiment = saved_control(settings)
    identities = IdentityStore(settings)
    tokens = {"admin": identities.bootstrap("Owner")["token"]}
    for role in ("operator", "reviewer", "viewer"):
        user = identities.create_user(role, role, "test")
        tokens[role] = identities.issue_token(user["id"], "Test", actor="test")["token"]
    with TestClient(create_app(settings, start_worker=False)) as client:
        assert client.get("/api/rankings").status_code == 401
        for role, token in tokens.items():
            headers = {"Authorization": "Bearer " + token}
            response = client.get("/api/rankings", headers=headers)
            assert response.status_code == (200 if role in {"admin", "operator"} else 403)
        headers = {"Authorization": "Bearer " + tokens["operator"]}
        response = client.get("/api/rankings?limit=1", headers=headers)
        data = response.json()
        assert data["window"]["scanned_runs"] == 1
        assert data["cells"][0]["experiment_id"] == experiment["id"]
        assert data["cells"][0]["ranks"]["quality"] is None
        for secret in ("instruction-canary", "input-canary", "oracle-canary"):
            assert secret not in response.text
        for limit in ("0", "501", "abc"):
            assert client.get("/api/rankings?limit=" + limit, headers=headers).status_code == 422
    assert store.get_experiment(experiment["id"]) == experiment


def test_rankings_cli_matrix_json_and_validation(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "workspace")
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(settings.data_dir))
    runner = CliRunner()
    empty = runner.invoke(app, ["rankings"])
    assert empty.exit_code == 0
    assert "No saved benchmark runs" in empty.output
    store, experiment = saved_control(settings)
    for metric in ("quality", "cost", "latency"):
        result = runner.invoke(app, ["rankings", "--metric", metric])
        assert result.exit_code == 0, result.output
        assert "Fixture control" in result.output
        assert "Unranked" in result.output
    result = runner.invoke(app, ["rankings", "--json", "--limit", "1"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["cells"][0]["experiment_id"] == experiment["id"]
    assert data["window"]["limit"] == 1
    for args in (["--limit", "0"], ["--limit", "501"], ["--metric", "overall"]):
        assert runner.invoke(app, ["rankings", *args]).exit_code == 2
    assert store.get_experiment(experiment["id"]) == experiment
