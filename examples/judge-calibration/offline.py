"""Run scripted measurement controls. No model inference or human study occurs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from everval import JudgeCalibration, Settings, write_judge_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(Path(__file__).with_name("control.json").read_text())
    service = JudgeCalibration(Settings(args.workspace))
    campaign = service.create(value)
    identifier = campaign["id"]
    items = {item["item_id"]: item for item in value["items"]}
    for partition in ("dev", "holdout"):
        if partition == "holdout":
            service.release_holdout(
                identifier,
                [judge["id"] for judge in value["judges"]],
                "Keep both predeclared installation controls; no model selection.",
            )
        rows = []
        for job in service.export_requests(identifier, partition)["jobs"]:
            if job["status"] != "queued":
                continue
            canonical = items[job["item_id"]]["reference"]["label"]
            choice = (
                ("A" if job["order"][0] == canonical else "B")
                if job["judge_id"] == "content-control"
                else "A"
            )
            rows.append(
                {
                    "job_id": job["job_id"],
                    "response": {
                        "preference": choice,
                        "rationale": "Scripted installation control; no model inference.",
                    },
                    "cost_usd": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_ms": 0,
                }
            )
        if rows:
            service.import_observations(identifier, rows)
    report = service.analyze(identifier)
    directory = args.workspace / "judge-control-report"
    directory.mkdir(parents=True, exist_ok=True)
    for suffix in ("json", "html"):
        destination = directory / f"report.{suffix}"
        if not destination.exists():
            write_judge_report(report, destination)
    print(
        json.dumps(
            {
                "campaign_id": identifier,
                "source": "scripted installation controls",
                "provider_calls": 0,
                "human_participants": 0,
                "report": str(directory / "report.html"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
