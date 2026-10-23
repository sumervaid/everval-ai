from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cvals.training_ingestion import export_checkpoint, hash_checkpoint_artifact, ingest_directory


def export(tmp_path: Path, **overrides):
    weights = tmp_path / "weights.bin"
    weights.write_bytes(b"original installation control, not trained model weights")
    arguments = {
        "weights": weights,
        "step": 10,
        "system_id": "test-system",
        "expected_system_hash": "a" * 64,
        "revision": "checkpoint-10",
        "provenance": "synthetic installation control",
    }
    arguments.update(overrides)
    return export_checkpoint(tmp_path / "step-10.json", **arguments)


def test_file_hashes_bytes_without_loading_model(tmp_path: Path) -> None:
    path = tmp_path / "weights.bin"
    content = b"not a serialized model"
    path.write_bytes(content)
    assert hash_checkpoint_artifact(path) == hashlib.sha256(content).hexdigest()


def test_tree_hash_is_stable_and_binds_names_and_content(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"one")
    (tmp_path / "b").write_bytes(b"two")
    first = hash_checkpoint_artifact(tmp_path)
    assert hash_checkpoint_artifact(tmp_path) == first
    (tmp_path / "a").rename(tmp_path / "c")
    assert hash_checkpoint_artifact(tmp_path) != first
    (tmp_path / "c").rename(tmp_path / "a")
    (tmp_path / "b").write_bytes(b"three")
    assert hash_checkpoint_artifact(tmp_path) != first


def test_hash_rejects_symlinks_empty_directories_and_special_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        hash_checkpoint_artifact(tmp_path)
    target = tmp_path / "weights"
    target.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        hash_checkpoint_artifact(link)
    with pytest.raises((ValueError, OSError)):
        hash_checkpoint_artifact(tmp_path)
    link.unlink()
    import os

    target.unlink()
    os.mkfifo(target)
    with pytest.raises(ValueError):
        hash_checkpoint_artifact(target)


def test_export_is_private_idempotent_and_cannot_overwrite(tmp_path: Path) -> None:
    first = export(tmp_path)
    assert export(tmp_path) == first
    manifest = tmp_path / "step-10.json"
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert json.loads(manifest.read_text()) == first
    with pytest.raises(ValueError, match="different"):
        export(tmp_path, step=20)
    assert json.loads(manifest.read_text()) == first
    assert not list(tmp_path.glob(".checkpoint-*"))


def test_export_rejects_link_destination(tmp_path: Path) -> None:
    target = tmp_path / "untouched"
    target.write_text("do not modify")
    (tmp_path / "step-10.json").symlink_to(target)
    with pytest.raises(OSError):
        export(tmp_path)
    assert target.read_text() == "do not modify"


class Recorder:
    def __init__(self):
        self.registered = []
        self.queued = []

    def register_checkpoint(self, run_id, manifest):
        self.registered.append((run_id, manifest.step))
        return {"id": str(manifest.step)}

    def queue_checkpoint(self, run_id, checkpoint_id):
        self.queued.append((run_id, checkpoint_id))
        return {"status": "queued"}


def test_ingestion_orders_steps_and_only_queues_explicitly(tmp_path: Path) -> None:
    export(tmp_path)
    second = json.loads((tmp_path / "step-10.json").read_text())
    second["step"] = 2
    (tmp_path / "z-step-2.json").write_text(json.dumps(second))
    service = Recorder()
    result = ingest_directory(service, "run", tmp_path)
    assert service.registered == [("run", 2), ("run", 10)]
    assert service.queued == []
    assert len(result) == 2
    ingest_directory(service, "run", tmp_path, queue=True)
    assert service.queued == [("run", "2"), ("run", "10")]


@pytest.mark.parametrize(
    "bad",
    [
        '{"step":1,"step":2}',
        '{"step":NaN}',
        "[]",
        '{"unknown":true}',
    ],
)
def test_bad_batch_is_validated_before_any_registration(tmp_path: Path, bad: str) -> None:
    export(tmp_path)
    (tmp_path / "z-bad.json").write_text(bad)
    service = Recorder()
    with pytest.raises(ValueError):
        ingest_directory(service, "run", tmp_path)
    assert service.registered == []


def test_manifest_must_not_be_written_into_hashed_tree(tmp_path: Path) -> None:
    weights = tmp_path / "weights"
    weights.mkdir()
    (weights / "part").write_bytes(b"immutable")
    with pytest.raises(ValueError, match="outside"):
        export_checkpoint(
            weights / "checkpoint.json",
            weights=weights,
            step=1,
            system_id="system",
            expected_system_hash="a" * 64,
            revision="one",
            provenance="synthetic",
        )
    assert not (weights / "checkpoint.json").exists()


def test_hash_propagates_unreadable_subtree(tmp_path: Path, monkeypatch) -> None:
    def walk(path, *, followlinks, onerror):
        onerror(PermissionError("unreadable checkpoint subtree"))
        yield str(path), [], []

    monkeypatch.setattr("cvals.training_ingestion.os.walk", walk)
    with pytest.raises(PermissionError, match="unreadable"):
        hash_checkpoint_artifact(tmp_path)


def test_hash_rechecks_previously_hashed_file(tmp_path: Path, monkeypatch) -> None:
    import cvals.training_ingestion as ingestion

    (tmp_path / "a").write_bytes(b"old")
    (tmp_path / "b").write_bytes(b"later")
    original = ingestion._hash_file

    def mutate_when_b_is_read(path):
        if path.name == "b":
            (tmp_path / "a").write_bytes(b"modified in place")
        return original(path)

    monkeypatch.setattr(ingestion, "_hash_file", mutate_when_b_is_read)
    with pytest.raises(ValueError, match="changed"):
        hash_checkpoint_artifact(tmp_path)


def test_hash_detects_atomically_replaced_file(tmp_path: Path, monkeypatch) -> None:
    import cvals.training_ingestion as ingestion

    original_file = tmp_path / "weights"
    original_file.write_bytes(b"original")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"replacement")
    original_fstat = ingestion.os.fstat
    calls = 0

    def replace_before_final_fstat(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement.replace(original_file)
        return original_fstat(descriptor)

    monkeypatch.setattr(ingestion.os, "fstat", replace_before_final_fstat)
    with pytest.raises(ValueError, match="changed"):
        hash_checkpoint_artifact(original_file)


def test_queued_ingestion_starts_worker_before_partial_batch_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from cvals.cli import app

    events = []

    class Service:
        def __init__(self, settings):
            self.settings = settings

    def partial(service, run_id, directory, *, queue):
        assert queue
        events.append("queued-first")
        raise ValueError("second checkpoint would exceed budget")

    monkeypatch.setattr("cvals.posttraining.PostTraining", Service)
    monkeypatch.setattr("cvals.runtime.ensure_worker", lambda settings: events.append("worker"))
    monkeypatch.setattr("cvals.training_ingestion.ingest_directory", partial)
    result = CliRunner().invoke(app, ["training", "ingest", "run", str(tmp_path), "--queue"])
    assert result.exit_code == 1
    assert events == ["worker", "queued-first"]


def test_schema_export_does_not_initialize_workspace(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from cvals.cli import app

    destination = tmp_path / "not-created"
    monkeypatch.setenv("EVERVAL_DATA_DIR", str(destination))
    result = CliRunner().invoke(app, ["training", "schema", "run"])
    assert result.exit_code == 0
    assert "training_run_id" in json.loads(result.stdout)["properties"]
    assert not destination.exists()
