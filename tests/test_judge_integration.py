"""Locked blind reviews flow into calibration and Inspect without label leakage."""

import json
import sys
from types import SimpleNamespace

from test_blind_studies import workspace as workspace
from test_judge_reference import reveal

from cvals.inspect_export import export_inspect, load_inspect_dataset
from cvals.judge_calibration import JudgeCalibration
from cvals.judge_reference import CollectionProvenance, export_study_references


def test_locked_reviews_import_and_inspect_share_canonical_reference(
    workspace, tmp_path, monkeypatch
):
    w = workspace
    bundle = export_study_references(
        w.settings,
        reveal(w)["id"],
        provenance=CollectionProvenance(
            source="scripted", description="Automated control reviewers."
        ),
    )
    assert all(item["reference_context"] is None for item in bundle["items"])
    service = JudgeCalibration(w.settings)
    identifier = service.create(
        {
            "name": "Locked reference integration",
            "rubric": bundle["rubric"],
            "judges": [{"id": "offline", "model": "offline-control", "revision": "1"}],
            "items": bundle["items"],
            "budget_usd": 0,
        }
    )["id"]
    items = {item["item_id"]: item for item in bundle["items"]}
    requests = service.export_requests(identifier)["jobs"]
    records = []
    for request in requests:
        item = items[request["item_id"]]
        reference = item["reference"]
        displayed_label = dict(zip(request["order"], ("A", "B"), strict=True))[reference["label"]]
        prompt = json.dumps(request["payload"])
        assert reference["provenance"] not in prompt
        assert all(row["reviewer_id"] not in prompt for row in reference["reviewer_labels"])
        assert "reference_output" not in prompt and "hidden_checks" not in prompt
        records.append(
            {
                "job_id": request["job_id"],
                "response": {"preference": displayed_label, "rationale": "Scripted control."},
                "cost_usd": 0,
            }
        )
    service.import_observations(identifier, records)
    analysis = service.analyze(identifier, "dev")
    assert analysis["analysis"]["judges"][0]["agreement"]["raw_agreement"] == 1.0
    assert analysis["analysis"]["reference_sources"] == {"scripted": 4}
    destination = tmp_path / "inspect"
    manifest = export_inspect(service, identifier, "offline", destination)
    monkeypatch.setitem(
        sys.modules,
        "inspect_ai.dataset",
        SimpleNamespace(Sample=lambda **fields: fields, MemoryDataset=list),
    )
    dataset = load_inspect_dataset(destination)
    expected = {row["job_id"]: row["response"]["preference"] for row in records}
    assert len(dataset) == manifest["samples"] == 8
    assert all(sample["target"] == expected[sample["id"]] for sample in dataset)
    assert len({sample["metadata"]["task_id"] for sample in dataset}) == 2
