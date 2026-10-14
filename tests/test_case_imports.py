"""Observed production outputs never silently become expected behavior."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from cvals.api import create_app
from cvals.case_imports import Approval, CaseImports
from cvals.config import Settings


def source():
    return {
        "name": "Invoice regression",
        "domain": "data",
        "instruction": "Add amounts.",
        "inputs": {
            "amounts": [20, 22],
            "email": "private@example.com",
            "note": "Customer PRIVATE-42",
        },
        "observed_output": {"total": 999},
        "source_id": "ticket-42",
    }


def approval():
    return {
        "expected_behavior": "Return the sum of all invoice amounts.",
        "rationale": "The accounting owner verified the expected total.",
        "reference_output": {"total": 42},
        "checks": [{"name": "Correct total", "kind": "numeric", "path": "total", "expected": 42}],
        "negative_controls": [{}, {"total": 999}],
    }


def test_staging_redacts_before_persistence_and_never_creates_ground_truth(tmp_path):
    settings = Settings(tmp_path)
    cases = CaseImports(settings)
    staged = cases.stage(
        json.dumps([source()]).encode(),
        "incidents.json",
        "operator",
        redacted_literals=["PRIVATE-42"],
    )
    case = staged["cases"][0]
    assert case["inputs"]["email"] == "[REDACTED]"
    assert case["inputs"]["note"] == "Customer [REDACTED]"
    assert case["observed_output"]["total"] == 999
    assert case["approval"] is None and case["status"] == "staged"
    with sqlite3.connect(settings.database_path) as db:
        dump = "\n".join(db.iterdump())
        assert "private@example.com" not in dump and "PRIVATE-42" not in dump
        assert db.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 0
    with pytest.raises(ValueError, match="Only approved"):
        cases.publish(staged["id"], [case["id"]], "No premature tests", "operator")


def test_human_approval_validates_controls_then_publishes_atomically(tmp_path):
    settings = Settings(tmp_path)
    cases = CaseImports(settings)
    batch = cases.stage(json.dumps({"cases": [source()]}).encode(), "cases.json", "operator")
    case_id = batch["cases"][0]["id"]
    wrong = approval()
    wrong["reference_output"] = {"total": 999}
    with pytest.raises(ValueError, match="did not validate"):
        cases.approve(case_id, Approval.model_validate(wrong), "reviewer")
    assert cases.get(batch["id"])["cases"][0]["status"] == "staged"
    saved = cases.approve(case_id, Approval.model_validate(approval()), "reviewer")
    assert saved["scenario"]["reference_output"]["total"] == 42
    result = cases.publish(batch["id"], [case_id], "Invoice incidents", "operator")
    assert result["validation"]["valid"]
    assert result["collection"]["scenarios"][0]["provenance"]["approved_by"] == "reviewer"
    assert "observed_output" not in result["collection"]["scenarios"][0]
    assert cases.get(batch["id"])["cases"][0]["collection_id"] == result["collection"]["id"]
    with pytest.raises(ValueError):
        cases.publish(batch["id"], [case_id], "Duplicate", "operator")


@pytest.mark.parametrize(
    "filename,raw",
    [
        ("cases.jsonl", json.dumps(source()) + "\n"),
        (
            "cases.csv",
            'name,domain,instruction,inputs,observed_output\nInvoice,data,Add amounts.,"{""amounts"":[20,22]}","{""total"":999}"\n',
        ),
    ],
)
def test_browser_upload_accepts_jsonl_and_csv(tmp_path, filename, raw):
    with TestClient(create_app(Settings(tmp_path), start_worker=False)) as client:
        response = client.post(
            "/api/case-imports", files={"file": (filename, raw)}, data={"redacted_literals": "[]"}
        )
        assert response.status_code == 201, response.text
        case_id = response.json()["cases"][0]["id"]
        approved = client.post(f"/api/production-cases/{case_id}/approve", json=approval())
        assert approved.status_code == 201, approved.text


def test_import_rejects_supplied_ground_truth_and_invalid_redactions(tmp_path):
    cases = CaseImports(Settings(tmp_path))
    with pytest.raises(ValueError):
        cases.stage(
            json.dumps([{**source(), "reference_output": {"total": 999}}]).encode(),
            "cases.json",
            "operator",
        )
    with pytest.raises(ValueError, match="Redaction"):
        cases.stage(
            json.dumps([source()]).encode(), "cases.json", "operator", redacted_literals=[""]
        )
