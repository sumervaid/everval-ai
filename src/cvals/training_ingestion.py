"""Checkpoint manifests for trainers and bounded local ingestion.

Weights are read only as bytes. This module does not deserialize models, train
them, start inference, or independently attest that an endpoint serves them.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from pydantic import BaseModel

from cvals.models import canonical_json

if TYPE_CHECKING:
    from cvals.posttraining import PostTraining

MAX_CHECKPOINT_FILES = 4096
MAX_MANIFEST_BYTES = 65536


def _read_regular(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Expected a bounded regular manifest file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Manifest exceeds its byte limit")
        return data
    finally:
        os.close(descriptor)


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _hash_file(path: Path) -> tuple[str, int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Checkpoint artifacts must be regular files")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        after = os.fstat(descriptor)
        if (
            _stamp(before) != _stamp(after)
            or size != before.st_size
            or _stamp(path.lstat()) != _stamp(after)
        ):
            raise ValueError("Checkpoint changed while hashing; export a finalized checkpoint")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _walk_error(error: OSError) -> None:
    raise error


def hash_checkpoint_artifact(path: Path) -> str:
    """Hash a finalized file, or a directory's sorted relative-path/file-digest manifest."""
    path = Path(path)
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        return _hash_file(path)[0]
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Checkpoint must be a regular file or directory, not a symbolic link")
    inventory: list[dict[str, Any]] = []
    stamps: list[tuple[Path, tuple[int, ...]]] = []
    for current, subdirs, names in os.walk(path, followlinks=False, onerror=_walk_error):
        folder = Path(current)
        before = folder.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("Checkpoint contains a symbolic link or special directory")
        stamps.append((folder, _stamp(before)))
        for name in sorted(subdirs):
            if not stat.S_ISDIR((folder / name).lstat().st_mode):
                raise ValueError("Checkpoint directories must not contain symbolic links")
        for name in sorted(names):
            filename = folder / name
            stamps.append((filename, _stamp(filename.lstat())))
            digest, size = _hash_file(filename)
            inventory.append(
                {
                    "path": filename.relative_to(path).as_posix(),
                    "sha256": digest,
                    "bytes": size,
                }
            )
            if len(inventory) > MAX_CHECKPOINT_FILES:
                raise ValueError("Checkpoint directory exceeds 4096 files")
    if not inventory:
        raise ValueError("Checkpoint directory is empty")
    for filename, stamp in stamps:
        if _stamp(filename.lstat()) != stamp:
            raise ValueError("Checkpoint directory or file changed while hashing")
    inventory.sort(key=lambda item: item["path"])
    return hashlib.sha256(
        canonical_json({"format": "everval-checkpoint-tree-v1", "files": inventory}).encode()
    ).hexdigest()


def export_checkpoint(
    destination: Path,
    *,
    weights: Path,
    step: int,
    system_id: str,
    expected_system_hash: str,
    revision: str,
    provenance: str,
) -> dict[str, Any]:
    """Write the exact manifest accepted by 'training checkpoint' or 'training ingest'.

    Call after checkpoint files are finalized and after configuring the immutable
    inference system for this checkpoint. Retrying identical content is safe;
    overwriting a different or symbolic-link destination is rejected.
    """
    from cvals.posttraining_models import CheckpointCreate

    weights, destination = Path(weights), Path(destination)
    if stat.S_ISDIR(weights.lstat().st_mode) and destination.resolve().is_relative_to(
        weights.resolve()
    ):
        raise ValueError("Write checkpoint manifests outside the weights directory")
    value = CheckpointCreate(
        step=step,
        weight_sha256=hash_checkpoint_artifact(weights),
        system_id=system_id,
        expected_system_hash=expected_system_hash,
        revision=revision,
        provenance=provenance,
    ).model_dump(mode="json")
    data = (canonical_json(value) + "\n").encode()
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("Checkpoint manifest exceeds the 64 KiB ingestion limit")
    destination = Path(destination)
    if destination.suffix != ".json":
        raise ValueError("Checkpoint manifest destination must end in .json")
    if not destination.parent.is_dir():
        raise ValueError("Create a dedicated manifest directory before exporting")
    if destination.exists() or destination.is_symlink():
        if _read_regular(destination, MAX_MANIFEST_BYTES) != data:
            raise ValueError("Existing checkpoint manifest has different content")
        return value
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if _read_regular(destination, MAX_MANIFEST_BYTES) != data:
                raise ValueError("Concurrent checkpoint manifest has different content") from None
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not accepted in checkpoint manifests")
        result[key] = value
    return result


def ingest_directory(
    service: PostTraining,
    run_id: str,
    directory: Path,
    *,
    queue: bool = False,
) -> list[dict[str, Any]]:
    """Register a bounded manifest batch; each checkpoint and its queue are idempotent."""
    from cvals.posttraining_models import CheckpointCreate

    directory = Path(directory)
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError("Manifest directory must be a directory, not a symbolic link")
    paths = sorted(directory.glob("*.json"))
    if len(paths) > 1000:
        raise ValueError("An ingestion directory may contain at most 1000 manifests")
    # Validate the whole batch before creating any records.
    manifests = []
    for path in paths:
        value = json.loads(
            _read_regular(path, MAX_MANIFEST_BYTES), object_pairs_hook=_unique_object
        )
        manifests.append((path, CheckpointCreate.model_validate(value)))
    manifests.sort(key=lambda item: (item[1].step, item[0].name))
    results = []
    for path, manifest in manifests:
        checkpoint = service.register_checkpoint(run_id, manifest)
        result = {"file": path.name, "checkpoint": checkpoint}
        if queue:
            result["queue"] = service.queue_checkpoint(run_id, checkpoint["id"])
        results.append(result)
    return results


def register_ingestion(training: typer.Typer) -> None:
    from cvals.cli import fail, output

    @training.command("schema")
    def schema(
        kind: Annotated[
            str,
            typer.Argument(
                help="run, checkpoint, qualification, qualification-evidence, investigation, mix, mix-observation, mix-config, mix-validation, or grader-drift"
            ),
        ],
    ) -> None:
        """Print the versioned JSON request schema without creating a workspace."""
        from cvals.posttraining_grader import GraderDriftRequest
        from cvals.posttraining_mix import MixConfig, MixObservation
        from cvals.posttraining_models import (
            CheckpointCreate,
            InvestigationCreate,
            MixCreate,
            MixValidation,
            QualificationCreate,
            TrainingRunCreate,
        )
        from cvals.posttraining_qualification import QualificationEvidence

        schemas: dict[str, type[BaseModel]] = {
            "run": TrainingRunCreate,
            "checkpoint": CheckpointCreate,
            "qualification": QualificationCreate,
            "qualification-evidence": QualificationEvidence,
            "investigation": InvestigationCreate,
            "mix": MixCreate,
            "mix-validation": MixValidation,
            "mix-observation": MixObservation,
            "mix-config": MixConfig,
            "grader-drift": GraderDriftRequest,
        }
        if kind not in schemas:
            fail(ValueError("Choose a schema: " + ", ".join(schemas)))
        output(schemas[kind].model_json_schema())

    @training.command("system-hash")
    def system_hash(system_id: str) -> None:
        """Print the immutable system fingerprint used by checkpoint declarations."""
        from cvals.config import Settings
        from cvals.models import System, content_hash
        from cvals.store import Store

        try:
            store = Store(Settings.from_env())
            store.init()
            saved = store.get_system(system_id)
            saved.pop("configured", None)
            system = System.model_validate(saved)
            output({"system_id": system.id, "sha256": content_hash(system.model_dump(mode="json"))})
        except (OSError, ValueError, KeyError) as exc:
            fail(exc)

    @training.command("grader-drift")
    def grader_drift(
        file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    ) -> None:
        """Compare supplied old/new grader outcomes on identical frozen outputs."""
        from cvals.posttraining_grader import compare_graders

        try:
            request = json.loads(
                _read_regular(file, 5 * 1024 * 1024), object_pairs_hook=_unique_object
            )
            output(compare_graders(request))
        except (OSError, ValueError, KeyError) as exc:
            fail(exc)

    @training.command("demo")
    def demo(
        workspace: Annotated[
            Path | None, typer.Option(help="New or empty isolated workspace.")
        ] = None,
        as_json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Exercise checkpoint monitoring without training, inference, keys, or Docker."""
        import shlex

        from rich.console import Console
        from rich.table import Table

        from cvals.config import Settings
        from cvals.training_demo import run_training_demo

        try:
            result = run_training_demo(Settings.from_env(), workspace=workspace)
        except (OSError, ValueError, KeyError) as exc:
            fail(exc)
        if as_json:
            output(result)
            return
        console = Console()
        console.print("EVERVAL  /  checkpoint monitoring", style="bold cyan")
        console.print(result["disclaimer"], markup=False, style="yellow")
        table = Table("Step", "Control", "Evidence")
        table.add_row("0", "Reference", "Frozen baseline across two domains")
        for comparison in result["comparisons"]:
            decisions = ", ".join(
                item["domain"] + ": " + item["decision"] for item in comparison["domains"]
            )
            table.add_row(
                str(comparison["candidate_step"]),
                "Empty submission" if comparison["candidate_step"] == 10 else "Restored reference",
                decisions,
            )
        console.print(table)
        console.print(
            f"{result['scheduled_attempts']} attempts · {result['experiments']} experiments · $0 provider spend",
            markup=False,
        )
        console.print(
            "Qualification retains missing human calibration; the restoration can remain inconclusive.",
            markup=False,
        )
        console.print(f"Summary: {result['summary_path']}", markup=False)
        console.print(
            "Open Training (Ctrl+T): EVERVAL_DATA_DIR="
            + shlex.quote(result["workspace"])
            + " everval tui",
            markup=False,
        )

    @training.command("ingest")
    def ingest(
        run_id: str,
        directory: Path,
        queue: Annotated[
            bool, typer.Option(help="Admit evaluations through the budgeted worker.")
        ] = False,
        follow: Annotated[
            bool, typer.Option(help="Watch finalized manifests until interrupted.")
        ] = False,
        interval: Annotated[int, typer.Option(min=1, max=3600)] = 5,
    ) -> None:
        """Import trainer manifests once, or follow a local checkpoint directory."""
        from cvals.config import Settings
        from cvals.posttraining import PostTraining

        try:
            service = PostTraining(Settings.from_env())
            previous = None
            while True:
                if queue:
                    from cvals.runtime import ensure_worker

                    ensure_worker(service.settings)
                result = ingest_directory(service, run_id, directory, queue=queue)
                fingerprint = canonical_json(result)
                if fingerprint != previous:
                    output(result)
                    previous = fingerprint
                if not follow:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            typer.echo(
                "Checkpoint ingestion stopped; previously admitted work is retained.", err=True
            )
        except (OSError, ValueError, KeyError) as exc:
            fail(exc)
