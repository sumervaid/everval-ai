"""Conversation behavior and execution contracts; no model calls."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from cvals.config import Settings
from cvals.conversation import ConversationService
from cvals.conversation_execution import ExecutionService
from cvals.engine import Engine
from cvals.models import Check, Collection, Scenario, System
from cvals.store import Store


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "workspace")
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="banking-tasks",
            name="Banking tickets",
            domain="data",
            scenarios=[
                Scenario(
                    id="route",
                    name="Route a ticket",
                    domain="data",
                    instruction="Return the category",
                    reference_output={"category": "card"},
                    checks=[
                        Check(
                            name="Correct category", kind="equals", path="category", expected="card"
                        )
                    ],
                )
            ],
        )
    )
    return settings, store, ConversationService(settings, store)


def request(extra=""):
    return (
        "Compare fixture-reference and fixture-incomplete on banking-tasks "
        "every two weeks with HTML and CSV reports. " + extra
    )


def test_complete_plain_request_queues_once_and_freezes_schedule(workspace):
    settings, store, service = workspace
    session = service.send(service.create()["id"], request())
    assert session["status"] == "active"
    assert session["draft"]["interval_minutes"] == 20160
    assert session["draft"]["report_formats"] == ["html", "csv"]
    assert session["draft"]["budget_usd"] == session["draft"]["lifetime_budget_usd"] == 0
    assert len(store.list_experiments()) == 1
    again = service.send(session["id"], "run it")
    assert again["experiment_id"] == session["experiment_id"]
    assert len(store.list_experiments()) == 1
    fresh_service = ConversationService(settings, Store(settings))
    restored = fresh_service.get(session["id"])
    assert restored["schedule_id"] == session["schedule_id"]
    assert restored["messages"][1]["content"] == request().strip()


def test_follow_up_answers_complete_original_request_without_new_confirmation(workspace):
    _, store, service = workspace
    session = service.send(
        service.create()["id"], "Compare fixture-reference and fixture-incomplete"
    )
    assert session["status"] == "draft"
    assert "task collection" in session["missing"]
    assert not store.list_experiments()
    session = service.send(session["id"], "Use Banking tickets every 2 weeks in Markdown")
    assert session["status"] == "active"
    assert session["draft"]["report_formats"] == ["markdown"]


@pytest.mark.parametrize(
    "unsupported, key",
    [
        ("Email the report to analyst@example.test.", "delivery"),
        ("Return a PDF report.", "format"),
        ("Return the results in XYZ format.", "format"),
        ("Run monthly.", "schedule"),
        ("Run every two weeks at 9am.", "schedule"),
        ("Use /tmp/private.csv.", "data"),
        ("Use the latest data each time.", "data"),
        ("Use a random sample of tasks.", "data"),
    ],
)
def test_unsupported_details_block_execution_and_survive_unrelated_followups(
    workspace, unsupported, key
):
    _, store, service = workspace
    session = service.send(service.create()["id"], request(unsupported))
    assert session["status"] == "draft"
    assert key in session["blockers"]
    session = service.send(session["id"], "Use HTML reports")
    if key != "format":
        assert key in session["blockers"]
        assert not store.list_experiments()


def test_corrections_clear_only_relevant_blocker(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("Email it as PDF."))
    assert set(session["blockers"]) == {"delivery", "format"}
    session = service.send(session["id"], "Use HTML instead")
    assert set(session["blockers"]) == {"delivery"}
    assert not store.list_experiments()
    session = service.send(session["id"], "Save reports locally")
    assert session["status"] == "active"


def test_unknown_second_model_is_not_silently_dropped(workspace):
    _, store, service = workspace
    session = service.send(
        service.create()["id"],
        "Compare fixture-reference and missing-model on banking-tasks every two weeks",
    )
    assert "models" in session["blockers"]
    assert not store.list_experiments()
    session = service.send(session["id"], "Compare fixture-reference and fixture-incomplete")
    assert session["status"] == "active"


def test_paid_models_need_explicit_separate_caps(workspace):
    _, store, service = workspace
    store.save_system(
        System(
            id="remote-model",
            name="Remote model",
            kind="http",
            model="example-model",
            base_url="https://provider.example/v1",
            input_cost_per_million=1,
            output_cost_per_million=2,
        )
    )
    session = service.send(
        service.create()["id"], "Compare remote-model on banking-tasks every two weeks; draft only"
    )
    assert session["missing"] == ["per-run budget", "total budget"]
    session = service.send(session["id"], "My budget is $100")
    assert "budget" in session["blockers"]
    session = service.send(session["id"], "$5 per run and $50 total")
    assert session["ready"] and session["status"] == "draft"
    assert session["draft"]["budget_usd"] == 5
    assert session["draft"]["lifetime_budget_usd"] == 50
    assert not store.list_experiments()


def test_local_control_defaults_do_not_carry_to_paid_models(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("draft only"))
    assert session["ready"]
    store.save_system(
        System(
            id="remote-model",
            name="Remote",
            kind="http",
            model="example",
            base_url="https://provider.example/v1",
            input_cost_per_million=1,
            output_cost_per_million=1,
        )
    )
    session = service.send(session["id"], "Use remote-model; draft only")
    assert session["draft"]["system_ids"] == ["remote-model"]
    assert session["draft"]["budget_usd"] is None
    assert session["draft"]["lifetime_budget_usd"] is None


def test_shared_model_names_need_exact_configuration(workspace):
    _, store, service = workspace
    for number in (1, 2):
        store.save_system(
            System(
                id=f"candidate-{number}",
                name=f"Candidate {number}",
                kind="http",
                model="shared-model",
                base_url="https://provider.example/v1",
                input_cost_per_million=1,
                output_cost_per_million=1,
            )
        )
    session = service.send(
        service.create()["id"], "Compare shared-model on banking-tasks every week; draft only"
    )
    assert "models" in session["blockers"]
    session = service.send(session["id"], "Compare candidate-1 and candidate-2; draft only")
    assert "models" not in session["blockers"]
    assert session["draft"]["system_ids"] == ["candidate-1", "candidate-2"]


def test_concurrent_activation_does_not_duplicate_experiment(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("draft only"))
    assert session["ready"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.activate(session["id"]), range(2)))
    assert len({result["experiment_id"] for result in results}) == 1
    assert len(store.list_experiments()) == 1


def test_recovery_after_execution_commits_before_session_save(workspace, monkeypatch):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("draft only"))
    original = ExecutionService.activate

    def interrupted(self, identifier, plan):
        original(self, identifier, plan)
        raise RuntimeError("Simulated connection loss after admission")

    monkeypatch.setattr(ExecutionService, "activate", interrupted)
    with pytest.raises(RuntimeError):
        service.activate(session["id"])
    assert service.get(session["id"])["status"] == "active"
    monkeypatch.setattr(ExecutionService, "activate", original)
    recovered = service.activate(session["id"])
    assert recovered["status"] == "active"
    assert len(store.list_experiments()) == 1


def test_pause_resume_and_reports_are_available_in_resumed_chat(workspace):
    settings, store, service = workspace
    session = service.send(service.create()["id"], request())
    assert service.send(session["id"], "pause")["status"] == "paused"
    assert service.send(session["id"], "resume")["status"] == "active"
    Engine(store, settings).run_experiment(session["experiment_id"])
    ExecutionService(settings, store).tick()
    session = service.get(session["id"])
    assert {report["format"] for report in session["reports"]} == {"html", "csv"}
    assert all(report["path"] for report in session["reports"])


def test_chat_does_not_expose_hidden_grading_material(workspace):
    _, _, service = workspace
    catalog = service.catalog()
    assert "reference_output" not in json.dumps(catalog)
    assert "checks" not in json.dumps(catalog)
    assert catalog["collections"][0]["scenario_count"] == 1


def test_secret_like_input_rejected_before_persistence(workspace):
    _, _, service = workspace
    session = service.create()
    with pytest.raises(ValueError, match="credentials"):
        service.send(session["id"], "sk-" + "example" * 8)
    assert len(service.get(session["id"])["messages"]) == 1


def test_invalid_total_cap_and_negative_amount_do_not_run(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("$5 per run and $2 total"))
    assert "budget" in session["blockers"]
    assert not store.list_experiments()
    session = service.send(session["id"], "$-10 total")
    assert "budget" in session["blockers"]
    assert not store.list_experiments()


def test_quoted_names_and_ids_are_accepted(workspace):
    _, store, service = workspace
    session = service.send(
        service.create()["id"],
        'Compare "fixture-reference" and "fixture-incomplete" on "Banking tickets" every two weeks with HTML reports.',
    )
    assert session["status"] == "active", session["blockers"]
    assert len(store.list_experiments()) == 1


@pytest.mark.parametrize(
    "replacement,key",
    [
        ("Compare nonexistent-model", "models"),
        ("Change model to nonexistent-model", "models"),
        ("Use nonexistent-model", "selection"),
        ("Run on NotTheSavedSet every two weeks", "data"),
        ("Change collection to NotTheSavedSet", "data"),
    ],
)
def test_unknown_replacement_never_reuses_stale_selection(workspace, replacement, key):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("draft only"))
    session = service.send(session["id"], replacement)
    assert key in session["blockers"]
    assert not store.list_experiments()


@pytest.mark.parametrize(
    "prefix", ["What if we compare", "Show me how to benchmark", "Create a plan to compare"]
)
def test_exploratory_request_is_not_execution_authorization(workspace, prefix):
    _, store, service = workspace
    session = service.send(
        service.create()["id"], prefix + " fixture-reference on banking-tasks every two weeks"
    )
    assert session["status"] == "draft"
    assert not store.list_experiments()


@pytest.mark.parametrize(
    "constraint,key",
    [
        ("without fixture-incomplete", "selection"),
        ("stop after 2 runs", "stop"),
        ("starting tomorrow", "schedule"),
        ("starting in two weeks", "schedule"),
    ],
)
def test_constraints_are_not_silently_ignored(workspace, constraint, key):
    _, store, service = workspace
    session = service.send(
        service.create()["id"],
        "Compare fixture-reference on banking-tasks every two weeks " + constraint,
    )
    assert key in session["blockers"]
    assert not store.list_experiments()


def test_repetition_instruction_reaches_experiment(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], request("Repeat each task 3 times."))
    assert session["status"] == "active", session["blockers"]
    assert store.get_experiment(session["experiment_id"])["repetitions"] == 3


def test_resource_display_name_does_not_invent_schedule(workspace):
    _, store, service = workspace
    original = store.get_collection("banking-tasks")
    original.update(id="daily-tasks", name="Daily tickets")
    store.save_collection(Collection.model_validate(original))
    session = service.send(service.create()["id"], "Compare fixture-reference on Daily tickets")
    assert "schedule" in session["missing"]
    assert not store.list_experiments()


def test_admission_failure_can_be_retried_after_external_fix(workspace, monkeypatch):
    _, store, service = workspace
    original = ExecutionService.activate

    def unavailable(self, identifier, plan):
        raise ValueError("Temporary admission issue")

    monkeypatch.setattr(ExecutionService, "activate", unavailable)
    session = service.send(service.create()["id"], request())
    assert "admission" in session["blockers"]
    assert not store.list_experiments()
    monkeypatch.setattr(ExecutionService, "activate", original)
    session = service.send(session["id"], "start")
    assert session["status"] == "active"


def test_long_display_name_does_not_select_prefix_model(workspace):
    _, store, service = workspace
    store.save_system(System(id="plain", name="Example model", kind="reference"))
    store.save_system(System(id="tuned", name="Example model tuned", kind="reference"))
    session = service.send(
        service.create()["id"], 'Compare "Example model tuned" on banking-tasks every two weeks'
    )
    assert session["status"] == "active", session["blockers"]
    assert session["draft"]["system_ids"] == ["tuned"]


def test_dotted_model_identifier_and_punctuation_resolve(workspace):
    _, store, service = workspace
    store.save_system(System(id="model-4.1", name="Model 4.1", kind="reference"))
    session = service.send(
        service.create()["id"], "Compare model-4.1 on banking-tasks every two weeks"
    )
    assert session["status"] == "active", session["blockers"]


def test_status_reads_scheduler_disabled_state(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], request())
    with store._connect(write=True) as connection:
        connection.execute(
            "UPDATE evaluation_schedules SET enabled=0,last_error='Budget exhausted' WHERE id=?",
            (session["schedule_id"],),
        )
    session = service.send(session["id"], "status")
    assert session["status"] == "paused"
    assert "paused" in session["messages"][-1]["content"]
    assert "Budget exhausted" in session["messages"][-1]["content"]


def test_sentence_punctuation_in_resource_followups(workspace):
    _, store, service = workspace
    session = service.send(service.create()["id"], "Compare fixture-reference.")
    assert session["draft"]["system_ids"] == ["fixture-reference"]
    assert not session["errors"]
    session = service.send(session["id"], "On banking-tasks.")
    assert session["draft"]["collection_id"] == "banking-tasks"
    assert not session["errors"]
    session = service.send(session["id"], "Every two weeks.")
    assert session["status"] == "active"
    assert len(store.list_experiments()) == 1
