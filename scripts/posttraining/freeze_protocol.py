"""Freeze a replication's actual local artifacts before training/evaluation.

Run in the prepared study environment. This utility does not call a model,
download anything, or replace any existing protocol or runtime freeze.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cvals.models import content_hash

from .analyze_real_study import _bounded_file, _write_once, load_case_registry
from .run_study import environment_manifest, verify_protocol

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "examples/post-training/real-study-protocol-template.json"
RUNTIME_SOURCES = (
    "scripts/posttraining/adapter.py",
    "scripts/posttraining/run_study.py",
    "scripts/posttraining/model_runtime.py",
    "scripts/posttraining/analyze_real_study.py",
)
FORBIDDEN_OUTCOME_FILES = ("observations.jsonl", "execution-plan.json")


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def assert_before_evaluation(study: Path) -> None:
    if any((study / name).exists() for name in FORBIDDEN_OUTCOME_FILES):
        raise ValueError("A prospective freeze cannot be created after evaluation has begun")
    if (study / "workspace").exists():
        raise ValueError("A prospective freeze requires a fresh evaluation workspace")


def freeze_initial(study: Path) -> Path:
    output = study / "frozen-protocol.json"
    if output.exists():
        raise ValueError("An initial protocol already exists; never replace it")
    assert_before_evaluation(study)
    if (study / "training").exists():
        raise ValueError("Freeze the initial protocol before any study training")
    protocol: dict[str, Any] = json.loads(TEMPLATE.read_text())
    protocol["frozen_at"] = datetime.now(UTC).isoformat()
    sources = protocol.pop("source_file_paths")
    protocol["source_files"] = {name: file_sha256(_bounded_file(ROOT, name)) for name in sources}
    artifacts = protocol.pop("artifact_file_paths")
    protocol["artifact_files_sha256"] = {
        name: file_sha256(_bounded_file(study, name)) for name in artifacts
    }
    actual_training = json.loads((study / "training-config.json").read_text())
    training_settings = {
        key: value for key, value in actual_training.items() if key not in {"model", "data"}
    }
    if training_settings != protocol["training"]:
        raise ValueError(
            "Prepared training settings differ from the prospective replication design"
        )
    if (
        Path(actual_training["model"]).resolve() != (study / "qwen-mlx-4bit").resolve()
        or Path(actual_training["data"]).resolve() != (study / "dataset").resolve()
    ):
        raise ValueError(
            "Prepared training paths must point to this study's local model and dataset"
        )
    for key, name in (
        ("source_manifest", "source-file-manifest.json"),
        ("converted_manifest", "converted-file-manifest.json"),
    ):
        protocol["model"][key] = json.loads((study / name).read_text())
    source = protocol["model"]["source_manifest"]
    if (
        source["model"] != protocol["model"]["repo"]
        or source["revision"] != protocol["model"]["revision"]
    ):
        raise ValueError("Prepared model source differs from the pinned upstream model")
    weight = next(item for item in source["files"] if item["path"] == "model.safetensors")
    if weight["sha256"] != protocol["model"]["upstream_weights_sha256"]:
        raise ValueError("Prepared upstream weight digest differs from the pinned model")
    dataset = protocol["dataset"]
    manifest_path = _bounded_file(study, dataset["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    if content_hash(manifest) != dataset["content_sha256"]:
        raise ValueError("Generated dataset differs from the reviewed fixed task population")
    dataset["manifest_sha256"] = file_sha256(manifest_path)
    dataset["files"] = manifest["files_sha256"]
    # Validate the complete candidate without writing an externally visible freeze.
    for relative, expected in dataset["files"].items():
        if file_sha256(_bounded_file(manifest_path.parent, relative)) != expected:
            raise ValueError("Generated dataset files differ from their retained manifest")
    _write_once(output, protocol)
    load_case_registry(output)
    return output


def freeze_runtime(study: Path) -> Path:
    output = study / "evaluation-runtime-freeze.json"
    if output.exists():
        verify_protocol(study)
        return output
    assert_before_evaluation(study)
    initial = study / "frozen-protocol.json"
    protocol = json.loads(initial.read_text())
    for name, expected in protocol["source_files"].items():
        if file_sha256(_bounded_file(ROOT, name)) != expected:
            raise ValueError("Initial source changed; runtime freeze cannot override it")
    for name, expected in protocol["artifact_files_sha256"].items():
        if file_sha256(_bounded_file(study, name)) != expected:
            raise ValueError("Initial study artifact changed")
    load_case_registry(initial)
    freeze = {
        "schema_version": 1,
        "frozen_at": datetime.now(UTC).isoformat(),
        "protocol_sha256": file_sha256(initial),
        "source_files": {name: file_sha256(ROOT / name) for name in RUNTIME_SOURCES},
        "runtime_freeze_sha256": file_sha256(study / "runtime-freeze.txt"),
        "environment": environment_manifest(study),
        "purpose": "Evaluation and analysis implementation frozen before the first benchmark response.",
    }
    _write_once(output, freeze)
    verify_protocol(study)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("action", choices=("protocol", "runtime"))
    args = parser.parse_args()
    study = args.study.expanduser().absolute()
    path = freeze_initial(study) if args.action == "protocol" else freeze_runtime(study)
    print(json.dumps({"artifact": str(path), "sha256": file_sha256(path)}))


if __name__ == "__main__":
    main()
