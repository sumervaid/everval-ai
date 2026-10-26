"""Source CLI workflows use the same conversations, scheduler and report receipts."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cvals.cli import app
from cvals.config import Settings
from cvals.models import Check, Collection, Scenario
from cvals.store import Store

runner = CliRunner()


def workspace(tmp_path: Path, monkeypatch) -> Store:
    path = tmp_path / "workspace"
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(path))
    monkeypatch.setenv("CVALS_DATA_DIR", str(path))
    monkeypatch.setattr(
        "cvals.runtime.ensure_worker",
        lambda settings: (_ for _ in ()).throw(AssertionError("No background worker in this test")),
    )
    settings = Settings(path)
    settings.prepare()
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    store.save_collection(
        Collection(
            id="support-tickets",
            name="Support tickets",
            domain="data",
            scenarios=[
                Scenario(
                    id="route",
                    name="Routing",
                    domain="data",
                    instruction="Return the support category.",
                    reference_output={"category": "card"},
                    checks=[
                        Check(name="Category", kind="equals", path="category", expected="card")
                    ],
                )
            ],
        )
    )
    return store


def test_chat_help_describes_session_and_no_worker_controls():
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0, result.output
    assert "plain language" in result.output
    assert "--message" in result.output and "--session" in result.output
    assert "--no-worker" in result.output


def test_chat_cli_queues_controls_and_worker_writes_requested_reports(tmp_path, monkeypatch):
    store = workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("cvals.sandbox.cleanup_sandboxes", lambda settings: {})
    monkeypatch.setattr("cvals.environments.cleanup_environments", lambda settings: {})
    request = (
        "Compare fixture-reference and fixture-incomplete on support-tickets "
        "every 2 weeks with HTML and CSV reports"
    )
    result = runner.invoke(app, ["chat", "--message", request, "--json", "--no-worker"])
    assert result.exit_code == 0, result.output
    session = json.loads(result.output)
    assert session["status"] == "active"
    assert len(store.list_experiments()) == 1
    processed = runner.invoke(app, ["worker", "--once"])
    assert processed.exit_code == 0, processed.output
    outcome = json.loads(processed.output)
    assert outcome["processed"] is True
    assert store.get_experiment(session["experiment_id"])["status"] == "completed"
    assert {receipt["format"] for receipt in outcome["reports"]} == {"html", "csv"}
    shown = runner.invoke(
        app, ["chat", "--session", session["id"], "--message", "status", "--json", "--no-worker"]
    )
    assert shown.exit_code == 0, shown.output
    saved = json.loads(shown.output)
    assert len(saved["reports"]) == 2
    assert all(Path(receipt["path"]).is_file() for receipt in saved["reports"])
    paused = runner.invoke(
        app, ["chat", "-s", session["id"], "-m", "pause", "--json", "--no-worker"]
    )
    assert paused.exit_code == 0, paused.output
    assert json.loads(paused.output)["status"] == "paused"
    listed = runner.invoke(app, ["chat", "--list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["id"] == session["id"]
    assert len(store.list_experiments()) == 1


def test_chat_cli_followups_reuse_saved_draft_and_print_resume_command(tmp_path, monkeypatch):
    store = workspace(tmp_path, monkeypatch)
    first = runner.invoke(
        app,
        ["chat", "-m", "Compare fixture-reference and fixture-incomplete", "--json", "--no-worker"],
    )
    assert first.exit_code == 0, first.output
    session = json.loads(first.output)
    assert session["status"] == "draft"
    assert "task collection" in session["missing"]
    assert store.list_experiments() == []
    second = runner.invoke(
        app,
        [
            "chat",
            "-s",
            session["id"],
            "-m",
            "Use support-tickets every 2 weeks; draft only",
            "--no-worker",
        ],
    )
    assert second.exit_code == 0, second.output
    assert "Every 2 weeks" in second.output
    assert "Resume: everval chat --session " + session["id"] in second.output
    assert store.list_experiments() == []


def test_chat_cli_invalid_combinations_do_not_initialize_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path / "uncreated"))
    result = runner.invoke(app, ["chat", "--list", "--message", "hello"])
    assert result.exit_code == 1
    assert not (tmp_path / "uncreated").exists()
    result = runner.invoke(app, ["chat", "--json"])
    assert result.exit_code == 1
    assert "--json needs" in result.output
