"""Inspect-compatible judge samples with separate evaluator-only references."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import stat
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from cvals.judge_calibration import JudgeCalibration, judge_scenario
from cvals.judge_calibration_models import CalibrationProtocol, Partition
from cvals.models import Manifest, canonical_json, content_hash


def _write(path: Path, data: str) -> str:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(data)
    return hashlib.sha256(data.encode()).hexdigest()


def export_inspect(
    service: JudgeCalibration,
    identifier: str,
    judge_id: str,
    destination: Path,
    *,
    partition: Partition = "dev",
) -> dict[str, Any]:
    """Export frozen requests, respecting holdout release before writing any files."""
    requests = service.export_requests(identifier, partition)
    protocol = CalibrationProtocol.model_validate(service.get(identifier)["protocol"])
    items = {item.item_id: item for item in protocol.items}
    jobs = [job for job in requests["jobs"] if job["judge_id"] == judge_id]
    if not jobs:
        raise ValueError("No eligible frozen requests for this judge and partition")
    public, references = [], []
    for job in jobs:
        item = items[job["item_id"]]
        scenario = judge_scenario(protocol, {**job, "id": job["job_id"]})
        public.append(
            {
                "id": job["job_id"],
                "input": scenario.instruction + "\n\n" + canonical_json(scenario.inputs),
                "metadata": {
                    "task_id": item.task_id,
                    "family_id": item.family_id,
                    "partition": partition,
                    "repetition": job["repetition"],
                    "protocol_hash": requests["protocol_hash"],
                },
            }
        )
        reference = item.reference.label
        mapping = dict(zip(job["order"], ("A", "B"), strict=True))
        target = mapping.get(reference, reference) if reference else None
        references.append(
            {
                "id": job["job_id"],
                "target": target,
                "source": item.reference.source,
                "reference_digest": content_hash(item.reference.model_dump(mode="json")),
            }
        )
    public_data = "".join(canonical_json(row) + "\n" for row in public)
    reference_data = "".join(canonical_json(row) + "\n" for row in references)
    for data in (public_data, reference_data):
        if len(data.encode()) > MAX_DATA_BYTES:
            raise ValueError("Inspect export file exceeds its byte limit")
        _rows(data.encode())
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = {
        "samples.jsonl": _write(destination / "samples.jsonl", public_data),
        "references.jsonl": _write(destination / "references.jsonl", reference_data),
    }
    manifest = {
        "schema_version": "everval.inspect/v1",
        "protocol_hash": requests["protocol_hash"],
        "judge_id": judge_id,
        "partition": partition,
        "samples": len(public),
        "labeled_samples": sum(row["target"] in {"A", "B", "tie"} for row in references),
        "files": files,
        "boundary": "Only samples.jsonl may enter a solver environment; references.jsonl stays with the evaluator.",
    }
    _write(destination / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


MAX_MANIFEST_BYTES = 64 * 1024
MAX_DATA_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 512 * 1024
MAX_ROWS = 100_000
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
TextId = Annotated[str, Field(min_length=1, max_length=200)]


class _ExportManifest(Manifest):
    schema_version: Literal["everval.inspect/v1"]
    protocol_hash: Digest
    judge_id: TextId
    partition: Partition
    samples: int = Field(ge=1, le=100_000, strict=True)
    labeled_samples: int = Field(ge=0, le=100_000, strict=True)
    files: dict[str, Digest]
    boundary: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def bounded_counts(self) -> _ExportManifest:
        if set(self.files) != {"samples.jsonl", "references.jsonl"}:
            raise ValueError("Inspect manifest must name exactly the two data files")
        if self.labeled_samples > self.samples or self.samples > MAX_ROWS:
            raise ValueError("Inspect manifest sample counts are inconsistent")
        return self


class _Metadata(Manifest):
    task_id: TextId
    family_id: TextId
    partition: Partition
    repetition: int = Field(ge=0, le=19, strict=True)
    protocol_hash: Digest


class _PublicSample(Manifest):
    id: Digest
    input: str = Field(min_length=1, max_length=512 * 1024)
    metadata: _Metadata


class _ReferenceSample(Manifest):
    id: Digest
    target: Literal["A", "B", "tie", "abstain"] | None
    source: Literal["human_collected", "execution", "scripted", "unverified"]
    reference_digest: Digest


def _bounded_read(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Inspect exports require regular files")
            if info.st_size > limit:
                raise ValueError("Inspect export file exceeds its byte limit")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Inspect export file exceeds its byte limit")
            return data
    except OSError as exc:
        raise ValueError("Inspect exports require readable regular files, not symlinks") from exc


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Inspect JSON contains a repeated object key")
        value[key] = item
    return value


def _decode(data: bytes) -> Any:
    def invalid_constant(value: str) -> None:
        raise ValueError("Inspect JSON cannot contain non-finite constants")

    try:
        return json.loads(data, object_pairs_hook=_unique_keys, parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Inspect export contains invalid or excessively nested JSON") from exc


def _rows(data: bytes) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(io.BytesIO(data), start=1):
        if index > MAX_ROWS:
            raise ValueError("Inspect export exceeds its row limit")
        if len(line) > MAX_ROW_BYTES:
            raise ValueError("Inspect export row exceeds its byte limit")
        if not line.strip():
            continue
        row = _decode(line)
        if not isinstance(row, dict):
            raise ValueError("Inspect rows must be JSON objects")
        rows.append(row)
    return rows


def load_inspect_dataset(directory: Path, *, labeled_only: bool = True) -> Any:
    """Load bounded, verified Inspect samples; targets stay outside model input.

    The default scorer dataset omits unscorable references. The export and its
    manifest retain the full denominator, which differs from human-vote analysis.
    """
    manifest = _ExportManifest.model_validate(
        _decode(_bounded_read(directory / "manifest.json", MAX_MANIFEST_BYTES))
    )
    records: dict[str, list[dict[str, Any]]] = {}
    for name in ("samples.jsonl", "references.jsonl"):
        data = _bounded_read(directory / name, MAX_DATA_BYTES)
        if hashlib.sha256(data).hexdigest() != manifest.files[name]:
            raise ValueError("Inspect export fingerprint changed")
        records[name] = _rows(data)
    public = [_PublicSample.model_validate(row) for row in records["samples.jsonl"]]
    private = [_ReferenceSample.model_validate(row) for row in records["references.jsonl"]]
    ids = [row.id for row in public]
    targets = {row.id: row.target for row in private}
    if len(ids) != len(set(ids)) or len(targets) != len(private) or set(ids) != set(targets):
        raise ValueError("Inspect sample/reference identities differ or repeat")
    if len(ids) != manifest.samples:
        raise ValueError("Inspect sample count changed")
    if sum(row.target in {"A", "B", "tie"} for row in private) != manifest.labeled_samples:
        raise ValueError("Inspect labeled sample count changed")
    if any(
        row.metadata.partition != manifest.partition
        or row.metadata.protocol_hash != manifest.protocol_hash
        for row in public
    ):
        raise ValueError("Inspect sample metadata differs from the frozen manifest")
    try:
        dataset = importlib.import_module("inspect_ai.dataset")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Install the pinned inspect extra: pip install 'everval-ai[inspect]'"
        ) from exc
    samples = []
    for row in public:
        target = targets[row.id]
        if labeled_only and target not in {"A", "B", "tie"}:
            continue
        samples.append(
            dataset.Sample(
                id=row.id,
                input=row.input,
                metadata=row.metadata.model_dump(mode="json"),
                target=target or "",
            )
        )
    return dataset.MemoryDataset(samples)
