"""Research adapter evidence boundaries and real loopback/engine integration."""

from __future__ import annotations

import importlib
import json
import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cvals.agent_sdk import AgentRequest
from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Check, Collection, ExperimentCreate, Scenario, System
from cvals.store import Store

sys.path.insert(0, str(Path(__file__).parents[1]))
try:
    ADAPTER = importlib.import_module("scripts.posttraining.adapter")
    RUNNER = importlib.import_module("scripts.posttraining.run_study")
finally:
    sys.path.pop(0)
CheckpointAdapter = ADAPTER.CheckpointAdapter
checkpoint_identity = ADAPTER.checkpoint_identity
parse_model_output = ADAPTER.parse_model_output
export_observations = RUNNER.export_observations
phase_schedule = RUNNER.phase_schedule
serve_adapter = RUNNER.serve_adapter
verify_phase_gate = RUNNER.verify_phase_gate


class Runtime:
    def __init__(self, text='{"answer":"yes"}', *, finish="stop"):
        self.text = text
        self.finish = finish
        self.calls = []

    def prompt_token_count(self, messages):
        return 10

    def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {
            "text": self.text,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "elapsed_ms": 2.0,
            "finish_reason": self.finish,
        }


def adapter(runtime=None):
    return CheckpointAdapter(
        runtime or Runtime(),
        model_id="test-local",
        revision="revision-1",
        weight_sha256="a" * 64,
        max_tokens=32,
        max_prompt_tokens=512,
    )


def request(**changes):
    value = {
        "revision": "revision-1",
        "request_id": "request-1",
        "model": "test-local",
        "task": {
            "id": "t",
            "name": "Task",
            "domain": "data",
            "instruction": "Answer yes.",
            "inputs": {},
        },
        "limits": {"input_tokens": 512, "output_tokens": 32, "timeout_seconds": 30},
    }
    value.update(changes)
    return AgentRequest.model_validate(value)


@pytest.mark.parametrize(
    "text",
    [
        'prefix {"answer":"yes"}',
        '```json\n{"answer":"yes"}\n```',
        '["yes"]',
        '{"answer":"yes","answer":"no"}',
        '{"answer":NaN}',
        '{"answer":Infinity}',
        '{"answer":1e999}',
        "null",
        '{"answer":',
    ],
)
def test_invalid_json_is_not_repaired(text):
    parsed, error = parse_model_output(text)
    assert parsed == {} and error
    result = adapter(Runtime(text))(request())
    assert result.output == {} and result.error is None
    assert result.usage.prompt_tokens == 10
    assert result.trace[0]["raw_text"] == text
    assert result.trace[0]["parse_error"]


def test_revision_and_model_mismatch_never_generate():
    runtime = Runtime()
    service = adapter(runtime)
    assert service(request(model="wrong")).error
    with TestClient(service.app()) as client:
        response = client.post(
            "/everval/v1/tasks", json=request(revision="wrong").model_dump(mode="json")
        )
    assert response.status_code == 409
    assert not runtime.calls


def test_input_limit_and_nonempty_inputs_fail_before_generation():
    runtime = Runtime()
    service = adapter(runtime)
    assert service(
        request(limits={"input_tokens": 1, "output_tokens": 32, "timeout_seconds": 30})
    ).error
    public = request().task.model_dump()
    public["inputs"] = {"hidden": "ambiguous"}
    assert service(request(task=public)).error
    assert not runtime.calls


def test_timeout_is_missing_grade_with_known_usage_and_raw_text():
    result = adapter(Runtime('{"answer":"yes"}', finish="timeout"))(request())
    assert result.output is None and result.error
    assert result.usage.completion_tokens == 5
    assert result.trace[0]["raw_text"] == '{"answer":"yes"}'


def test_checkpoint_identity_changes_for_actual_artifacts_only(tmp_path):
    base = tmp_path / "base"
    weights = tmp_path / "weights"
    base.mkdir()
    weights.mkdir()
    (base / "weights").write_bytes(b"base")
    (weights / "adapters").write_bytes(b"initialized")
    first = checkpoint_identity(base, weights)
    assert checkpoint_identity(base, weights) == first
    (weights / "adapters").write_bytes(b"trained")
    assert checkpoint_identity(base, weights)["weight_sha256"] != first["weight_sha256"]


def test_phase_order_is_enforced_before_any_runtime_load(tmp_path):
    protocol = {"checkpoint_steps": [0, 80, 160, 320]}
    assert phase_schedule(protocol, "development") == [(17, 0), (17, 80), (17, 160)]
    assert phase_schedule(protocol, "validation")[0] == (17, 320)
    assert verify_phase_gate(tmp_path, "a" * 64, "development") is None
    with pytest.raises(FileNotFoundError):
        verify_phase_gate(tmp_path, "a" * 64, "validation")
    (tmp_path / "observations.jsonl").write_text(json.dumps({"seed": 29, "step": 0}) + "\n")
    with pytest.raises(ValueError, match="closed"):
        verify_phase_gate(tmp_path, "a" * 64, "development")


@pytest.mark.parametrize("text,expected", [('{"answer":"yes"}', True), ("not JSON", False)])
def test_real_http_engine_retains_bad_answers_raw_text_zero_cost_and_latency(
    tmp_path, text, expected
):
    runtime = Runtime(text)
    service = adapter(runtime)
    settings = Settings(tmp_path / "workspace", allow_remote_providers=False)
    store = Store(settings)
    store.init()
    task = Scenario(
        id="case-0",
        name="Private grader control",
        domain="data",
        instruction="Answer yes.",
        inputs={},
        reference_output={"answer": "yes"},
        checks=[Check(name="Answer", kind="equals", path="answer", expected="yes")],
        provenance={"study_domain": "routing", "task_family": "family", "eval_id": "routing.p0"},
    )
    store.save_collection(Collection(id="control", name="HTTP control", scenarios=[task]))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    system = System(
        id="live",
        name="Local control",
        kind="http",
        harness="external-agent",
        model="test-local",
        base_url=f"http://127.0.0.1:{port}",
        adapter_revision="revision-1",
        adapter_input_token_limit=512,
        adapter_output_token_limit=32,
        input_cost_per_million=0,
        output_cost_per_million=0,
    )
    store.save_system(system)
    experiment = store.create_experiment(
        ExperimentCreate(name="Actual HTTP engine", collection_id="control", system_ids=["live"])
    )
    with serve_adapter(service, port):
        result = Engine(store, settings).run_experiment(experiment["id"])
    assert result["status"] == "completed"
    trial = result["trials"][0]
    assert trial["passed"] is expected
    assert trial["cost_usd"] == 0
    assert not result.get("budget_uncertain")
    assert trial["timing"]["provider_ms"] > 0
    state = {
        "checkpoints": {
            "17:0": {
                "seed": 17,
                "step": 0,
                "checkpoint_id": "checkpoint",
                "weight_sha256": "a" * 64,
                "adapter_revision": "revision-1",
                "experiments": [{"experiment_id": experiment["id"]}],
            }
        }
    }
    rows = export_observations(tmp_path, state, store)
    assert rows[0]["raw_text"] == text
    assert rows[0]["passed"] is expected
    assert rows[0]["elapsed_seconds"] > 0
    assert rows[0]["cost_usd"] == 0
    assert len(runtime.calls) == 1
    assert "PRIVATE" not in str(runtime.calls)
    assert runtime.calls[0][0][-1] == {"role": "user", "content": "Answer yes."}


@pytest.fixture
def frozen_study(tmp_path, monkeypatch):
    study = tmp_path / "study"
    source = tmp_path / "source"
    study.mkdir()
    source.mkdir()
    monkeypatch.setattr(RUNNER, "ROOT", source)
    monkeypatch.setattr(RUNNER, "environment_manifest", lambda study: {"test_control": True})
    sources = {}
    for name in ("adapter", "run_study", "model_runtime", "analyze_real_study"):
        relative = f"scripts/posttraining/{name}.py"
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\n")
        sources[relative] = ADAPTER.file_sha256(path)
    dataset = study / "dataset"
    dataset.mkdir()
    (dataset / "dataset-manifest.json").write_text("{}")
    (dataset / "cases.json").write_text("[]")
    (study / "runtime-freeze.txt").write_text("dependency==1\n")
    protocol = {
        "schema_version": 1,
        "seeds": [17, 29, 43],
        "checkpoint_steps": [0, 80, 160, 320],
        "seed_splits": {"17": "development", "29": "validation", "43": "validation"},
        "measurement": {
            "system_prompt": ADAPTER.SYSTEM_PROMPT,
            "temperature": 0,
            "repetitions": 1,
            "max_new_tokens": 32,
            "input_limit": 512,
            "invalid_json_policy": "completed_failed_empty_object",
        },
        "dataset": {
            "manifest_sha256": ADAPTER.file_sha256(dataset / "dataset-manifest.json"),
            "files": {"cases.json": ADAPTER.file_sha256(dataset / "cases.json")},
        },
        "source_files": {
            "scripts/posttraining/model_runtime.py": sources[
                "scripts/posttraining/model_runtime.py"
            ]
        },
        "artifact_files_sha256": {
            "runtime-freeze.txt": ADAPTER.file_sha256(study / "runtime-freeze.txt")
        },
    }
    RUNNER.write_json(study / "frozen-protocol.json", protocol)
    RUNNER.write_json(
        study / "evaluation-runtime-freeze.json",
        {
            "protocol_sha256": ADAPTER.file_sha256(study / "frozen-protocol.json"),
            "environment": {"test_control": True},
            "source_files": sources,
            "runtime_freeze_sha256": ADAPTER.file_sha256(study / "runtime-freeze.txt"),
        },
    )
    return study, source


@pytest.mark.parametrize("change", ["dataset", "source", "artifact", "override", "protocol"])
def test_freeze_rejects_changed_evidence_before_runtime_loading(frozen_study, change):
    study, source = frozen_study
    RUNNER.verify_protocol(study)
    if change == "dataset":
        (study / "dataset/cases.json").write_text("[1]")
    elif change == "source":
        (source / "scripts/posttraining/adapter.py").write_text("# changed")
    elif change == "artifact":
        (study / "runtime-freeze.txt").write_text("dependency==2")
    else:
        path = study / "evaluation-runtime-freeze.json"
        value = json.loads(path.read_text())
        if change == "override":
            value["source_files"]["scripts/posttraining/model_runtime.py"] = "f" * 64
        else:
            value["protocol_sha256"] = "f" * 64
        RUNNER.write_json(path, value)
    with pytest.raises(ValueError):
        RUNNER.verify_protocol(study)


@pytest.mark.parametrize(
    "changed",
    [
        "seed",
        "step",
        "base_sha256",
        "adapter_sha256",
        "training_config_sha256",
        "initial_zero_effect_adapter",
    ],
)
def test_checkpoint_cannot_be_swapped_before_first_registration(tmp_path, changed):
    directory = tmp_path / "training/seed-17/checkpoint-0"
    directory.mkdir(parents=True)
    weights = directory / "adapters.safetensors"
    weights.write_bytes(b"actual weights")
    config = directory.parent / "training-config.json"
    config.write_text('{"seed":17}')
    (tmp_path / "training-seed-17-config.json").write_text(config.read_text())
    metadata = {
        "format": "everval-trained-lora-v1",
        "seed": 17,
        "step": 0,
        "base_sha256": "b" * 64,
        "adapter_sha256": ADAPTER.file_sha256(weights),
        "training_config_sha256": ADAPTER.file_sha256(config),
        "initial_zero_effect_adapter": True,
    }
    RUNNER.write_json(directory / "checkpoint.json", metadata)
    protocol = {"model": {"converted_manifest": {"sha256": "b" * 64}}}
    assert RUNNER.verify_checkpoint(tmp_path, protocol, 17, 0) == directory
    metadata[changed] = False if changed == "initial_zero_effect_adapter" else "changed"
    RUNNER.write_json(directory / "checkpoint.json", metadata)
    with pytest.raises(ValueError, match="metadata"):
        RUNNER.verify_checkpoint(tmp_path, protocol, 17, 0)
