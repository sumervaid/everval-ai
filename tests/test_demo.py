from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
from typer.testing import CliRunner

from cvals.config import Settings
from cvals.demo import run_demo
from cvals.demo_cli import register
from cvals.models import content_hash


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Demo attempted a network or process operation")


@pytest.mark.parametrize("example", ["all", "coding", "documents", "data"])
def test_demo_uses_real_retained_evidence_without_network_or_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, example: str
) -> None:
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    active = tmp_path / "active"
    result = run_demo(Settings(active), example=example)
    assert result["provider_calls"] == 0
    assert result["provider_cost_usd"] == 0
    assert result["audit"]["valid"]
    assert not (active / "workspace.sqlite3").exists()
    assert Path(result["workspace"]).is_relative_to(active / "demos")
    assert [row["report"]["systems"][0]["pass_rate"] for row in result["runs"]] == [1.0, 0.0, 1.0]
    assert all(
        not reproduction["passed"] and not reproduction["uses_provider"]
        for reproduction in result["reproductions"]
    )
    assert result["comparisons"]["change"]["compatible"]
    assert (
        result["comparisons"]["candidate"]["comparisons"][0]["conclusion"]
        == "fixture controls only"
    )
    assert result["proposal"]["deployed"] is False
    summary = json.loads(Path(result["artifacts"]["summary"]).read_text())
    evidence = json.loads(Path(result["artifacts"]["private_evidence"]).read_text())
    assert summary["evidence_digest"] == content_hash(evidence)
    serialized = json.dumps(summary)
    for private_key in ("reference_output", "instruction", "checks", "negative_controls", "output"):
        assert f'"{private_key}"' not in serialized
    assert "installation controls" in Path(result["artifacts"]["badge"]).read_text()
    assert Path(result["artifacts"]["private_evidence"]).stat().st_mode & 0o777 == 0o600


def test_demo_never_reuses_or_overwrites_a_workspace(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    with pytest.raises(ValueError, match="isolated"):
        run_demo(Settings(active), workspace=active)
    target = tmp_path / "occupied"
    target.mkdir()
    marker = target / "keep"
    marker.write_text("retained")
    with pytest.raises(ValueError, match="new or empty"):
        run_demo(Settings(active), workspace=target)
    assert marker.read_text() == "retained"
    with pytest.raises(ValueError, match="requires --example coding"):
        run_demo(Settings(active), example="all", sandbox=True)


def test_demo_command_json_and_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path))
    app = typer.Typer()
    register(app)
    app.command("other")(lambda: None)
    runner = CliRunner()
    result = runner.invoke(app, ["demo", "--example", "data", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["provider_calls"] == 0
    result = runner.invoke(app, ["demo", "--example", "documents"])
    assert result.exit_code == 0, result.output
    assert "Installation controls only" in result.stdout
    assert "Provider calls: 0" in result.stdout


def test_explicit_evidence_summary_has_no_private_content(tmp_path: Path) -> None:
    from cvals.demo import export_summary

    result = run_demo(Settings(tmp_path / "active"), example="data")
    settings = Settings(Path(result["workspace"]))
    export = export_summary(settings, result["runs"][0]["id"], tmp_path / "share")
    summary = json.loads(Path(export["summary"]).read_text())
    assert export["uploaded"] is False
    assert summary["fixture_controls_present"]
    assert not summary["recorded_submissions_present"]
    assert summary["evidence_kind"] == "installation controls"
    assert summary["systems"][0]["passed"] == 1
    assert summary["systems"][0]["cost_complete"]
    serialized = json.dumps(summary)
    for private_key in (
        "name",
        "reference_output",
        "instruction",
        "checks",
        "negative_controls",
        "output",
        "trace",
        "snapshot",
    ):
        assert f'"{private_key}"' not in serialized
    badge = Path(export["badge"]).read_text()
    assert 'href="summary.json"' in badge
    assert summary["evidence_hash"] in badge
    with pytest.raises(FileExistsError):
        export_summary(settings, result["runs"][0]["id"], tmp_path / "share")


def test_summary_replay_unknown_cost_stays_unknown(tmp_path: Path) -> None:
    from cvals.demo import builtin_pack, export_summary
    from cvals.engine import Engine
    from cvals.models import ExperimentCreate, ReplaySubmission, System
    from cvals.store import Store

    settings = Settings(tmp_path / "workspace")
    store = Store(settings)
    store.init()
    collection = builtin_pack("data").collection
    collection.scenarios[0].instruction = "PRIVATE INSTRUCTION"
    saved = store.save_collection(collection)
    system = System(
        id="recorded",
        name="PRIVATE CUSTOMER NAME",
        kind="replay",
        collection_hash=content_hash(saved),
        submissions={
            "demo-data": ReplaySubmission(
                output=collection.scenarios[0].reference_output, trace=[{"secret": "PRIVATE TOKEN"}]
            )
        },
    )
    store.save_system(system)
    queued = store.create_experiment(
        ExperimentCreate(
            name="PRIVATE RUN NAME", collection_id=collection.id, system_ids=[system.id]
        )
    )
    Engine(store, settings).run_experiment(queued["id"])
    export = export_summary(settings, queued["id"], tmp_path / "summary")
    raw = Path(export["summary"]).read_text()
    assert "PRIVATE" not in raw
    summary = json.loads(raw)
    assert summary["recorded_submissions_present"]
    assert summary["systems"][0]["cost_usd"] is None
    assert summary["systems"][0]["cost_complete"] is False
