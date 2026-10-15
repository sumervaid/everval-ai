"""Paid acceptance admission fails closed before any provider attempt."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import System, content_hash
from cvals.store import Store


def load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / (name + ".py")
    spec = importlib.util.spec_from_file_location("acceptance_guard_" + name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(module: ModuleType, monkeypatch: pytest.MonkeyPatch, root: Path, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", [module.__file__, "--output-dir", str(root), *args])
    module.main()


@pytest.fixture(autouse=True)
def forbid_provider_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Acceptance test reached provider execution")

    monkeypatch.setattr(Engine, "run_experiment", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "acceptance-fixture-key-never-sent")


def assert_no_paid_admission(root: Path) -> None:
    with sqlite3.connect(root / "workspace" / "workspace.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM spend_ledger").fetchone()[0] == 0


@pytest.mark.parametrize("script_name", ["openai_live", "openai_workflows"])
def test_tampered_audit_blocks_paid_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script_name: str
) -> None:
    root = tmp_path / "campaign"
    invoke(load_script("openai_live"), monkeypatch, root, "--phase", "plan")
    settings = Settings(data_dir=root / "workspace")
    assert AuditLog(settings).verify()["valid"]
    with sqlite3.connect(root / "workspace" / "workspace.sqlite3") as connection:
        connection.execute("DROP TRIGGER workspace_audit_no_update")
        connection.execute(
            "UPDATE workspace_audit SET details=? WHERE sequence=1",
            ('{"tampered":true}',),
        )
    assert not AuditLog(settings).verify()["valid"]

    args = ("--phase", "pilot") if script_name == "openai_live" else ("--execute",)
    with pytest.raises(SystemExit, match="Invalid audit chain"):
        invoke(load_script(script_name), monkeypatch, root, *args)

    assert_no_paid_admission(root)
    assert not (root / "pilot-started.json").exists()
    assert not (root / "workflows-started.json").exists()


def test_workflow_marker_hashes_the_persisted_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    invoke(load_script("openai_live"), monkeypatch, root, "--phase", "plan")
    workflow = load_script("openai_workflows")
    invoke(workflow, monkeypatch, root)
    plan_path = root / "workflows-plan.json"
    stored_bytes = plan_path.read_bytes()
    persisted = json.loads(stored_bytes)

    # A later execute invocation regenerates metadata, but must attest the plan
    # that was retained for review. Stop on a failed local control before admission.
    monkeypatch.setattr(workflow, "utc_now", lambda: "2099-01-01T00:00:00+00:00")
    monkeypatch.setattr(
        workflow, "validate_collection", lambda *args: {"valid": False, "scenarios": []}
    )
    with pytest.raises(AssertionError, match="positive and negative controls"):
        invoke(workflow, monkeypatch, root, "--execute")

    marker = json.loads((root / "workflows-started.json").read_text())
    assert marker["plan_hash"] == content_hash(persisted)
    assert plan_path.read_bytes() == stored_bytes
    assert json.loads((root / "workflows-results.json").read_text())["status"] == "failed"
    assert_no_paid_admission(root)


def test_changed_frozen_workflow_budget_is_rejected_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    invoke(load_script("openai_live"), monkeypatch, root, "--phase", "plan")
    workflow = load_script("openai_workflows")
    invoke(workflow, monkeypatch, root)
    path = root / "workflows-plan.json"
    plan = json.loads(path.read_text())
    plan["optimizer_budget_usd"] -= 1
    path.write_text(json.dumps(plan))

    with pytest.raises(SystemExit, match="Frozen workflow plan changed"):
        invoke(workflow, monkeypatch, root, "--execute")
    assert_no_paid_admission(root)
    assert not (root / "workflows-started.json").exists()


def test_completed_pilot_with_failed_audit_does_not_admit_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cvals.sandbox

    root = tmp_path / "campaign"
    live = load_script("openai_live")
    invoke(live, monkeypatch, root, "--phase", "plan")
    manifest = json.loads((root / "manifest.json").read_text())
    image = "acceptance-image@sha256:" + "a" * 64
    monkeypatch.setattr(cvals.sandbox, "_image", lambda settings: image)
    (root / "controls-results.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "cohort_hash": manifest["cohort_hash"],
                "sandbox_image": image,
                "installed_code_sha256": live.installed_code_hash(),
            }
        )
    )
    (root / "pilot-results.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "all_trials_completed": True,
                "audit": {"valid": False},
            }
        )
    )
    assert AuditLog(Settings(data_dir=root / "workspace")).verify()["valid"]
    with pytest.raises(SystemExit, match="Pilot did not complete cleanly"):
        invoke(live, monkeypatch, root, "--phase", "benchmark")
    assert_no_paid_admission(root)
    assert not (root / "benchmark-started.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "different-model"),
        ("base_url", "https://example.test/v1"),
        ("input_cost_per_million", 3.0),
        ("output_cost_per_million", 11.0),
        ("additional_instructions", "Unreviewed instructions"),
        ("api_key_env", "UNREVIEWED_API_KEY"),
    ],
)
def test_saved_system_changes_cannot_override_frozen_configuration(
    tmp_path: Path, field: str, value: object
) -> None:
    root = tmp_path / "campaign"
    store = Store(Settings(data_dir=root / "workspace"))
    store.init()
    frozen = System(
        id="frozen-provider",
        name="Frozen provider",
        kind="http",
        model="original-model",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        input_cost_per_million=2.5,
        output_cost_per_million=10,
    )
    store.save_system(frozen)
    workflow = load_script("openai_workflows")
    workflow.assert_frozen_systems(store, [frozen])
    changed = System.model_validate({**frozen.model_dump(), field: value})
    store.save_system(changed)
    with pytest.raises(ValueError, match="Stored system differs from the frozen OpenAI plan"):
        workflow.assert_frozen_systems(store, [frozen])
    assert_no_paid_admission(root)


def test_equally_priced_model_replacement_cannot_change_frozen_workflow_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    invoke(load_script("openai_live"), monkeypatch, root, "--phase", "plan")
    workflow = load_script("openai_workflows")
    invoke(workflow, monkeypatch, root)
    plan_path = root / "workflows-plan.json"
    frozen_plan_bytes = plan_path.read_bytes()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert json.loads(frozen_plan_bytes)["models_hash"] == content_hash(manifest["models"])

    # Align mutable storage and the shared manifest, preserving all declared
    # prices, IDs, request limits, and the length of the model identifier.
    original = manifest["models"][0]
    changed = {**original, "model": original["model"][:-1] + "x"}
    manifest["models"][0] = changed
    manifest_path.write_text(json.dumps(manifest))
    store = Store(Settings(data_dir=root / "workspace"))
    store.save_system(System.model_validate(changed))
    workflow.assert_frozen_systems(
        store, [System.model_validate(model) for model in manifest["models"]]
    )

    with pytest.raises(SystemExit, match="Frozen workflow plan changed: model"):
        invoke(workflow, monkeypatch, root, "--execute")
    assert plan_path.read_bytes() == frozen_plan_bytes
    assert_no_paid_admission(root)
    assert not (root / "workflows-started.json").exists()


def test_legacy_workflow_plan_requires_a_new_campaign_without_rewriting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    invoke(load_script("openai_live"), monkeypatch, root, "--phase", "plan")
    workflow = load_script("openai_workflows")
    invoke(workflow, monkeypatch, root)
    path = root / "workflows-plan.json"
    legacy = json.loads(path.read_text())
    legacy.pop("models_hash")
    path.write_text(json.dumps(legacy))
    retained_bytes = path.read_bytes()

    with pytest.raises(SystemExit, match="predates model binding"):
        invoke(workflow, monkeypatch, root)
    assert path.read_bytes() == retained_bytes
    assert_no_paid_admission(root)
    assert not (root / "workflows-started.json").exists()


def test_planned_cohort_hash_matches_persisted_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    invoke(load_script("openai_live"), monkeypatch, root, "--phase", "plan")
    manifest = json.loads((root / "manifest.json").read_text())
    store = Store(Settings(data_dir=root / "workspace"))
    stored = store.get_collection(manifest["cohort_id"])
    assert content_hash(stored) == manifest["cohort_hash"]
    for scenario in stored["scenarios"]:
        for check in scenario["checks"]:
            assert type(check["weight"]) is float
            assert type(check["tolerance"]) is float
    assert_no_paid_admission(root)
