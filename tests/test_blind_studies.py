"""Studies freeze complete cohorts and reveal descriptive results only after review."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cvals.blind_studies import BlindStudies, StudyRequest
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore, Principal
from cvals.models import Collection, ExperimentCreate, System, content_hash
from cvals.review_queues import Judgment, QueueRequest, ReviewQueues, Rubric
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(tmp_path)
    identities = IdentityStore(settings)
    admin = identities.bootstrap("Study custodian")
    actor = admin["user"]["id"]
    reviewers = [
        identities.create_user(name, "reviewer", actor) for name in ("Reader one", "Reader two")
    ]
    principals = [Principal(user["id"], user["name"], "reviewer", "team") for user in reviewers]
    store = Store(settings)
    collection = Collection.model_validate(
        {
            "name": "Private source",
            "scenarios": [
                {
                    "name": "Invoice " + str(total),
                    "domain": "data",
                    "instruction": "Add the amounts.",
                    "inputs": {"amounts": [total - 1, 1]},
                    "checks": [
                        {"name": "Total", "kind": "equals", "path": "total", "expected": total}
                    ],
                    "reference_output": {"total": total},
                }
                for total in (5, 7)
            ],
        }
    )
    store.save_collection(collection)
    systems = [
        System(name=name, kind=kind, model="private-model-" + name)
        for name, kind in (("Complete fixture", "reference"), ("Empty fixture", "incomplete"))
    ]
    for system in systems:
        store.save_system(system)
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Private experiment",
            collection_id=collection.id,
            system_ids=[system.id for system in systems],
            repetitions=2,
        )
    )
    Engine(store, settings).run_experiment(experiment["id"])
    queues = ReviewQueues(settings)
    rubric = queues.create_rubric(
        Rubric(
            name="Output quality",
            version="1",
            instructions="Score the result against the public task.",
            dimensions=[
                {"id": "correct", "name": "Correct", "guidance": "One for a correct total."}
            ],
        ),
        actor,
    )
    studies = BlindStudies(settings)
    request = StudyRequest(
        name="Complete fixture vs Empty fixture",
        experiment_id=experiment["id"],
        rubric_id=rubric["id"],
        reviewer_ids=[reviewer["id"] for reviewer in reviewers],
        system_ids=[system.id for system in systems],
    )
    return SimpleNamespace(
        settings=settings,
        identities=identities,
        actor=actor,
        reviewers=reviewers,
        principals=principals,
        store=store,
        systems=systems,
        experiment=experiment,
        queues=queues,
        rubric=rubric,
        studies=studies,
        request=request,
    )


def complete(workspace, study):
    judgments = []
    for who in workspace.principals:
        for assignment in workspace.queues.assigned(who):
            if assignment.get("study_id") != study["id"]:
                continue
            item = workspace.queues.item(assignment["id"], who)
            expected = sum(item["task"]["inputs"]["amounts"])
            scores = {
                label: {"correct": float(response["output"].get("total") == expected)}
                for label, response in item["responses"].items()
            }
            preferred = max(scores, key=lambda label: scores[label]["correct"])
            judgments.append(
                workspace.queues.judge(
                    item["id"],
                    who,
                    Judgment(
                        scores=scores,
                        preference=preferred,
                        disposition="acceptable",
                        rationale="The preferred response gives the correct total.",
                    ),
                )
            )
    return judgments


def test_complete_cohort_reveals_correct_mapping_and_retains_evidence(workspace):
    w = workspace
    study = w.studies.create(w.request, w.actor)
    assert study["status"] == "collecting"
    assert (study["items"], study["assigned_reviews"], study["missing_reviews"]) == (4, 8, 8)
    assert study["retrospective"] is True
    assert study["confidential_execution"] is False
    assert "filesystem owner" in study["boundary"]
    assert w.studies.list() == [study]
    for value in (study, w.queues.queues(), w.queues.brief(study["queue_id"])):
        serialized = json.dumps(value)
        assert w.request.name not in serialized
        assert w.request.experiment_id not in serialized
        assert not any(system.id in serialized or system.name in serialized for system in w.systems)
    assert w.queues.brief(study["queue_id"])["study_id"] == study["id"]
    with pytest.raises(ValueError, match="sealed"):
        w.studies.report(study["id"])
    judgments = complete(w, study)
    before = w.studies.get(study["id"])
    assert before["completed_reviews"] == 8
    locked = w.studies.lock(study["id"], w.actor, before["evidence_hash"])
    assert locked["status"] == "locked"
    assert locked["evidence_hash"] != before["evidence_hash"]
    with pytest.raises(ValueError, match="sealed"):
        w.studies.report(study["id"])
    with pytest.raises(ValueError, match="sealed"):
        w.studies.require_revealed_experiment(w.experiment["id"])
    for who in w.principals:
        for assignment in w.queues.assigned(who):
            assert assignment["locked"] is True
            assert w.queues.item(assignment["id"], who)["locked"] is True
    revealed = w.studies.reveal(
        study["id"], w.actor, locked["evidence_hash"], "All reviews finalized."
    )
    assert revealed["status"] == "revealed"
    assert revealed["name"] == w.request.name
    report = w.studies.report(study["id"])
    assert report["review"]["preferences"] == {"Complete fixture": 8}
    assert set(report["review"]["judgment_ids"]) == {judgment["id"] for judgment in judgments}
    assert report["review"]["fixture_controls_present"] is True
    assert report["review"]["submission_replays_present"] is False
    assert report["protocol"]["rubric_hash"] == w.rubric["hash"]
    with w.store._connect() as db:
        snapshot, _ = w.store._read_experiment(db, w.experiment["id"])
    assert report["protocol"]["source_snapshot_hash"] == content_hash(snapshot)
    assert report["reveal"]["reveal_rationale"] == "All reviews finalized."
    w.studies.require_revealed_experiment(w.experiment["id"])


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewer_ids": ["only-one"]},
        {"reviewer_ids": ["duplicate", "duplicate"]},
        {"system_ids": ["only-one"]},
        {"system_ids": ["duplicate", "duplicate"]},
        {"system_ids": ["one", "two", "three"]},
        {"mode": "confidential"},
        {"mode": "tee"},
        {"blind": False},
        {"force": True},
        {"name": "   "},
    ],
)
def test_protocol_rejects_unsupported_or_ambiguous_blinding(workspace, changes):
    with pytest.raises(ValidationError):
        StudyRequest.model_validate({**workspace.request.model_dump(), **changes})


@pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
def test_reviewer_only_accounts_are_required(workspace, role):
    w = workspace
    user = w.identities.create_user("Privileged or summary reader", role, w.actor)
    request = StudyRequest.model_validate(
        {**w.request.model_dump(), "reviewer_ids": [user["id"], w.reviewers[0]["id"]]}
    )
    with pytest.raises(ValueError, match="reviewer-only"):
        w.studies.create(request, w.actor)
    assert w.studies.list() == []
    assert w.queues.queues() == []


def test_requires_team_identities(tmp_path):
    studies = BlindStudies(Settings(tmp_path))
    request = StudyRequest(
        name="Not yet configured",
        experiment_id="source",
        rubric_id="rubric",
        reviewer_ids=["one", "two"],
        system_ids=["a", "b"],
    )
    with pytest.raises(ValueError, match="Enable team identities"):
        studies.create(request, "local-console")


def test_partial_coverage_cannot_lock_or_reveal(workspace):
    w = workspace
    study = w.studies.create(w.request, w.actor)
    who = w.principals[0]
    item = w.queues.assigned(who)[0]
    w.queues.judge(
        item["id"],
        who,
        Judgment(
            scores={"A": {"correct": 0.5}, "B": {"correct": 0.5}},
            preference="tie",
            disposition="needs_discussion",
            rationale="More analysis needed.",
        ),
    )
    current = w.studies.get(study["id"])
    assert current["missing_reviews"] == 7
    with pytest.raises(ValueError, match="Every frozen assignment"):
        w.studies.lock(study["id"], w.actor, current["evidence_hash"])
    with pytest.raises(ValueError, match="Lock every required"):
        w.studies.reveal(study["id"], w.actor, current["evidence_hash"], "Premature.")
    with pytest.raises(ValueError, match="frozen"):
        w.queues.assign(study["queue_id"], [who.id], w.actor)
    assert w.studies.get(study["id"]) == current


def test_recorded_identified_exposure_is_rejected_even_after_unassignment(workspace):
    w = workspace
    outsider = w.identities.create_user("Outsider", "reviewer", w.actor)
    queue = w.queues.create(
        QueueRequest(
            name="Identified results",
            experiment_id=w.request.experiment_id,
            rubric_id=w.request.rubric_id,
            reviewer_ids=[w.reviewers[0]["id"]],
            system_ids=w.request.system_ids,
            blind=False,
        ),
        w.actor,
    )
    w.queues.assign(queue["id"], [outsider["id"]], w.actor)
    with pytest.raises(ValueError, match="identified queue"):
        w.studies.create(w.request, w.actor)
    assert w.studies.list() == []


def test_later_identified_queue_cannot_expose_study_reviewers(workspace):
    w = workspace
    study = w.studies.create(w.request, w.actor)
    with pytest.raises(ValueError, match="protects these reviewers"):
        w.queues.create(
            QueueRequest(
                name="Shortcut",
                experiment_id=w.request.experiment_id,
                rubric_id=w.request.rubric_id,
                reviewer_ids=w.request.reviewer_ids,
                system_ids=w.request.system_ids,
                blind=False,
            ),
            w.actor,
        )
    outsider = w.identities.create_user("Outsider", "reviewer", w.actor)
    external = w.queues.create(
        QueueRequest(
            name="Another queue",
            experiment_id=w.request.experiment_id,
            rubric_id=w.request.rubric_id,
            reviewer_ids=[outsider["id"]],
            system_ids=w.request.system_ids,
            blind=False,
        ),
        w.actor,
    )
    with pytest.raises(ValueError, match="protects these reviewers"):
        w.queues.assign(external["id"], [w.reviewers[0]["id"]], w.actor)
    assert w.studies.get(study["id"])["missing_reviews"] == 8


def test_local_custodian_recovery_still_requires_completed_reviews(workspace):
    w = workspace
    study = w.studies.create(w.request, w.actor)
    with pytest.raises(ValueError, match="Every frozen assignment"):
        w.studies.lock(study["id"], "local-console", study["evidence_hash"])
    complete(w, study)
    locked = w.studies.lock(
        study["id"], "local-console", w.studies.get(study["id"])["evidence_hash"]
    )
    w.studies.reveal(
        study["id"], "local-console", locked["evidence_hash"], "Local recovery after review."
    )
    assert w.studies.report(study["id"])["reveal"]["revealed_by"] == "local-console"


def test_missing_study_linkage_never_downgrades_to_ordinary_review(workspace):
    w = workspace
    with w.store._connect(write=True) as db:
        queue = w.queues._create(
            db,
            QueueRequest(
                name="Incomplete migration",
                experiment_id=w.request.experiment_id,
                rubric_id=w.request.rubric_id,
                reviewer_ids=w.request.reviewer_ids,
                system_ids=w.request.system_ids,
                blind=True,
            ),
            w.actor,
            study_id="missing-study",
        )
        item_id = db.execute(
            "SELECT id FROM review_queue_items WHERE queue_id=?", (queue["id"],)
        ).fetchone()[0]
    who = w.principals[0]
    for operation in (
        w.queues.queues,
        lambda: w.queues.brief(queue["id"]),
        lambda: w.queues.assigned(who),
        lambda: w.queues.item(item_id, who),
        lambda: w.queues.assign(queue["id"], [who.id], w.actor),
    ):
        with pytest.raises(ValueError, match="linkage is missing"):
            operation()


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_only_completed_experiments_are_eligible(workspace, status):
    w = workspace
    with w.store._connect(write=True) as db:
        db.execute("UPDATE experiments SET status=? WHERE id=?", (status, w.experiment["id"]))
    with pytest.raises(ValueError, match="completed source"):
        w.studies.create(w.request, w.actor)
    assert w.studies.list() == []
    assert w.queues.queues() == []
