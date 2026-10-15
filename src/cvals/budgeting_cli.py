"""Forecast repeated evaluations without admitting provider work."""

from __future__ import annotations

from typing import Annotated

import typer

from cvals.budgeting import BudgetPlanner


def register(parent: typer.Typer) -> None:
    from cvals.cli import fail, output, workspace

    app = typer.Typer(no_args_is_help=True, help="Inspect spend and forecast repeated evaluations.")

    @app.command("status")
    def status() -> None:
        settings, _ = workspace()
        output(BudgetPlanner(settings).status())

    @app.command("project")
    def project(
        experiment_id: str,
        runs: Annotated[int, typer.Option(min=1, max=100000)] = 30,
        screening_experiment_id: str | None = None,
        escalation_rate: Annotated[float, typer.Option(min=0, max=1)] = 1.0,
        cap_usd: Annotated[float | None, typer.Option(min=0)] = None,
    ) -> None:
        try:
            settings, _ = workspace()
            output(
                BudgetPlanner(settings).project(
                    experiment_id,
                    runs=runs,
                    screening_experiment_id=screening_experiment_id,
                    escalation_rate=escalation_rate,
                    cap_usd=cap_usd,
                )
            )
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    parent.add_typer(app, name="budget")
