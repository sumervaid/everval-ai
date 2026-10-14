"""Public interfaces preserve role and evidence boundaries."""

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


def test_experience_routes_enforce_roles_and_keep_summary_public(tmp_path):
    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    collection = store.save_collection(
        Collection(
            name="Interface checks",
            scenarios=[
                Scenario(
                    name="Private oracle",
                    domain="data",
                    instruction="Return a result",
                    inputs={"rows": [1]},
                    checks=[
                        Check(
                            name="Correct",
                            kind="equals",
                            path="answer",
                            expected="hidden-oracle-canary",
                        )
                    ],
                    reference_output={"answer": "hidden-oracle-canary"},
                )
            ],
        )
    )
    reference = next(s["id"] for s in store.list_systems() if s["kind"] == "reference")
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Interface run", collection_id=collection["id"], system_ids=[reference]
        )
    )
    experiment = Engine(store, settings).run_experiment(experiment["id"])
    identities = IdentityStore(settings)
    admin = {"Authorization": "Bearer " + identities.bootstrap("Owner")["token"]}
    headers = {"admin": admin}
    for role in ("operator", "reviewer", "viewer"):
        user = identities.create_user(role, role, "test")
        headers[role] = {
            "Authorization": "Bearer "
            + identities.issue_token(user["id"], "Test", actor="test")["token"]
        }
    experiment_id = experiment["id"]
    trial_id = experiment["trials"][0]["id"]
    with TestClient(create_app(settings, start_worker=False)) as client:
        for path in (
            f"/api/experiments/{experiment_id}/triage",
            f"/api/experiments/{experiment_id}/economics",
            f"/api/experiments/{experiment_id}/trials/{trial_id}/diagnostics",
        ):
            assert client.get(path, headers=headers["operator"]).status_code == 200
            assert client.get(path, headers=headers["reviewer"]).status_code == 403
            assert client.get(path, headers=headers["viewer"]).status_code == 403
        brief = client.get(f"/api/experiments/{experiment_id}/brief", headers=headers["viewer"])
        assert brief.status_code == 200
        assert "hidden-oracle-canary" not in brief.text
        assert brief.json()["contains_task_content"] is False
        assert (
            client.get(
                f"/api/experiments/{experiment_id}/brief", headers=headers["reviewer"]
            ).status_code
            == 403
        )
        regrade = f"/api/experiments/{experiment_id}/trials/{trial_id}/regrade"
        assert client.post(regrade, headers=headers["reviewer"]).status_code == 403
        result = client.post(regrade, headers=headers["operator"])
        assert result.status_code == 201 and result.json()["passed"] is True
        assert (
            client.put(
                "/api/spending-limits", headers=headers["operator"], json={"daily_usd": 2}
            ).status_code
            == 403
        )
        assert (
            client.put("/api/spending-limits", headers=admin, json={"daily_usd": 2}).status_code
            == 200
        )
        assert (
            client.put("/api/spending-limits", headers=admin, json={"daily_usd": -1}).status_code
            == 422
        )
        limits = client.get("/api/spending-limits", headers=headers["operator"]).json()
        assert limits[0]["daily_usd"] == 2
    assert store.get_experiment(experiment_id) == experiment


def test_cli_registers_workflows_and_freezes_adapter_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path / "workspace"))
    runner = CliRunner()
    for group in ("team", "guides", "changes", "recurring", "artifacts", "diagnostics", "spending"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "systems",
            "add-agent",
            "Local integration",
            "--model",
            "test",
            "--base-url",
            "http://127.0.0.1:9000",
            "--revision",
            "sha256:example",
            "--input-limit",
            "100",
            "--output-limit",
            "200",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["harness"] == "external-agent" and data["adapter_revision"] == "sha256:example"
    assert data["adapter_input_token_limit"] == 100 and data["adapter_output_token_limit"] == 200
    result = runner.invoke(app, ["spending", "set", "--daily", "5", "--monthly", "20"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["monthly_usd"] == 20
