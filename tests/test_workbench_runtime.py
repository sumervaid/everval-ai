"""Durability and audit boundaries of the terminal application service."""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import time

import pytest
from typer.testing import CliRunner

from cvals.audit import AuditLog
from cvals.cli import app
from cvals.config import Settings
from cvals.engine import Engine
from cvals.jobs import Jobs
from cvals.models import Check, Collection, Scenario
from cvals.runtime import worker_lock, worker_running
from cvals.workbench import Workbench


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "workspace")


def test_audit_rolls_back_with_business_transaction(settings):
    audit = AuditLog(settings)
    with pytest.raises(RuntimeError), audit.store._connect(write=True) as connection:
        audit.record("project.connected", "test", {"source": "local"}, connection=connection)
        raise RuntimeError("rollback")
    assert audit.list() == []
    with audit.store._connect(write=True) as connection:
        audit.record("project.connected", "test", {"source": "local"}, connection=connection)
    assert audit.verify()["valid"]
    assert audit.verify()["count"] == 1


def test_audit_concurrent_append_and_independent_anchor(settings):
    audit = AuditLog(settings)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: audit.record("test", str(index), {}), range(16)))
    verification = audit.verify()
    assert verification["valid"]
    assert verification["count"] == 16
    head = verification["head"]
    assert audit.verify(head)["valid"]
    assert not audit.verify("f" * 64)["valid"]
    with audit.store._connect(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE workspace_audit SET resource='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM workspace_audit")
    # A privileged local edit is detected against the retained chain/digest.
    with audit.store._connect(write=True) as connection:
        connection.execute("DROP TRIGGER workspace_audit_no_update")
        connection.execute("UPDATE workspace_audit SET actor='changed' WHERE sequence=2")
    assert not audit.verify(head)["valid"]


def test_audit_redacts_structured_credentials(settings):
    audit = AuditLog(settings)
    audit.record(
        "system.saved",
        "test",
        {
            "api_key": "CANARY",
            "nested": {"password": "CANARY"},
            "input_tokens": 10,
            "api_key_env": "MODEL_KEY",
        },
    )
    assert "CANARY" not in json.dumps(audit.list())
    assert audit.list()[0]["details"]["input_tokens"] == 10
    assert audit.verify()["valid"]


def test_repository_job_survives_application_recreation(settings, monkeypatch):
    from cvals.projects import Projects

    monkeypatch.setattr(Projects, "discover", lambda self, **kwargs: {"candidates": [], **kwargs})
    jobs = Jobs(settings)
    queued = jobs.enqueue("discover", {"project_id": "project", "limit": 2})
    assert Jobs(settings).get(queued["id"])["status"] == "queued"
    assert Jobs(settings).run_pending_once()
    result = Jobs(settings).wait(queued["id"], timeout=1)
    assert result["project_id"] == "project"
    assert result["job_id"] == queued["id"]
    assert not jobs.run_pending_once()
    assert AuditLog(settings).verify()["valid"]


def test_expired_repository_job_requires_explicit_retry(settings):
    jobs = Jobs(settings)
    queued = jobs.enqueue("validate", {"project_id": "p", "candidate_id": "c"})
    with jobs.store._connect(write=True) as connection:
        connection.execute(
            "UPDATE workbench_jobs SET status='running',owner='lost',lease_until=? WHERE id=?",
            (time.time() - 1, queued["id"]),
        )
    assert not jobs.run_pending_once()
    recovered = jobs.get(queued["id"])
    assert recovered["status"] == "error"
    assert "interrupted" in recovered["error"]
    with pytest.raises(ValueError, match="interrupted"):
        jobs.wait(queued["id"])


def test_live_job_lease_prevents_duplicate_claim(settings):
    jobs = Jobs(settings)
    queued = jobs.enqueue("discover", {"project_id": "p"})
    with jobs.store._connect(write=True) as connection:
        connection.execute(
            "UPDATE workbench_jobs SET status='running',owner='active',lease_until=? WHERE id=?",
            (time.time() + 100, queued["id"]),
        )
    assert not Jobs(settings).run_pending_once()


def test_job_failure_omits_unexpected_exception_contents(settings, monkeypatch):
    from cvals.projects import Projects

    def broken(*args, **kwargs):
        raise RuntimeError("private credential CANARY")

    monkeypatch.setattr(Projects, "discover", broken)
    jobs = Jobs(settings)
    queued = jobs.enqueue("discover", {"project_id": "p"})
    assert jobs.run_pending_once()
    failure = jobs.get(queued["id"])
    assert "CANARY" not in json.dumps(failure)
    assert failure["status"] == "error"
    assert "RuntimeError" in failure["error"]


def test_workspace_worker_lock_is_exclusive_and_reusable(settings):
    first = worker_lock(settings)
    assert first is not None
    try:
        assert worker_running(settings)
        assert worker_lock(settings) is None
    finally:
        first.close()
    assert not worker_running(settings)
    second = worker_lock(settings)
    assert second is not None
    second.close()


def test_workbench_evidence_export_and_review_are_real(settings, monkeypatch):
    monkeypatch.setattr("cvals.workbench.ensure_worker", lambda _: {"running": True})
    workbench = Workbench(settings)
    scenario = Scenario(
        name="Add",
        domain="data",
        instruction="Add 2 and 3",
        inputs={"rows": [2, 3]},
        checks=[Check(name="Total", kind="numeric", path="total", expected=5)],
        reference_output={"total": 5},
    )
    collection = Collection(name="Arithmetic", domain="data", scenarios=[scenario])
    workbench.store.save_collection(collection)
    queued = workbench.run(collection.id, ["fixture-reference"], "Control")
    assert queued["status"] == "queued"
    Engine(workbench.store, settings).run_experiment(queued["id"])
    evidence = workbench.experiment(queued["id"])
    assert evidence["trials"][0]["passed"] is True
    assert evidence["experiment"]["id"] == queued["id"]
    review = workbench.add_review(
        queued["id"], evidence["trials"][0]["id"], "Reviewer", "clarity", 0.8, "Unambiguous output"
    )
    assert review["identity_assurance"] == "self-reported label"
    from pathlib import Path

    first = Path(workbench.export(queued["id"], "json")["path"])
    second = Path(workbench.export(queued["id"], "json")["path"])
    assert first != second
    assert json.loads(first.read_text())["trials"][0]["passed"]
    assert json.loads(first.read_text())["human_reviews"][0]["id"] == review["id"]
    assert first.stat().st_mode & 0o777 == 0o600
    assert workbench.diagnostics()["audit"]["valid"]


def test_everval_cli_initialization_and_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path / "workspace"))
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--starters"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["installed_collections"] == 4
    result = runner.invoke(app, ["projects", "list"])
    assert result.exit_code == 0 and json.loads(result.output) == []
    result = runner.invoke(app, ["audit", "verify"])
    assert result.exit_code == 0 and json.loads(result.output)["valid"]


def test_job_queue_rejects_credentials_and_mutable_preparation(settings):
    jobs = Jobs(settings)
    with pytest.raises(ValueError, match="unsupported fields"):
        jobs.enqueue("discover", {"project_id": "p", "api_key": "CANARY"})
    with pytest.raises(ValueError, match="immutable"):
        jobs.enqueue(
            "prepare", {"project_id": "p", "revision": "main", "base_image": "python:latest"}
        )
    with pytest.raises(ValueError, match="pinned"):
        jobs.enqueue(
            "prepare", {"project_id": "p", "revision": "a" * 40, "base_image": "python:latest"}
        )
    assert jobs.list() == []
    assert "CANARY" not in json.dumps(jobs.audit.list())


def test_job_lists_keep_large_results_in_detail_only(settings):
    jobs = Jobs(settings)
    job = jobs.enqueue("discover", {"project_id": "p"})
    result = {"private_candidate_material": "large" * 100000}
    with jobs.store._connect(write=True) as connection:
        connection.execute(
            "UPDATE workbench_jobs SET status='completed',result=? WHERE id=?",
            (json.dumps(result), job["id"]),
        )
    summary = jobs.list()[0]
    assert summary["result"] is None and summary["has_result"]
    assert "private_candidate_material" not in json.dumps(summary)
    assert jobs.get(job["id"])["result"] == result


def test_failed_claim_audit_rolls_back_job(settings, monkeypatch):
    jobs = Jobs(settings)
    job = jobs.enqueue("discover", {"project_id": "p"})

    def failed(*args, **kwargs):
        raise ValueError("Audit write failed")

    monkeypatch.setattr(jobs.audit, "record", failed)
    with pytest.raises(ValueError, match="Audit write failed"):
        jobs.run_pending_once()
    assert jobs.get(job["id"])["status"] == "queued"


def test_empty_job_poll_uses_no_write_transaction(settings, monkeypatch):
    from contextlib import contextmanager

    jobs = Jobs(settings)
    original = jobs.store._connect
    writes = []

    @contextmanager
    def connect(*, write=False):
        writes.append(write)
        with original(write=write) as connection:
            yield connection

    monkeypatch.setattr(jobs.store, "_connect", connect)
    assert not jobs.run_pending_once()
    assert writes == [False]


def test_worker_health_has_no_storage_side_effects(tmp_path):
    missing = Settings(data_dir=tmp_path / "not-created")
    assert not worker_running(missing)
    assert not missing.data_dir.exists()


def test_worker_lock_rejects_symlinks(settings, tmp_path):
    settings.prepare()
    target = tmp_path / "private"
    target.write_text("do not overwrite")
    (settings.data_dir / "worker.lock").symlink_to(target)
    with pytest.raises(OSError):
        worker_lock(settings)
    assert target.read_text() == "do not overwrite"


def test_everval_settings_override_legacy_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("CVALS_DATA_DIR", str(tmp_path / "legacy"))
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path / "current"))
    monkeypatch.setenv("CVALS_ALLOW_REMOTE_PROVIDERS", "true")
    monkeypatch.setenv("EVERVAL_ALLOW_REMOTE_PROVIDERS", "false")
    monkeypatch.setenv("EVERVAL_TOKEN", "test-owner-token")
    configured = Settings.from_env()
    assert configured.data_dir == tmp_path / "current"
    assert not configured.allow_remote_providers
    assert configured.token == "test-owner-token"


def test_solver_release_export_excludes_private_data_and_preserves_binding(tmp_path, monkeypatch):
    from pathlib import Path

    from cvals.benchmarks import Benchmarks
    from cvals.models import content_hash

    monkeypatch.setenv("EVERVAL_DATA_DIR", str(tmp_path / "workspace"))
    scenario = Scenario(
        name="Total",
        domain="data",
        instruction="Add rows",
        inputs={"rows": [2, 3]},
        checks=[Check(name="Total", kind="numeric", path="total", expected=5)],
        expected={"private": "CANARY"},
        reference_output={"total": 5},
    )
    collection = Collection(name="Data", domain="data", scenarios=[scenario]).model_dump(
        mode="json"
    )
    monkeypatch.setattr(
        Benchmarks,
        "export",
        lambda self, _: {
            "collections": {"dev": collection},
            "contains_private_grader_data": True,
            "validations": [{"private": "CANARY"}],
        },
    )
    result = CliRunner().invoke(app, ["benchmarks", "export", "release-test"])
    assert result.exit_code == 0, result.output
    bundle = Path(json.loads(result.output)["path"]).read_text()
    assert "CANARY" not in bundle
    exported = json.loads(bundle)
    assert not exported["contains_private_grader_data"]
    assert exported["collections"]["dev"]["collection_hash"] == content_hash(collection)
    assert "checks" not in exported["collections"]["dev"]["scenarios"][0]
