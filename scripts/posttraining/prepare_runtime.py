"""Prepare the pinned local study without launching training or evaluation.

Only the download subcommand uses the network. Conversion uses Apple Silicon;
configuration and manifests are write-once and refuse conflicting prior files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .model_runtime import fingerprint, sha256

HERE = Path(__file__).resolve().parent
SOURCE_MANIFEST = HERE / "model-source-manifest.json"
SEEDS = [17, 29, 43]
STEPS = [0, 80, 160, 320]


def write_once(path: Path, value: Any) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    write_once_text(path, encoded)


def write_once_text(path: Path, value: str) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink: {path.name}")
    if path.exists():
        if path.read_text() != value:
            raise ValueError(f"Existing artifact differs: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as handle:
        handle.write(value)
    path.chmod(0o600)


def source_manifest() -> dict[str, Any]:
    return json.loads(SOURCE_MANIFEST.read_text())


def verify_source(directory: Path) -> dict[str, Any]:
    expected = source_manifest()
    actual = fingerprint(directory)
    if actual["files"] != expected["files"]:
        raise ValueError("Local source model differs from the pinned file manifest")
    return expected


def download_file(url: str, target: Path, *, size: int, digest: str) -> None:
    """Download anonymously to an exclusive partial file; verify before publishing."""
    if target.is_symlink():
        raise ValueError("Model source must not be a symlink")
    if target.exists():
        if target.stat().st_size != size or sha256(target) != digest:
            raise ValueError(f"Existing source differs: {target.name}")
        return
    partial = target.with_suffix(target.suffix + ".part")
    if partial.exists() or partial.is_symlink():
        raise ValueError(f"Interrupted download exists; inspect it first: {partial.name}")
    total = 0
    actual = hashlib.sha256()
    created = False
    try:
        with partial.open("xb") as output:
            created = True
            request = urllib.request.Request(url, headers={"User-Agent": "Everval-study/1"})
            with urllib.request.urlopen(request, timeout=120) as response:
                if not response.url.startswith("https://"):
                    raise ValueError("Download redirected outside HTTPS")
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > size:
                        raise ValueError("Downloaded artifact exceeds its declared size")
                    actual.update(block)
                    output.write(block)
        if total != size or actual.hexdigest() != digest:
            raise ValueError(f"Downloaded source digest mismatch: {target.name}")
        partial.chmod(0o600)
        # Link is exclusive: a concurrent creator cannot be overwritten.
        os.link(partial, target)
    finally:
        if created:
            partial.unlink(missing_ok=True)


def download(study: Path) -> None:
    manifest = source_manifest()
    destination = study / "qwen-upstream"
    if destination.is_symlink():
        raise ValueError("Model destination must be a real directory")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = f"https://huggingface.co/{manifest['model']}/resolve/{manifest['revision']}"
    for item in manifest["files"]:
        name = item["path"]
        if Path(name).name != name:
            raise ValueError("Pinned model files must be flat regular files")
        print(f"Verifying {name}", flush=True)
        download_file(
            f"{base}/{name}",
            destination / name,
            size=item["bytes"],
            digest=item["sha256"],
        )
    verify_source(destination)
    write_once(study / "source-file-manifest.json", manifest)
    write_once(
        study / "upstream-model.json",
        {
            "model": manifest["model"],
            "revision": manifest["revision"],
            "license": "apache-2.0",
            "files": [item["path"] for item in manifest["files"]],
            "platform": platform.platform(),
        },
    )


def convert(study: Path) -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This conversion recipe is supported on Apple Silicon macOS")
    verify_source(study / "qwen-upstream")
    target = study / "qwen-mlx-4bit"
    if target.exists() or target.is_symlink():
        raise ValueError("Converted model already exists; do not overwrite a frozen model")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "mlx_lm.convert",
            "--hf-path",
            str(study / "qwen-upstream"),
            "--mlx-path",
            str(target),
            "--quantize",
            "--q-bits",
            "4",
            "--q-group-size",
            "64",
            "--q-mode",
            "affine",
            "--dtype",
            "bfloat16",
        ],
        check=True,
    )
    write_once(study / "converted-file-manifest.json", fingerprint(target))


def training_configs(study: Path) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    shared = {
        "model": str(study / "qwen-mlx-4bit"),
        "data": str(study / "dataset"),
        "iters": 320,
        "checkpoint_steps": STEPS,
        "batch_size": 4,
        "learning_rate": 1e-4,
        "num_layers": 4,
        "rank": 8,
        "max_seq_length": 512,
    }
    declared = {
        **shared,
        "seeds": SEEDS,
        "lora_scale": 16.0,
        "lora_dropout": 0.0,
        "lora_keys": ["self_attn.q_proj", "self_attn.v_proj"],
        "optimizer": "Adam",
        "prompt_masking": True,
        "gradient_checkpointing": False,
        "gradient_accumulation_steps": 1,
        "validation_batches": 2,
        "validation_interval": 320,
    }
    seeds = {
        seed: {**shared, "seed": seed, "output": str(study / f"training/seed-{seed}")}
        for seed in SEEDS
    }
    return declared, seeds


def dependency_snapshot() -> str:
    rows = []
    for line in (HERE / "requirements.in").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==")
        package = name.split("[")[0]
        installed = importlib.metadata.version(package)
        if installed != expected:
            raise ValueError(f"Installed {package} differs from the study dependency pin")
        rows.append(f"{package}=={installed}")
    rows.append(f"everval-ai=={importlib.metadata.version('everval-ai')}")
    return "\n".join(sorted(rows, key=str.casefold)) + "\n"


def configure(study: Path) -> None:
    verify_source(study / "qwen-upstream")
    base = fingerprint(study / "qwen-mlx-4bit")
    existing = json.loads((study / "converted-file-manifest.json").read_text())
    if base != existing:
        raise ValueError("Converted model changed after conversion")
    for filename in ("train.jsonl", "valid.jsonl", "dataset-manifest.json"):
        path = study / "dataset" / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("Build the original dataset before configuration")
    snapshot = dependency_snapshot()
    declared, seeds = training_configs(study)
    write_once_text(study / "requirements.lock", (HERE / "requirements.lock").read_text())
    write_once_text(study / "runtime-freeze.txt", snapshot)
    write_once(study / "training-config.json", declared)
    for seed, value in seeds.items():
        write_once(study / f"training-seed-{seed}-config.json", value)
    print("Prepared model, runtime and training manifests. Freeze the protocol before training.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("command", choices=("download", "convert", "configure"))
    args = parser.parse_args()
    study = args.study.expanduser().absolute()
    if study.is_symlink():
        raise ValueError("Study root must be a real directory")
    study.mkdir(parents=True, exist_ok=True, mode=0o700)
    {"download": download, "convert": convert, "configure": configure}[args.command](study)


if __name__ == "__main__":
    main()
