"""Inspect exports preserve presented labels and evaluator separation."""

import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest
from test_judge_calibration import protocol, successful_import

from cvals import inspect_export
from cvals.config import Settings
from cvals.inspect_export import export_inspect, load_inspect_dataset
from cvals.judge_calibration import JudgeCalibration


def test_public_samples_exclude_references_and_targets_follow_order(tmp_path):
    service = JudgeCalibration(Settings(tmp_path / "workspace"))
    identifier = service.create(protocol())["id"]
    destination = tmp_path / "inspect"
    summary = export_inspect(service, identifier, "judge-1", destination)
    public = (destination / "samples.jsonl").read_text()
    assert "SECRET-GENERATOR" not in public and "SECRET-REVIEWER" not in public
    assert "SCRIPTED-SECRET-PROVENANCE" not in public
    rows = [json.loads(line) for line in public.splitlines()]
    assert all(set(row) == {"id", "input", "metadata"} for row in rows)
    refs = [
        json.loads(line) for line in (destination / "references.jsonl").read_text().splitlines()
    ]
    assert {row["target"] for row in refs} == {"A", "B"}
    assert summary["samples"] == summary["labeled_samples"] == 2
    assert (destination / "references.jsonl").stat().st_mode & 0o777 == 0o600


def test_holdout_export_requires_frozen_release(tmp_path):
    service = JudgeCalibration(Settings(tmp_path / "workspace"))
    identifier = service.create(protocol())["id"]
    destination = tmp_path / "inspect"
    with pytest.raises(ValueError, match="selection"):
        export_inspect(service, identifier, "judge-1", destination, partition="holdout")
    assert not destination.exists()
    successful_import(service, identifier)
    service.release_holdout(identifier, ["judge-1"], "Prespecified judge.")
    result = export_inspect(service, identifier, "judge-1", destination, partition="holdout")
    assert result["samples"] == 2


def test_loader_joins_only_verified_matching_references(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path / "workspace"))
    identifier = service.create(protocol())["id"]
    destination = tmp_path / "inspect"
    export_inspect(service, identifier, "judge-1", destination)
    stub = SimpleNamespace(Sample=lambda **fields: fields, MemoryDataset=list)
    monkeypatch.setitem(sys.modules, "inspect_ai.dataset", stub)
    samples = load_inspect_dataset(destination)
    assert {row["target"] for row in samples} == {"A", "B"}
    assert all("references" not in row["metadata"] for row in samples)
    with (destination / "references.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="fingerprint"):
        load_inspect_dataset(destination)


@pytest.fixture
def exported(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path / "workspace"))
    identifier = service.create(protocol())["id"]
    destination = tmp_path / "inspect"
    export_inspect(service, identifier, "judge-1", destination)
    stub = SimpleNamespace(Sample=lambda **fields: fields, MemoryDataset=list)
    monkeypatch.setitem(sys.modules, "inspect_ai.dataset", stub)
    return destination


def replace_data(directory, name, data):
    """Update the manifest as well to exercise validation beyond hash checks."""
    encoded = data.encode()
    (directory / name).write_bytes(encoded)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"][name] = hashlib.sha256(encoded).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest))


@pytest.mark.parametrize(
    ("constant", "limit"),
    [("MAX_MANIFEST_BYTES", 32), ("MAX_DATA_BYTES", 32), ("MAX_ROW_BYTES", 32), ("MAX_ROWS", 1)],
)
def test_loader_applies_file_row_and_count_bounds(exported, monkeypatch, constant, limit):
    monkeypatch.setattr(inspect_export, constant, limit)
    with pytest.raises(ValueError, match="limit|counts"):
        load_inspect_dataset(exported)


def test_row_bound_counts_blank_lines(exported, monkeypatch):
    data = (exported / "samples.jsonl").read_text() + "\n"
    replace_data(exported, "samples.jsonl", data)
    monkeypatch.setattr(inspect_export, "MAX_ROWS", 2)
    with pytest.raises(ValueError, match="row limit"):
        load_inspect_dataset(exported)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_loader_rejects_non_regular_files_without_blocking(exported, kind):
    path = exported / "samples.jsonl"
    original = exported / "original.jsonl"
    path.rename(original)
    if kind == "symlink":
        path.symlink_to(original)
    else:
        os.mkfifo(path)
    with pytest.raises(ValueError, match="regular files"):
        load_inspect_dataset(exported)


@pytest.mark.parametrize(
    "data",
    [
        '{"id":"a","id":"b"}\n',
        '{"id":NaN}\n',
        "[1,2,3]\n",
        "[" * 2000 + "0" + "]" * 2000 + "\n",
    ],
)
def test_loader_rejects_ambiguous_or_invalid_json_even_when_rehashed(exported, data):
    replace_data(exported, "samples.jsonl", data)
    with pytest.raises(ValueError):
        load_inspect_dataset(exported)


@pytest.mark.parametrize("mutation", ["reference", "partition", "protocol", "duplicate", "unknown"])
def test_loader_rejects_rehashed_identity_and_metadata_changes(exported, mutation):
    rows = [json.loads(line) for line in (exported / "samples.jsonl").read_text().splitlines()]
    if mutation == "reference":
        rows[0]["metadata"]["reference"] = {"label": "A", "reviewer_id": "private"}
    elif mutation == "partition":
        rows[0]["metadata"]["partition"] = "holdout"
    elif mutation == "protocol":
        rows[0]["metadata"]["protocol_hash"] = "0" * 64
    elif mutation == "duplicate":
        rows[1]["id"] = rows[0]["id"]
    else:
        rows[0]["id"] = "0" * 64
    replace_data(exported, "samples.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError):
        load_inspect_dataset(exported)


def test_loader_checks_labeled_denominator_and_retains_unscorable_rows(exported):
    rows = [json.loads(line) for line in (exported / "references.jsonl").read_text().splitlines()]
    rows[0]["target"] = None
    replace_data(exported, "references.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="labeled sample count"):
        load_inspect_dataset(exported)
    manifest = json.loads((exported / "manifest.json").read_text())
    manifest["labeled_samples"] = 1
    (exported / "manifest.json").write_text(json.dumps(manifest))
    assert len(load_inspect_dataset(exported)) == 1
    all_rows = load_inspect_dataset(exported, labeled_only=False)
    assert len(all_rows) == manifest["samples"] == 2
    assert all_rows[0]["target"] == ""
    assert all("target" not in row["input"] for row in all_rows)


def test_export_rejects_unloadable_size_before_creating_destination(tmp_path, monkeypatch):
    service = JudgeCalibration(Settings(tmp_path / "workspace"))
    identifier = service.create(protocol())["id"]
    destination = tmp_path / "inspect"
    monkeypatch.setattr(inspect_export, "MAX_DATA_BYTES", 32)
    with pytest.raises(ValueError, match="byte limit"):
        export_inspect(service, identifier, "judge-1", destination)
    assert not destination.exists()
