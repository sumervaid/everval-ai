from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from cvals.autopilot import Autopilot
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System, canonical_json
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    scenarios = [
        Scenario(
            id=f"sum-{i}",
            name=f"Sum {i}",
            domain="data",
            instruction="Return the sum",
            checks=[Check(id="value", name="Sum", kind="numeric", path="value", expected=i)],
            reference_output={"value": i},
        )
        for i in range(3)
    ]
    store.save_collection(Collection(id="full", name="Full", scenarios=scenarios))
    store.save_collection(Collection(id="screen", name="Screen", scenarios=scenarios[:1]))

    def baseline(collection, budget):
        result = store.create_experiment(
            ExperimentCreate(
                name="Baseline",
                collection_id=collection,
                system_ids=["fixture-reference"],
                budget_usd=budget,
            )
        )
        return Engine(store, settings).run_experiment(result["id"])

    return settings, store, baseline("full", 2), baseline("screen", 1)


def policy(workspace, **kwargs):
    settings, store, full, screen = workspace
    service = Autopilot(settings, store)
    record = service.create(
        "Daily quality",
        full["id"],
        screen["id"],
        lifetime_budget_usd=6,
        interval_minutes=5,
        enabled=True,
        **kwargs,
    )
    return service, record, record["next_due_epoch"] + 1


def run(workspace, identifier):
    settings, store, _, _ = workspace
    return Engine(store, settings).run_experiment(identifier)


def set_state(store, identifier, **updates):
    with store._connect(write=True) as connection:
        _, state = store._read_experiment(connection, identifier)
        state.update(updates)
        store._save_state(connection, state)


def test_paused_default_and_screening_projection(workspace):
    settings, store, full, screen = workspace
    service = Autopilot(settings, store)
    record = service.create("Paused", full["id"], screen["id"], lifetime_budget_usd=9)
    assert not record["enabled"]
    assert service.tick(record["next_due_epoch"] + 1) == []
    projection = service.projection(record["id"], 1)
    assert projection["cycle_reservation_usd"] == 3
    assert projection["periodic_cycles"] == 24
    assert projection["fully_reserved_cycles_remaining"] == 3
    assert "not predicted invoices" in projection["assumptions"]


def test_screen_without_regression_releases_unused_full_reservation(workspace):
    service, record, now = policy(workspace)
    screen = service.tick(now)[0]
    assert service.budget(record["id"])["committed_usd"] == 3
    run(workspace, screen)
    assert service.tick(now + 61) == []
    history = service.history(record["id"])
    assert history[0]["status"] == "screened_out"
    assert history[0]["full_experiment_id"] is None
    assert service.budget(record["id"])["committed_usd"] == 0
    assert service.budget(record["id"])["accounted_usd"] == 0


def test_regression_escalates_with_same_frozen_system_and_cohort(workspace):
    _, store, source, _ = workspace
    service, record, now = policy(workspace)
    store.save_system(
        System(id="fixture-reference", name="Changed implementation", kind="incomplete")
    )
    screen = service.tick(now)[0]
    assert run(workspace, screen)["trials"][0]["passed"] is False
    store.save_system(System(id="fixture-reference", name="Later implementation", kind="reference"))
    full = service.tick(now + 61)[0]
    result = store.get_experiment(full)
    assert result["snapshot"]["systems"][0]["kind"] == "incomplete"
    assert result["collection_hash"] == source["collection_hash"]
    assert len(result["trials"]) == 3
    assert service.history(record["id"])[0]["screening_regressions"] == [
        "fixture-reference/sum-0/1"
    ]
    run(workspace, full)
    assert service.tick(now + 122) == []
    assert service.history(record["id"])[0]["status"] == "completed"


def test_change_trigger_deduplicates_and_observes_saved_configuration(workspace):
    _, store, _, _ = workspace
    service, record, now = policy(workspace, trigger="change", escalate="change")
    assert service.tick(now) == []
    service.configure(record["id"], config={"prompt_revision": "v2"})
    first = service.tick(now + 61)
    assert len(first) == 1
    assert service.tick(now + 122) == []
    run(workspace, first[0])
    full = service.tick(now + 183)[0]
    run(workspace, full)
    service.tick(now + 244)
    assert service.tick(now + 305) == []
    assert len(service.history(record["id"])) == 1
    assert len(store.list_experiments()) == 4


def test_concurrent_ticks_admit_one_reserved_occurrence(workspace):
    service, record, now = policy(workspace)
    second = Autopilot(workspace[0], workspace[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda controller: controller.tick(now), [service, second]))
    assert sum(len(result) for result in results) == 1
    assert len(service.history(record["id"])) == 1
    assert service.budget(record["id"])["committed_usd"] == 3


def test_crash_rolls_back_experiment_and_reservation_then_recovers(workspace, monkeypatch):
    service, record, now = policy(workspace)
    store = workspace[1]
    original = store._insert_experiment

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Simulated process loss")

    monkeypatch.setattr(store, "_insert_experiment", crash)
    with pytest.raises(RuntimeError):
        service.tick(now)
    assert len(store.list_experiments()) == 2
    assert service.history(record["id"]) == []
    assert service.budget(record["id"])["committed_usd"] == 0
    monkeypatch.setattr(store, "_insert_experiment", original)
    assert len(service.tick(now)) == 1


def test_admission_failure_has_no_orphan_and_disables_policy(workspace, monkeypatch):
    service, record, now = policy(workspace)
    original = workspace[1]._insert_experiment

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("Source admission changed")

    monkeypatch.setattr(workspace[1], "_insert_experiment", fail)
    assert service.tick(now) == []
    assert len(workspace[1].list_experiments()) == 2
    assert not service.get(record["id"])["enabled"]
    assert service.get(record["id"])["last_error"] == "Source admission changed"


def test_unknown_cost_retains_both_stages_and_blocks_resume(workspace):
    service, record, now = policy(workspace)
    screen = service.tick(now)[0]
    run(workspace, screen)
    set_state(workspace[1], screen, budget_uncertain=True, budget_committed_usd=0.5)
    assert service.tick(now + 61) == []
    budget = service.budget(record["id"])
    assert budget["committed_usd"] == 3
    assert budget["accounted_usd"] == 0
    assert budget["uncertain"]
    assert not service.get(record["id"])["enabled"]
    assert service.history(record["id"])[0]["status"] == "halted"
    with pytest.raises(ValueError, match="Uncertain billing"):
        service.configure(record["id"], enabled=True)


def test_cumulative_cap_is_rechecked_after_known_settlement(workspace):
    service, record, now = policy(workspace, escalate="always")
    service.configure(record["id"], lifetime_budget_usd=3)
    screen = service.tick(now)[0]
    run(workspace, screen)
    set_state(workspace[1], screen, budget_committed_usd=0.75)
    full = service.tick(now + 61)[0]
    run(workspace, full)
    set_state(workspace[1], full, budget_committed_usd=1.25)
    service.tick(now + 122)
    assert service.history(record["id"])[0]["status"] == "completed"
    assert service.budget(record["id"])["committed_usd"] == 2
    assert service.tick(now + 1000) == []
    assert not service.get(record["id"])["enabled"]
    assert "reserve both stages" in service.get(record["id"])["last_error"]
    assert service.history(record["id"])[0]["status"] == "completed"
    with pytest.raises(ValueError, match="retained charges"):
        service.configure(record["id"], lifetime_budget_usd=1)


def test_pausing_preserves_inflight_screen_and_prevents_full_admission(workspace):
    service, record, now = policy(workspace, escalate="always")
    screen = service.tick(now)[0]
    service.configure(record["id"], enabled=False)
    run(workspace, screen)
    assert service.tick(now + 61) == []
    assert service.history(record["id"])[0]["status"] == "screening"
    assert service.budget(record["id"])["committed_usd"] == 2
    service.configure(record["id"], enabled=True)
    assert len(service.tick(now + 122)) == 1


def test_error_trial_halts_instead_of_mislabeling_quality_regression(workspace):
    service, record, now = policy(workspace, escalate="always")
    screen = service.tick(now)[0]
    run(workspace, screen)
    store = workspace[1]
    with store._connect(write=True) as connection:
        row = connection.execute(
            "SELECT data FROM trials WHERE experiment_id=?", (screen,)
        ).fetchone()
        trial = json.loads(row[0])
        trial.update(status="error", passed=None)
        store._save_trial(connection, trial)
    assert service.tick(now + 61) == []
    assert not service.get(record["id"])["enabled"]
    assert "infrastructure-error" in service.get(record["id"])["last_error"]


def test_frozen_sources_ignore_saved_collection_edits(workspace):
    service, _, now = policy(workspace)
    store = workspace[1]
    changed = store.get_collection("screen")
    changed.update(version="2", name="New mutable dataset")
    changed["scenarios"][0]["checks"][0]["expected"] = 999
    store.save_collection(Collection.model_validate(changed))
    generated = service.tick(now)[0]
    assert store.get_experiment(generated)["collection_hash"] == workspace[3]["collection_hash"]


def test_screen_factory_is_explicit_and_records_smaller_frozen_cohort(workspace):
    service = Autopilot(workspace[0], workspace[1])
    fresh = service.screen(workspace[2]["id"], 2)
    assert fresh["status"] == "queued"
    assert fresh["parent_experiment_id"] == workspace[2]["id"]
    assert len(fresh["snapshot"]["collection"]["scenarios"]) == 2
    run(workspace, fresh["id"])
    assert (
        service.create("Prepared", workspace[2]["id"], fresh["id"], lifetime_budget_usd=4)[
            "full_scenarios"
        ]
        == 3
    )
    with pytest.raises(ValueError, match="smaller"):
        service.screen(workspace[2]["id"], 3)


def test_source_subset_and_system_mismatch_are_rejected(workspace):
    service = Autopilot(workspace[0], workspace[1])
    with pytest.raises(ValueError, match="strict nonempty subset"):
        service.create("Same", workspace[2]["id"], workspace[2]["id"], lifetime_budget_usd=4)
    store = workspace[1]
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Other system",
            collection_id="screen",
            system_ids=["fixture-incomplete"],
            budget_usd=1,
        )
    )
    run(workspace, experiment["id"])
    with pytest.raises(ValueError, match="identical frozen systems"):
        service.create("Mismatch", workspace[2]["id"], experiment["id"], lifetime_budget_usd=4)


@pytest.mark.parametrize("partition", ["holdout", "all"])
def test_holdout_and_combined_source_partitions_are_excluded(workspace, partition):
    store = workspace[1]
    value = store.get_collection("full")
    value.update(id="reserved", benchmark_partition=partition)
    store.save_collection(Collection.model_validate(value))
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Reserved",
            collection_id="reserved",
            system_ids=["fixture-reference"],
            budget_usd=2,
        )
    )
    run(workspace, experiment["id"])
    service = Autopilot(workspace[0], store)
    with pytest.raises(ValueError, match="holdout"):
        service.create("Not permitted", experiment["id"], workspace[3]["id"], lifetime_budget_usd=4)
    with pytest.raises(ValueError, match="holdout"):
        service.screen(experiment["id"], 1)


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), -1, True, 2])
def test_invalid_or_insufficient_cycle_cap(workspace, cap):
    with pytest.raises(ValueError, match="Budget|budget"):
        Autopilot(workspace[0], workspace[1]).create(
            "Invalid", workspace[2]["id"], workspace[3]["id"], lifetime_budget_usd=cap
        )


def test_config_rejects_inline_credentials(workspace):
    service, record, _ = policy(workspace)
    with pytest.raises(ValueError, match="never secret values"):
        service.configure(record["id"], config={"api_key": "must-not-be-stored"})
    assert service.get(record["id"])["config"] == {}


def test_local_head_watch_and_discovery_queue_are_opt_in(workspace, tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repository), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (repository / "README.md").write_text("Fixture repository\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "Initial")
    service = Autopilot(workspace[0], workspace[1])
    project = service.projects.connect(str(repository))
    record = service.create(
        "Local watch",
        workspace[2]["id"],
        workspace[3]["id"],
        lifetime_budget_usd=6,
        enabled=True,
        trigger="change",
        project_id=project["id"],
        discover=True,
    )
    now = record["next_check_epoch"] + 1
    assert service.tick(now) == []
    assert len(service.jobs.list()) == 1
    assert service.jobs.list()[0]["kind"] == "discover"
    git("commit", "--quiet", "--allow-empty", "-m", "New source revision")
    assert len(service.tick(now + 61)) == 1
    occurrence = service.history(record["id"])[0]
    assert occurrence["observation"]["commit"] == git("rev-parse", "HEAD")
    assert "does not attest a deployment" in occurrence["deployment_verification"]
    assert len(service.jobs.list()) == 1
    assert service.projects.candidates(project["id"]) == []


def test_occurrence_integrity_is_checked(workspace):
    service, record, now = policy(workspace)
    service.tick(now)
    with workspace[1]._connect(write=True) as connection:
        connection.execute(
            "UPDATE autopilot_occurrences SET data=? WHERE policy_id=?",
            (canonical_json({"edited": True}), record["id"]),
        )
    with pytest.raises(ValueError, match="integrity"):
        service.history(record["id"])


def failed_cycle(workspace):
    service, record, now = policy(workspace)
    workspace[1].save_system(System(id="fixture-reference", name="Regression", kind="incomplete"))
    screen = service.tick(now)[0]
    run(workspace, screen)
    full = service.tick(now + 61)[0]
    run(workspace, full)
    service.tick(now + 122)
    return service, record, now


def test_provider_free_investigation_retains_links_and_runs_once(workspace):
    service, record, _ = failed_cycle(workspace)
    investigation = service.investigate_pending()
    assert investigation["status"] == "completed"
    assert len(investigation["selected"]) == 3
    assert len(investigation["results"]) == 3
    assert all(
        item.get("repeat_regrade_id") and item.get("minimize_output_id")
        for item in investigation["results"]
    )
    assert (
        len({(item["scenario_id"], item["system_id"]) for item in investigation["selected"]}) == 3
    )
    assert service.investigate_pending() is None
    assert service.investigations(record["id"])[0] == investigation
    assert len(workspace[1].list_experiments()) == 4


def test_interrupted_investigation_is_visible_and_never_retried_silently(workspace, monkeypatch):
    import time

    from cvals.research import ResearchLab

    service, record, _ = failed_cycle(workspace)

    def crash(*args, **kwargs):
        raise RuntimeError("Simulated process loss")

    monkeypatch.setattr(ResearchLab, "repeat_regrade", crash)
    started = time.time()
    with pytest.raises(RuntimeError):
        service.investigate_pending(started)
    assert service.investigations(record["id"])[0]["status"] == "running"
    assert service.investigate_pending(started + 301) is None
    assert service.investigations(record["id"])[0]["status"] == "interrupted"


def test_passing_cycle_records_investigation_skip_without_research_calls(workspace, monkeypatch):
    from cvals.research import ResearchLab

    service, record, now = policy(workspace)
    screen = service.tick(now)[0]
    run(workspace, screen)
    service.tick(now + 61)

    def unexpected(*args, **kwargs):
        raise AssertionError("No research call expected for a passing trial")

    monkeypatch.setattr(ResearchLab, "repeat_regrade", unexpected)
    assert service.investigate_pending()["status"] == "skipped"
    assert len(service.investigations(record["id"])) == 1


def test_unknown_zero_reservation_cost_still_blocks_policy(workspace):
    service, record, now = policy(workspace)
    screen = service.tick(now)[0]
    run(workspace, screen)
    with workspace[1]._connect(write=True) as connection:
        trial = json.loads(
            connection.execute(
                "SELECT data FROM trials WHERE experiment_id=?", (screen,)
            ).fetchone()[0]
        )
        trial["cost_usd"] = None
        workspace[1]._save_trial(connection, trial)
    assert not workspace[1].get_experiment(screen)["budget_uncertain"]
    assert service.tick(now + 61) == []
    assert service.budget(record["id"])["uncertain"]
    assert service.budget(record["id"])["committed_usd"] == 3
    with pytest.raises(ValueError, match="Uncertain billing"):
        service.configure(record["id"], enabled=True)


def test_missing_source_trial_cost_is_rejected_even_without_uncertainty_flag(workspace):
    service = Autopilot(workspace[0], workspace[1])
    with workspace[1]._connect(write=True) as connection:
        trial = json.loads(
            connection.execute(
                "SELECT data FROM trials WHERE experiment_id=? LIMIT 1", (workspace[2]["id"],)
            ).fetchone()[0]
        )
        trial["cost_usd"] = None
        workspace[1]._save_trial(connection, trial)
    with pytest.raises(ValueError, match="known cost"):
        service.create(
            "Unknown source", workspace[2]["id"], workspace[3]["id"], lifetime_budget_usd=4
        )


def test_halted_occurrence_underlying_queue_prevents_overlap_on_resume(workspace, monkeypatch):
    service, record, now = policy(workspace)
    first = service.tick(now)[0]
    observation = service._observation

    def unavailable(*args, **kwargs):
        raise ValueError("Repository unavailable")

    monkeypatch.setattr(service, "_observation", unavailable)
    assert service.tick(now + 61) == []
    assert service.history(record["id"])[0]["status"] == "halted"
    monkeypatch.setattr(service, "_observation", observation)
    service.configure(record["id"], enabled=True)
    assert service.tick(now + 1000) == []
    assert len(workspace[1].list_experiments()) == 3
    run(workspace, first)
    assert len(service.tick(now + 1061)) == 1


def test_autonomous_investigation_rejects_expensive_schema_without_evaluation(
    workspace, monkeypatch
):
    from cvals.research import ResearchLab

    settings, store, _, _ = workspace
    scenarios = [
        Scenario(
            id=f"schema-{i}",
            name=f"Shape {i}",
            domain="data",
            instruction="Return unique objects",
            checks=[
                Check(
                    id="shape",
                    name="Shape",
                    kind="json_schema",
                    path="values",
                    expected={"type": "array", "uniqueItems": True},
                )
            ],
            reference_output={"values": [{"value": i}]},
        )
        for i in range(2)
    ]
    store.save_collection(Collection(id="schema-full", name="Full", scenarios=scenarios))
    store.save_collection(Collection(id="schema-screen", name="Screen", scenarios=scenarios[:1]))
    sources = []
    for collection_id in ("schema-full", "schema-screen"):
        source = store.create_experiment(
            ExperimentCreate(
                name="Schema baseline",
                collection_id=collection_id,
                system_ids=["fixture-reference"],
                budget_usd=1,
            )
        )
        run(workspace, source["id"])
        sources.append(source["id"])
    service = Autopilot(settings, store)
    record = service.create(
        "Schema policy", sources[0], sources[1], lifetime_budget_usd=2, enabled=True
    )
    store.save_system(System(id="fixture-reference", name="Regression", kind="incomplete"))
    now = record["next_due_epoch"] + 1
    screen = service.tick(now)[0]
    run(workspace, screen)
    full = service.tick(now + 61)[0]
    run(workspace, full)
    service.tick(now + 122)

    def unexpected(*args, **kwargs):
        raise AssertionError("Pathological grading must never reach synchronous research")

    monkeypatch.setattr(ResearchLab, "repeat_regrade", unexpected)
    result = service.investigate_pending()
    assert result["status"] == "skipped"
    assert result["selected"] == []
    assert result["skipped"]
    assert "work bounds" in result["skipped"][0]["reason"]
