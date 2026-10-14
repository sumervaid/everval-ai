"""Authorization applies to concrete routes, identities, and revocable tokens."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.identity import IdentityStore


@pytest.fixture
def team(tmp_path):
    settings = Settings(tmp_path / "workspace", token="legacy-shared-token")
    identities = IdentityStore(settings)
    bootstrap = identities.bootstrap("Administrator")
    headers = {"Authorization": "Bearer " + bootstrap["token"]}
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        yield settings, identities, client, bootstrap, headers


def issue(identities, role):
    user = identities.create_user(role.title(), role, "test-admin")
    token = identities.issue_token(user["id"], "Test session", actor="test-admin")
    return user, token, {"Authorization": "Bearer " + token["token"]}


def test_bootstrap_is_atomic_one_time_and_tokens_are_never_stored_raw(team):
    settings, identities, client, bootstrap, headers = team
    with pytest.raises(ValueError, match="already provisioned"):
        identities.bootstrap("Another admin")
    assert client.get("/api/session", headers=headers).json()["role"] == "admin"
    with sqlite3.connect(settings.database_path) as db:
        row = db.execute(
            "SELECT salt,digest FROM team_tokens WHERE id=?", (bootstrap["id"],)
        ).fetchone()
        assert len(row[0]) == 32 and len(row[1]) == 128
        assert bootstrap["token"] not in "\n".join(db.iterdump())
    assert bootstrap["token"] not in json.dumps(identities.tokens())
    assert bootstrap["token"] not in json.dumps(AuditLog(settings).list())


def test_team_mode_does_not_accept_shared_token_or_missing_credentials(team):
    _, _, client, _, headers = team
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/collections").status_code == 401
    assert (
        client.get(
            "/api/collections", headers={"Authorization": "Bearer legacy-shared-token"}
        ).status_code
        == 401
    )
    assert client.get("/api/collections", headers=headers).status_code == 200


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_restricted_roles_cannot_reach_generic_evidence_or_mutations(team, role):
    _, identities, client, _, _ = team
    _, _, headers = issue(identities, role)
    paths = [
        "/api/collections",
        "/api/collections/anything/agent-view",
        "/api/experiments",
        "/api/experiments/anything",
        "/api/experiments/anything/export",
        "/api/experiments/anything/events",
        "/api/experiments/anything/reviews",
        "/api/experiments/anything/gates",
        "/api/audit",
        "/api/jobs",
        "/api/settings",
        "/api/schema",
        "/api/systems",
        "/api/case-imports",
        "/api/team/tokens",
    ]
    for path in paths:
        assert client.get(path, headers=headers).status_code == 403, path
    for path in (
        "/api/collections",
        "/api/experiments",
        "/api/starters",
        "/api/submissions",
        "/api/review-queues",
    ):
        assert client.post(path, json={}, headers=headers).status_code == 403, path
    assert client.delete("/api/systems/anything", headers=headers).status_code == 403
    assert (
        client.patch("/api/schedules/anything", json={"enabled": True}, headers=headers).status_code
        == 403
    )


def test_operator_cannot_escalate_roles_and_viewer_gets_only_summary(team):
    _, identities, client, _, _ = team
    _, _, operator = issue(identities, "operator")
    _, _, viewer = issue(identities, "viewer")
    assert client.get("/api/collections", headers=operator).status_code == 200
    assert (
        client.post(
            "/api/team/users", json={"name": "Escalation", "role": "admin"}, headers=operator
        ).status_code
        == 403
    )
    assert client.get("/api/team/summary", headers=viewer).status_code == 200
    assert client.get("/api/review-inbox", headers=viewer).status_code == 403


def test_unregistered_new_endpoints_fail_closed_even_for_admin(team):
    _, _, client, _, headers = team

    @client.app.get("/api/not-registered")
    def new_endpoint():
        return {"secret": "must never reach caller"}

    assert client.get("/api/not-registered", headers=headers).status_code == 403
    assert client.post("/api/health", headers=headers).status_code == 403
    assert client.get("/api/missing", headers=headers).status_code == 403


def test_revocation_disabling_expiry_and_last_admin_protection(team):
    _, identities, client, bootstrap, admin = team
    user, token, headers = issue(identities, "reviewer")
    assert client.get("/api/session", headers=headers).status_code == 200
    assert client.post(f"/api/team/tokens/{token['id']}/revoke", headers=admin).status_code == 204
    assert client.get("/api/session", headers=headers).status_code == 401
    replacement = identities.issue_token(user["id"], "Replacement", actor="test-admin")
    new_headers = {"Authorization": "Bearer " + replacement["token"]}
    identities.set_active(user["id"], False, "test-admin")
    assert client.get("/api/session", headers=new_headers).status_code == 401
    identities.set_active(user["id"], True, "test-admin")
    with identities.store._connect(write=True) as db:
        db.execute(
            "UPDATE team_tokens SET expires_at='2000-01-01' WHERE id=?", (replacement["id"],)
        )
    assert client.get("/api/session", headers=new_headers).status_code == 401
    assert (
        client.post(f"/api/team/tokens/{bootstrap['id']}/revoke", headers=admin).status_code == 400
    )
    assert client.get("/api/session", headers=admin).status_code == 200


def test_local_mode_remains_usable_without_provisioning(tmp_path):
    with TestClient(create_app(Settings(tmp_path), start_worker=False)) as client:
        assert client.get("/api/session").json()["mode"] == "local"
        assert client.get("/api/collections").status_code == 200
        assert (
            client.post(
                "/api/team/users", json={"name": "Reviewer", "role": "reviewer"}
            ).status_code
            == 400
        )
