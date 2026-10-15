"""A short terminal walkthrough with retained, clearly labeled fixture evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cvals.config import Settings
from cvals.demo import DISCLAIMER, export_summary, run_demo


def register(parent: typer.Typer) -> None:
    from cvals.cli import fail, output

    @parent.command("demo")
    def demo(
        example: Annotated[str, typer.Option(help="all, coding, documents, or data")] = "all",
        workspace: Annotated[
            Path | None,
            typer.Option(
                help="New or empty isolated directory; defaults under the configured data root."
            ),
        ] = None,
        as_json: Annotated[
            bool, typer.Option("--json", help="Emit retained evidence as JSON.")
        ] = False,
        sandbox: Annotated[
            bool,
            typer.Option(
                help="Coding only: explicitly execute original Python fixture tests in the configured Docker sandbox."
            ),
        ] = False,
    ) -> None:
        """Run the installation story without a key, network, or Docker by default."""
        try:
            result = run_demo(
                Settings.from_env(), example=example, workspace=workspace, sandbox=sandbox
            )
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)
        if as_json:
            output(result)
            return
        console = Console()
        console.print(
            Panel(
                Text(
                    "EVERVAL  /  evidence in motion\n\nA configuration changes. A check fails. The evidence stays.",
                    style="bold",
                ),
                border_style="green",
                padding=(1, 2),
            )
        )
        console.print(Text(DISCLAIMER, style="yellow"))
        table = Table(box=None, padding=(0, 2), expand=True)
        table.add_column("STEP", style="cyan")
        table.add_column("RECORDED RESULT")
        for number, row in enumerate(result["runs"], 1):
            metrics = row["report"]["systems"][0]
            table.add_row(
                f"0{number}  {row['phase'].upper()}",
                f"{metrics['passed']}/{metrics['scheduled']} passed · {row['id'][:12]}",
            )
        table.add_row(
            "04  INVESTIGATE",
            f"{len(result['reproductions'])} failures regraded from stored outputs",
        )
        table.add_row("05  COMPARE", "Same frozen task cohort; fixture evidence only")
        table.add_row("06  RETAIN", "Audit chain verified · evidence and summary saved locally")
        console.print(table)
        console.print(
            Text(f"\nProvider calls: 0 · Provider spend: $0.00\nWorkspace: {result['workspace']}")
        )
        console.print(
            Text(
                f"Summary: {result['artifacts']['summary']}\nPrivate grader evidence: {result['artifacts']['private_evidence']}"
            )
        )
        console.print(
            Text(
                "\nNext: everval packs catalog  ·  See docs/demo.md for a real agent workflow.",
                style="dim",
            )
        )

    evidence = typer.Typer(
        no_args_is_help=True,
        help="Export a deliberate local summary without task contents or outputs.",
    )

    @evidence.command("export")
    def export_evidence(experiment_id: str, directory: Path) -> None:
        """Create a new directory with a summary and evidence-linked badge; never upload."""
        try:
            output(export_summary(Settings.from_env(), experiment_id, directory))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    parent.add_typer(evidence, name="evidence")
