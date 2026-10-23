"""CPU-only checks for reproducible local study preparation."""

from __future__ import annotations

import hashlib
import importlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
try:
    setup = importlib.import_module("scripts.posttraining.prepare_runtime")
finally:
    sys.path.pop(0)


class Response(io.BytesIO):
    url = "https://example.test/weights"


def test_download_rejects_mismatched_bytes_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **kw: Response(b"changed"))
    target = tmp_path / "weights.safetensors"
    with pytest.raises(ValueError, match="digest mismatch"):
        setup.download_file("https://example.test/weights", target, size=7, digest="0" * 64)
    assert not target.exists()
    assert not target.with_suffix(".safetensors.part").exists()


def test_download_verifies_existing_file_without_network(tmp_path, monkeypatch):
    target = tmp_path / "weights.safetensors"
    target.write_bytes(b"verified")

    def no_network(*args, **kwargs):
        pytest.fail("A verified local file should not be downloaded again")

    monkeypatch.setattr(setup.urllib.request, "urlopen", no_network)
    setup.download_file(
        "https://example.test/weights",
        target,
        size=8,
        digest=hashlib.sha256(b"verified").hexdigest(),
    )
    target.write_bytes(b"modified")
    with pytest.raises(ValueError, match="Existing source differs"):
        setup.download_file("https://example.test/weights", target, size=8, digest="0" * 64)


def test_partial_download_is_preserved_for_inspection(tmp_path):
    target = tmp_path / "weights.safetensors"
    partial = target.with_suffix(".safetensors.part")
    partial.write_bytes(b"prior interrupted bytes")
    with pytest.raises(ValueError, match="Interrupted download"):
        setup.download_file("https://example.test/weights", target, size=1, digest="0" * 64)
    assert partial.read_bytes() == b"prior interrupted bytes"


def test_write_once_refuses_conflicting_or_symlinked_manifests(tmp_path):
    path = tmp_path / "config.json"
    setup.write_once(path, {"seed": 17})
    setup.write_once(path, {"seed": 17})
    with pytest.raises(ValueError, match="Existing artifact differs"):
        setup.write_once(path, {"seed": 29})
    link = tmp_path / "alias.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        setup.write_once(link, {"seed": 17})
    assert path.read_text() == '{\n  "seed": 17\n}\n'


def test_configs_use_independent_outputs_and_frozen_schedule(tmp_path):
    shared, seeds = setup.training_configs(tmp_path)
    assert shared["seeds"] == [17, 29, 43]
    assert shared["checkpoint_steps"] == [0, 80, 160, 320]
    assert len({config["output"] for config in seeds.values()}) == 3
    for seed, config in seeds.items():
        assert config["seed"] == seed
        assert Path(config["model"]) == tmp_path / "qwen-mlx-4bit"
        assert config["iters"] == 320
        assert config["batch_size"] == 4
        assert "resume" not in config
