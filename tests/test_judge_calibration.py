"""Judge evidence must retain failures, hide reference labels and respect frozen splits."""

from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from cvals.config import Settings
from cvals.judge_calibration import JudgeCalibration, normalize_response, write_report
from cvals.judge_calibration_models import CalibrationProtocol
from cvals.providers import Submission


def protocol(*, live=False, budget=1):
    judge = {"id": "judge-1", "model": "declared-model", "revision": "pinned-revision"}
    if live:
        judge["system"] = {
            "id": "judge-provider",
            "name": "Judge",
            "kind": "http",
            "model": "declared-model",
            "base_url": "http://localhost:11434/v1",
            "max_tokens": 64,
            "input_cost_per_million": 1,
            "output_cost_per_million": 1,
        }
    return {
        "name": "Measurement control",
        "judges": [judge],
        "budget_usd": budget,
        "rubric": {
            "name": "Correctness",
            "version": "1",
            "instructions": "Prefer the correct response.",
            "dimensions": [
                {"id": "correct", "name": "Correctness", "guidance": "Score factual correctness."}
            ],
        },
        "items": [
            {
                "item_id": name,
                "task_id": "task-" + name,
                "family_id": "family-" + name,
                "partition": partition,
                "task": {"instruction": "Solve " + name, "inputs": {"question": name}},
                "responses": {"A": {"answer": "correct"}, "B": {"answer": "incorrect"}},
                "generator_models": {"A": "SECRET-GENERATOR", "B": "OTHER-SECRET-GENERATOR"},
                "reference_context": {"public_fact": "Declared additional evidence"},
                "reference": {
                    "source": "scripted",
                    "label": "A",
                    "provenance": "SCRIPTED-SECRET-PROVENANCE",
                    "reviewer_labels": [{"reviewer_id": "SECRET-REVIEWER", "label": "A"}],
                },
            }
            for name, partition in (("development", "dev"), ("test", "holdout"))
        ],
    }


@pytest.fixture
def study(tmp_path):
    service = JudgeCalibration(Settings(tmp_path / "workspace"))
    return service, service.create(protocol())["id"]


def successful_import(service, identifier, partition="dev"):
    rows = [
        {
            "job_id": request["job_id"],
            "response": {
                "preference": "A" if request["order"][0] == "A" else "B",
                "rationale": "The arithmetic is correct.",
            },
            "cost_usd": 0,
        }
        for request in service.export_requests(identifier, partition)["jobs"]
    ]
    service.import_observations(identifier, rows)
    return rows


def test_prompt_is_whitelisted_and_reference_arm_separate(tmp_path):
    value = protocol()
    value["judges"].append(
        {
            "id": "guided",
            "model": "other-model",
            "revision": "1",
            "prompt_variant": "reference_context",
        }
    )
    value["planned_comparisons"] = 2
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(value)["id"]
    requests = service.export_requests(identifier)["jobs"]
    for row in requests:
        prompt = json.dumps(row["payload"])
        assert "SECRET" not in prompt
        assert ("reference_context" in prompt) == (row["judge_id"] == "guided")
        assert "'abstain'" in prompt
    assert len(requests) == 4


def test_every_item_has_both_orders_and_repeated_ids_are_unique(tmp_path):
    value = protocol()
    value["repetitions"] = 3
    service = JudgeCalibration(Settings(tmp_path))
    created = service.create(value)
    requests = service.export_requests(created["id"])["jobs"]
    assert len(requests) == 6 and len({r["job_id"] for r in requests}) == 6
    for rep in range(3):
        assert {tuple(r["order"]) for r in requests if r["repetition"] == rep} == {
            ("A", "B"),
            ("B", "A"),
        }


def test_holdout_sealed_until_development_finalized(study):
    service, identifier = study
    with pytest.raises(ValueError, match="selection"):
        service.export_requests(identifier, "holdout")
    with pytest.raises(ValueError, match="Finalize"):
        service.release_holdout(identifier, ["judge-1"], "Frozen policy")
    successful_import(service, identifier)
    service.release_holdout(identifier, ["judge-1"], "Frozen policy")
    assert len(service.export_requests(identifier, "holdout")["jobs"]) == 2
    with pytest.raises(ValueError, match="immutable"):
        service.release_holdout(identifier, ["judge-1"], "Changed")


@pytest.mark.parametrize("attribute", ["task_id", "family_id"])
def test_partition_leakage_rejected(attribute):
    value = protocol()
    value["items"][1][attribute] = value["items"][0][attribute]
    with pytest.raises(ValueError, match="crosses"):
        CalibrationProtocol.model_validate(value)


def test_human_reference_requires_retained_labels():
    value = protocol()
    value["items"][0]["reference"].update(source="human_collected", reviewer_labels=[])
    with pytest.raises(ValueError, match="reviewer labels"):
        CalibrationProtocol.model_validate(value)


def test_default_reference_is_unverified():
    value = protocol()
    del value["items"][0]["reference"]
    assert CalibrationProtocol.model_validate(value).items[0].reference.source == "unverified"


def test_protocol_idempotent_and_immutable(study):
    service, identifier = study
    assert service.create(protocol())["id"] == identifier
    with (
        service.store._connect(write=True) as db,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        db.execute("UPDATE judge_campaigns SET protocol='{}' WHERE id=?", (identifier,))


def test_swapped_labels_and_scores_are_canonical():
    rubric = CalibrationProtocol.model_validate(protocol()).rubric
    value = normalize_response(
        {
            "preference": "A",
            "rationale": "supported",
            "scores": {"A": {"correct": 0.8}, "B": {"correct": 0.1}},
        },
        ["B", "A"],
        rubric,
    )
    assert value["preference"] == "B" and value["scores"] == {
        "B": {"correct": 0.8},
        "A": {"correct": 0.1},
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("not json", "invalid"),
        ('{"preference":"C","rationale":"unknown"}', "invalid"),
        ({"preference": "A", "rationale": ""}, "invalid"),
        ({"preference": "A", "rationale": "yes", "extra": True}, "invalid"),
        (
            {
                "preference": "A",
                "rationale": "yes",
                "scores": {"A": {"correct": True}, "B": {"correct": 0}},
            },
            "invalid",
        ),
        (
            {
                "preference": "A",
                "rationale": "yes",
                "scores": {"A": {"wrong": 1}, "B": {"wrong": 0}},
            },
            "invalid",
        ),
        ({"preference": "abstain", "rationale": "Insufficient evidence"}, "abstained"),
        (None, "missing"),
    ],
)
def test_invalid_abstained_and_missing_distinct(raw, expected):
    assert (
        normalize_response(raw, ["A", "B"], CalibrationProtocol.model_validate(protocol()).rubric)[
            "status"
        ]
        == expected
    )


def test_imports_append_only_and_conflicting_batch_rolls_back(study):
    service, identifier = study
    requests = service.export_requests(identifier)["jobs"]
    row = {
        "job_id": requests[0]["job_id"],
        "response": {"preference": "A", "rationale": "supported"},
        "cost_usd": 0,
    }
    with pytest.raises(ValueError, match="does not belong"):
        service.import_observations(identifier, [row, {"job_id": "unplanned", "cost_usd": 0}])
    assert service.get(identifier)["statuses"] == {"queued": 4}
    service.import_observations(identifier, [row])
    service.import_observations(identifier, [row])
    with pytest.raises(ValueError, match="immutable"):
        service.import_observations(
            identifier, [{**row, "response": {"preference": "B", "rationale": "changed"}}]
        )
    with pytest.raises(ValueError, match="repeat"):
        service.import_observations(identifier, [row, row])


def test_analysis_keeps_missing_rows_and_provenance(study):
    service, identifier = study
    successful_import(service, identifier)
    service.release_holdout(identifier, ["judge-1"], "All declared judges")
    report = service.analyze(identifier)
    text = json.dumps(report)
    assert "scripted" in text and "missing" in text
    assert report["analysis_type"] == "heldout" and report["purpose"] == "judge_calibration"
    with service.store._connect() as db:
        assert db.execute("SELECT count(*) FROM experiments").fetchone()[0] == 0


def test_imported_unknown_cost_blocks_live_calls(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(protocol(live=True))["id"]
    request = service.export_requests(identifier)["jobs"][0]
    service.import_observations(
        identifier,
        [
            {
                "job_id": request["job_id"],
                "error": "Interrupted outside Everval",
                "reservation_usd": 0.2,
            }
        ],
    )
    monkeypatch.setattr(
        "cvals.judge_calibration.submit", lambda *a: pytest.fail("Unexpected provider call")
    )
    with pytest.raises(ValueError, match="Unknown"):
        service.run(identifier)
    assert service.get(identifier)["committed_usd"] == 0.2


def test_insufficient_budget_never_calls_provider(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(protocol(live=True, budget=0.0000001))["id"]
    monkeypatch.setattr(
        "cvals.judge_calibration.submit", lambda *a: pytest.fail("Unexpected provider call")
    )
    result = service.run(identifier)
    assert result["statuses"]["budget_exceeded"] == 2 and result["committed_usd"] == 0


def test_live_runner_uses_shared_ledger_without_retries(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(protocol(live=True))["id"]
    calls = []

    def respond(scenario, *args):
        assert "SECRET" not in json.dumps(scenario.agent_view())
        calls.append(scenario.id)
        return Submission(
            output={"preference": "A", "rationale": "Measured"},
            cost_usd=0.01,
            input_tokens=100,
            output_tokens=5,
        )

    monkeypatch.setattr("cvals.judge_calibration.submit", respond)
    result = service.run(identifier)
    assert len(calls) == 2 and result["known_cost_usd"] == 0.02
    assert service.run(identifier)["known_cost_usd"] == 0.02 and len(calls) == 2
    with service.store._connect() as db:
        records = db.execute(
            "SELECT committed,uncertain FROM spend_ledger WHERE experiment_id=?", (identifier,)
        ).fetchall()
        assert len(records) == 2 and all(
            row["committed"] == 0.01 and not row["uncertain"] for row in records
        )


def test_unknown_live_cost_blocks_remaining_calls(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(protocol(live=True))["id"]
    calls = []

    def respond(*args):
        calls.append(1)
        return Submission(output=None, error="Provider timeout")

    monkeypatch.setattr("cvals.judge_calibration.submit", respond)
    with pytest.raises(ValueError, match="Unknown"):
        service.run(identifier)
    assert len(calls) == 1 and service.get(identifier)["state"]["budget_uncertain"]
    with pytest.raises(ValueError, match="Unknown"):
        service.run(identifier)
    assert len(calls) == 1


def test_running_call_survives_restart_without_reexecution(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    service = JudgeCalibration(settings)
    identifier = service.create(protocol(live=True))["id"]
    claimed = service._claim(identifier, "dev")
    assert claimed is not None and claimed[0]["status"] == "running"
    restarted = JudgeCalibration(settings)
    monkeypatch.setattr("cvals.judge_calibration.submit", lambda *a: pytest.fail("Duplicate call"))
    assert restarted.run(identifier)["statuses"]["running"] == 1
    result = restarted.mark_interrupted(identifier)
    assert (
        result["state"]["budget_uncertain"]
        and result["committed_usd"] == claimed[0]["committed_usd"]
    )
    assert result["statuses"]["error"] == 1


def test_html_escapes_untrusted_text(tmp_path):
    report = {
        "name": "<script>alert(1)</script>",
        "analysis_type": "heldout",
        "protocol_hash": "abc",
        "analysis": {"value": "<img src=x onerror=alert(1)>"},
    }
    path = tmp_path / "report.html"
    write_report(report, path)
    text = path.read_text()
    assert "<script>" not in text and "<img src" not in text and "&lt;script&gt;" in text


def test_cli_exports_without_reference_leak(tmp_path, monkeypatch):
    from cvals.cli import app

    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path / "workspace"))
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol()))
    runner = CliRunner()
    result = runner.invoke(app, ["judges", "create", str(path)])
    assert result.exit_code == 0, result.output
    assert "SECRET" not in result.output
    exported = runner.invoke(app, ["judges", "requests", json.loads(result.output)["id"]])
    assert exported.exit_code == 0, exported.output
    assert "SECRET" not in exported.output and len(json.loads(exported.output)["jobs"]) == 2


def test_renaming_identical_task_does_not_bypass_holdout():
    value = protocol()
    value["items"][1]["task"] = value["items"][0]["task"]
    with pytest.raises(ValueError, match="Identical public tasks"):
        CalibrationProtocol.model_validate(value)


def test_a_task_cannot_change_declared_family():
    value = protocol()
    value["items"][1]["partition"] = "dev"
    value["items"][1]["task_id"] = value["items"][0]["task_id"]
    with pytest.raises(ValueError, match="one declared family"):
        CalibrationProtocol.model_validate(value)


def test_duplicate_json_choice_is_invalid():
    rubric = CalibrationProtocol.model_validate(protocol()).rubric
    result = normalize_response(
        '{"preference":"A","preference":"B","rationale":"ambiguous"}', ["A", "B"], rubric
    )
    assert result["status"] == "invalid"


def test_corrupted_presentation_order_rejected(study):
    service, identifier = study
    with service.store._connect(write=True) as db:
        row = db.execute(
            "SELECT id,data FROM judge_jobs WHERE campaign_id=? LIMIT 1", (identifier,)
        ).fetchone()
        value = json.loads(row["data"])
        value["order"].reverse()
        db.execute("UPDATE judge_jobs SET data=? WHERE id=?", (json.dumps(value), row["id"]))
    with pytest.raises(ValueError, match="fingerprint"):
        service.export_requests(identifier)


def test_report_is_private_and_never_overwrites(tmp_path):
    report = {"name": "Report", "analysis_type": "heldout", "protocol_hash": "abc"}
    path = tmp_path / "report.json"
    write_report(report, path)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_report({**report, "name": "Changed"}, path)
    assert json.loads(path.read_text())["name"] == "Report"


def test_human_vote_endpoint_is_frozen_and_does_not_invent_consensus(tmp_path):
    value = protocol()
    value["primary_endpoint"] = "human_votes"
    value["items"] = value["items"][1:]
    value["items"][0]["reference"].update(
        source="human_collected",
        label=None,
        provenance="Declared public reference panel.",
        reviewer_labels=[
            {"reviewer_id": "reader-1", "label": "A"},
            {"reviewer_id": "reader-2", "label": "B"},
        ],
    )
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(value)["id"]
    service.release_holdout(identifier, ["judge-1"], "No tuning; predeclared judge")
    successful_import(service, identifier, "holdout")
    report = service.analyze(identifier)
    assert report["primary_endpoint"] == "human_votes"
    assert report["analysis"]["judges"][0]["primary"]["estimate"] == 0.5
    assert report["analysis"]["judges"][0]["agreement"]["raw_agreement"] is None


def test_primary_contrasts_require_declared_judges_and_retained_selection(tmp_path):
    value = protocol()
    value["items"] = value["items"][1:]
    value["judges"].append({"id": "other", "model": "second", "revision": "1"})
    value["primary_contrasts"] = [{"left": "judge-1", "right": "other"}]
    value["planned_comparisons"] = 1
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(value)["id"]
    with pytest.raises(ValueError, match="primary contrast"):
        service.release_holdout(identifier, ["judge-1"], "Drop a planned contrast")
    service.release_holdout(identifier, ["judge-1", "other"], "Keep planned contrast")
    successful_import(service, identifier, "holdout")
    report = service.analyze(identifier)
    assert len(report["primary_contrasts"]) == 1
    assert report["primary_contrasts"][0]["difference"]["estimate"] == 0


def test_workspace_cap_is_shared_with_judge_calls(tmp_path, monkeypatch):
    from cvals.spend_limits import SpendLimits

    settings = Settings(tmp_path)
    service = JudgeCalibration(settings)
    identifier = service.create(protocol(live=True))["id"]
    SpendLimits(settings).configure(lifetime_usd=0)
    monkeypatch.setattr(
        "cvals.judge_calibration.submit", lambda *a: pytest.fail("Workspace cap bypass")
    )
    assert service.run(identifier)["statuses"]["budget_exceeded"] == 2


def test_invalid_live_response_retains_actual_usage(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(protocol(live=True))["id"]
    monkeypatch.setattr(
        "cvals.judge_calibration.submit",
        lambda *a: Submission(
            output={"preference": "unknown", "rationale": "invalid"},
            cost_usd=0.01,
            input_tokens=10,
            output_tokens=5,
        ),
    )
    result = service.run(identifier)
    assert result["statuses"]["invalid"] == 2 and result["known_cost_usd"] == 0.02
    with service.store._connect() as db:
        jobs = service._jobs(db, identifier)
    assert all(job["result"]["input_tokens"] == 10 for job in jobs if job["status"] == "invalid")


def test_recursive_json_response_is_retained_as_invalid():
    rubric = CalibrationProtocol.model_validate(protocol()).rubric
    value = "[" * 2000 + "0" + "]" * 2000
    assert normalize_response(value, ["A", "B"], rubric)["status"] == "invalid"


@pytest.mark.parametrize("field", ["cost_usd", "reservation_usd", "duration_ms"])
def test_boolean_accounting_is_not_numeric_evidence(field):
    from cvals.judge_calibration_models import ImportedObservation

    with pytest.raises(ValueError):
        ImportedObservation.model_validate({"job_id": "planned", field: False})


def test_cancellation_keeps_active_reservation_and_stops_queued_work(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path))
    identifier = service.create(protocol(live=True))["id"]
    claimed = service._claim(identifier, "dev")
    assert claimed is not None
    result = service.cancel(identifier)
    assert result["statuses"] == {"running": 1, "missing": 3}
    assert result["committed_usd"] == claimed[0]["committed_usd"]
    monkeypatch.setattr("cvals.judge_calibration.submit", lambda *a: pytest.fail("Cancelled call"))
    assert service.run(identifier)["state"]["cancel_requested"]


def test_report_does_not_turn_missing_billing_into_zero_spend(study):
    service, identifier = study
    request = service.export_requests(identifier)["jobs"][0]
    service.import_observations(
        identifier, [{"job_id": request["job_id"], "error": "Unknown external charge"}]
    )
    result = service.analyze(identifier, "dev")
    assert result["accounting"]["unknown_cost_judgments"] == 1
    assert result["accounting"]["billing_uncertain"] is True
    assert result["accounting"]["known_cost_usd"] == 0


@pytest.mark.parametrize("context", [None, "", "  ", {}, []])
def test_guided_judge_requires_meaningful_frozen_public_evidence(context):
    value = protocol()
    value["judges"][0]["prompt_variant"] = "reference_context"
    value["items"][0]["reference_context"] = context
    with pytest.raises(ValueError, match="public evidence"):
        CalibrationProtocol.model_validate(value)


def test_cli_import_rejects_duplicate_preference_before_persisting(study, tmp_path):
    from cvals.cli import app

    service, identifier = study
    job = service.export_requests(identifier)["jobs"][0]["job_id"]
    path = tmp_path / "ambiguous-observation.json"
    path.write_text(
        '[{"job_id":'
        + json.dumps(job)
        + ',"response":{"preference":"A","preference":"B","rationale":"Ambiguous"},'
        '"cost_usd":0}]'
    )
    result = CliRunner().invoke(
        app,
        ["judges", "import", identifier, str(path)],
        env={"EVERVAL_DATA_DIR": str(service.settings.data_dir)},
    )
    assert result.exit_code != 0 and "repeated object keys" in result.output
    assert service.get(identifier)["imported_judgments"] == 0
