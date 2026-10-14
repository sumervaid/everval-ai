"""Review assignments and server-side identity suppression preserve reviewer boundaries."""

import json

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Collection, ExperimentCreate, System
from cvals.review_queues import QueueRequest, ReviewQueues, Rubric
from cvals.store import Store


@pytest.fixture
def review_workspace(tmp_path):
    settings = Settings(tmp_path)
    identities = IdentityStore(settings)
    admin = identities.bootstrap("Admin")
    reviewers = [
        identities.create_user(name, "reviewer", admin["user"]["id"])
        for name in ("First", "Second")
    ]
    tokens = [
        identities.issue_token(user["id"], "Review", actor=admin["user"]["id"])
        for user in reviewers
    ]
    store = Store(settings)
    collection = Collection.model_validate(
        {
            "name": "Private collection",
            "scenarios": [
                {
                    "name": "Invoice",
                    "domain": "data",
                    "instruction": "Add amounts.",
                    "inputs": {"amounts": [2, 3]},
                    "expected": {"private-marker": "HIDDEN-GRADER-SECRET"},
                    "checks": [
                        {
                            "name": "Private grading rule",
                            "kind": "equals",
                            "path": "total",
                            "expected": 5,
                        }
                    ],
                    "reference_output": {"total": 5},
                }
            ],
        }
    )
    store.save_collection(collection)
    systems = [
        System(name=name, kind=kind, model="PRIVATE-MODEL-" + name)
        for name, kind in (("SecretAlpha", "reference"), ("SecretBeta", "incomplete"))
    ]
    for system in systems:
        store.save_system(system)
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Private run title",
            collection_id=collection.id,
            system_ids=[system.id for system in systems],
        )
    )
    Engine(store, settings).run_experiment(experiment["id"])
    queues = ReviewQueues(settings)
    rubric = queues.create_rubric(
        Rubric(
            name="Task quality",
            version="1",
            instructions="Assess the public task only.",
            dimensions=[
                {
                    "id": "correctness",
                    "name": "Correctness",
                    "guidance": "Zero is incorrect; one is fully correct.",
                }
            ],
        ),
        admin["user"]["id"],
    )
    queue = queues.create(
        QueueRequest(
            name="Reviewed task set",
            experiment_id=experiment["id"],
            rubric_id=rubric["id"],
            reviewer_ids=[reviewers[0]["id"]],
            system_ids=[system.id for system in systems],
        ),
        admin["user"]["id"],
    )
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        yield (
            settings,
            identities,
            queues,
            client,
            admin,
            reviewers,
            tokens,
            experiment,
            systems,
            queue,
            rubric,
        )


def headers(token):
    return {"Authorization": "Bearer " + token["token"]}


def judgment():
    return {
        "scores": {"A": {"correctness": 1.0}, "B": {"correctness": 0.0}},
        "preference": "A",
        "disposition": "acceptable",
        "rationale": "The result satisfies the task.",
    }


def test_blind_item_is_whitelisted_and_generic_endpoints_cannot_bypass(review_workspace):
    _, _, _, client, _, _, tokens, experiment, systems, _, _ = review_workspace
    auth = headers(tokens[0])
    inbox = client.get("/api/review-inbox", headers=auth).json()
    assert len(inbox) == 1
    response = client.get("/api/review-items/" + inbox[0]["id"], headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert set(body["responses"]) == {"A", "B"}
    assert all(set(item) == {"output"} for item in body["responses"].values())
    serialized = response.text
    for private in [
        "HIDDEN-GRADER-SECRET",
        "Private grading rule",
        "SecretAlpha",
        "SecretBeta",
        "PRIVATE-MODEL",
        experiment["id"],
        *[system.id for system in systems],
    ]:
        assert private not in serialized
    assert (
        client.get(f"/api/experiments/{experiment['id']}/export", headers=auth).status_code == 403
    )
    assert client.get("/api/review-queues", headers=auth).status_code == 403


def test_unassigned_and_removed_assignments_cannot_read_or_write(review_workspace):
    _, _, queues, client, admin, reviewers, tokens, _, _, queue, _ = review_workspace
    item = client.get("/api/review-inbox", headers=headers(tokens[0])).json()[0]["id"]
    assert client.get("/api/review-inbox", headers=headers(tokens[1])).json() == []
    assert client.get("/api/review-items/" + item, headers=headers(tokens[1])).status_code == 404
    assert (
        client.post(
            f"/api/review-items/{item}/judgments", json=judgment(), headers=headers(tokens[1])
        ).status_code
        == 404
    )
    queues.assign(queue["id"], [reviewers[1]["id"]], admin["user"]["id"])
    assert client.get("/api/review-items/" + item, headers=headers(tokens[0])).status_code == 404
    assert (
        client.post(
            f"/api/review-items/{item}/judgments", json=judgment(), headers=headers(tokens[0])
        ).status_code
        == 404
    )


def test_authenticated_judgments_frozen_rubric_and_decision_evidence(review_workspace):
    _, _, queues, client, admin, reviewers, tokens, _, _, queue, rubric = review_workspace
    auth = headers(tokens[0])
    item = client.get("/api/review-inbox", headers=auth).json()[0]["id"]
    forged = {**judgment(), "reviewer_id": reviewers[1]["id"]}
    assert (
        client.post(f"/api/review-items/{item}/judgments", json=forged, headers=auth).status_code
        == 422
    )
    first = client.post(f"/api/review-items/{item}/judgments", json=judgment(), headers=auth).json()
    assert first["reviewer_id"] == reviewers[0]["id"]
    assert first["identity_assurance"] == "authenticated bearer token"
    second = client.post(
        f"/api/review-items/{item}/judgments",
        json={**judgment(), "rationale": "Clarified judgment."},
        headers=auth,
    ).json()
    assert second["supersedes"] == first["id"]
    changed = {key: rubric[key] for key in ("name", "version", "instructions", "dimensions")}
    changed["instructions"] = "Different criteria"
    with pytest.raises(ValueError, match="immutable"):
        queues.create_rubric(Rubric.model_validate(changed), admin["user"]["id"])
    brief = client.get(f"/api/review-queues/{queue['id']}/brief", headers=headers(admin)).json()
    assert brief["completed_reviews"] == 1
    assert brief["fixture_controls_present"] is True
    assert brief["submission_replays_present"] is False
    decision = client.post(
        f"/api/review-queues/{queue['id']}/decisions",
        json={
            "outcome": "more_evidence",
            "rationale": "One task is too narrow.",
            "evidence_hash": brief["evidence_hash"],
        },
        headers=headers(admin),
    )
    assert decision.status_code == 201
    assert decision.json()["evidence"]["judgment_ids"] == [second["id"]]
    assert first["id"] not in json.dumps(decision.json()["evidence"]["judgment_ids"])


def test_decision_rejects_a_stale_displayed_brief(review_workspace):
    _, _, _, client, admin, _, tokens, _, _, queue, _ = review_workspace
    admin_auth = headers(admin)
    reviewer_auth = headers(tokens[0])
    item = client.get("/api/review-inbox", headers=reviewer_auth).json()[0]["id"]
    first_brief = client.get(f"/api/review-queues/{queue['id']}/brief", headers=admin_auth).json()
    recorded = client.post(
        f"/api/review-items/{item}/judgments", json=judgment(), headers=reviewer_auth
    )
    assert recorded.status_code == 201
    stale = client.post(
        f"/api/review-queues/{queue['id']}/decisions",
        json={
            "outcome": "more_evidence",
            "rationale": "A decision must use the displayed evidence.",
            "evidence_hash": first_brief["evidence_hash"],
        },
        headers=admin_auth,
    )
    assert stale.status_code == 400
    assert "evidence changed" in stale.text
    current = client.get(f"/api/review-queues/{queue['id']}/brief", headers=admin_auth).json()
    assert current["decisions"] == []
    assert current["evidence_hash"] != first_brief["evidence_hash"]
