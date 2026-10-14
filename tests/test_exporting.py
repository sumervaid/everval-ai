import csv
import io

import pytest

from cvals.exporting import export_experiment


def fixture():
    return {
        "id": "report-1",
        "name": "<script>alert(1)</script>",
        "collection_hash": "abc",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "systems": [],
        "trials": [
            {
                "scenario_name": "=SUM(1,2)",
                "system_name": "test",
                "scenario_id": "s1",
                "system_id": "one",
                "repetition": 0,
                "status": "completed",
                "passed": True,
                "score": 1,
                "cost_usd": 0,
                "duration_ms": 1,
                "error": None,
            }
        ],
    }


def test_html_escapes_user_content():
    text, media = export_experiment(fixture(), "html")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert media == "text/html"


def test_csv_prevents_formula_interpretation():
    text, _ = export_experiment(fixture(), "csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["scenario_name"].startswith("'=")


def test_unknown_export_rejected():
    with pytest.raises(ValueError):
        export_experiment(fixture(), "exe")
