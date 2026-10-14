"""Fixtures are evaluated as evidence, never advertised as model performance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cvals.config import Settings
from cvals.domains import validate_collection
from cvals.ingestion import ingest_source, inspect_repository, snapshot_repository
from cvals.packs import starter_collections


def test_noncode_references_pass_and_controls_fail(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    collections = starter_collections()
    assert [(collection.domain, len(collection.scenarios)) for collection in collections] == [
        ("coding", 10),
        ("documents", 3),
        ("data", 3),
        ("workflow", 3),
    ]
    for collection in collections[1:]:
        report = validate_collection(collection, settings)
        assert report["valid"], report
        for scenario in report["scenarios"]:
            assert scenario["checks"]
            assert all(control["rejected"] for control in scenario["negative_controls"])


def test_starters_are_independent_and_agent_view_contains_no_answers() -> None:
    first, second = starter_collections(), starter_collections()
    first[0].scenarios[0].inputs["extra"] = "changed"
    assert "extra" not in second[0].scenarios[0].inputs
    for collection in second:
        for scenario in collection.scenarios:
            public = scenario.agent_view()
            assert set(public) == {"id", "name", "domain", "instruction", "inputs"}
            assert "negative_controls" not in json.dumps(public)
            assert scenario.provenance["origin"] == "original_fixture"
            assert scenario.reference_output is not None


def test_weak_scenario_is_not_validated(tmp_path: Path) -> None:
    collection = starter_collections()[2]
    scenario = collection.scenarios[0]
    scenario.provenance["negative_controls"] = [scenario.reference_output]
    report = validate_collection(collection, Settings(data_dir=tmp_path))
    assert not report["valid"]
    assert "passed; checks need strengthening" in report["scenarios"][0]["errors"][0]


def test_csv_preview_preserves_values_without_executing_formulas() -> None:
    result = ingest_source("records.csv", b"id,amount\nA,001\nB,=1+2\n")
    assert result["record_count"] == 2
    assert result["records"] == [{"id": "A", "amount": "001"}, {"id": "B", "amount": "=1+2"}]
    assert len(result["sha256"]) == 64


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("a.csv", b"name,name\n1,2\n"),
        ("a.csv", b"name,amount\n1\n"),
        ("a.json", b'{"value":1,"value":2}'),
        ("a.json", b'{"value":NaN}'),
        ("a.json", b'{"value":Infinity}'),
        ("a.json", b"[" * 40 + b"0" + b"]" * 40),
        ("a.json", b"123"),
        ("a.txt", b"\xff"),
        ("a.txt", b"bad\x00text"),
        ("a.py", b"print('never execute')"),
        ("a.pdf", b"not a PDF"),
        ("a.txt", b""),
        ("a.txt", b"x" * (5 * 1024 * 1024 + 1)),
    ],
)
def test_bad_sources_are_rejected(filename: str, content: bytes) -> None:
    with pytest.raises(ValueError):
        ingest_source(filename, content)


def test_text_source_has_stable_source_id_and_safe_display_name() -> None:
    result = ingest_source("../../memo.md", b"# Policy\nKeep data for 30 days.")
    assert result["filename"] == "memo.md"
    assert result["sources"] == [{"id": result["id"], "text": "# Policy\nKeep data for 30 days."}]
    assert ingest_source("other.md", b"# Policy\nKeep data for 30 days.")["id"] == result["id"]


def _git(path: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    )
    return process.stdout.strip()


def test_repository_inspection_and_snapshot_never_run_hooks(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "fixture@example.test")
    _git(repository, "config", "user.name", "Repository fixture")
    (repository / "solution.py").write_text("raise RuntimeError('must never execute on host')\n")
    (repository / "test_secret.py").write_text("SECRET_REFERENCE = 'answer'\n")
    (repository / "shortcut").symlink_to("/etc/passwd")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "Initial fixture")
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "solution.py").write_text("raise RuntimeError('still must not execute')\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "Repair fixture")
    marker = tmp_path / "hook-was-run"
    hook = repository / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    _git(repository, "config", "core.fsmonitor", str(hook))
    result = inspect_repository(str(repository))
    assert result["executed_repository_code"] is False
    assert result["languages"] == ["Python"]
    assert result["candidates"][0]["base_commit"] == base
    assert result["candidates"][0]["changed_files"] == ["solution.py"]
    assert result["candidates"][1]["review_status"] == "unsupported"
    snapshot = snapshot_repository(str(repository), base)
    assert set(snapshot["files"]) == {"solution.py"}
    assert len(snapshot["omitted"]) == 2
    assert not snapshot["includes_git_history"]
    assert not snapshot["complete"]
    assert not marker.exists()
    assert (
        repository / "solution.py"
    ).read_text() == "raise RuntimeError('still must not execute')\n"


@pytest.mark.parametrize(
    "revision", ["HEAD", "--help", "abc", "../main", "a" * 40 + ";touch /tmp/no"]
)
def test_snapshot_requires_an_immutable_hash(tmp_path: Path, revision: str) -> None:
    with pytest.raises(ValueError):
        snapshot_repository(str(tmp_path), revision)


def test_pdf_text_extraction_and_blank_document_rejection() -> None:
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Approved within 4 hours.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    result = ingest_source("policy.pdf", buffer.getvalue())
    assert result["pages"] == 1
    assert "Approved within 4 hours." in result["text"]
    assert result["sources"][0]["page"] == 1
    blank = PdfWriter()
    blank.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    blank.write(buffer)
    with pytest.raises(ValueError, match="no extractable text"):
        ingest_source("scan.pdf", buffer.getvalue())


def test_local_parser_output_is_bounded() -> None:
    import os
    import sys

    from cvals.domains._process import run_bounded

    with pytest.raises(ValueError, match="size limit"):
        run_bounded(
            [sys.executable, "-I", "-c", "print('x' * 100000)"],
            env={"PATH": os.environ.get("PATH", "")},
            timeout=5,
            limit_bytes=1000,
        )


def test_all_optional_checks_use_all_checks_for_validation(tmp_path: Path) -> None:
    collection = starter_collections()[2]
    scenario = collection.scenarios[0]
    for check in scenario.checks:
        check.required = False
    settings = Settings(data_dir=tmp_path)
    assert validate_collection(collection, settings)["valid"]
    assert scenario.reference_output is not None
    scenario.reference_output["payable_total_cents"] = -1
    report = validate_collection(collection, settings)
    assert not report["valid"]
    assert "Reference output did not pass" in report["scenarios"][0]["errors"][0]
