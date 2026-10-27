"""Reference export preserves blinding, provenance, grouping and disagreements."""

import copy
import json

import pytest
from pydantic import ValidationError
from test_blind_studies import complete
from test_blind_studies import workspace as workspace

from cvals.judge_reference import (
    CollectionProvenance,
    export_study_references,
    verify_reference_bundle,
)
from cvals.models import content_hash
from cvals.review_queues import Judgment


def reveal(w):
    study = w.studies.create(w.request, w.actor)
    complete(w, study)
    current = w.studies.get(study["id"])
    locked = w.studies.lock(study["id"], w.actor, current["evidence_hash"])
    return w.studies.reveal(study["id"], w.actor, locked["evidence_hash"], "Reviews complete.")


def test_export_requires_reveal_and_custodian(workspace):
    w = workspace
    study = w.studies.create(w.request, w.actor)
    with pytest.raises(ValueError, match="sealed"):
        export_study_references(w.settings, study["id"])
    complete(w, study)
    current = w.studies.get(study["id"])
    locked = w.studies.lock(study["id"], w.actor, current["evidence_hash"])
    with pytest.raises(ValueError, match="sealed"):
        export_study_references(w.settings, study["id"])
    w.studies.reveal(study["id"], w.actor, locked["evidence_hash"], "Done.")
    with pytest.raises(ValueError, match="custodian"):
        export_study_references(w.settings, study["id"], actor=w.principals[0].id)


def test_export_preserves_repetition_groups_and_canonical_labels(workspace):
    w = workspace
    study = reveal(w)
    bundle = export_study_references(w.settings, study["id"])
    verify_reference_bundle(bundle)
    assert len(bundle["items"]) == 4
    assert len({item["task_id"] for item in bundle["items"]}) == 2
    assert all(item["reference"]["source"] == "unverified" for item in bundle["items"])
    assert all(item["partition"] == "dev" for item in bundle["items"])
    assert all(item["reference"]["label"] == "A" for item in bundle["items"])
    assert all(item["responses"]["A"].get("total") for item in bundle["items"])
    assert all(len(item["reference"]["reviewer_labels"]) == 2 for item in bundle["items"])
    serialized = json.dumps(bundle)
    assert all(who.name not in serialized and who.id not in serialized for who in w.principals)
    for item in bundle["items"]:
        assert set(item["task"]) == {"name", "instruction", "inputs"}
        assert "checks" not in item["task"] and "reference_output" not in item["task"]
        assert json.loads(item["reference"]["provenance"])["fixture_controls_present"] is True


def test_disagreement_is_not_silently_adjudicated(workspace):
    w = workspace
    study = w.studies.create(w.request, w.actor)
    complete(w, study)
    who = w.principals[0]
    assignment = w.queues.assigned(who)[0]
    w.queues.judge(
        assignment["id"],
        who,
        Judgment(
            scores={"A": {"correct": 0.5}, "B": {"correct": 0.5}},
            preference="tie",
            disposition="needs_discussion",
            rationale="Disagree.",
        ),
    )
    current = w.studies.get(study["id"])
    locked = w.studies.lock(study["id"], w.actor, current["evidence_hash"])
    w.studies.reveal(study["id"], w.actor, locked["evidence_hash"], "Retain disagreement.")
    bundle = export_study_references(w.settings, study["id"])
    disputed = next(item for item in bundle["items"] if item["item_id"] == assignment["id"])
    assert disputed["reference"]["label"] is None
    assert {value["label"] for value in disputed["reference"]["reviewer_labels"]} == {"A", "tie"}


def test_human_collection_needs_explicit_provenance(workspace):
    with pytest.raises(ValidationError, match="protocol"):
        CollectionProvenance(source="human_collected")
    w = workspace
    study = reveal(w)
    provenance = CollectionProvenance(
        source="scripted", description="Scripted fixture reviewers; no human participants."
    )
    result = export_study_references(w.settings, study["id"], provenance=provenance)
    assert all(item["reference"]["source"] == "scripted" for item in result["items"])


def test_export_rejects_corrupted_locked_item_even_if_database_triggers_bypassed(workspace):
    w = workspace
    study = reveal(w)
    with w.store._connect(write=True) as db:
        db.execute("DROP TRIGGER review_queue_items_no_update")
        row = db.execute("SELECT id,data FROM review_queue_items LIMIT 1").fetchone()
        changed = json.loads(row["data"])
        changed["task"]["instruction"] = "Changed task"
        # A rewritten item digest still conflicts with the frozen source task.
        changed["evidence_hash"] = content_hash(
            {key: value for key, value in changed.items() if key != "evidence_hash"}
        )
        db.execute(
            "UPDATE review_queue_items SET data=? WHERE id=?", (json.dumps(changed), row["id"])
        )
    with pytest.raises(ValueError, match="Public task evidence"):
        export_study_references(w.settings, study["id"])


def test_bundle_hash_detects_label_changes(workspace):
    w = workspace
    bundle = export_study_references(w.settings, reveal(w)["id"])
    changed = copy.deepcopy(bundle)
    changed["items"][0]["reference"]["label"] = "B"
    with pytest.raises(ValueError, match="integrity"):
        verify_reference_bundle(changed)
