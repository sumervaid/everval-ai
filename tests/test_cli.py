import json

from typer.testing import CliRunner

from cvals import __version__
from cvals.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == __version__


def test_init_and_fixture_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("CVALS_DATA_DIR", str(tmp_path / "workspace"))
    result = runner.invoke(app, ["init", "--starters"])
    assert result.exit_code == 0, result.output
    collections = json.loads(runner.invoke(app, ["collections", "list"]).output)
    assert len(collections) >= 3
    docs = next(item for item in collections if item["domain"] == "documents")
    systems = json.loads(runner.invoke(app, ["systems", "list"]).output)
    reference = next(item for item in systems if item["kind"] == "reference")
    result = runner.invoke(app, ["run", docs["id"], "--system", reference["id"]])
    assert result.exit_code == 0, result.output
    experiment = json.loads(result.output)
    assert experiment["status"] == "completed"
    assert all(trial["passed"] for trial in experiment["trials"])
    result = runner.invoke(app, ["replay", experiment["id"]])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total_trials"] == len(docs["scenarios"])
    destination = tmp_path / "report.html"
    result = runner.invoke(
        app,
        [
            "experiments",
            "export",
            experiment["id"],
            "--output",
            str(destination),
            "--format",
            "html",
        ],
    )
    assert result.exit_code == 0, result.output
    assert destination.is_file()
    backup = tmp_path / "backup.sqlite3"
    result = runner.invoke(app, ["backup", "--output", str(backup)])
    assert result.exit_code == 0, result.output
    assert backup.stat().st_size > 0


def test_remote_bind_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CVALS_DATA_DIR", str(tmp_path / "workspace"))
    monkeypatch.delenv("CVALS_TOKEN", raising=False)
    monkeypatch.delenv("EVERVAL_TOKEN", raising=False)
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 1
    assert "EVERVAL_TOKEN" in result.output


def test_cli_reviews_schedules_and_due_worker(tmp_path, monkeypatch):
    from cvals.config import Settings
    from cvals.store import Store

    monkeypatch.setenv("CVALS_DATA_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr("cvals.sandbox.cleanup_sandboxes", lambda settings: {})
    assert runner.invoke(app, ["init", "--starters"]).exit_code == 0
    result = runner.invoke(app, ["run", "starter-documents", "-s", "fixture-reference"])
    assert result.exit_code == 0, result.output
    experiment = json.loads(result.output)
    experiment_id = experiment["id"]
    trial_id = experiment["trials"][0]["id"]
    review = runner.invoke(
        app,
        [
            "reviews",
            "add",
            experiment_id,
            trial_id,
            "--reviewer",
            "Local reviewer",
            "--dimension",
            "clarity",
            "--score",
            "0.8",
            "--rationale",
            "The response cites the governing policy.",
        ],
    )
    assert review.exit_code == 0, review.output
    value = json.loads(review.output)
    assert value["identity_assurance"] == "self-reported label"
    assert value["score"] == 0.8
    shown = json.loads(runner.invoke(app, ["experiments", "show", experiment_id]).output)
    assert shown["human_reviews"][0]["id"] == value["id"]
    assert all(trial["passed"] for trial in shown["trials"])
    result = runner.invoke(
        app, ["schedules", "create", experiment_id, "--name", "Daily policy checks", "--paused"]
    )
    assert result.exit_code == 0, result.output
    schedule = json.loads(result.output)
    assert schedule["enabled"] is False
    assert schedule["interval_minutes"] == 1440
    result = runner.invoke(app, ["schedules", "resume", schedule["id"]])
    assert result.exit_code == 0, result.output
    store = Store(Settings.from_env())
    with store._connect(write=True) as connection:
        connection.execute(
            "UPDATE evaluation_schedules SET next_run_epoch=0 WHERE id=?", (schedule["id"],)
        )
    result = runner.invoke(app, ["worker", "--once"])
    assert result.exit_code == 0, result.output
    occurrence_id = json.loads(result.output)["scheduled"][0]
    repeated = store.get_experiment(occurrence_id)
    assert repeated["status"] == "completed"
    assert repeated["snapshot"]["collection"] == shown["snapshot"]["collection"]
    result = runner.invoke(app, ["schedules", "pause", schedule["id"]])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["enabled"] is False


def test_task_export_excludes_grading_material(tmp_path, monkeypatch):
    monkeypatch.setenv("CVALS_DATA_DIR", str(tmp_path / "workspace"))
    assert runner.invoke(app, ["init", "--starters"]).exit_code == 0
    path = tmp_path / "tasks.json"
    result = runner.invoke(
        app, ["collections", "tasks", "starter-documents", "--output", str(path)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(path.read_text())
    assert payload["collection_hash"]
    assert payload["scenarios"]
    for scenario in payload["scenarios"]:
        assert set(scenario) == {"id", "name", "domain", "instruction", "inputs"}
    refused = runner.invoke(
        app, ["collections", "tasks", "starter-documents", "--output", str(path)]
    )
    assert refused.exit_code == 1
    assert json.loads(path.read_text()) == payload
