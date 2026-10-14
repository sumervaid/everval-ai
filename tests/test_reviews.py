from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.reviews import ReviewStore
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    scenario = Scenario(
        id="sum",
        name="Sum",
        domain="data",
        instruction="Return the sum",
        checks=[Check(name="Sum", kind="numeric", path="value", expected=5)],
        reference_output={"value": 5},
    )
    store.save_collection(Collection(id="tasks", name="Tasks", scenarios=[scenario]))
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Review target", collection_id="tasks", system_ids=["fixture-reference"]
        )
    )
    result = Engine(store, settings).run_experiment(experiment["id"])
    return settings, store, result


def test_reviews_are_separate_append_only_history(workspace):
    settings, store, experiment = workspace
    reviews = ReviewStore(settings)
    trial = experiment["trials"][0]
    first = reviews.add(
        experiment["id"], trial["id"], "Morgan", "clarity", 0.5, "The result needs an explanation."
    )
    second = reviews.add(
        experiment["id"],
        trial["id"],
        "Morgan",
        "clarity",
        0.8,
        "Revised judgment after examining the inputs.",
    )
    history = reviews.list(experiment["id"])
    assert len(history) == 2
    assert not history[0]["latest"] and history[1]["latest"]
    assert second["supersedes"] == first["id"]
    assert second["identity_assurance"] == "self-reported label"
    assert history[0]["evidence_hash"] == history[1]["evidence_hash"]
    unchanged = store.get_experiment(experiment["id"])
    assert unchanged["trials"][0]["score"] == 1
    assert unchanged["report"] == experiment["report"]


def test_distinct_reviewers_and_dimensions_keep_independent_latest(workspace):
    settings, _, experiment = workspace
    reviews = ReviewStore(settings)
    trial = experiment["trials"][0]["id"]
    reviews.add(experiment["id"], trial, "Morgan", "clarity", 0.5, "Partial")
    reviews.add(experiment["id"], trial, "Taylor", "clarity", 0.9, "Clear")
    reviews.add(experiment["id"], trial, "Morgan", "completeness", 1, "Complete")
    assert all(review["latest"] for review in reviews.list(experiment["id"]))


def test_reviews_cannot_target_another_experiment(workspace):
    settings, store, experiment = workspace
    other = store.rerun_experiment(experiment["id"])
    with pytest.raises(ValueError, match="belong"):
        ReviewStore(settings).add(
            other["id"], experiment["trials"][0]["id"], "Morgan", "clarity", 0.5, "Reason"
        )


def test_reviews_require_finalized_trial(workspace):
    settings, store, experiment = workspace
    queued = store.rerun_experiment(experiment["id"])
    with pytest.raises(ValueError, match="finalized"):
        ReviewStore(settings).add(
            queued["id"], queued["trials"][0]["id"], "Morgan", "clarity", 0.5, "Reason"
        )


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), True])
def test_review_score_bounds(workspace, score):
    settings, _, experiment = workspace
    with pytest.raises(ValidationError):
        ReviewStore(settings).add(
            experiment["id"], experiment["trials"][0]["id"], "Morgan", "clarity", score, "Reason"
        )


def test_review_database_rejects_updates_and_deletions(workspace):
    settings, _, experiment = workspace
    reviews = ReviewStore(settings)
    review = reviews.add(
        experiment["id"], experiment["trials"][0]["id"], "Morgan", "clarity", 1, "Clear"
    )
    with sqlite3.connect(settings.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM human_reviews WHERE id=?", (review["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE human_reviews SET reviewer='Someone else' WHERE id=?", (review["id"],)
            )
    assert len(reviews.list(experiment["id"])) == 1


def test_blank_review_and_missing_experiment_fail(workspace):
    settings, _, experiment = workspace
    reviews = ReviewStore(settings)
    with pytest.raises(ValidationError):
        reviews.add(experiment["id"], experiment["trials"][0]["id"], " ", "clarity", 0.5, "Reason")
    with pytest.raises(KeyError):
        reviews.list("missing")
