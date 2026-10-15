"""Terminal commands for bounded investigation, quality probes and optimization."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from cvals.config import Settings
from cvals.optimization import Optimizer
from cvals.research import ResearchLab


def register(app: typer.Typer) -> None:
    from cvals.cli import fail, output

    research = typer.Typer(
        no_args_is_help=True, help="Investigate retained outputs without provider calls."
    )
    optimize = typer.Typer(
        no_args_is_help=True, help="Compare explicit candidates with a reserved final holdout."
    )
    app.add_typer(research, name="research")
    app.add_typer(optimize, name="optimize")

    def call(operation: Any) -> None:
        try:
            output(operation())
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @research.command("repeat")
    def repeat(
        experiment_id: str,
        trial_id: str,
        repetitions: Annotated[int, typer.Option(min=2, max=10)] = 3,
    ) -> None:
        call(
            lambda: ResearchLab(Settings.from_env()).repeat_regrade(
                experiment_id, trial_id, repetitions
            )
        )

    @research.command("minimize")
    def minimize(
        experiment_id: str,
        trial_id: str,
        max_evaluations: Annotated[int, typer.Option(min=2, max=200)] = 80,
    ) -> None:
        call(
            lambda: ResearchLab(Settings.from_env()).minimize_output(
                experiment_id, trial_id, max_evaluations
            )
        )

    @research.command("quality")
    def quality(
        collection_id: str,
        max_scenarios: Annotated[int, typer.Option(min=1, max=200)] = 100,
        max_pairs: Annotated[int, typer.Option(min=1, max=20000)] = 5000,
    ) -> None:
        call(
            lambda: ResearchLab(Settings.from_env()).quality(
                collection_id, max_scenarios, max_pairs
            )
        )

    @research.command("list")
    def records() -> None:
        call(lambda: ResearchLab(Settings.from_env()).list_records())

    @research.command("show")
    def show(identifier: str) -> None:
        call(lambda: ResearchLab(Settings.from_env()).get_record(identifier))

    @optimize.command("project")
    def project(
        development_collection_id: str,
        holdout_collection_id: str,
        system: Annotated[list[str], typer.Option("--system")],
        budget: Annotated[float, typer.Option(min=0, max=100000)],
        repetitions: Annotated[int, typer.Option(min=1, max=10)] = 1,
    ) -> None:
        """Inspect configured admission bounds without reserving or running a study."""
        call(
            lambda: Optimizer(Settings.from_env()).project(
                development_collection_id, holdout_collection_id, system, budget, repetitions
            )
        )

    @optimize.command("create")
    def create(
        name: str,
        development_collection_id: str,
        holdout_collection_id: str,
        system: Annotated[list[str], typer.Option("--system")],
        budget: Annotated[float, typer.Option(min=0, max=100000)],
        min_pass_rate: Annotated[float, typer.Option(min=0, max=1)] = 1.0,
        max_p95_ms: Annotated[float, typer.Option(min=1, max=604800000)] = 60000,
        repetitions: Annotated[int, typer.Option(min=1, max=10)] = 1,
    ) -> None:
        """Queue development; the worker admits the one final holdout when eligible."""

        def operation() -> dict[str, Any]:
            from cvals.runtime import ensure_worker

            settings = Settings.from_env()
            ensure_worker(settings)
            return Optimizer(settings).create(
                name,
                development_collection_id,
                holdout_collection_id,
                system,
                budget,
                min_pass_rate,
                max_p95_ms,
                repetitions,
            )

        call(operation)

    @optimize.command("list")
    def studies() -> None:
        call(lambda: Optimizer(Settings.from_env()).list())

    @optimize.command("show")
    def study(identifier: str) -> None:
        call(lambda: Optimizer(Settings.from_env()).get(identifier))

    @optimize.command("advance")
    def advance(identifier: str) -> None:
        call(lambda: Optimizer(Settings.from_env()).advance(identifier))

    @optimize.command("cancel")
    def cancel(identifier: str) -> None:
        call(lambda: Optimizer(Settings.from_env()).cancel(identifier))

    @optimize.command("proposal")
    def proposal(identifier: str) -> None:
        def operation() -> dict[str, Any]:
            result = Optimizer(Settings.from_env()).get(identifier)
            if result["proposal"] is None:
                raise ValueError(
                    "No reviewable proposal exists; inspect the study state and evidence"
                )
            return dict(result["proposal"])

        call(operation)
