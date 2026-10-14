"""CLI entry points for failure investigation, economics, and spending controls."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cvals.cli import fail, output, workspace
from cvals.models import System
from cvals.workbench import Workbench


def register(parent: typer.Typer, systems: typer.Typer) -> None:
    diagnostics = typer.Typer(
        no_args_is_help=True, help="Investigate recorded attempts without calling a model."
    )
    spending = typer.Typer(
        no_args_is_help=True, help="Configure cumulative workspace and project spending caps."
    )
    artifacts = typer.Typer(
        no_args_is_help=True, help="Inspect and import offline private dependency archives."
    )

    @diagnostics.command("triage")
    def triage(experiment_id: str) -> None:
        try:
            settings, _ = workspace()
            output(Workbench(settings).triage(experiment_id))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @diagnostics.command("show")
    def diagnose(experiment_id: str, trial_id: str) -> None:
        try:
            settings, _ = workspace()
            output(Workbench(settings).diagnose(experiment_id, trial_id))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @diagnostics.command("regrade")
    def regrade(experiment_id: str, trial_id: str) -> None:
        try:
            settings, _ = workspace()
            result = Workbench(settings).regrade(experiment_id, trial_id)
            output(result)
            if result["passed"] is not True:
                raise typer.Exit(1)
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @parent.command("economics")
    def economics(experiment_id: str) -> None:
        try:
            settings, _ = workspace()
            output(Workbench(settings).economics(experiment_id))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @parent.command("brief")
    def brief(experiment_id: str) -> None:
        try:
            settings, _ = workspace()
            output(Workbench(settings).decision_brief(experiment_id))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @spending.command("list")
    def list_limits() -> None:
        from cvals.spend_limits import SpendLimits

        settings, _ = workspace()
        output(SpendLimits(settings).list())

    @spending.command("set")
    def set_limit(
        scope: str = "workspace",
        daily: Annotated[float | None, typer.Option(min=0)] = None,
        monthly: Annotated[float | None, typer.Option(min=0)] = None,
        lifetime: Annotated[float | None, typer.Option(min=0)] = None,
    ) -> None:
        """Replace a scope's caps; omitted periods are unlimited."""
        from cvals.spend_limits import SpendLimits

        try:
            settings, _ = workspace()
            output(
                SpendLimits(settings).configure(
                    scope, daily_usd=daily, monthly_usd=monthly, lifetime_usd=lifetime
                )
            )
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @artifacts.command("list")
    def list_artifacts() -> None:
        from cvals.private_artifacts import PrivateArtifacts

        settings, _ = workspace()
        output(PrivateArtifacts(settings).list())

    @artifacts.command("import")
    def import_artifact(
        path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
        kind: Annotated[str, typer.Option(help="wheel or npm")],
        registry: Annotated[
            str, typer.Option(help="HTTPS registry provenance, without credentials.")
        ],
    ) -> None:
        from cvals.private_artifacts import PrivateArtifacts

        try:
            settings, _ = workspace()
            output(PrivateArtifacts(settings).import_file(path, kind=kind, registry=registry))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @systems.command("add-agent")
    def add_agent(
        name: str,
        model: Annotated[str, typer.Option()],
        base_url: Annotated[str, typer.Option(help="Base URL of an Everval v1 agent adapter.")],
        revision: Annotated[
            str, typer.Option(help="Immutable adapter build or configuration revision.")
        ],
        api_key_env: str = "",
        input_rate: Annotated[float, typer.Option(min=0)] = 0,
        output_rate: Annotated[float, typer.Option(min=0)] = 0,
        input_limit: Annotated[int, typer.Option(min=1, max=100000000)] = 131072,
        output_limit: Annotated[int, typer.Option(min=1, max=100000000)] = 32768,
        timeout: Annotated[float, typer.Option(min=1, max=120)] = 60,
    ) -> None:
        """Register an existing agent using a versioned public-task adapter."""
        try:
            _, store = workspace()
            output(
                store.save_system(
                    System(
                        name=name,
                        kind="http",
                        harness="external-agent",
                        model=model,
                        base_url=base_url,
                        adapter_revision=revision,
                        api_key_env=api_key_env,
                        input_cost_per_million=input_rate,
                        output_cost_per_million=output_rate,
                        adapter_input_token_limit=input_limit,
                        adapter_output_token_limit=output_limit,
                        timeout_seconds=timeout,
                    )
                )
            )
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    parent.add_typer(diagnostics, name="diagnostics")
    parent.add_typer(spending, name="spending")
    parent.add_typer(artifacts, name="artifacts")
