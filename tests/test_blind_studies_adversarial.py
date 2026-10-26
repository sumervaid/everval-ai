"""Adversarial checks for study sealing, identity boundaries, and atomic transitions."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.blind_studies import BlindStudies, StudyRequest
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Collection, ExperimentCreate, System, canonical_json
from cvals.review_queues import Judgment, QueueRequest, ReviewQueues, Rubric
from cvals.store import Store


@pytest.fixture
def blind_workspace(tmp_path):
    settings = Settings(tmp_path)
    identities = IdentityStore(settings)
    admin = identities.bootstrap("Custodian")
    actor = admin["user"]["id"]
    reviewers = [
        identities.create_user(name, "reviewer", actor)
        for name in ("First reviewer", "Second reviewer")
    ]
    tokens = [identities.issue_token(user["id"], "Study", actor=actor) for user in reviewers]
    viewer = identities.create_user("Result reader", "viewer", actor)
    viewer_token = identities.issue_token(viewer["id"], "Results", actor=actor)
    store = Store(settings)
    collection = Collection.model_validate(
        {
            "name": "PRIVATE-COLLECTION",
            "scenarios": [
                {
                    "name": "Public invoice task",
                    "domain": "data",
                    "instruction": "Add the invoice amounts.",
                    "inputs": {"amounts": [2, 3]},
                    "expected": {"hidden": "PRIVATE-GRADING-ANSWER"},
                    "checks": [
                        {"name": "PRIVATE-CHECK", "kind": "equals", "path": "total", "expected": 5}
                    ],
                    "reference_output": {"total": 5},
                }
            ],
        }
    )
    store.save_collection(collection)
    systems = [
        System(name=name, kind=kind, model="PRIVATE-MODEL-" + name)
        for name, kind in (("PRIVATE-ALPHA", "reference"), ("PRIVATE-BETA", "incomplete"))
    ]
    for system in systems:
        store.save_system(system)
    experiment = store.create_experiment(
        ExperimentCreate(
            name="PRIVATE-RUN",
            collection_id=collection.id,
            system_ids=[system.id for system in systems],
        )
    )
    Engine(store, settings).run_experiment(experiment["id"])
    queues = ReviewQueues(settings)
    rubric = queues.create_rubric(
        Rubric(
            name="PRIVATE-ALPHA quality rubric",
            version="1",
            instructions="Assess only whether the response satisfies the task.",
            dimensions=[{"id": "quality", "name": "Quality", "guidance": "Zero to one."}],
        ),
        actor,
    )
    studies = BlindStudies(settings)
    request = StudyRequest(
        name="PRIVATE-ALPHA against PRIVATE-BETA",
        experiment_id=experiment["id"],
        rubric_id=rubric["id"],
        reviewer_ids=[user["id"] for user in reviewers],
        system_ids=[system.id for system in systems],
    )
    with TestClient(create_app(settings, start_worker=False)) as client:
        yield SimpleNamespace(
            settings=settings,
            identities=identities,
            admin=admin,
            actor=actor,
            reviewers=reviewers,
            tokens=tokens,
            viewer_token=viewer_token,
            store=store,
            collection=collection,
            systems=systems,
            experiment=experiment,
            queues=queues,
            rubric=rubric,
            studies=studies,
            request=request,
            client=client,
        )


def _headers(token):
    return {"Authorization": "Bearer " + token["token"]}


def _judgment():
    return Judgment(
        scores={"A": {"quality": 1.0}, "B": {"quality": 0.0}},
        preference="A",
        disposition="acceptable",
        rationale="The response completes the task.",
    )


def _complete(workspace):
    for token in workspace.tokens:
        who = workspace.identities.authenticate(token["token"])
        assert who is not None
        for item in workspace.queues.assigned(who):
            workspace.queues.judge(item["id"], who, _judgment())


def _lock(workspace, study):
    _complete(workspace)
    current = workspace.studies.get(study["id"])
    return workspace.studies.lock(study["id"], workspace.actor, current["evidence_hash"])


def _assert_sealed(text, workspace):
    for private in (
        "PRIVATE-ALPHA",
        "PRIVATE-BETA",
        "PRIVATE-MODEL",
        "PRIVATE-GRADING-ANSWER",
        "PRIVATE-CHECK",
        "PRIVATE-COLLECTION",
        "PRIVATE-RUN",
        workspace.experiment["id"],
        *(system.id for system in workspace.systems),
    ):
        assert private not in text


def test_viewer_routes_do_not_bypass_seal(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    auth = _headers(w.viewer_token)
    for url in (
        "/api/blind-studies",
        f"/api/blind-studies/{study['id']}",
        "/api/team/summary",
        "/api/review-queues",
        f"/api/review-queues/{study['queue_id']}/brief",
    ):
        response = w.client.get(url, headers=auth)
        assert response.status_code == 200, (url, response.text)
        _assert_sealed(response.text, w)
    for suffix in ("", "?format=html"):
        response = w.client.get(f"/api/blind-studies/{study['id']}/report{suffix}", headers=auth)
        assert response.status_code == 400
        _assert_sealed(response.text, w)
    response = w.client.get(f"/api/experiments/{w.experiment['id']}/brief", headers=auth)
    assert response.status_code == 400
    _assert_sealed(response.text, w)
    assert w.client.get("/api/blind-studies/catalog", headers=auth).status_code == 403


def test_sibling_queue_cannot_publish_an_unrevealed_source(blind_workspace):
    w = blind_workspace
    outsider = w.identities.create_user("Other reviewer", "reviewer", w.actor)
    sibling = w.queues.create(
        QueueRequest(
            name="PRIVATE-ALPHA identified queue",
            experiment_id=w.experiment["id"],
            rubric_id=w.rubric["id"],
            reviewer_ids=[outsider["id"]],
            system_ids=w.request.system_ids,
            blind=False,
        ),
        w.actor,
    )
    w.studies.create(w.request, w.actor)
    for url in (
        "/api/review-queues",
        "/api/team/summary",
        f"/api/review-queues/{sibling['id']}/brief",
    ):
        response = w.client.get(url, headers=_headers(w.viewer_token))
        assert response.status_code in {200, 400}
        _assert_sealed(response.text, w)


@pytest.mark.parametrize("credential", ["reviewer", "viewer"])
def test_readers_cannot_control_study_or_fetch_raw_evidence(blind_workspace, credential):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    token = w.tokens[0] if credential == "reviewer" else w.viewer_token
    auth = _headers(token)
    for action, body in (
        ("lock", {"expected_hash": study["evidence_hash"]}),
        ("reveal", {"expected_hash": study["evidence_hash"], "rationale": "Release."}),
    ):
        assert (
            w.client.post(
                f"/api/blind-studies/{study['id']}/{action}", json=body, headers=auth
            ).status_code
            == 403
        )
    for path in (
        f"/api/experiments/{w.experiment['id']}",
        f"/api/experiments/{w.experiment['id']}/export",
        "/api/blind-studies/catalog",
    ):
        assert w.client.get(path, headers=auth).status_code == 403
    assert w.studies.get(study["id"])["status"] == "collecting"


def test_old_assignment_and_decision_endpoints_cannot_change_protocol(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    auth = _headers(w.admin)
    assignment = w.client.post(
        f"/api/review-queues/{study['queue_id']}/assignments",
        json={"reviewer_ids": [w.reviewers[0]["id"]]},
        headers=auth,
    )
    assert assignment.status_code == 400
    decision = w.client.post(
        f"/api/review-queues/{study['queue_id']}/decisions",
        json={"outcome": "adopt", "rationale": "Bypass", "evidence_hash": study["evidence_hash"]},
        headers=auth,
    )
    assert decision.status_code == 400
    summary = w.studies.get(study["id"])
    assert summary["assigned_reviews"] == 2
    assert summary["evidence_hash"] == study["evidence_hash"]


def test_revoked_tokens_and_disabled_reviewers_do_not_shrink_required_coverage(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    first = w.identities.authenticate(w.tokens[0]["token"])
    item = w.queues.assigned(first)[0]
    w.queues.judge(item["id"], first, _judgment())
    w.identities.revoke(w.tokens[1]["id"], w.actor)
    assert w.client.get("/api/review-inbox", headers=_headers(w.tokens[1])).status_code == 401
    w.identities.set_active(w.reviewers[1]["id"], False, w.actor)
    summary = w.studies.get(study["id"])
    assert summary["assigned_reviews"] == 2
    assert summary["completed_reviews"] == 1
    assert summary["missing_reviews"] == 1
    with pytest.raises(ValueError):
        w.studies.lock(study["id"], w.actor, summary["evidence_hash"])
    with pytest.raises(ValueError):
        w.studies.reveal(study["id"], w.actor, summary["evidence_hash"], "Premature.")
    assert w.studies.get(study["id"])["status"] == "collecting"


def test_changed_reviewer_role_cannot_submit_or_close(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    _complete(w)
    reviewer = w.identities.authenticate(w.tokens[0]["token"])
    item = w.queues.assigned(reviewer)[0]
    # Simulate a future provisioning/role-management integration changing the role.
    with w.store._connect(write=True) as db:
        db.execute("UPDATE team_users SET role='operator' WHERE id=?", (reviewer.id,))
    auth = _headers(w.tokens[0])
    assert w.client.get(f"/api/review-items/{item['id']}", headers=auth).status_code == 404
    assert (
        w.client.post(
            f"/api/review-items/{item['id']}/judgments",
            json=_judgment().model_dump(mode="json"),
            headers=auth,
        ).status_code
        == 404
    )
    summary = w.studies.get(study["id"])
    with pytest.raises(ValueError):
        w.studies.lock(study["id"], w.actor, summary["evidence_hash"])


def test_stale_review_snapshot_and_post_lock_amendments_are_rejected(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    _complete(w)
    old = w.studies.get(study["id"])
    who = w.identities.authenticate(w.tokens[0]["token"])
    item = w.queues.assigned(who)[0]
    changed = _judgment().model_copy(
        update={"preference": "tie", "rationale": "Both are adequate."}
    )
    w.queues.judge(item["id"], who, changed)
    with pytest.raises(ValueError, match="changed"):
        w.studies.lock(study["id"], w.actor, old["evidence_hash"])
    current = w.studies.get(study["id"])
    locked = w.studies.lock(study["id"], w.actor, current["evidence_hash"])
    with pytest.raises(ValueError):
        w.queues.judge(item["id"], who, _judgment())
    with pytest.raises(ValueError):
        w.queues.assign(study["queue_id"], w.request.reviewer_ids, w.actor)
    with pytest.raises(ValueError):
        w.studies.reveal(study["id"], w.actor, old["evidence_hash"], "Stale release.")
    assert w.studies.get(study["id"])["evidence_hash"] == locked["evidence_hash"]


def test_failed_create_cannot_leave_an_unprotected_queue(blind_workspace, monkeypatch):
    w = blind_workspace
    with w.store._connect() as db:
        before = db.execute("SELECT COUNT(*) FROM workspace_audit").fetchone()[0]

    def unavailable_audit(*args, **kwargs):
        raise RuntimeError("Audit sink unavailable")

    monkeypatch.setattr("cvals.blind_studies.append_audit", unavailable_audit)
    with pytest.raises(RuntimeError, match="Audit sink"):
        w.studies.create(w.request, w.actor)
    with w.store._connect() as db:
        for table in ("blind_studies", "review_queues", "review_queue_items", "review_assignments"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workspace_audit").fetchone()[0] == before


@pytest.mark.parametrize("transition", ["lock", "reveal"])
def test_transition_rolls_back_if_audit_append_fails(blind_workspace, monkeypatch, transition):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    _complete(w)
    if transition == "reveal":
        current = w.studies.get(study["id"])
        w.studies.lock(study["id"], w.actor, current["evidence_hash"])
    before = w.studies.get(study["id"])

    def unavailable_audit(*args, **kwargs):
        raise RuntimeError("Audit sink unavailable")

    monkeypatch.setattr("cvals.blind_studies.append_audit", unavailable_audit)
    with pytest.raises(RuntimeError, match="Audit sink"):
        if transition == "lock":
            w.studies.lock(study["id"], w.actor, before["evidence_hash"])
        else:
            w.studies.reveal(study["id"], w.actor, before["evidence_hash"], "Approved release.")
    after = w.studies.get(study["id"])
    assert after == before
    with pytest.raises(ValueError):
        w.studies.report(study["id"])


def test_concurrent_transition_retries_have_one_audit_event(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    _complete(w)
    current = w.studies.get(study["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        locked = list(
            pool.map(
                lambda _: w.studies.lock(study["id"], w.actor, current["evidence_hash"]), range(2)
            )
        )
    assert locked[0] == locked[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        revealed = list(
            pool.map(
                lambda _: w.studies.reveal(
                    study["id"], w.actor, locked[0]["evidence_hash"], "Approved release."
                ),
                range(2),
            )
        )
    assert revealed[0] == revealed[1]
    with w.store._connect() as db:
        for action in ("blind_study.locked", "blind_study.revealed"):
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM workspace_audit WHERE action=? AND resource=?",
                    (action, study["id"]),
                ).fetchone()[0]
                == 1
            )


def test_corrupted_state_fails_closed_on_summary_and_report(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    _lock(w, study)
    with w.store._connect(write=True) as db:
        state = json.loads(
            db.execute("SELECT state FROM blind_studies WHERE id=?", (study["id"],)).fetchone()[0]
        )
        state["status"] = "revealed"
        # Corruption simulation: no legitimate transition updates the retained hash.
        db.execute(
            "UPDATE blind_studies SET state=? WHERE id=?", (canonical_json(state), study["id"])
        )
    for operation in (w.studies.get, w.studies.report):
        with pytest.raises(ValueError, match="integrity"):
            operation(study["id"])
    response = w.client.get(
        f"/api/review-queues/{study['queue_id']}/brief", headers=_headers(w.viewer_token)
    )
    assert response.status_code == 400
    _assert_sealed(response.text, w)


def test_frozen_protocol_cannot_be_repointed(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        w.store._connect(write=True) as db,
    ):
        db.execute("UPDATE blind_studies SET queue_id='replacement' WHERE id=?", (study["id"],))
    assert w.studies.get(study["id"])["queue_id"] == study["queue_id"]


def test_only_original_custodian_can_transition(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    _complete(w)
    other = w.identities.create_user("Other operator", "operator", w.actor)
    summary = w.studies.get(study["id"])
    with pytest.raises(ValueError, match="custodian"):
        w.studies.lock(study["id"], other["id"], summary["evidence_hash"])
    locked = w.studies.lock(study["id"], w.actor, summary["evidence_hash"])
    with pytest.raises(ValueError, match="custodian"):
        w.studies.reveal(study["id"], other["id"], locked["evidence_hash"], "Release.")


def test_http_lifecycle_and_exports_preserve_the_blinding_boundary(blind_workspace):
    w = blind_workspace
    admin_auth = _headers(w.admin)
    catalog = w.client.get("/api/blind-studies/catalog", headers=admin_auth)
    assert catalog.status_code == 200
    assert catalog.json()["team_enabled"] is True
    assert {user["id"] for user in catalog.json()["reviewers"]} == set(w.request.reviewer_ids)
    assert all(user["role"] == "reviewer" for user in catalog.json()["reviewers"])
    assert w.experiment["id"] in {run["id"] for run in catalog.json()["experiments"]}
    assert any(
        mode["id"] == "confidential_execution" and mode["available"] is False
        for mode in catalog.json()["modes"]
    )
    unavailable = {**w.request.model_dump(mode="json"), "mode": "confidential_execution"}
    assert (
        w.client.post("/api/blind-studies", json=unavailable, headers=admin_auth).status_code == 422
    )
    assert w.studies.list() == []
    created = w.client.post(
        "/api/blind-studies", json=w.request.model_dump(mode="json"), headers=admin_auth
    )
    assert created.status_code == 201, created.text
    study = created.json()
    assert study["status"] == "collecting"
    _assert_sealed(created.text, w)
    assert (
        w.client.post(
            f"/api/blind-studies/{study['id']}/lock",
            json={"expected_hash": study["evidence_hash"]},
            headers=admin_auth,
        ).status_code
        == 400
    )
    item_id = None
    for token in w.tokens:
        auth = _headers(token)
        inbox = w.client.get("/api/review-inbox", headers=auth)
        assert inbox.status_code == 200
        assert len(inbox.json()) == 1
        item_id = inbox.json()[0]["id"]
        item = w.client.get(f"/api/review-items/{item_id}", headers=auth)
        assert item.status_code == 200
        assert set(item.json()["responses"]) == {"A", "B"}
        assert all(set(value) == {"output"} for value in item.json()["responses"].values())
        assert item.json()["previous"] is None
        submitted = w.client.post(
            f"/api/review-items/{item_id}/judgments",
            json=_judgment().model_dump(mode="json"),
            headers=auth,
        )
        assert submitted.status_code == 201, submitted.text
    current = w.client.get(f"/api/blind-studies/{study['id']}", headers=admin_auth).json()
    assert current["missing_reviews"] == 0
    locked = w.client.post(
        f"/api/blind-studies/{study['id']}/lock",
        json={"expected_hash": current["evidence_hash"]},
        headers=admin_auth,
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["status"] == "locked"
    assert (
        w.client.post(
            f"/api/review-items/{item_id}/judgments",
            json=_judgment().model_dump(mode="json"),
            headers=_headers(w.tokens[-1]),
        ).status_code
        == 400
    )
    viewer_auth = _headers(w.viewer_token)
    assert (
        w.client.get(f"/api/blind-studies/{study['id']}/report", headers=viewer_auth).status_code
        == 400
    )
    rationale = "<script>alert('release')</script>"
    revealed = w.client.post(
        f"/api/blind-studies/{study['id']}/reveal",
        json={"expected_hash": locked.json()["evidence_hash"], "rationale": rationale},
        headers=admin_auth,
    )
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["status"] == "revealed"
    response = w.client.get(f"/api/blind-studies/{study['id']}/report", headers=viewer_auth)
    assert response.status_code == 200
    report = response.json()
    assert report["study"]["status"] == "revealed"
    assert report["study"]["confidential_execution"] is False
    assert report["study"]["retrospective"] is True
    assert report["review"]["completed_reviews"] == 2
    assert {system["name"] for system in report["review"]["systems"]} == {
        "PRIVATE-ALPHA",
        "PRIVATE-BETA",
    }
    for private in ("PRIVATE-GRADING-ANSWER", "PRIVATE-CHECK", "PRIVATE-MODEL"):
        assert private not in response.text
    html = w.client.get(f"/api/blind-studies/{study['id']}/report?format=html", headers=viewer_auth)
    assert html.status_code == 200
    assert "<script>alert" not in html.text
    assert "&lt;script&gt;" in html.text
    assert "default-src" in html.text
    assert (
        w.client.get(
            f"/api/experiments/{w.experiment['id']}/brief", headers=viewer_auth
        ).status_code
        == 200
    )
    assert (
        w.client.get(
            f"/api/blind-studies/{study['id']}/report?format=exe", headers=viewer_auth
        ).status_code
        == 422
    )
    assert w.client.get("/api/blind-studies/missing/report", headers=viewer_auth).status_code == 404


def test_related_studies_must_all_lock_before_first_reveal(blind_workspace):
    w = blind_workspace
    first = w.studies.create(w.request, w.actor)
    second = w.studies.create(w.request.model_copy(update={"name": "Second study"}), w.actor)
    _complete(w)
    current = w.studies.get(first["id"])
    first_locked = w.studies.lock(first["id"], w.actor, current["evidence_hash"])
    with pytest.raises(ValueError, match="all studies"):
        w.studies.reveal(first["id"], w.actor, first_locked["evidence_hash"], "Too early.")
    current = w.studies.get(second["id"])
    w.studies.lock(second["id"], w.actor, current["evidence_hash"])
    w.studies.reveal(first["id"], w.actor, first_locked["evidence_hash"], "All judgments locked.")
    assert w.studies.get(first["id"])["status"] == "revealed"
    assert w.studies.get(second["id"])["status"] == "locked"
    # Identified source summaries remain closed while another study awaits its reveal.
    assert (
        w.client.get(
            f"/api/experiments/{w.experiment['id']}/brief", headers=_headers(w.viewer_token)
        ).status_code
        == 400
    )


def test_concurrent_create_and_reveal_cannot_admit_an_exposed_source(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    locked = _lock(w, study)

    def create():
        try:
            return w.studies.create(w.request.model_copy(update={"name": "Racing study"}), w.actor)
        except ValueError as error:
            return error

    def reveal():
        try:
            return w.studies.reveal(study["id"], w.actor, locked["evidence_hash"], "Release.")
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        created_future = pool.submit(create)
        revealed_future = pool.submit(reveal)
        created, revealed = created_future.result(), revealed_future.result()
    assert sum(isinstance(result, ValueError) for result in (created, revealed)) == 1
    studies = w.studies.list()
    if isinstance(created, ValueError):
        assert len(studies) == 1
        assert studies[0]["status"] == "revealed"
    else:
        assert created["status"] == "collecting"
        assert w.studies.get(study["id"])["status"] == "locked"


def test_live_catalog_edits_do_not_change_frozen_tasks_or_model_mapping(blind_workspace):
    w = blind_workspace
    study = w.studies.create(w.request, w.actor)
    who = w.identities.authenticate(w.tokens[0]["token"])
    item_id = w.queues.assigned(who)[0]["id"]
    original = w.queues.item(item_id, who)
    source = w.collection.model_copy(deep=True, update={"version": "2.0.0"})
    source.scenarios[0].inputs = {"amounts": [900, 100]}
    source.scenarios[0].instruction = "A replacement task must not enter the frozen study."
    w.store.save_collection(source)
    for system in w.systems:
        w.store.save_system(system.model_copy(update={"name": "Replacement vendor"}))
    assert w.queues.item(item_id, who) == original
    locked = _lock(w, study)
    w.studies.reveal(study["id"], w.actor, locked["evidence_hash"], "Frozen evidence verified.")
    report = w.studies.report(study["id"])
    assert {system["name"] for system in report["review"]["systems"]} == {
        "PRIVATE-ALPHA",
        "PRIVATE-BETA",
    }
    assert "Replacement vendor" not in json.dumps(report)


def test_catalog_excludes_unfinished_sources(blind_workspace):
    w = blind_workspace
    queued = w.store.create_experiment(
        ExperimentCreate(
            name="Not ready for review",
            collection_id=w.collection.id,
            system_ids=w.request.system_ids,
        )
    )
    catalog = w.client.get("/api/blind-studies/catalog", headers=_headers(w.admin))
    assert catalog.status_code == 200
    identifiers = {item["id"] for item in catalog.json()["experiments"]}
    assert w.experiment["id"] in identifiers
    assert queued["id"] not in identifiers
