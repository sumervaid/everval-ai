"""Local reference export commands registered under everval judges."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from cvals.config import Settings
from cvals.judge_reference import CollectionProvenance, export_study_references


def register_reference_commands(app: typer.Typer) -> None:
    @app.command("export-inspect")
    def inspect_export(
        identifier: str,
        judge: Annotated[str, typer.Option("--judge")],
        output: Annotated[Path, typer.Option("--output", help="New private export directory.")],
        partition: Annotated[str, typer.Option("--partition")] = "dev",
    ) -> None:
        """Export Inspect samples with evaluator references in a separate file."""
        from cvals.inspect_export import export_inspect
        from cvals.judge_calibration import JudgeCalibration

        try:
            if partition not in {"dev", "holdout"}:
                raise ValueError("Choose dev or holdout")
            result = export_inspect(
                JudgeCalibration(Settings.from_env()),
                identifier,
                judge,
                output,
                partition="dev" if partition == "dev" else "holdout",
            )
            typer.echo(json.dumps(result, indent=2))
        except (ValueError, KeyError, OSError) as exc:
            typer.echo(f"Inspect export failed: {exc}", err=True)
            raise typer.Exit(1) from exc

    @app.command("export-study")
    def export_study(
        study_id: str,
        output: Annotated[
            Path, typer.Option("--output", help="New private JSON file; never overwritten.")
        ],
        provenance: Annotated[
            Path | None, typer.Option("--provenance", help="Explicit collection provenance JSON.")
        ] = None,
    ) -> None:
        """Export locked, revealed judgments with explicit collection provenance."""
        try:
            supplied = (
                CollectionProvenance.model_validate_json(provenance.read_bytes())
                if provenance
                else CollectionProvenance()
            )
            bundle = export_study_references(Settings.from_env(), study_id, provenance=supplied)
            payload = json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(payload)
            typer.echo(
                json.dumps(
                    {
                        "output": str(output),
                        "items": len(bundle["items"]),
                        "provenance": supplied.source,
                        "bundle_hash": bundle["bundle_hash"],
                    }
                )
            )
        except (ValueError, KeyError, OSError) as exc:
            typer.echo(f"Reference export failed: {exc}", err=True)
            raise typer.Exit(1) from exc
