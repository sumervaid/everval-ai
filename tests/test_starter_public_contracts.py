"""Public starter contracts agree with their exact private checks."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from cvals.config import Settings
from cvals.evaluators import evaluate
from cvals.models import ExperimentCreate, Scenario
from cvals.packs import starter_collections
from cvals.store import Store


def task(identifier: str) -> Scenario:
    return next(
        scenario
        for collection in starter_collections()
        for scenario in collection.scenarios
        if scenario.id == identifier
    )


def passing(scenario: Scenario, output: dict[str, Any], settings: Settings) -> bool:
    checks = evaluate(scenario, output, settings)
    return bool(checks) and all(check["passed"] for check in checks)


def test_contract_format_is_public_without_relaxing_fact_checks(tmp_path: Path) -> None:
    scenario = task("documents-contract-notice")
    instruction = scenario.agent_view()["instruction"]
    assert "lowercase" in instruction
    assert "time expression" in instruction
    assert "Do not return a full sentence" in instruction
    settings = Settings(data_dir=tmp_path)
    output = {
        "notice_days": 45,
        "delivery": "registered mail",
        "consequence": "following renewal date",
        "citations": [
            {
                "source_id": "agreement-7",
                "quote": scenario.inputs["sources"][0]["text"],
            }
        ],
    }
    assert passing(scenario, output, settings)
    # These earlier answers were semantically sound but their formatting contract
    # was undocumented. Their retained raw grades remain unchanged.
    sentence_answer = {
        **output,
        "delivery": "Registered mail",
        "consequence": "A late notice takes effect at the following renewal date.",
    }
    assert not passing(scenario, sentence_answer, settings)
    assert not passing(scenario, {**output, "notice_days": 2}, settings)
    assert not passing(scenario, {**output, "delivery": "email"}, settings)
    assert not passing(scenario, {**output, "citations": []}, settings)


def test_refund_contract_exposes_transitions_and_rejects_wrong_behavior(tmp_path: Path) -> None:
    scenario = task("workflow-refund-approval")
    instruction = scenario.agent_view()["instruction"]
    for name in ("pending_approval", "request_manager_approval", "pay_refund"):
        assert name in instruction
    assert "strings, not objects" in instruction
    assert "do not add or remove fields" in instruction
    settings = Settings(data_dir=tmp_path)
    state = deepcopy(scenario.inputs["initial_state"])
    state["ticket"]["status"] = "pending_approval"
    state["refund"]["status"] = "pending_approval"
    correct = {"final_state": state, "actions": ["request_manager_approval"]}
    assert passing(scenario, correct, settings)

    premature = deepcopy(correct)
    premature["final_state"]["refund"]["paid_cents"] = 7500
    assert not passing(scenario, premature, settings)

    unchanged_ticket = deepcopy(correct)
    unchanged_ticket["final_state"]["ticket"]["status"] = "open"
    assert not passing(scenario, unchanged_ticket, settings)

    action_object = deepcopy(correct)
    action_object["actions"] = [{"type": "request_manager_approval"}]
    assert not passing(scenario, action_object, settings)

    extra_field = deepcopy(correct)
    extra_field["final_state"]["refund"]["requested_cents"] = 7500
    assert not passing(scenario, extra_field, settings)


def test_new_starter_version_does_not_rewrite_frozen_prior_contract(tmp_path: Path) -> None:
    collections = starter_collections()
    assert {collection.id: collection.version for collection in collections} == {
        "starter-coding": "1.0.0",
        "starter-documents": "1.1.0",
        "starter-data": "1.1.0",
        "starter-workflow": "1.1.0",
    }
    current = next(collection for collection in collections if collection.domain == "workflow")
    prior = current.model_copy(deep=True)
    prior.version = "1.0.0"
    prior.scenarios[0].instruction = (
        "Simulate this refund request. Return the complete final_state and ordered actions. "
        "A requested refund above 5000 cents needs manager approval before any payout. "
        "No approval has been given. The customer record must remain unchanged."
    )
    store = Store(Settings(data_dir=tmp_path))
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(prior)
    frozen = store.create_experiment(
        ExperimentCreate(
            name="Prior frozen contract",
            collection_id=prior.id,
            system_ids=["fixture-reference"],
        )
    )
    store.save_collection(current)
    retained = store.get_experiment(frozen["id"])
    assert retained["snapshot"] == frozen["snapshot"]
    assert retained["snapshot_hash"] == frozen["snapshot_hash"]
    assert retained["snapshot"]["collection"]["version"] == "1.0.0"
    assert retained["snapshot"]["collection"]["scenarios"][0]["instruction"] == (
        prior.scenarios[0].instruction
    )
    assert current.scenarios[0].checks == prior.scenarios[0].checks
    assert current.scenarios[0].reference_output == prior.scenarios[0].reference_output
