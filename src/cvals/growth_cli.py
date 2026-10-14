"""CLI entry points for guided setup, health and local change admission."""

from __future__ import annotations

import time
from typing import Annotated, Any

import typer

from cvals.benchmark_health import BenchmarkHealth
from cvals.change_runs import ChangeRuns
from cvals.config import Settings
from cvals.onboarding import Onboarding
from cvals.schedules import ScheduleStore
from cvals.store import Store


def register(app: typer.Typer) -> None:
    from cvals.cli import fail, output

    guides = typer.Typer(no_args_is_help=True, help="Resume an evidence-backed first real run.")
    changes = typer.Typer(
        no_args_is_help=True, help="Link local evaluations to source and agent changes."
    )
    recurring = typer.Typer(
        no_args_is_help=True, help="Inspect cumulative budgets and frozen weekly digests."
    )
    app.add_typer(guides, name="guides")
    app.add_typer(changes, name="changes")
    app.add_typer(recurring, name="recurring")

    def call(operation: Any) -> None:
        try:
            output(operation())
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @guides.command("start")
    def guide_start(name: str = "First repository evaluation", source: str | None = None) -> None:
        call(lambda: Onboarding(Settings.from_env()).start(name, source))

    @guides.command("list")
    def guide_list() -> None:
        call(lambda: Onboarding(Settings.from_env()).list())

    @guides.command("status")
    def guide_status(identifier: str) -> None:
        call(lambda: Onboarding(Settings.from_env()).status(identifier))

    @guides.command("select")
    def guide_select(identifier: str, field: str, value: str) -> None:
        call(lambda: Onboarding(Settings.from_env()).select(identifier, **{field: value}))

    @app.command("benchmark-health")
    def benchmark_health(
        project_id: str, stale_days: Annotated[int, typer.Option(min=1, max=3650)] = 30
    ) -> None:
        call(lambda: BenchmarkHealth(Settings.from_env()).project(project_id, stale_days))

    @changes.command("register")
    def register_changes(
        name: str,
        project_id: str,
        experiment_id: str,
        config_revision: str = "",
        enabled: bool = False,
    ) -> None:
        call(
            lambda: ChangeRuns(Settings.from_env()).register(
                name, project_id, experiment_id, {"revision": config_revision}, enabled
            )
        )

    @changes.command("list")
    def list_changes() -> None:
        call(lambda: ChangeRuns(Settings.from_env()).list())

    @changes.command("configure")
    def configure_changes(
        identifier: str,
        config_revision: str | None = None,
        enabled: Annotated[bool | None, typer.Option("--enabled/--paused")] = None,
    ) -> None:
        call(
            lambda: ChangeRuns(Settings.from_env()).revise(
                identifier,
                config={"revision": config_revision} if config_revision is not None else None,
                enabled=enabled,
            )
        )

    @changes.command("history")
    def change_history(identifier: str) -> None:
        call(lambda: ChangeRuns(Settings.from_env()).evaluations(identifier))

    def trigger(identifier: str, commit: str | None, poll: bool) -> dict[str, Any]:
        from cvals.runtime import ensure_worker

        settings = Settings.from_env()
        ensure_worker(settings)
        service = ChangeRuns(settings)
        return service.poll(identifier) if poll else service.trigger(identifier, commit)

    @changes.command("trigger")
    def trigger_changes(identifier: str, commit: str | None = None, poll: bool = False) -> None:
        """Queue on this workspace; rerunning an identical trigger returns its existing run."""
        call(lambda: trigger(identifier, commit, poll))

    @changes.command("check")
    def check_changes(
        identifier: str,
        commit: str | None = None,
        poll: bool = False,
        timeout: Annotated[int, typer.Option(min=1, max=86400)] = 600,
        min_pass_rate: Annotated[float, typer.Option(min=0, max=1)] = 1.0,
    ) -> None:
        """Local CI check: wait for evidence and exit nonzero unless all gates pass."""
        from cvals.policies import PolicyStore

        try:
            record = trigger(identifier, commit, poll)
            store = Store(Settings.from_env())
            deadline = time.monotonic() + timeout
            while True:
                result = store.get_experiment(record["experiment_id"])
                if result["status"] not in {"queued", "running"}:
                    break
                if time.monotonic() >= deadline:
                    output(
                        {
                            "status": "timeout",
                            "experiment_id": record["experiment_id"],
                            "message": "Evaluation remains durable; inspect it before retrying.",
                        }
                    )
                    raise typer.Exit(2)
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            decision = PolicyStore(Settings.from_env()).evaluate(
                result,
                {
                    "name": "Local CI quality",
                    "min_pass_rate": min_pass_rate,
                },
            )
            output({"change": record, "decision": decision})
            if decision.get("status") != "passed":
                raise typer.Exit(1)
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @recurring.command("budget")
    def schedule_budget(
        identifier: str, cap: Annotated[float | None, typer.Option(min=0)] = None
    ) -> None:
        def operation() -> dict[str, Any]:
            settings = Settings.from_env()
            schedules = ScheduleStore(settings, Store(settings))
            return (
                schedules.budget(identifier)
                if cap is None
                else schedules.set_budget(identifier, cap)
            )

        call(operation)

    @recurring.command("digest")
    def weekly_digest(
        identifier: str, days: Annotated[int, typer.Option(min=1, max=366)] = 7
    ) -> None:
        call(
            lambda: ScheduleStore(Settings.from_env(), Store(Settings.from_env())).digest(
                identifier, days
            )
        )

    @recurring.command("digests")
    def digest_history(identifier: str) -> None:
        call(
            lambda: ScheduleStore(Settings.from_env(), Store(Settings.from_env())).digests(
                identifier
            )
        )
