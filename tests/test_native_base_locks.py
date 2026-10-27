"""Native dependency preparation rejects unverified artifacts before installation."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "scripts/judge_study/environments/inherited-base"
SPEC = importlib.util.spec_from_file_location(
    "native_locked_artifacts", DIRECTORY / "fetch_locked.py"
)
assert SPEC is not None and SPEC.loader is not None
fetcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetcher)


class Response(io.BytesIO):
    def geturl(self):
        return "https://files.pythonhosted.org/packages/artifact.whl"


def wheel(data=b"verified package"):
    return {
        "filename": "example-1-py3-none-any.whl",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "url": "https://files.pythonhosted.org/packages/artifact.whl",
    }


def test_exact_bytes_are_verified_and_reused_without_a_download(tmp_path, monkeypatch):
    data = b"verified package"
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: Response(data))
    path = fetcher.fetch_one(wheel(data), "wheels", tmp_path)
    assert path.read_bytes() == data
    monkeypatch.setattr(
        fetcher.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Unexpected second download")
    )
    assert fetcher.fetch_one(wheel(data), "wheels", tmp_path) == path


@pytest.mark.parametrize("data", [b"bad", b"unverified data!", b"x" * 100])
def test_bad_digest_or_byte_count_leaves_no_installable_artifact(tmp_path, monkeypatch, data):
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: Response(data))
    with pytest.raises(ValueError):
        fetcher.fetch_one(wheel(), "wheels", tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field,value",
    [
        ("filename", "../outside.whl"),
        ("filename", "bad name.whl"),
        ("sha256", "0" * 32),
        ("size_bytes", True),
        ("size_bytes", 0),
        ("url", "https://user:password@files.pythonhosted.org/packages/artifact.whl"),
        ("url", "http://files.pythonhosted.org/packages/artifact.whl"),
        ("url", "https://unreviewed.example/artifact.whl"),
        ("url", "https://files.pythonhosted.org/packages/artifact.whl?token=x"),
    ],
)
def test_unreviewed_metadata_is_rejected_before_fetch(field, value):
    item = wheel()
    item[field] = value
    with pytest.raises(ValueError):
        fetcher.artifact(item, "wheels")


def test_existing_wrong_artifact_is_preserved_and_rejected(tmp_path):
    target = tmp_path / wheel()["filename"]
    target.write_bytes(b"existing evidence")
    with pytest.raises(ValueError, match="Existing artifact"):
        fetcher.fetch_one(wheel(), "wheels", tmp_path)
    assert target.read_bytes() == b"existing evidence"


def test_preexisting_partial_is_not_deleted(tmp_path, monkeypatch):
    path = tmp_path / (wheel()["filename"] + ".partial")
    path.write_bytes(b"another owner")
    monkeypatch.setattr(
        fetcher.urllib.request, "urlopen", lambda *a, **k: Response(b"verified package")
    )
    with pytest.raises(FileExistsError):
        fetcher.fetch_one(wheel(), "wheels", tmp_path)
    assert path.read_bytes() == b"another owner"


def test_published_locks_are_complete_sanitized_and_source_bound():
    manifest = json.loads((DIRECTORY / "manifest-sha256.json").read_text())
    for name, expected in manifest.items():
        assert hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest() == expected
    conda = json.loads((DIRECTORY / "conda-packages.lock.json").read_text())["artifacts"]
    wheels = json.loads((DIRECTORY / "wheels.lock.json").read_text())["artifacts"]
    assert len(conda) == 27 and len(wheels) == 9
    for kind, rows in (("conda", conda), ("wheels", wheels)):
        for item in rows:
            fetcher.artifact(item, kind)
    assert sum(item["matched_historical_wheel_digest"] for item in wheels) == 6
    for path in DIRECTORY.glob("*.json"):
        text = path.read_text()
        assert "/Users/" not in text and "/Volumes/" not in text and "sk-proj-" not in text
    provenance = json.loads((DIRECTORY / "provenance.json").read_text())
    assert not provenance["byte_identical_historical_rebuild_claimed"]
    assert not provenance["resolved_ubuntu_digest_retained"]
