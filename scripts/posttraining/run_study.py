"""Run frozen local checkpoints through Everval's durable HTTP evaluation engine."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.metadata
import json
import re
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import uvicorn

from cvals.config import Settings
from cvals.engine import Engine
from cvals.models import Collection, System, canonical_json, content_hash
from cvals.posttraining import PostTraining
from cvals.store import EXPERIMENT_TERMINAL, Store

from .adapter import (
    COST_SCOPE,
    SYSTEM_PROMPT,
    CheckpointAdapter,
    adapter_revision,
    checkpoint_identity,
    file_sha256,
)

ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def checked_relative(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Frozen file names must be relative, without traversal")
    return root / path


def environment_manifest(study: Path) -> dict[str, Any]:
    """Bind both installed package versions and actual installed evaluator bytes."""
    import cvals

    versions = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    for line in (study / "runtime-freeze.txt").read_text().splitlines():
        if "==" in line:
            name, expected = line.split("==", 1)
            if versions.get(re.sub(r"[-_.]+", "-", name).lower()) != expected:
                raise ValueError("Installed runtime differs from retained dependency freeze")
    wheels = list((study / "runtime-wheels").glob("everval_ai-*.whl"))
    if len(wheels) != 1 or versions.get("everval-ai") != "0.3.1":
        raise ValueError("Expected one retained Everval 0.3.1 wheel")
    package = Path(cvals.__file__).parent
    evaluator = {
        path.relative_to(package).as_posix(): file_sha256(path)
        for path in sorted(package.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    return {
        "python": sys.version,
        "distributions": versions,
        "everval_wheel_sha256": file_sha256(wheels[0]),
        "installed_evaluator_sha256": content_hash(evaluator),
    }


def verify_protocol(study: Path) -> tuple[dict[str, Any], str, dict[str, Any]]:
    path = study / "frozen-protocol.json"
    protocol = read_json(path)
    digest = file_sha256(path)
    if protocol["schema_version"] != 1:
        raise ValueError("Unsupported frozen study schema")
    if protocol["seeds"] != [17, 29, 43] or protocol["checkpoint_steps"] != [0, 80, 160, 320]:
        raise ValueError("The runner requires the prospectively reviewed study grid")
    if protocol["seed_splits"] != {"17": "development", "29": "validation", "43": "validation"}:
        raise ValueError("Seed split differs from the reviewed design")
    measurement = protocol["measurement"]
    if (
        measurement["system_prompt"] != SYSTEM_PROMPT
        or measurement["temperature"] != 0
        or measurement["repetitions"] != 1
        or measurement["max_new_tokens"] != 32
        or measurement["input_limit"] != 512
        or measurement["invalid_json_policy"] != "completed_failed_empty_object"
    ):
        raise ValueError("Measurement settings differ from the reviewed adapter")
    manifest_path = study / "dataset/dataset-manifest.json"
    if file_sha256(manifest_path) != protocol["dataset"]["manifest_sha256"]:
        raise ValueError("Dataset manifest differs from the frozen protocol")
    manifest = read_json(manifest_path)
    for name, expected in protocol["dataset"]["files"].items():
        if file_sha256(checked_relative(study / "dataset", name)) != expected:
            raise ValueError(f"Frozen dataset file changed: {name}")
    freeze = read_json(study / "evaluation-runtime-freeze.json")
    if freeze["protocol_sha256"] != digest:
        raise ValueError("Evaluation runtime freeze belongs to another protocol")
    required = {
        "scripts/posttraining/adapter.py",
        "scripts/posttraining/run_study.py",
        "scripts/posttraining/model_runtime.py",
        "scripts/posttraining/analyze_real_study.py",
    }
    original_sources = protocol.get("source_files", {})
    for name, expected in original_sources.items():
        if name in freeze["source_files"] and freeze["source_files"][name] != expected:
            raise ValueError("Evaluation freeze cannot override an initial source digest")
    sources = {**original_sources, **freeze["source_files"]}
    for name, expected in protocol["artifact_files_sha256"].items():
        if file_sha256(checked_relative(study, name)) != expected:
            raise ValueError(f"Frozen study artifact changed: {name}")
    if not required <= set(sources):
        raise ValueError("Protocol does not bind all execution and analysis source files")
    for name, expected in sources.items():
        if file_sha256(checked_relative(ROOT, name)) != expected:
            raise ValueError(f"Frozen execution source changed: {name}")
    if file_sha256(study / "runtime-freeze.txt") != freeze["runtime_freeze_sha256"]:
        raise ValueError("Runtime dependency record changed after freezing")
    if environment_manifest(study) != freeze["environment"]:
        raise ValueError("Installed environment differs from the evaluation runtime freeze")
    return protocol, digest, manifest


def phase_schedule(protocol: dict[str, Any], phase: str) -> list[tuple[int, int]]:
    steps = protocol["checkpoint_steps"]
    if phase == "development":
        return [(17, step) for step in steps[:-1]]
    if phase == "validation":
        return [(17, steps[-1]), *[(seed, step) for seed in (29, 43) for step in steps]]
    raise ValueError("Unknown study phase")


def verify_phase_gate(study: Path, protocol_sha256: str, phase: str) -> str | None:
    observations = study / "observations.jsonl"
    rows = (
        [json.loads(line) for line in observations.read_text().splitlines()]
        if observations.exists()
        else []
    )
    development = [row for row in rows if row["seed"] == 17 and row["step"] in (0, 80, 160)]
    if phase == "development":
        if len(rows) != len(development) or (study / "analysis/mix-selection.json").exists():
            raise ValueError("Development execution is closed after mix selection or validation")
        return None
    from .analyze_real_study import (
        load_case_registry,
        validate_case_evidence,
        validate_checkpoint_evidence,
        verify_selection,
    )

    selection = read_json(study / "analysis/mix-selection.json")
    validate_case_evidence(development, load_case_registry(study / "frozen-protocol.json"))
    validate_checkpoint_evidence(development, study / "frozen-protocol.json")
    verify_selection(selection, protocol_sha256=protocol_sha256, development_rows=development)
    return selection["selection_sha256"]


@contextlib.contextmanager
def exclusive_study(study: Path) -> Iterator[None]:
    with (study / "runner.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another study runner is active") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def serve_adapter(adapter: CheckpointAdapter, port: int) -> Iterator[None]:
    """Own the exact loopback socket and stop only this process's server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(32)
    server = uvicorn.Server(
        uvicorn.Config(adapter.app(), log_level="error", access_log=False, lifespan="off")
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Local checkpoint service failed to start")
            time.sleep(0.02)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=125)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("Inference server failed to stop; do not load another checkpoint")


def execution_state(study: Path, digest: str) -> dict[str, Any]:
    path = study / "execution-plan.json"
    if path.exists():
        state = read_json(path)
        if state["protocol_sha256"] != digest:
            raise ValueError("Workspace belongs to a different frozen protocol")
        if state["runtime_freeze_sha256"] != file_sha256(study / "runtime-freeze.txt"):
            raise ValueError("Runtime dependencies changed after execution plan creation")
        return state
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    state = {
        "schema_version": 1,
        "protocol_sha256": digest,
        "runtime_freeze_sha256": file_sha256(study / "runtime-freeze.txt"),
        "model_path": str(study / "qwen-mlx-4bit"),
        "workspace": str(study / "workspace"),
        "port": port,
        "checkpoints": {},
        "cost_scope": COST_SCOPE,
    }
    write_json(path, state)
    return state


def prepare_runs(
    service: PostTraining,
    protocol: dict[str, Any],
    manifest: dict[str, Any],
    study: Path,
    digest: str,
) -> dict[int, str]:
    runs = {}
    analysis = protocol["analysis"]
    for seed in protocol["seeds"]:
        split = protocol["seed_splits"][str(seed)]
        domains = []
        for domain in protocol["domains"]:
            entry = manifest["collections"][split][domain]
            collection = Collection.model_validate(read_json(study / "dataset" / entry["path"]))
            service.store.save_collection(collection)
            domains.append(
                {
                    "name": domain,
                    "collection_id": collection.id,
                    "task_families": entry["task_families"],
                    "practical_threshold": analysis["practical_threshold"],
                }
            )
        result = service.create_run(
            {
                "name": f"Qwen 0.5B local SFT · seed {seed} · {split}",
                "training_run_id": f"{protocol['study_id']}-seed-{seed}",
                "provenance_kind": "declared_training",
                "provenance": (
                    "Actual local MLX LoRA training and live inference. Original generated diagnostics, "
                    "not customer workloads. Frozen protocol SHA256 "
                    + digest
                    + ". Initial seeded LoRA checkpoints are functionally equivalent to the base. "
                    + COST_SCOPE
                ),
                "training_config_sha256": digest,
                "protocol": {
                    "inference_scope": "exploratory",
                    "independence_statement": (
                        "Training settings and checkpoint grid frozen before evaluation; no evaluation "
                        "feedback controls training. Paraphrases cluster by generated record. Shared "
                        "templates limit independence and population generalization."
                    ),
                    "domains": domains,
                    "baseline_step": protocol["checkpoint_steps"][0],
                    "comparison_steps": protocol["checkpoint_steps"][1:],
                    "alpha": analysis["alpha_per_run"],
                    "max_looks": analysis["max_looks"],
                    "min_clusters": analysis["min_clusters"],
                    "repetitions": 1,
                    "budget_per_domain_usd": 0.0,
                    "lifetime_budget_usd": 0.0,
                },
            }
        )
        runs[seed] = result["id"]
    return runs


def observations_from_store(state: dict[str, Any], store: Store) -> list[dict[str, Any]]:
    rows = []
    for value in state["checkpoints"].values():
        for link in value.get("experiments", []):
            experiment = store.get_experiment(link["experiment_id"])
            scenarios = {s["id"]: s for s in experiment["snapshot"]["collection"]["scenarios"]}
            for trial in experiment["trials"]:
                provenance = scenarios[trial["scenario_id"]]["provenance"]
                trace = next(
                    (
                        item
                        for item in trial.get("provider_metadata", {}).get("trace", [])
                        if item.get("kind") == "local_checkpoint_generation"
                    ),
                    {},
                )
                elapsed_ms = trial.get("timing", {}).get("provider_ms")
                rows.append(
                    {
                        "seed": value["seed"],
                        "step": value["step"],
                        "case_id": trial["scenario_id"],
                        "status": trial["status"],
                        "passed": trial.get("passed"),
                        "elapsed_seconds": elapsed_ms / 1000 if elapsed_ms is not None else None,
                        "inference_seconds": trace.get("inference_ms", 0) / 1000 if trace else None,
                        "domain": provenance["study_domain"],
                        "family_id": provenance["task_family"],
                        "eval_id": provenance["eval_id"],
                        "split": "development" if value["seed"] == 17 else "validation",
                        "experiment_id": experiment["id"],
                        "trial_id": trial["id"],
                        "checkpoint_id": value["checkpoint_id"],
                        "weight_sha256": value["weight_sha256"],
                        "adapter_revision": value["adapter_revision"],
                        "input_tokens": trial.get("input_tokens"),
                        "output_tokens": trial.get("output_tokens"),
                        "cost_usd": trial.get("cost_usd"),
                        "raw_text": trace.get("raw_text"),
                        "parse_error": trace.get("parse_error"),
                        "finish_reason": trace.get("finish_reason"),
                        "output": trial.get("output"),
                        "error": trial.get("error"),
                    }
                )
    rows.sort(key=lambda row: (row["seed"], row["step"], row["case_id"]))
    return rows


def export_observations(study: Path, state: dict[str, Any], store: Store) -> list[dict[str, Any]]:
    rows = observations_from_store(state, store)
    temporary = study / "observations.jsonl.tmp"
    temporary.write_text("".join(canonical_json(row) + "\n" for row in rows))
    temporary.chmod(0o600)
    temporary.replace(study / "observations.jsonl")
    return rows


def verify_checkpoint(study: Path, protocol: dict[str, Any], seed: int, step: int) -> Path:
    directory = study / f"training/seed-{seed}/checkpoint-{step}"
    metadata = read_json(directory / "checkpoint.json")
    config = study / f"training/seed-{seed}/training-config.json"
    frozen_config = study / f"training-seed-{seed}-config.json"
    if (
        metadata["format"] != "everval-trained-lora-v1"
        or metadata["seed"] != seed
        or metadata["step"] != step
        or metadata["base_sha256"] != protocol["model"]["converted_manifest"]["sha256"]
        or metadata["adapter_sha256"] != file_sha256(directory / "adapters.safetensors")
        or metadata["training_config_sha256"] != file_sha256(config)
        or read_json(config) != read_json(frozen_config)
        or metadata["initial_zero_effect_adapter"] is not (step == 0)
    ):
        raise ValueError(
            "Finalized checkpoint metadata does not match its frozen training declaration"
        )
    return directory


def run(study: Path, phase: str) -> None:
    study = study.expanduser().absolute()
    protocol, digest, manifest = verify_protocol(study)
    with exclusive_study(study):
        selection_hash = verify_phase_gate(study, digest, phase)
        state = execution_state(study, digest)
        if selection_hash:
            if state.get("mix_selection_sha256", selection_hash) != selection_hash:
                raise ValueError("Mix selection changed during validation")
            state["mix_selection_sha256"] = selection_hash
        settings = Settings(data_dir=Path(state["workspace"]), allow_remote_providers=False)
        store = Store(settings)
        service = PostTraining(settings, store)
        runs = prepare_runs(service, protocol, manifest, study, digest)
        state["runs"] = {str(seed): run_id for seed, run_id in runs.items()}
        write_json(study / "execution-plan.json", state)
        from .model_runtime import fingerprint, load_runtime

        for seed, step in phase_schedule(protocol, phase):
            verify_protocol(study)
            key = f"{seed}:{step}"
            base = Path(state["model_path"])
            adapters = verify_checkpoint(study, protocol, seed, step)
            if fingerprint(base) != protocol["model"]["converted_manifest"]:
                raise ValueError("Converted base differs from the frozen model manifest")
            identity = checkpoint_identity(base, adapters)
            model_id = f"qwen05b-s{seed}-step{step}"
            revision = adapter_revision(
                identity,
                model_id=model_id,
                max_tokens=32,
                max_prompt_tokens=512,
                requirements_lock=study / "runtime-freeze.txt",
                runtime_source=ROOT / "scripts/posttraining/model_runtime.py",
            )
            previous = state["checkpoints"].get(key)
            if previous and (
                previous["weight_sha256"] != identity["weight_sha256"]
                or previous["adapter_revision"] != revision
            ):
                raise ValueError("Previously registered checkpoint or adapter changed")
            system = System(
                id=model_id,
                name=model_id,
                kind="http",
                harness="external-agent",
                model=model_id,
                base_url=f"http://127.0.0.1:{state['port']}",
                adapter_revision=revision,
                adapter_input_token_limit=512,
                adapter_output_token_limit=32,
                max_tokens=32,
                timeout_seconds=120,
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                pricing_version="local-provider-cash-zero/1",
                provenance={"protocol_sha256": digest, **identity, "cost_scope": COST_SCOPE},
            )
            store.save_system(system)
            checkpoint = service.register_checkpoint(
                runs[seed],
                {
                    "step": step,
                    "weight_sha256": identity["weight_sha256"],
                    "system_id": system.id,
                    "expected_system_hash": content_hash(system.model_dump(mode="json")),
                    "revision": revision,
                    "provenance": "Finalized local base plus actual LoRA artifact manifest. "
                    + canonical_json(identity),
                },
            )
            linked = service.queue_checkpoint(runs[seed], checkpoint["id"])
            state["checkpoints"][key] = {
                "seed": seed,
                "step": step,
                "checkpoint_id": checkpoint["id"],
                **identity,
                "adapter_revision": revision,
                "experiments": linked["experiments"],
            }
            write_json(study / "execution-plan.json", state)
            for link in linked["experiments"]:
                existing = store.get_experiment(link["experiment_id"])
                if existing["status"] in EXPERIMENT_TERMINAL and (
                    existing["status"] != "completed"
                    or existing.get("budget_uncertain")
                    or any(trial["status"] != "completed" for trial in existing["trials"])
                ):
                    export_observations(study, state, store)
                    raise RuntimeError("Prior incomplete or failed execution cannot be retried")
            pending = [
                link
                for link in linked["experiments"]
                if store.execution_state(link["experiment_id"])["status"] not in EXPERIMENT_TERMINAL
            ]
            if pending:
                runtime = load_runtime(base, adapters)
                try:
                    if checkpoint_identity(base, adapters) != identity:
                        raise ValueError("Checkpoint artifacts changed while loading")
                    adapter = CheckpointAdapter(
                        runtime,
                        model_id=model_id,
                        revision=revision,
                        weight_sha256=identity["weight_sha256"],
                        max_tokens=32,
                        max_prompt_tokens=512,
                    )
                    with serve_adapter(adapter, state["port"]):
                        for link in pending:
                            print(
                                f"Evaluating seed={seed} step={step} domain={link['domain']}",
                                flush=True,
                            )
                            experiment = Engine(store, settings).run_experiment(
                                link["experiment_id"]
                            )
                            export_observations(study, state, store)
                            if (
                                experiment["status"] != "completed"
                                or experiment.get("budget_uncertain")
                                or any(t["status"] != "completed" for t in experiment["trials"])
                            ):
                                raise RuntimeError(
                                    "Study stopped on incomplete execution; evidence retained"
                                )
                finally:
                    runtime.close()
            export_observations(study, state, store)
            if step:
                service.analyze(runs[seed], checkpoint["id"])
            write_json(study / f"dashboards/seed-{seed}.json", service.dashboard(runs[seed]))
            print(f"Completed seed={seed} step={step}; provider cash cost $0.", flush=True)
        print(f"Completed phase={phase}; observations retained locally.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "validation"), required=True)
    args = parser.parse_args()
    run(args.study_root, args.phase)


if __name__ == "__main__":
    main()
