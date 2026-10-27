"""Operator commands for frozen judge calibration studies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from cvals.config import Settings
from cvals.judge_calibration import JudgeCalibration, write_report
from cvals.judge_calibration_models import Partition

judges_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Measure judge reliability against explicit frozen references.",
)


def _read(path: Path) -> Any:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Calibration input exceeds 64 MiB")
    return json.loads(path.read_text())


def _summary(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k != "protocol"}


def register(app: typer.Typer) -> None:
    from cvals.cli import fail, output

    app.add_typer(judges_app, name="judges")

    def service() -> JudgeCalibration:
        return JudgeCalibration(Settings.from_env())

    def call(operation: Any) -> None:
        try:
            output(operation())
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @judges_app.command("create")
    def create(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
        """Freeze rubric, references, judges, task-family split and both response orders."""
        call(lambda: _summary(service().create(_read(path))))

    @judges_app.command("list")
    def listing() -> None:
        call(lambda: service().list())

    @judges_app.command("show")
    def show(identifier: str, include_protocol: bool = False) -> None:
        """Inspect progress; include-protocol exposes private reference material locally."""
        call(
            lambda: (
                service().get(identifier)
                if include_protocol
                else _summary(service().get(identifier))
            )
        )

    @judges_app.command("requests")
    def requests(identifier: str, partition: Partition = "dev") -> None:
        """Export provider-ready JSON; reference labels and generator identities are excluded."""
        call(lambda: service().export_requests(identifier, partition))

    @judges_app.command("import")
    def import_rows(
        identifier: str, path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
    ) -> None:
        """Retain externally collected observations; exact duplicate imports are idempotent."""

        def operation() -> dict[str, Any]:
            rows = _read(path)
            if not isinstance(rows, list):
                raise ValueError("Observation input must be a JSON array")
            return _summary(service().import_observations(identifier, rows))

        call(operation)

    @judges_app.command("release-holdout")
    def release_holdout(
        identifier: str,
        judge: Annotated[list[str], typer.Option("--judge")],
        reason: Annotated[str, typer.Option("--reason")],
    ) -> None:
        """Freeze selected judges before opening held-out requests."""
        call(lambda: _summary(service().release_holdout(identifier, judge, reason)))

    @judges_app.command("run")
    def run(identifier: str, partition: Partition = "dev") -> None:
        """Execute budgeted calls using frozen HTTP systems, with no automatic retries."""
        call(lambda: _summary(service().run(identifier, partition)))

    @judges_app.command("cancel")
    def cancel(identifier: str) -> None:
        """Stop queued work; any running call remains recorded and reserved."""
        call(lambda: _summary(service().cancel(identifier)))

    @judges_app.command("mark-interrupted")
    def interrupted(identifier: str) -> None:
        """After stopping the caller, retain unresolved calls and their reservations."""
        call(lambda: _summary(service().mark_interrupted(identifier)))

    @judges_app.command("analyze")
    def analyze(
        identifier: str,
        partition: Partition = "holdout",
        destination: Annotated[Path | None, typer.Option("--output")] = None,
    ) -> None:
        """Analyze held-out reference agreement, bias and coverage; export JSON or HTML."""

        def operation() -> Any:
            report = service().analyze(identifier, partition)
            if destination is None:
                return report
            write_report(report, destination)
            return {"output": str(destination), "protocol_hash": report["protocol_hash"]}

        call(operation)

    try:
        from cvals.judge_reference_cli import register_reference_commands
    except ModuleNotFoundError as exc:
        if exc.name != "cvals.judge_reference_cli":
            raise
    else:
        register_reference_commands(judges_app)
