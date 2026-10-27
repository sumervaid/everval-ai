"""Private, integrity-checked references from revealed blind-review studies."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Literal

from pydantic import Field, model_validator

from cvals.blind_studies import BlindStudies, _evidence
from cvals.config import Settings
from cvals.judge_calibration_models import CalibrationItem
from cvals.models import Manifest, canonical_json, content_hash, utc_now


class CollectionProvenance(Manifest):
    """Collection claims are supplied by the custodian, never inferred from tokens."""

    source: Literal["human_collected", "scripted", "unverified"] = "unverified"
    description: str = Field(
        default="Reviewer provenance has not been independently established.", max_length=5000
    )
    collection_protocol: str = Field(default="", max_length=2000)
    independent_reviewers: bool | None = None

    @model_validator(mode="after")
    def collection_claim(self) -> CollectionProvenance:
        if self.source == "human_collected" and (
            not self.description.strip()
            or not self.collection_protocol.strip()
            or self.independent_reviewers is not True
        ):
            raise ValueError(
                "Human collection requires a protocol, description and explicit independence declaration"
            )
        return self


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def export_study_references(
    settings: Settings,
    study_id: str,
    *,
    actor: str = "local-console",
    provenance: CollectionProvenance | None = None,
) -> dict[str, Any]:
    """Export one revealed study locally; do not upload this private bundle.

    Reviewer names and credentials are excluded. Reviewers receive stable,
    study-scoped pseudonyms. Disagreement remains unlabeled rather than being
    silently adjudicated. Repetitions retain their original shared task identity.
    """
    collection = CollectionProvenance.model_validate(
        (provenance or CollectionProvenance()).model_dump()
    )
    studies = BlindStudies(settings)
    with studies.store._connect() as db:
        db.execute("BEGIN")  # Read every hash and linked record from one SQLite snapshot.
        record = studies._read(db, study_id)
        studies._coordinator(db, record, actor)
        protocol, state = record["protocol"], record["state"]
        _require(state["status"] == "revealed", "Study results remain sealed until reveal")
        evidence = _evidence(db, record)
        _require(
            evidence["judgments"] == state["locked_judgments"],
            "Locked judgment evidence changed",
        )
        snapshot, _ = studies.store._read_experiment(db, protocol["experiment_id"])
        _require(
            content_hash(snapshot) == protocol["source_snapshot_hash"],
            "Frozen source snapshot changed",
        )
        queue = json.loads(
            db.execute(
                "SELECT data FROM review_queues WHERE id=?", (record["queue_id"],)
            ).fetchone()[0]
        )
        rubric = queue["rubric"]
        rubric_body = {
            key: rubric[key] for key in ("name", "version", "instructions", "dimensions")
        }
        _require(
            content_hash(rubric_body) == protocol["rubric_hash"] == rubric["hash"],
            "Frozen rubric changed",
        )
        scenarios = {value["id"]: value for value in snapshot["collection"]["scenarios"]}
        systems = {value["id"]: value for value in snapshot["systems"]}
        canonical_labels = {
            system: label for system, label in zip(protocol["system_ids"], "AB", strict=True)
        }
        judgments: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for locked in state["locked_judgments"]:
            row = db.execute(
                "SELECT data FROM review_judgments WHERE id=?", (locked["id"],)
            ).fetchone()
            _require(row is not None, "A locked judgment is missing")
            value = json.loads(row[0])
            _require(content_hash(value) == locked["hash"], "A locked judgment changed")
            judgments[value["item_id"]].append(value)

        items: list[dict[str, Any]] = []
        lineage: list[dict[str, Any]] = []
        source_partition = snapshot["collection"].get("benchmark_partition")
        partition = "holdout" if source_partition == "holdout" else "dev"
        for row in db.execute(
            "SELECT data FROM review_queue_items WHERE queue_id=? ORDER BY position",
            (record["queue_id"],),
        ):
            item = json.loads(row[0])
            item_hash = item["evidence_hash"]
            _require(
                content_hash({key: value for key, value in item.items() if key != "evidence_hash"})
                == item_hash,
                "Frozen review item changed",
            )
            _require(set(item["responses"]) == {"A", "B"}, "A paired item needs both responses")
            label_map: dict[str, str] = {}
            outputs: dict[str, Any] = {}
            trial_rows: list[dict[str, Any]] = []
            for label, response in item["responses"].items():
                system_id = response["system_id"]
                _require(system_id in canonical_labels, "Response system left the frozen study")
                canonical = canonical_labels[system_id]
                label_map[label] = canonical
                outputs[canonical] = response["output"]
                trial_row = db.execute(
                    "SELECT data FROM trials WHERE id=? AND experiment_id=?",
                    (response["trial_id"], protocol["experiment_id"]),
                ).fetchone()
                _require(trial_row is not None, "Source trial is missing")
                trial = json.loads(trial_row[0])
                _require(
                    trial["system_id"] == system_id
                    and content_hash(trial["output"]) == content_hash(response["output"]),
                    "Response no longer matches its frozen trial",
                )
                trial_rows.append(trial)
            _require(len(outputs) == 2, "Response mapping is not one-to-one")
            task_ids = {trial["scenario_id"] for trial in trial_rows}
            repetitions = {trial["repetition"] for trial in trial_rows}
            _require(
                len(task_ids) == len(repetitions) == 1, "Responses refer to different task attempts"
            )
            task_id = next(iter(task_ids))
            scenario = scenarios[task_id]
            task = {key: scenario[key] for key in ("name", "domain", "instruction", "inputs")}
            _require(
                content_hash(task) == content_hash(item["task"]), "Public task evidence changed"
            )
            family_id = scenario.get("provenance", {}).get("family_id", task_id)
            _require(
                isinstance(family_id, str) and 0 < len(family_id) <= 200, "Invalid task family"
            )
            labels: list[dict[str, Any]] = []
            raw_reviews = []
            for judgment in judgments[item["id"]]:
                _require(
                    judgment["evidence_hash"] == item_hash
                    and judgment["rubric_hash"] == rubric["hash"],
                    "Judgment refers to different task or rubric evidence",
                )
                preferred = judgment["preference"]
                _require(preferred in {"A", "B", "tie"}, "Invalid locked preference")
                canonical_preference = label_map.get(preferred, "tie")
                reviewer = content_hash({"study": study_id, "reviewer": judgment["reviewer_id"]})
                labels.append({"reviewer_id": reviewer, "label": canonical_preference})
                raw_reviews.append(
                    {
                        "reviewer_id": reviewer,
                        "judgment_id": judgment["id"],
                        "judgment_hash": content_hash(judgment),
                        "presented_preference": preferred,
                        "canonical_preference": canonical_preference,
                        "presented_scores": judgment["scores"],
                        "canonical_scores": {
                            label_map[label]: scores for label, scores in judgment["scores"].items()
                        },
                        "rationale": judgment["rationale"],
                        "disposition": judgment["disposition"],
                        "created_at": judgment["created_at"],
                    }
                )
            _require(
                len(labels) == len(protocol["reviewer_ids"]), "Incomplete locked review coverage"
            )
            unique = {value["label"] for value in labels}
            consensus = next(iter(unique)) if len(unique) == 1 else None
            item_lineage = {
                "item_id": item["id"],
                "item_hash": item_hash,
                "task_id": task_id,
                "repetition": next(iter(repetitions)),
                "presented_to_canonical": label_map,
                "output_hashes": {label: content_hash(output) for label, output in outputs.items()},
                "raw_reviews": raw_reviews,
            }
            lineage.append(item_lineage)
            items.append(
                {
                    "item_id": item["id"],
                    "task_id": task_id,
                    "family_id": family_id,
                    "partition": partition,
                    "task": {"instruction": task["instruction"], "inputs": task["inputs"]},
                    "responses": outputs,
                    "generator_models": {
                        canonical_labels[system_id]: systems[system_id].get("model") or system_id
                        for system_id in protocol["system_ids"]
                    },
                    "reference_context": None,
                    "reference": {
                        "source": collection.source,
                        "label": consensus,
                        "reviewer_labels": labels,
                        "provenance": canonical_json(
                            {
                                **collection.model_dump(mode="json"),
                                "assurance": "Custodian declaration, not independent identity verification",
                                "consensus_method": "unanimous_without_adjudication",
                                "study_id": study_id,
                                "fixture_controls_present": any(
                                    systems[system_id]["kind"] in {"reference", "incomplete"}
                                    for system_id in protocol["system_ids"]
                                ),
                            }
                        ),
                        "source_digest": content_hash(item_lineage),
                    },
                }
            )
        items = [CalibrationItem.model_validate(item).model_dump(mode="json") for item in items]
        _require(len(items) == evidence["items"], "Export coverage differs from locked study")
        bundle = {
            "schema_version": "everval.judge-reference/v1",
            "created_at": utc_now(),
            "study_id": study_id,
            "protocol_hash": record["protocol_hash"],
            "locked_evidence_hash": state["locked_evidence_hash"],
            "source_snapshot_hash": protocol["source_snapshot_hash"],
            "rubric": rubric_body,
            "provenance": collection.model_dump(mode="json"),
            "partition_note": "Existing holdout membership is preserved; other sources default to development.",
            "privacy": "Private evaluator bundle: labels, judgments and generator identities are not judge inputs.",
            "items": items,
            "lineage": lineage,
        }
        return {**bundle, "bundle_hash": content_hash(bundle)}


def verify_reference_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("schema_version") != "everval.judge-reference/v1":
        raise ValueError("Unsupported reference bundle")
    expected = content_hash({key: value for key, value in bundle.items() if key != "bundle_hash"})
    if bundle.get("bundle_hash") != expected:
        raise ValueError("Reference bundle integrity check failed")
    canonical_json(bundle)
