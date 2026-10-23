from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvals.cli import app
from cvals.config import Settings
from cvals.posttraining import PostTraining
from cvals.training_demo import run_training_demo


def test_complete_synthetic_checkpoint_workflow(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("The walkthrough must not call a provider")

    monkeypatch.setattr("httpx.Client.send", forbidden)
    monkeypatch.setattr("httpx.AsyncClient.send", forbidden)
    result = run_training_demo(Settings(tmp_path / "active"), workspace=tmp_path / "demo")
    assert result["model_calls"] == result["provider_spend_usd"] == 0
    assert result["checkpoint_steps"] == [0, 10, 20]
    assert result["qualification"]["status"] == "gaps_found"
    assert result["qualification"]["automatic_trust"] is False
    assert result["mix_control"]["selected"]["detection_recall"] == 0
    assert result["mix_control"]["selected"]["reference_regressions"] == 1
    assert all(d["decision"] == "regression" for d in result["comparisons"][0]["domains"])
    assert all(d["decision"] == "inconclusive" for d in result["comparisons"][1]["domains"])
    dashboard = PostTraining(Settings(tmp_path / "demo")).dashboard(result["run_id"])
    assert len(dashboard["investigations"]) == 1
    assert len(dashboard["alerts"]) == 2
    assert dashboard["budget"]["unknown_cost_count"] == 0
    assert all(cp["status"] == "completed" for cp in dashboard["checkpoints"])
    assert Path(result["summary_path"]).is_file()


def test_demo_refuses_active_or_nonempty_workspace(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    with pytest.raises(ValueError, match="active"):
        run_training_demo(settings, workspace=tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing").write_text("retain")
    with pytest.raises(ValueError, match="empty"):
        run_training_demo(settings, workspace=occupied)
    assert (occupied / "existing").read_text() == "retain"


def test_training_cli_exposes_ingestion_and_walkthrough() -> None:
    result = CliRunner().invoke(app, ["training", "--help"])
    assert result.exit_code == 0
    for name in ("ingest", "demo", "grader-drift", "system-hash", "qualify", "validate-mix"):
        assert name in result.stdout
