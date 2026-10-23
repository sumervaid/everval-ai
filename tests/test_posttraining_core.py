"""Checkpoint monitoring controls use local fixture systems, never provider calls."""

from __future__ import annotations

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, Scenario, System, content_hash
from cvals.posttraining import PostTraining
from cvals.research import ResearchLab
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    scenarios = [
        Scenario(
            id=f"task-{i}",
            name=f"Task {i}",
            domain="data",
            instruction="Return value",
            checks=[Check(id="answer", name="Answer", kind="numeric", path="value", expected=i)],
            reference_output={"value": i},
        )
        for i in range(24)
    ]
    store.save_collection(
        Collection(id="tasks", name="Frozen tasks", version="1", scenarios=scenarios)
    )
    return settings, store, PostTraining(settings, store)


def protocol(**updates):
    value = {
        "name": "Checkpoint controls",
        "training_run_id": "training-test",
        "provenance_kind": "synthetic_control",
        "provenance": "Original local controls, not model training",
        "training_config_sha256": "a" * 64,
        "protocol": {
            "domains": [
                {
                    "name": "arithmetic",
                    "collection_id": "tasks",
                    "task_families": {f"task-{i}": f"family-{i}" for i in range(24)},
                    "practical_threshold": 0.05,
                }
            ],
            "baseline_step": 0,
            "comparison_steps": [100, 200],
            "max_looks": 2,
            "alpha": 0.05,
            "min_clusters": 5,
            "repetitions": 1,
            "budget_per_domain_usd": 0,
            "lifetime_budget_usd": 0,
        },
    }
    value["protocol"].update(updates)
    return value


def checkpoint(store, step, system_id="fixture-reference", **updates):
    system = {
        key: value for key, value in store.get_system(system_id).items() if key != "configured"
    }
    return {
        "step": step,
        "weight_sha256": content_hash({"step": step}),
        "system_id": system_id,
        "expected_system_hash": content_hash(system),
        "revision": system.get("model") or "control/1",
        "provenance": "Fixture declaration; no trained weights",
        **updates,
    }


def setup_pair(workspace):
    settings, store, service = workspace
    run = service.create_run(protocol())
    baseline = service.register_checkpoint(run["id"], checkpoint(store, 0))
    candidate = service.register_checkpoint(run["id"], checkpoint(store, 100, "fixture-incomplete"))
    for cp in (baseline, candidate):
        for item in service.queue_checkpoint(run["id"], cp["id"])["experiments"]:
            Engine(store, settings).run_experiment(item["experiment_id"])
    return run, baseline, candidate


def test_protocol_idempotence_immutability_and_exact_family_coverage(workspace):
    _, _, service = workspace
    declaration = protocol()
    run = service.create_run(declaration)
    assert service.create_run(declaration) == run
    changed = copy.deepcopy(declaration)
    changed["protocol"]["alpha"] = 0.1
    with pytest.raises(ValueError, match="different protocol"):
        service.create_run(changed)
    assert "collections" not in run
    declaration["training_run_id"] = "other"
    declaration["protocol"]["domains"][0]["task_families"].pop("task-0")
    with pytest.raises(ValueError, match="cover every"):
        service.create_run(declaration)
    with (
        service.store._connect(write=True) as db,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        db.execute("UPDATE training_runs SET hash='tampered' WHERE id=?", (run["id"],))


@pytest.mark.parametrize(
    "change",
    [
        {"comparison_steps": [100, 100]},
        {"comparison_steps": [0]},
        {"comparison_steps": [200, 100]},
        {"comparison_steps": [100, 200], "max_looks": 1},
        {"baseline_step": True},
        {"alpha": float("nan")},
    ],
)
def test_protocol_rejects_invalid_grid(workspace, change):
    with pytest.raises(ValueError):
        workspace[2].create_run(protocol(**change))


def test_checkpoint_out_of_order_duplicates_and_cross_run_guards(workspace):
    _, store, service = workspace
    run = service.create_run(protocol())
    candidate_request = checkpoint(store, 100)
    candidate = service.register_checkpoint(run["id"], candidate_request)
    baseline = service.register_checkpoint(run["id"], checkpoint(store, 0))
    assert service.register_checkpoint(run["id"], candidate_request) == candidate
    assert [cp["step"] for cp in service.dashboard(run["id"])["checkpoints"]] == [0, 100]
    with pytest.raises(ValueError, match="different declaration"):
        service.register_checkpoint(run["id"], {**candidate_request, "revision": "changed"})
    with pytest.raises(ValueError, match="another step"):
        service.register_checkpoint(
            run["id"], checkpoint(store, 200, weight_sha256=candidate["weight_sha256"])
        )
    with pytest.raises(ValueError, match="outside"):
        service.register_checkpoint(run["id"], checkpoint(store, 300))
    other = service.create_run({**protocol(), "training_run_id": "other"})
    with pytest.raises(ValueError, match="another training"):
        service.queue_checkpoint(other["id"], baseline["id"])


def test_system_hash_binding_and_live_revision_contract(workspace):
    _, store, service = workspace
    live = System(
        id="http-test",
        name="Local endpoint",
        kind="http",
        model="checkpoint-0",
        base_url="http://127.0.0.1:9999/v1",
        input_cost_per_million=0,
        output_cost_per_million=0,
    )
    store.save_system(live)
    run = service.create_run({**protocol(), "provenance_kind": "declared_training"})
    with pytest.raises(ValueError, match="live HTTP"):
        service.register_checkpoint(run["id"], checkpoint(store, 0))
    with pytest.raises(ValueError, match="configuration"):
        service.register_checkpoint(
            run["id"], checkpoint(store, 0, "http-test", expected_system_hash="f" * 64)
        )
    with pytest.raises(ValueError, match="revision"):
        service.register_checkpoint(run["id"], checkpoint(store, 0, "http-test", revision="wrong"))
    cp = service.register_checkpoint(run["id"], checkpoint(store, 0, "http-test"))
    assert cp["weight_verification"] == "declared; not independently verified"


def test_queue_is_atomic_idempotent_and_frozen_after_store_changes(workspace):
    settings, store, service = workspace
    run = service.create_run(protocol(budget_per_domain_usd=1, lifetime_budget_usd=1))
    cp = service.register_checkpoint(run["id"], checkpoint(store, 0))
    changed = Collection.model_validate(store.get_collection("tasks"))
    changed.version = "2"
    changed.scenarios[0].instruction = "Changed after protocol was frozen"
    store.save_collection(changed)
    changed_system = System.model_validate(
        {k: v for k, v in store.get_system("fixture-reference").items() if k != "configured"}
    )
    changed_system.kind = "incomplete"
    store.save_system(changed_system)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.queue_checkpoint(run["id"], cp["id"]), range(2)))
    assert len(store.list_experiments()) == 1
    assert sum(result["idempotent"] for result in results) == 1
    experiment = store.get_experiment(results[0]["experiments"][0]["experiment_id"])
    assert experiment["snapshot"]["collection"]["version"] == "1"
    assert experiment["snapshot"]["systems"][0]["kind"] == "reference"
    Engine(store, settings).run_experiment(experiment["id"])
    candidate = service.register_checkpoint(run["id"], checkpoint(store, 100))
    with pytest.raises(ValueError, match="budget"):
        service.queue_checkpoint(run["id"], candidate["id"])
    assert service.dashboard(run["id"])["budget"]["allocated_usd"] == 1
    assert service.dashboard(run["id"])["budget"]["known_cost_usd"] == 0


def test_holdout_collections_never_enter_monitoring(workspace):
    _, store, service = workspace
    collection = Collection.model_validate(store.get_collection("tasks"))
    collection.id = "holdout"
    collection.benchmark_partition = "holdout"
    store.save_collection(collection)
    request = protocol()
    request["protocol"]["domains"][0]["collection_id"] = "holdout"
    with pytest.raises(ValueError, match="holdout"):
        service.create_run(request)


def test_queued_missing_and_error_results_stay_unknown(workspace):
    settings, store, service = workspace
    run = service.create_run(protocol())
    cp = service.register_checkpoint(run["id"], checkpoint(store, 0))
    dashboard = service.dashboard(run["id"])
    metric = dashboard["checkpoints"][0]["metrics"][0]
    assert metric["status"] == "not_queued" and metric["value"] is None
    assert metric["n"] == 0 and metric["expected_n"] == 24
    experiment_id = service.queue_checkpoint(run["id"], cp["id"])["experiments"][0]["experiment_id"]
    metric = service.dashboard(run["id"])["checkpoints"][0]["metrics"][0]
    assert metric["status"] == "queued" and not metric["cost_complete"]
    completed = Engine(store, settings).run_experiment(experiment_id)
    with store._connect(write=True) as db:
        trial = completed["trials"][0]
        trial.update(
            status="error",
            passed=None,
            score=None,
            cost_usd=None,
            error="Injected infrastructure control",
        )
        store._save_trial(db, trial)
    metric = service.dashboard(run["id"])["checkpoints"][0]["metrics"][0]
    assert metric["n"] == 23 and metric["missing"] == 1 and metric["errors"] == 1
    assert metric["value"] is None and metric["lower"] is None
    assert metric["unknown_cost_count"] == 1 and not metric["cost_complete"]


def test_comparison_is_persisted_once_with_family_alpha_and_staleness(workspace):
    _, store, service = workspace
    run, _, candidate = setup_pair(workspace)
    comparison = service.analyze(run["id"], candidate["id"])
    assert comparison["domains"][0]["decision"] == "regression"
    assert comparison["synthetic_control"]
    assert comparison["look_index"] == 1 and comparison["allocated_alpha"] == 0.025
    assert service.analyze(run["id"], candidate["id"]) == comparison
    assert len(service.dashboard(run["id"])["comparisons"]) == 1
    assert service.dashboard(run["id"])["alerts"][0]["synthetic_control"]
    exp_id = comparison["domains"][0]["candidate_experiment_id"]
    with store._connect(write=True) as db:
        trial = Store._get(db, "trials", store.get_experiment(exp_id)["trials"][0]["id"])
        trial["passed"] = True
        trial["score"] = 1
        store._save_trial(db, trial)
    with pytest.raises(ValueError, match="stale"):
        service.analyze(run["id"], candidate["id"])
    dashboard = service.dashboard(run["id"])
    assert dashboard["comparisons"][0]["stale"]
    assert dashboard["alerts"] == []


def test_unfinished_comparison_does_not_consume_look(workspace):
    _, store, service = workspace
    run = service.create_run(protocol())
    baseline = service.register_checkpoint(run["id"], checkpoint(store, 0))
    candidate = service.register_checkpoint(run["id"], checkpoint(store, 100))
    for cp in (baseline, candidate):
        service.queue_checkpoint(run["id"], cp["id"])
    with pytest.raises(ValueError, match="finish"):
        service.analyze(run["id"], candidate["id"])
    assert service.dashboard(run["id"])["comparisons"] == []
    assert service.tick() == []


def test_final_error_evidence_invalid_and_does_not_disappear(workspace):
    _, store, service = workspace
    run, _, candidate = setup_pair(workspace)
    linked = service.queue_checkpoint(run["id"], candidate["id"])["experiments"][0]
    with store._connect(write=True) as db:
        trial = store.get_experiment(linked["experiment_id"])["trials"][0]
        trial.update(status="error", passed=None, score=None)
        store._save_trial(db, trial)
    result = service.analyze(run["id"], candidate["id"])
    assert result["look_index"] == 1
    assert result["domains"][0]["decision"] == "invalid"
    assert result["domains"][0]["expected_pairs"] == 24
    assert result["domains"][0]["matched_pairs"] == 23
    assert service.tick() == []


def test_worker_tick_analyzes_once_without_queueing(workspace):
    _, store, service = workspace
    run, _, candidate = setup_pair(workspace)
    before = len(store.list_experiments())
    results = service.tick()
    assert len(results) == 1 and results[0]["candidate_checkpoint_id"] == candidate["id"]
    assert service.tick() == []
    assert len(store.list_experiments()) == before
    assert service.dashboard(run["id"])["comparisons"][0]["look_index"] == 1


def test_investigation_binds_controlled_regrade_and_retains_judgment_scope(workspace):
    settings, store, service = workspace
    run, _, candidate = setup_pair(workspace)
    comparison = service.analyze(run["id"], candidate["id"])
    experiment_id = comparison["domains"][0]["candidate_experiment_id"]
    trial_id = store.get_experiment(experiment_id)["trials"][0]["id"]
    regrade = ResearchLab(settings).repeat_regrade(experiment_id, trial_id, 2)
    request = {
        "comparison_id": comparison["id"],
        "observations": "Fixture loses all answers.",
        "hypotheses": ["Deliberately incomplete output"],
        "recommendation": "Inspect controls",
        "next_checks": ["Compare retained outputs"],
        "regrade_record_ids": [regrade["id"]],
    }
    result = service.investigate(run["id"], request)
    assert result == service.investigate(run["id"], request)
    assert result["regrades"][0]["id"] == regrade["id"]
    assert "causality is not established" in result["attribution"]


def test_mix_requires_predeclared_complete_withheld_manifest(workspace):
    _, _, service = workspace
    run = service.create_run(protocol())
    development = [
        {
            "checkpoint_id": cp,
            "task_family": family,
            "eval_id": evaluation,
            "domain": "data",
            "delta": -0.5 if cp == "dev-1" else 0.0,
            "cost_usd": 1.0,
        }
        for cp in ("dev-1", "dev-2")
        for family in ("dev-family",)
        for evaluation in ("a", "b")
    ]
    manifest = [
        {
            "checkpoint_id": "held-1",
            "task_family": "held-family",
            "eval_id": evaluation,
            "domain": "data",
        }
        for evaluation in ("a", "b")
    ]
    request = {
        "name": "Mix",
        "development": development,
        "validation_manifest": manifest,
        "config": {"budget_usd": 1.0, "protected_domains": ["data"]},
    }
    selection = service.select_mix(run["id"], request)
    assert service.select_mix(run["id"], request) == selection
    with pytest.raises(ValueError, match="reserved"):
        service.select_mix(run["id"], {**request, "name": "Try another selection"})
    leaked = copy.deepcopy(request)
    leaked["name"] = "Holdout becomes development"
    for row in leaked["development"]:
        row["checkpoint_id"] = "held-1"
        row["task_family"] = "held-family"
    # Use one duplicate-free development checkpoint so the reservation is the failure.
    leaked["development"] = leaked["development"][:2]
    for row in leaked["validation_manifest"]:
        row["checkpoint_id"] = "new-held"
        row["task_family"] = "new-held-family"
    with pytest.raises(ValueError, match="reserved"):
        service.select_mix(run["id"], leaked)
    assert "user supplied" in selection["evidence_origin"]
    validation = [{**row, "delta": -0.4, "cost_usd": 1.0} for row in manifest]
    with pytest.raises(ValueError, match="complete frozen"):
        service.validate_mix(run["id"], selection["id"], {"validation": validation[:1]})
    result = service.validate_mix(run["id"], selection["id"], {"validation": validation})
    assert service.validate_mix(run["id"], selection["id"], {"validation": validation}) == result
    validation[0]["delta"] = 0.0
    with pytest.raises(ValueError, match="already frozen"):
        service.validate_mix(run["id"], selection["id"], {"validation": validation})


@pytest.mark.parametrize("role", ["viewer", "reviewer"])
def test_api_private_training_routes_require_operator(workspace, role):
    settings, _, _ = workspace
    identities = IdentityStore(settings)
    identities.bootstrap("Admin")
    user = identities.create_user("Restricted", role, "admin")
    token = identities.issue_token(user["id"], "Test", actor="admin")["token"]
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings, start_worker=False)) as client:
        for path in ("/api/training/runs", "/api/training/runs/secret"):
            assert client.get(path, headers=headers).status_code == 403
        for suffix in (
            "",
            "/secret/checkpoints",
            "/secret/checkpoints/cp/queue",
            "/secret/checkpoints/cp/analyze",
            "/secret/investigations",
            "/secret/qualifications",
            "/secret/mix",
            "/secret/mix/m/validate",
        ):
            assert (
                client.post("/api/training/runs" + suffix, json={}, headers=headers).status_code
                == 403
            )


def test_api_end_to_end_registration_and_finite_validation(workspace):
    settings, store, _ = workspace
    with TestClient(create_app(settings, start_worker=False)) as client:
        response = client.post("/api/training/runs", json=protocol())
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        assert client.get("/api/training/runs").json()[0]["id"] == run_id
        response = client.post(
            f"/api/training/runs/{run_id}/checkpoints", json=checkpoint(store, 0)
        )
        assert response.status_code == 201, response.text
        cp_id = response.json()["id"]
        assert (
            client.post(f"/api/training/runs/{run_id}/checkpoints/{cp_id}/queue").status_code == 200
        )
        dashboard = client.get(f"/api/training/runs/{run_id}").json()
        assert dashboard["checkpoints"][0]["status"] == "queued"
        assert "reference_output" not in json.dumps(dashboard)
        request = checkpoint(store, 100)
        request["step"] = True
        assert (
            client.post(f"/api/training/runs/{run_id}/checkpoints", json=request).status_code == 422
        )


def test_queue_rolls_back_all_domains_and_allocations(workspace, monkeypatch):
    _, store, service = workspace
    second = Collection.model_validate(store.get_collection("tasks"))
    second.id = "second"
    store.save_collection(second)
    request = protocol(budget_per_domain_usd=1, lifetime_budget_usd=2)
    request["protocol"]["domains"].append(
        {
            **request["protocol"]["domains"][0],
            "name": "second-domain",
            "collection_id": "second",
        }
    )
    run = service.create_run(request)
    cp = service.register_checkpoint(run["id"], checkpoint(store, 0))
    original = store._insert_experiment
    calls = []

    def failing(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("Injected admission failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_insert_experiment", failing)
    with pytest.raises(ValueError, match="Injected"):
        service.queue_checkpoint(run["id"], cp["id"])
    assert store.list_experiments() == []
    assert service.dashboard(run["id"])["budget"]["allocated_usd"] == 0
    monkeypatch.setattr(store, "_insert_experiment", original)
    assert len(service.queue_checkpoint(run["id"], cp["id"])["experiments"]) == 2


def test_frozen_execution_and_inference_changes_cannot_be_training_effect(workspace):
    settings, store, service = workspace
    run = service.create_run(protocol())
    baseline = service.register_checkpoint(run["id"], checkpoint(store, 0))
    changed = System(id="changed", name="Changed sampling", kind="reference", max_tokens=17)
    store.save_system(changed)
    candidate = service.register_checkpoint(run["id"], checkpoint(store, 100, "changed"))
    for cp in (baseline, candidate):
        for linked in service.queue_checkpoint(run["id"], cp["id"])["experiments"]:
            Engine(store, settings).run_experiment(linked["experiment_id"])
    result = service.analyze(run["id"], candidate["id"])["domains"][0]
    assert not result["valid"] and result["decision"] == "invalid"
    assert "inference_configuration" in result["execution_contract_issues"]
    alternate = PostTraining(Settings(settings.data_dir, sandbox_image="different-image"))
    later = alternate.register_checkpoint(run["id"], checkpoint(store, 200))
    with pytest.raises(ValueError, match="execution contract"):
        alternate.queue_checkpoint(run["id"], later["id"])
    assert len(store.list_experiments()) == 2


def test_qualification_binds_version_and_comparison_discloses_missing_review(workspace):
    _, _, service = workspace
    run, _, candidate = setup_pair(workspace)
    request = {
        "domain": "arithmetic",
        "evidence": {
            "measurement_claim": "Checks exact arithmetic",
            "owner": "researcher",
            "population": "Original arithmetic controls",
            "limitations": "Small synthetic controls",
            "task_version": "1",
            "grader_version": "deterministic/1",
        },
    }
    wrong = copy.deepcopy(request)
    wrong["evidence"]["grader_version"] = "another-grader"
    with pytest.raises(ValueError, match="grader_version"):
        service.qualify(run["id"], wrong)
    record = service.qualify(run["id"], request)
    assert record["result"]["status"] == "gaps_found"
    result = service.analyze(run["id"], candidate["id"])
    assert result["domains"][0]["qualification"]["record_id"] == record["id"]
    assert result["domains"][0]["measurement_review_required"]
    assert result["inference_scope"] == "exploratory"
    alert = service.dashboard(run["id"])["alerts"][0]
    assert alert["qualification"]["status"] == "gaps_found"
    assert alert["inference_scope"] == "exploratory"


def test_mix_freeze_rejects_omitted_evaluation_and_partial_checkpoint_grid(workspace):
    _, _, service = workspace
    run = service.create_run(protocol())
    development = [
        {
            "checkpoint_id": "dev",
            "task_family": "dev-family",
            "eval_id": evaluation,
            "domain": "data",
            "delta": -0.2,
            "cost_usd": 1.0,
        }
        for evaluation in ("a", "b")
    ]
    manifest = [
        {
            "checkpoint_id": "held-1",
            "task_family": "held-family",
            "eval_id": evaluation,
            "domain": "data",
        }
        for evaluation in ("a", "b")
    ]
    request = {
        "name": "Mix",
        "development": development,
        "validation_manifest": manifest[:1],
        "config": {"budget_usd": 1.0, "protected_domains": ["data"]},
    }
    with pytest.raises(ValueError, match="every frozen"):
        service.select_mix(run["id"], request)
    request["validation_manifest"] = [*manifest, {**manifest[0], "checkpoint_id": "held-2"}]
    with pytest.raises(ValueError, match="complete checkpoint"):
        service.select_mix(run["id"], request)


def test_predeclared_scope_requires_independence_plan_and_pagination(workspace):
    _, _, service = workspace
    with pytest.raises(ValueError, match="independence"):
        service.create_run(protocol(inference_scope="predeclared"))
    request = protocol(
        inference_scope="predeclared",
        independence_statement="Checkpoint schedule fixed independently of withheld task outcomes.",
    )
    first = service.create_run(request)
    second = service.create_run({**protocol(), "training_run_id": "second"})
    assert service.list_runs(limit=1)[0]["id"] == second["id"]
    assert service.list_runs(limit=1, offset=1)[0]["id"] == first["id"]
    with pytest.raises(ValueError, match="pagination"):
        service.list_runs(limit=True)


@pytest.mark.parametrize("uncertain,committed", [(True, 0.5), (False, 1.1)])
def test_unknown_or_excess_charges_block_new_and_already_queued_http_trials(
    workspace, uncertain, committed
):
    _, store, service = workspace
    store.save_system(
        System(
            id="live",
            name="Local declared deployment",
            kind="http",
            model="checkpoint-0",
            base_url="http://127.0.0.1:9999/v1",
            input_cost_per_million=0,
            output_cost_per_million=0,
        )
    )
    request = {
        **protocol(budget_per_domain_usd=1, lifetime_budget_usd=10),
        "provenance_kind": "declared_training",
    }
    run = service.create_run(request)
    checkpoints = [
        service.register_checkpoint(run["id"], checkpoint(store, step, "live"))
        for step in (0, 100, 200)
    ]
    baseline = service.queue_checkpoint(run["id"], checkpoints[0]["id"])["experiments"][0]
    queued = service.queue_checkpoint(run["id"], checkpoints[1]["id"])["experiments"][0]
    with store._connect(write=True) as db:
        _, state = store._read_experiment(db, baseline["experiment_id"])
        state.update(budget_uncertain=uncertain, budget_committed_usd=committed)
        store._save_state(db, state)
    with pytest.raises(ValueError, match="further live admission is blocked"):
        service.queue_checkpoint(run["id"], checkpoints[2]["id"])
    assert len(store.list_experiments()) == 2
    # An already queued checkpoint is governed by the same check at real Store admission.
    experiment_id = queued["experiment_id"]
    assert store.claim("owner", experiment_id) == experiment_id
    trial_id = store.get_experiment(experiment_id)["trials"][0]["id"]
    blocked = store.start_trial(experiment_id, "owner", trial_id, reservation=0.01)
    assert blocked["status"] == "budget_exceeded"
    assert blocked["attempts"] == [] and blocked["cost_usd"] == 0
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM spend_ledger").fetchone()[0] == 0
    budget = service.dashboard(run["id"])["budget"]
    assert budget["admission_blocked"]
    assert budget["conservative_commitment_usd"] == pytest.approx(max(1, committed) + 1)


def test_training_budget_hook_leaves_legacy_and_free_controls_usable(workspace, tmp_path):
    from cvals.posttraining import training_budget_block

    legacy = Store(Settings(tmp_path / "legacy"))
    legacy.init()
    with legacy._connect() as db:
        assert training_budget_block(db, "unrelated") is None
    settings, store, service = workspace
    run = service.create_run(protocol())
    baseline = service.register_checkpoint(run["id"], checkpoint(store, 0))
    queued = service.queue_checkpoint(run["id"], baseline["id"])["experiments"][0]
    with store._connect(write=True) as db:
        _, state = store._read_experiment(db, queued["experiment_id"])
        state["budget_uncertain"] = True
        store._save_state(db, state)
    candidate = service.register_checkpoint(run["id"], checkpoint(store, 100))
    linked = service.queue_checkpoint(run["id"], candidate["id"])["experiments"][0]
    result = Engine(store, settings).run_experiment(linked["experiment_id"])
    assert result["status"] == "completed"
    assert all(trial["cost_usd"] == 0 for trial in result["trials"])


def test_actual_worker_periodic_tick_executes_and_analyzes_without_manual_analyze(
    workspace, monkeypatch
):
    import time

    from cvals.api import Worker

    settings, store, service = workspace
    run = service.create_run(protocol())
    for step, system in ((0, "fixture-reference"), (100, "fixture-incomplete")):
        cp = service.register_checkpoint(run["id"], checkpoint(store, step, system))
        service.queue_checkpoint(run["id"], cp["id"])
    # The control uses no containers; avoid probing an unrelated daemon during startup.
    monkeypatch.setattr("cvals.sandbox.cleanup_sandboxes", lambda settings: None)
    worker = Worker(store, settings)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        comparisons = []
        while time.monotonic() < deadline:
            comparisons = service.dashboard(run["id"])["comparisons"]
            if comparisons:
                break
            worker.stop_event.wait(0.1)
        assert len(comparisons) == 1
        assert comparisons[0]["domains"][0]["decision"] == "regression"
        assert comparisons[0]["synthetic_control"]
        assert all(experiment["status"] == "completed" for experiment in store.list_experiments())
    finally:
        worker.stop()
    assert not worker.running


def test_partial_known_provider_cost_is_a_floor_for_training_commitment(workspace):
    _, store, service = workspace
    store.save_system(
        System(
            id="partial-live",
            name="Local declaration",
            kind="http",
            model="checkpoint-0",
            base_url="http://127.0.0.1:9999/v1",
            input_cost_per_million=0,
            output_cost_per_million=0,
        )
    )
    run = service.create_run(
        {
            **protocol(budget_per_domain_usd=1, lifetime_budget_usd=10),
            "provenance_kind": "declared_training",
        }
    )
    cp = service.register_checkpoint(run["id"], checkpoint(store, 0, "partial-live"))
    linked = service.queue_checkpoint(run["id"], cp["id"])["experiments"][0]
    experiment_id = linked["experiment_id"]
    store.claim("owner", experiment_id)
    trial_id = store.get_experiment(experiment_id)["trials"][0]["id"]
    assert store.start_trial(experiment_id, "owner", trial_id, 0.01)["status"] == "running"
    # Simulated receipt, no network call: total cost unknown, but the adapter reports $5 already billed.
    store.finish_trial(
        experiment_id,
        "owner",
        trial_id,
        {
            "status": "error",
            "cost_usd": None,
            "passed": None,
            "provider_metadata": {"known_cost_usd": 5, "budget_uncertain": True},
            "error": "Injected partial-accounting receipt",
        },
    )
    budget = service.dashboard(run["id"])["budget"]
    assert budget["known_cost_usd"] == 5
    assert budget["conservative_commitment_usd"] == 5
    assert budget["admission_blocked"]
    assert budget["overrun_experiment_ids"] == [experiment_id]
