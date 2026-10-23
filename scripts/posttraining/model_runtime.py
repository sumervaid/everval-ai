"""Pinned local MLX runtime for the prospective checkpoint study.

This optional study runner is separate from Everval's installed dependencies.
It loads local safetensors only, never remote model code, and emits no telemetry.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Any

for _key, _value in {
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "TOKENIZERS_PARALLELISM": "false",
}.items():
    os.environ[_key] = _value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(directory: Path) -> dict[str, Any]:
    directory = directory.absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Model directories must be real local directories")
    files = []
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Model artifact contains a symlink or special file")
        files.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if not files or len(files) > 4096:
        raise ValueError("Model artifact must contain 1–4096 regular files")
    value = {"format": "everval-local-weights-v1", "files": files}
    value["sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


class Runtime:
    def __init__(self, model: Any, tokenizer: Any):
        self.model = model
        self.tokenizer = tokenizer
        self._lock = threading.Lock()

    def _tokens(self, messages: list[dict[str, str]]) -> list[int]:
        if not messages or len(messages) > 32:
            raise ValueError("Supply 1–32 chat messages")
        if any(
            set(row) != {"role", "content"}
            or row["role"] not in {"system", "user", "assistant"}
            or not isinstance(row["content"], str)
            or len(row["content"]) > 32768
            for row in messages
        ):
            raise ValueError("Messages must contain bounded role/content strings")
        return list(
            self.tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        )

    def prompt_token_count(self, messages: list[dict[str, str]]) -> int:
        return len(self._tokens(messages))

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 32,
        *,
        max_prompt_tokens: int = 512,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        if type(max_tokens) is not int or not 1 <= max_tokens <= 256:
            raise ValueError("Generation limit must be 1–256 tokens")
        if type(max_prompt_tokens) is not int or not 1 <= max_prompt_tokens <= 4096:
            raise ValueError("Prompt limit must be 1–4096 tokens")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("Timeout must be finite and in (0,120]")
        with self._lock:
            start = time.monotonic()
            tokens = self._tokens(messages)
            if len(tokens) > max_prompt_tokens:
                raise ValueError("Tokenized prompt exceeds the declared input ceiling")
            pieces: list[str] = []
            generated = 0
            reason = "length"
            generator = stream_generate(
                self.model,
                self.tokenizer,
                tokens,
                max_tokens=max_tokens,
                sampler=make_sampler(temp=0),
            )
            try:
                for result in generator:
                    pieces.append(result.text)
                    generated = result.generation_tokens
                    if result.finish_reason:
                        reason = result.finish_reason
                    if time.monotonic() - start > timeout_seconds:
                        reason = "timeout"
                        break
            finally:
                generator.close()
            return {
                "text": "".join(pieces),
                "prompt_tokens": len(tokens),
                "completion_tokens": generated,
                "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
                "finish_reason": reason,
            }

    def close(self) -> None:
        import mlx.core as mx

        self.model = None
        self.tokenizer = None
        gc.collect()
        mx.clear_cache()


def load_runtime(model_path: Path, adapter_path: Path | None = None) -> Runtime:
    """Load pinned local artifacts; caller retains/verifies their expected digests."""
    import mlx.core as mx
    from mlx_lm import load

    model_path = Path(model_path).absolute()
    if not model_path.is_dir() or model_path.is_symlink():
        raise ValueError("A regular local model directory is required")
    if adapter_path is not None:
        adapter_path = Path(adapter_path).absolute()
        if not adapter_path.is_dir() or adapter_path.is_symlink():
            raise ValueError("A regular local adapter directory is required")
    model, tokenizer = load(
        str(model_path),
        adapter_path=str(adapter_path) if adapter_path else None,
        trust_remote_code=False,
        tokenizer_config={"trust_remote_code": False},
    )
    model.eval()
    mx.eval(model.parameters())
    return Runtime(model, tokenizer)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
    path.chmod(0o600)


def train(config: dict[str, Any]) -> dict[str, Any]:
    """Run one independent SFT trajectory, preserving actual adapter tensors."""
    import types

    import mlx.core as mx
    import mlx.optimizers as optim
    import numpy as np
    from mlx.utils import tree_flatten
    from mlx_lm import load
    from mlx_lm.lora import CONFIG_DEFAULTS
    from mlx_lm.tuner.callbacks import TrainingCallback
    from mlx_lm.tuner.datasets import CacheDataset, load_dataset
    from mlx_lm.tuner.trainer import TrainingArgs
    from mlx_lm.tuner.trainer import train as train_model
    from mlx_lm.tuner.utils import linear_to_lora_layers

    required = {
        "model",
        "data",
        "output",
        "seed",
        "iters",
        "checkpoint_steps",
        "batch_size",
        "learning_rate",
        "num_layers",
        "rank",
        "max_seq_length",
    }
    if set(config) != required:
        raise ValueError("Training configuration has missing or unexpected fields")
    if (
        config["checkpoint_steps"] != sorted(set(config["checkpoint_steps"]))
        or 0 not in config["checkpoint_steps"]
    ):
        raise ValueError("Declare a sorted unique checkpoint grid including step zero")
    if any(
        type(step) is not int or not 0 <= step <= config["iters"]
        for step in config["checkpoint_steps"]
    ):
        raise ValueError("Checkpoint steps must lie within the training schedule")
    if not 1 <= config["iters"] <= 1000 or not 1 <= config["batch_size"] <= 4:
        raise ValueError("Study training is bounded to 1000 steps and batches of four")
    output = Path(config["output"]).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    model_path, data_path = Path(config["model"]).absolute(), Path(config["data"]).absolute()
    base = fingerprint(model_path)
    data_files = {
        name: {"sha256": sha256(data_path / name), "bytes": (data_path / name).stat().st_size}
        for name in ("train.jsonl", "valid.jsonl")
    }
    config = {**config, "model": str(model_path), "data": str(data_path), "output": str(output)}
    _write_json(output / "training-config.json", config)
    _write_json(output / "base-manifest.json", base)
    _write_json(output / "dataset-manifest.json", data_files)
    _write_json(
        output / "runtime-versions.json",
        {
            name: importlib.metadata.version(name)
            for name in ("mlx", "mlx-lm", "transformers", "numpy", "safetensors", "datasets")
        },
    )
    seed = config["seed"]
    np.random.seed(seed)
    mx.random.seed(seed)
    model, tokenizer = load(
        str(model_path), trust_remote_code=False, tokenizer_config={"trust_remote_code": False}
    )
    lora_parameters = {
        "rank": config["rank"],
        "dropout": 0.0,
        "scale": 16.0,
        "keys": ["self_attn.q_proj", "self_attn.v_proj"],
    }
    model.freeze()
    linear_to_lora_layers(model, config["num_layers"], lora_parameters)
    mx.eval(model.trainable_parameters())
    args = types.SimpleNamespace(
        **{
            **CONFIG_DEFAULTS,
            "model": str(model_path),
            "data": str(data_path),
            "seed": seed,
            "train": True,
            "mask_prompt": True,
            "trust_remote_code": False,
            "num_layers": config["num_layers"],
            "lora_parameters": lora_parameters,
        }
    )
    train_set, valid_set, _ = load_dataset(args, tokenizer)
    work = output / "working"
    work.mkdir(mode=0o700)
    adapter_config = {
        "fine_tune_type": "lora",
        "num_layers": config["num_layers"],
        "lora_parameters": lora_parameters,
    }
    # Step zero contains real initialized LoRA tensors. Its B matrices are zero.
    zero = output / "checkpoint-0"
    zero.mkdir(mode=0o700)
    mx.save_safetensors(
        str(zero / "adapters.safetensors"), dict(tree_flatten(model.trainable_parameters()))
    )
    _write_json(zero / "adapter_config.json", adapter_config)
    records = []

    class Callback(TrainingCallback):
        def on_train_loss_report(self, train_info: dict[str, Any]) -> None:
            records.append({"kind": "training", **train_info})
            with (output / "losses.jsonl").open("a") as handle:
                handle.write(json.dumps(records[-1], allow_nan=False) + "\n")

        def on_val_loss_report(self, val_info: dict[str, Any]) -> None:
            records.append({"kind": "training_validation", **val_info})
            with (output / "losses.jsonl").open("a") as handle:
                handle.write(json.dumps(records[-1], allow_nan=False) + "\n")

    positive_steps = [step for step in config["checkpoint_steps"] if step]
    save_every = math.gcd(*positive_steps)
    training_args = TrainingArgs(
        batch_size=config["batch_size"],
        iters=config["iters"],
        val_batches=2,
        steps_per_report=min(10, config["iters"]),
        steps_per_eval=config["iters"],
        steps_per_save=save_every,
        max_seq_length=config["max_seq_length"],
        adapter_file=str(work / "adapters.safetensors"),
        grad_checkpoint=False,
    )
    start = time.monotonic()
    train_model(
        model=model,
        args=training_args,
        optimizer=optim.Adam(learning_rate=config["learning_rate"]),
        train_dataset=CacheDataset(train_set),
        val_dataset=CacheDataset(valid_set),
        training_callback=Callback(),
    )
    duration = time.monotonic() - start
    summary = {
        "seed": seed,
        "iterations": config["iters"],
        "elapsed_seconds": duration,
        "peak_memory_bytes": mx.get_peak_memory(),
        "checkpoints": [],
        "trainable_parameters": sum(
            array.size for _, array in tree_flatten(model.trainable_parameters())
        ),
        "training_examples": len(train_set),
        "validation_examples": len(valid_set),
    }
    for step in config["checkpoint_steps"]:
        directory = output / f"checkpoint-{step}"
        if step:
            directory.mkdir(mode=0o700)
            shutil.copyfile(
                work / f"{step:07d}_adapters.safetensors", directory / "adapters.safetensors"
            )
            _write_json(directory / "adapter_config.json", adapter_config)
        weights_path = directory / "adapters.safetensors"
        tensors = mx.load(str(weights_path))
        # Canonical float32 value hashes also expose actual parameter changes across checkpoints.
        arrays = [
            {
                "name": name,
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "float32_value_sha256": hashlib.sha256(
                    np.asarray(array.astype(mx.float32)).tobytes()
                ).hexdigest(),
            }
            for name, array in sorted(tensors.items())
        ]
        metadata = {
            "format": "everval-trained-lora-v1",
            "seed": seed,
            "step": step,
            "base_sha256": base["sha256"],
            "adapter_sha256": sha256(weights_path),
            "training_config_sha256": sha256(output / "training-config.json"),
            "arrays": arrays,
            "initial_zero_effect_adapter": step == 0,
        }
        _write_json(directory / "checkpoint.json", metadata)
        weights_path.chmod(0o600)
        summary["checkpoints"].append(
            {
                "step": step,
                "directory": str(directory),
                "adapter_sha256": metadata["adapter_sha256"],
            }
        )
    _write_json(output / "training-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    training = subparsers.add_parser("train")
    training.add_argument("--config", type=Path, required=True)
    inspect = subparsers.add_parser("fingerprint")
    inspect.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = (
        train(json.loads(args.config.read_text()))
        if args.command == "train"
        else fingerprint(args.directory)
    )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
