"""Command line tools for repeatable evaluation workflows."""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from cvals import __version__
from cvals.config import Settings
from cvals.engine import Engine
from cvals.exporting import export_experiment
from cvals.models import Collection, ExperimentCreate, System
from cvals.store import Store

app = typer.Typer(
    pretty_exceptions_show_locals=False,
    no_args_is_help=True,
    help="Evaluate agents on code, documents, data, and workflows.",
)
collections_app = typer.Typer(
    pretty_exceptions_show_locals=False,
    no_args_is_help=True,
    help="Import, inspect, and validate task collections.",
)
systems_app = typer.Typer(
    pretty_exceptions_show_locals=False,
    no_args_is_help=True,
    help="Configure systems without storing provider keys.",
)
experiments_app = typer.Typer(
    pretty_exceptions_show_locals=False,
    no_args_is_help=True,
    help="Inspect recorded experiments and export evidence.",
)
schedules_app = typer.Typer(no_args_is_help=True, help="Manage recurring frozen evaluations.")
reviews_app = typer.Typer(
    no_args_is_help=True, help="Append human judgments to finalized evidence."
)
app.add_typer(schedules_app, name="schedules")
app.add_typer(reviews_app, name="reviews")
app.add_typer(collections_app, name="collections")
app.add_typer(systems_app, name="systems")
app.add_typer(experiments_app, name="experiments")
console = Console()
err = Console(stderr=True)


def workspace() -> tuple[Settings, Store]:
    settings = Settings.from_env()
    settings.prepare()
    store = Store(settings)
    store.init()
    return settings, store


def output(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def fail(exc: Exception) -> NoReturn:
    err.print(f"Error: {exc}", markup=False)
    raise typer.Exit(1) from exc


@app.callback(invoke_without_command=True)
def main(version: Annotated[bool, typer.Option("--version", is_eager=True)] = False) -> None:
    """Everval AI: a local evaluation workspace."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def init(
    starters: Annotated[
        bool, typer.Option(help="Install original example task collections.")
    ] = False,
) -> None:
    """Create a workspace and optionally install example collections."""
    try:
        settings, store = workspace()
        store.ensure_fixture_systems()
        installed = 0
        if starters:
            from cvals.packs import starter_collections

            existing = {item["id"] for item in store.list_collections()}
            for item in starter_collections():
                if item.id not in existing:
                    store.save_collection(item)
                    installed += 1
        output({"data_dir": str(settings.data_dir), "installed_collections": installed})
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def doctor(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable status.")
    ] = False,
) -> None:
    """Check storage, local tools, and the optional coding sandbox."""
    import platform
    import shutil

    from cvals.api import docker_available

    settings = Settings.from_env()
    issues = []
    try:
        settings.prepare()
        free = shutil.disk_usage(settings.data_dir).free
    except (ValueError, OSError) as exc:
        issues.append(str(exc))
        free = 0
    from cvals.identity import IdentityStore

    team_enabled = IdentityStore(settings).enabled() if not issues else False
    state = {
        "version": __version__,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "data_dir": str(settings.data_dir),
        "storage_free_bytes": free,
        "docker_available": docker_available(settings),
        "remote_providers_enabled": settings.allow_remote_providers,
        "internal_provider_origins": list(settings.internal_provider_origins),
        "provider_ca_configured": settings.provider_ca_bundle is not None,
        "auth_enabled": bool(settings.token) or team_enabled,
        "auth_mode": "team" if team_enabled else "workspace-token" if settings.token else "local",
        "issues": issues,
    }
    if json_output:
        output(state)
    else:
        table = Table("Check", "Result", title="Workspace health", show_lines=False)
        for key, value in state.items():
            table.add_row(key.replace("_", " ").capitalize(), str(value))
        console.print(table)
        if not state["docker_available"]:
            console.print(
                "Document, data, and workflow checks work without Docker. Coding checks require an isolated runtime."
            )
    if issues:
        raise typer.Exit(1)


@app.command()
def serve(
    host: Annotated[
        str, typer.Option(help="Listen address; non-loopback requires authentication.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    worker: Annotated[bool, typer.Option("--worker/--no-worker")] = True,
) -> None:
    """Start the web app and API, with a durable local worker."""
    from cvals.api import create_app

    settings = Settings.from_env()
    from cvals.identity import IdentityStore

    if host not in {"127.0.0.1", "localhost", "::1"} and not (
        settings.token or IdentityStore(settings).enabled()
    ):
        fail(ValueError("Set EVERVAL_TOKEN before exposing the workspace beyond loopback."))
    uvicorn.run(
        create_app(settings, start_worker=worker),
        host=host,
        port=port,
        proxy_headers=False,
        server_header=False,
        access_log=False,
    )


@app.command("worker")
def consume(
    once: Annotated[bool, typer.Option(help="Process one queued experiment and exit.")] = False,
) -> None:
    """Process queued experiments; recover expired leases on startup."""
    settings, store = workspace()
    from cvals.jobs import Jobs
    from cvals.runtime import worker_lock
    from cvals.schedules import ScheduleStore

    lock = worker_lock(settings)
    if lock is None:
        fail(ValueError("A dedicated worker already serves this workspace"))
    engine = Engine(store, settings)
    engine.recover()
    from cvals.environments import cleanup_environments
    from cvals.sandbox import cleanup_sandboxes

    for cleanup in (cleanup_sandboxes, cleanup_environments):
        try:
            cleanup(settings)
        except (OSError, ValueError) as exc:
            logging.warning("Runtime cleanup unavailable (%s)", type(exc).__name__)
    schedules = ScheduleStore(settings, store)
    from cvals.autopilot import Autopilot
    from cvals.conversation_execution import ExecutionService
    from cvals.optimization import Optimizer

    autopilot = Autopilot(settings, store)
    conversations = ExecutionService(settings, store)
    optimizer = Optimizer(settings)
    jobs = Jobs(settings)
    if once:
        scheduled = schedules.tick()
        autopilot.tick()
        optimizer.tick()
        autopilot.investigate_pending()
        output(
            {
                "scheduled": scheduled,
                "job": jobs.run_pending_once(),
                "processed": engine.run_pending_once(),
                "reports": conversations.tick(),
            }
        )
        return
    stopping = False

    def stop(signum: int, frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("Everval AI worker ready", flush=True)
    next_schedule_check = 0.0
    try:
        while not stopping:
            try:
                if time.monotonic() >= next_schedule_check:
                    schedules.tick()
                    autopilot.tick()
                    optimizer.tick()
                    autopilot.investigate_pending()
                    conversations.tick()
                    next_schedule_check = time.monotonic() + 5
                job = jobs.run_pending_once()
                experiment = engine.run_pending_once()
                if experiment:
                    conversations.tick()
                if job or experiment:
                    print(
                        json.dumps({"job_processed": job, "experiment_processed": experiment}),
                        flush=True,
                    )
                else:
                    time.sleep(0.5)
            except (OSError, sqlite3.Error) as exc:
                logging.error("Workspace unavailable (%s); retrying", type(exc).__name__)
                time.sleep(1)
    finally:
        lock.close()


@app.command("mine")
def mine_repository_command(
    path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
    save: Annotated[bool, typer.Option(help="Save the extracted draft collection.")] = False,
) -> None:
    """Extract draft tasks from supported literal Python tests in Git history."""
    from cvals.domains.mining import mine_repository

    result = mine_repository(str(path), limit)
    if result.get("collection") is not None:
        item = result["collection"]
        if save:
            _, store = workspace()
            result["collection"] = store.save_collection(item)
        else:
            result["collection"] = item.model_dump(mode="json")
    output(result)


@app.command("inspect")
def inspect_repository_command(
    path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
) -> None:
    """Inspect Git history without running repository code or hooks."""
    from cvals.ingestion import inspect_repository

    output(inspect_repository(str(path), limit))


@systems_app.command("list")
def list_systems() -> None:
    """List fixture controls and configured model endpoints."""
    _, store = workspace()
    output(store.list_systems())


@systems_app.command("add-http")
def add_http_system(
    name: str,
    model: Annotated[str, typer.Option()],
    base_url: Annotated[str, typer.Option(help="A chat-completions compatible endpoint base URL.")],
    api_key_env: Annotated[
        str, typer.Option(help="Environment variable name, never the key value.")
    ] = "",
    input_rate: Annotated[float, typer.Option(min=0, help="USD per million input tokens.")] = 0,
    output_rate: Annotated[float, typer.Option(min=0, help="USD per million output tokens.")] = 0,
    max_tokens: Annotated[int, typer.Option(min=1, max=32768)] = 2048,
    harness: Annotated[str, typer.Option(help="single-turn or tools")] = "single-turn",
    max_steps: Annotated[int, typer.Option(min=1, max=30)] = 8,
) -> None:
    """Register a model endpoint. External network access is separately enabled."""
    _, store = workspace()
    body = System(
        name=name,
        kind="http",
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        max_tokens=max_tokens,
        harness=harness,  # type: ignore[arg-type]
        max_steps=max_steps,
        input_cost_per_million=input_rate,
        output_cost_per_million=output_rate,
    )
    output(store.save_system(body))


@app.command()
def run(
    collection_id: str,
    system: Annotated[
        list[str], typer.Option("--system", "-s", help="System ID; repeat to compare.")
    ],
    name: Annotated[str, typer.Option()] = "Evaluation",
    repetitions: Annotated[int, typer.Option("--repetitions", "-r", min=1, max=30)] = 1,
    budget: Annotated[
        float, typer.Option("--budget", min=0, help="Maximum model spending in USD.")
    ] = 0,
    wait: Annotated[bool, typer.Option("--wait/--enqueue")] = True,
) -> None:
    """Run a frozen comparison, or enqueue it for a separate worker."""
    try:
        settings, store = workspace()
        value = store.create_experiment(
            ExperimentCreate(
                name=name,
                collection_id=collection_id,
                system_ids=system,
                repetitions=repetitions,
                budget_usd=budget,
            )
        )
        if wait:
            value = Engine(store, settings).run_experiment(value["id"])
        output(value)
        if value.get("status") == "failed":
            raise typer.Exit(1)
    except (ValueError, KeyError) as exc:
        fail(exc)


@experiments_app.command("list")
def list_experiments() -> None:
    _, store = workspace()
    output(store.list_experiments())


@experiments_app.command("show")
def show_experiment(experiment_id: str) -> None:
    from cvals.reviews import ReviewStore

    settings, store = workspace()
    item = store.get_experiment(experiment_id)
    item["human_reviews"] = ReviewStore(settings).list(experiment_id)
    output(item)


@experiments_app.command("cancel")
def cancel_experiment(experiment_id: str) -> None:
    """Cancel pending work while preserving completed and partial evidence."""
    _, store = workspace()
    output(store.cancel_experiment(experiment_id))


@experiments_app.command("rerun")
def rerun_experiment(
    experiment_id: str, wait: Annotated[bool, typer.Option("--wait/--enqueue")] = True
) -> None:
    """Create a new run from the original frozen configuration."""
    settings, store = workspace()
    value = store.rerun_experiment(experiment_id)
    if wait:
        value = Engine(store, settings).run_experiment(value["id"])
    output(value)
    if value.get("status") == "failed":
        raise typer.Exit(1)


@experiments_app.command("export")
def export_experiment_command(
    experiment_id: str,
    destination: Annotated[Path, typer.Option("--output")],
    format: Annotated[str, typer.Option()] = "json",
) -> None:
    """Export recorded evidence as JSON, CSV, or a self-contained HTML report."""
    _, store = workspace()
    from cvals.reviews import ReviewStore

    item = store.get_experiment(experiment_id)
    item["human_reviews"] = ReviewStore(store.settings).list(experiment_id)
    content, _ = export_experiment(item, format)
    _write_new(destination, content)
    typer.echo(str(destination))


@app.command("import-results")
def import_results(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    wait: Annotated[bool, typer.Option("--wait/--enqueue")] = True,
) -> None:
    """Grade recorded submissions from any agent against a frozen task collection."""
    from cvals.api import SubmissionImport

    settings, store = workspace()
    if path.stat().st_size > settings.max_request_bytes:
        fail(ValueError("Submission file exceeds the upload limit."))
    body = SubmissionImport.model_validate_json(path.read_bytes())
    value = store.import_submissions(
        collection_id=body.collection_id,
        name=body.name,
        collection_hash=body.collection_hash,
        submissions={key: item.model_dump(mode="json") for key, item in body.submissions.items()},
        provenance=body.provenance,
        system_name=body.system_name,
    )
    if wait:
        value = Engine(store, settings).run_experiment(value["id"])
    output(value)
    if value.get("status") == "failed":
        raise typer.Exit(1)


@collections_app.command("tasks")
def export_task_inputs(
    collection_id: str, destination: Annotated[Path, typer.Option("--output")]
) -> None:
    """Export solver inputs and a fingerprint without reference outputs or checks."""
    from cvals.models import content_hash

    _, store = workspace()
    item = Collection.model_validate(store.get_collection(collection_id))
    payload = {
        "schema_version": 1,
        "collection_id": item.id,
        "name": item.name,
        "collection_hash": content_hash(item.model_dump(mode="json")),
        "scenarios": [scenario.agent_view() for scenario in item.scenarios],
    }
    _write_new(destination, json.dumps(payload, indent=2, ensure_ascii=False))
    typer.echo(str(destination))


@app.command("replay")
def replay(experiment_id: str) -> None:
    """Regenerate analysis from saved evidence without calling a model."""
    from cvals.reports import build_report

    _, store = workspace()
    output(build_report(store.get_experiment(experiment_id)))


@schedules_app.command("list")
def list_schedules() -> None:
    """List recurring evaluations and their next due times."""
    from cvals.schedules import ScheduleStore

    settings, store = workspace()
    output(ScheduleStore(settings, store).list())


@schedules_app.command("create")
def create_schedule(
    experiment_id: str,
    name: Annotated[str, typer.Option()],
    interval_minutes: Annotated[int, typer.Option(min=5, max=525600)] = 1440,
    enabled: Annotated[bool, typer.Option("--enabled/--paused")] = True,
    lifetime_budget: Annotated[float | None, typer.Option(min=0)] = None,
) -> None:
    """Repeat a frozen experiment within per-run and cumulative spending limits."""
    from cvals.schedules import ScheduleStore

    try:
        settings, store = workspace()
        output(
            ScheduleStore(settings, store).create(
                name, experiment_id, interval_minutes, enabled, lifetime_budget_usd=lifetime_budget
            )
        )
    except (ValueError, KeyError) as exc:
        fail(exc)


def _schedule_enabled(schedule_id: str, enabled: bool) -> None:
    from cvals.schedules import ScheduleStore

    try:
        settings, store = workspace()
        output(ScheduleStore(settings, store).set_enabled(schedule_id, enabled))
    except (ValueError, KeyError) as exc:
        fail(exc)


@schedules_app.command("pause")
def pause_schedule(schedule_id: str) -> None:
    """Stop future occurrences without canceling work already queued."""
    _schedule_enabled(schedule_id, False)


@schedules_app.command("resume")
def resume_schedule(schedule_id: str) -> None:
    """Restart the interval after validating the original provider configuration."""
    _schedule_enabled(schedule_id, True)


@reviews_app.command("list")
def list_reviews(experiment_id: str) -> None:
    """List the full append-only human judgment history."""
    from cvals.reviews import ReviewStore

    try:
        settings, _ = workspace()
        output(ReviewStore(settings).list(experiment_id))
    except (ValueError, KeyError) as exc:
        fail(exc)


@reviews_app.command("add")
def add_review(
    experiment_id: str,
    trial_id: str,
    reviewer: Annotated[str, typer.Option(help="Self-reported reviewer label.")],
    dimension: Annotated[str, typer.Option(help="Rubric dimension, such as clarity.")],
    score: Annotated[float, typer.Option(min=0, max=1)],
    rationale: Annotated[str, typer.Option(help="Reasoning supported by the recorded evidence.")],
) -> None:
    """Append a judgment without changing automated scores or earlier reviews."""
    from cvals.reviews import ReviewStore

    try:
        settings, _ = workspace()
        output(
            ReviewStore(settings).add(
                experiment_id, trial_id, reviewer, dimension, score, rationale
            )
        )
    except (ValueError, KeyError) as exc:
        fail(exc)


@app.command("backup")
def backup(destination: Annotated[Path, typer.Option("--output")]) -> None:
    """Create a consistent SQLite backup of task, configuration, and trial records."""
    settings, _ = workspace()
    if destination.exists():
        fail(ValueError("Destination exists; choose a new backup filename."))
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with (
            sqlite3.connect(settings.database_path) as source,
            sqlite3.connect(destination) as target,
        ):
            source.backup(target)
        output(
            {
                "backup": str(destination),
                "bytes": destination.stat().st_size,
                "note": "Copy the artifacts directory separately when retaining external artifacts.",
            }
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.write("\n")
    except FileExistsError:
        fail(ValueError(f"Destination already exists: {path}"))


@collections_app.command("list")
def list_collections() -> None:
    """List collections and their immutable current versions."""
    _, store = workspace()
    output(store.list_collections())


@collections_app.command("import")
def import_collection(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Import a versioned JSON or YAML collection."""
    from cvals.api import _load_collection

    try:
        _, store = workspace()
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("Collection exceeds the 5 MiB limit.")
        value = Collection.model_validate(_load_collection(path.read_bytes(), path.name))
        output(store.save_collection(value))
    except (ValueError, OSError) as exc:
        fail(exc)


@collections_app.command("export")
def export_collection(
    collection_id: str, destination: Annotated[Path, typer.Option("--output")]
) -> None:
    """Write a portable collection manifest, including private grading data."""
    _, store = workspace()
    value = store.get_collection(collection_id)
    _write_new(destination, json.dumps(value, indent=2, ensure_ascii=False))
    typer.echo(str(destination))


@collections_app.command("validate")
def validate_collection_command(collection_id: str) -> None:
    """Verify reference controls and negative controls for every scenario."""
    from cvals.domains import validate_collection

    settings, store = workspace()
    result = validate_collection(
        Collection.model_validate(store.get_collection(collection_id)), settings
    )
    output(result)
    if not result["valid"]:
        raise typer.Exit(1)


# Registration follows the shared command helpers to avoid circular initialization.
from cvals import terminal_cli as _terminal_cli  # noqa: E402,F401
from cvals.experience_cli import register as _register_experience  # noqa: E402
from cvals.growth_cli import register as _register_growth  # noqa: E402
from cvals.team_cli import register_team_cli  # noqa: E402

_register_experience(app, systems_app)
_register_growth(app)
register_team_cli(app)


from cvals.autopilot_cli import register as _register_autopilot  # noqa: E402
from cvals.budgeting_cli import register as _register_budgeting  # noqa: E402
from cvals.chat_cli import register as _register_chat  # noqa: E402
from cvals.community_cli import register as _register_community  # noqa: E402
from cvals.demo_cli import register as _register_demo  # noqa: E402
from cvals.posttraining_cli import register as _register_training  # noqa: E402
from cvals.rankings_cli import register as _register_rankings  # noqa: E402
from cvals.research_cli import register as _register_research  # noqa: E402
from cvals.variants import register as _register_variants  # noqa: E402

_register_chat(app)
_register_autopilot(app)
_register_research(app)
_register_rankings(app)
_register_training(app)
_register_community(app)
_register_demo(app)
_register_budgeting(app)
_register_variants(systems_app)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    app()
