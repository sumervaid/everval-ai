"""Explicit terminal actions for frozen checkpoint monitoring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from cvals.config import Settings
from cvals.posttraining import PostTraining


def register(app: typer.Typer) -> None:
    from cvals.cli import fail, output

    training = typer.Typer(
        no_args_is_help=True,
        help="Monitor frozen checkpoint evaluations and investigate decisions.",
    )
    app.add_typer(training, name="training")

    def service() -> PostTraining:
        return PostTraining(Settings.from_env())

    def call(operation: Any) -> None:
        try:
            output(operation())
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    def manifest(path: Path) -> dict[str, Any]:
        with path.open("rb") as handle:
            raw = handle.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("Training request exceeds 5 MiB")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Training request must be a JSON object")
        return value

    @training.command("create")
    def create(file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]) -> None:
        """Freeze a protocol before collecting checkpoint results."""
        call(lambda: service().create_run(manifest(file)))

    @training.command("list")
    def list_runs(
        limit: Annotated[int, typer.Option(min=1, max=100)] = 100,
        offset: Annotated[int, typer.Option(min=0, max=1000000)] = 0,
    ) -> None:
        call(lambda: service().list_runs(limit, offset))

    @training.command("show")
    def show(run_id: str) -> None:
        """Read coverage, lag, cost accounting and evidence-bound decisions."""
        call(lambda: service().dashboard(run_id))

    @training.command("checkpoint")
    def checkpoint(
        run_id: str, file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]
    ) -> None:
        """Declare checkpoint lineage without making model calls."""
        call(lambda: service().register_checkpoint(run_id, manifest(file)))

    @training.command("queue")
    def queue(run_id: str, checkpoint_id: str) -> None:
        """Queue frozen evaluations within their predeclared budget."""

        def operation() -> dict[str, Any]:
            from cvals.runtime import ensure_worker

            settings = Settings.from_env()
            result = PostTraining(settings).queue_checkpoint(run_id, checkpoint_id)
            ensure_worker(settings)
            return result

        call(operation)

    @training.command("analyze")
    def analyze(run_id: str, checkpoint_id: str) -> None:
        """Finalize one planned look after all linked experiments terminate."""
        call(lambda: service().analyze(run_id, checkpoint_id))

    @training.command("investigate")
    def investigate(
        run_id: str, file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]
    ) -> None:
        call(lambda: service().investigate(run_id, manifest(file)))

    @training.command("qualify")
    def qualify(
        run_id: str, file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]
    ) -> None:
        call(lambda: service().qualify(run_id, manifest(file)))

    @training.command("mix")
    def mix(
        run_id: str, file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)]
    ) -> None:
        """Freeze a development-only selection and its withheld validation grid."""
        call(lambda: service().select_mix(run_id, manifest(file)))

    @training.command("validate-mix")
    def validate_mix(
        run_id: str,
        selection_id: str,
        file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    ) -> None:
        call(lambda: service().validate_mix(run_id, selection_id, manifest(file)))

    from cvals.training_ingestion import register_ingestion

    register_ingestion(training)
