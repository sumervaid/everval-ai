"""Operator commands for local autonomous evaluation policies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, cast

import typer

from cvals.autopilot import Autopilot
from cvals.config import Settings


def register(app: typer.Typer) -> None:
    from cvals.cli import fail, output

    group = typer.Typer(
        no_args_is_help=True,
        help="Watch changes, screen cheaply, and escalate within a cumulative budget.",
    )
    app.add_typer(group, name="autopilot")

    def call(operation: Callable[[Autopilot], Any]) -> None:
        try:
            output(operation(Autopilot(Settings.from_env())))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @group.command("create")
    def create(
        name: str,
        experiment_id: str,
        screening_experiment_id: str,
        cap: Annotated[float, typer.Option(min=0, help="Lifetime USD ceiling for both stages.")],
        interval: Annotated[int, typer.Option(min=5, max=525600)] = 60,
        trigger: str = "periodic",
        escalate: str = "regression",
        project_id: str | None = None,
        enabled: bool = False,
        discover: bool = False,
        config_revision: str = "",
    ) -> None:
        """Register frozen full and smaller screening baselines. Paused by default."""
        call(
            lambda service: service.create(
                name,
                experiment_id,
                screening_experiment_id,
                lifetime_budget_usd=cap,
                interval_minutes=interval,
                trigger=cast(Any, trigger),
                escalate=cast(Any, escalate),
                project_id=project_id,
                enabled=enabled,
                discover=discover,
                config={"revision": config_revision},
            )
        )

    @group.command("list")
    def list_policies() -> None:
        call(lambda service: service.list())

    @group.command("show")
    def show(identifier: str) -> None:
        call(
            lambda service: {
                "policy": service.get(identifier),
                "budget": service.budget(identifier),
                "history": service.history(identifier),
                "investigations": service.investigations(identifier),
            }
        )

    @group.command("configure")
    def configure(
        identifier: str,
        enabled: Annotated[bool | None, typer.Option("--enabled/--paused")] = None,
        cap: Annotated[float | None, typer.Option(min=0)] = None,
        config_revision: str | None = None,
    ) -> None:
        call(
            lambda service: service.configure(
                identifier,
                enabled=enabled,
                lifetime_budget_usd=cap,
                config={"revision": config_revision} if config_revision is not None else None,
            )
        )

    @group.command("budget")
    def budget(identifier: str, days: Annotated[int, typer.Option(min=1, max=366)] = 30) -> None:
        """Show retained charges/reservations and explicit budget-ceiling assumptions."""
        call(
            lambda service: {
                "budget": service.budget(identifier),
                "projection": service.projection(identifier, days),
            }
        )

    @group.command("tick")
    def tick() -> None:
        """Process one bounded controller heartbeat; the worker executes queued trials."""
        call(lambda service: {"queued_experiment_ids": service.tick()})

    @group.command("screen")
    def screen(experiment_id: str, size: Annotated[int, typer.Option(min=1, max=999)] = 5) -> None:
        """Queue a smaller baseline for an unreleased curated development collection."""
        call(lambda service: service.screen(experiment_id, size))
