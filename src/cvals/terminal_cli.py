"""Terminal, repository, and evidence commands for Everval AI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer

from cvals.cli import app, fail, output
from cvals.config import Settings
from cvals.workbench import Workbench

projects = typer.Typer(no_args_is_help=True, help="Connect repositories and curate real work.")
benchmarks = typer.Typer(
    no_args_is_help=True, help="Publish immutable validated benchmark releases."
)
jobs = typer.Typer(no_args_is_help=True, help="Inspect durable repository jobs.")
audit = typer.Typer(no_args_is_help=True, help="Inspect the local audit chain.")
environments = typer.Typer(
    no_args_is_help=True,
    help="Prepare isolated Python, JavaScript and TypeScript dependency environments.",
)
app.add_typer(projects, name="projects")
app.add_typer(benchmarks, name="benchmarks")
app.add_typer(jobs, name="jobs")
app.add_typer(audit, name="audit")
app.add_typer(environments, name="environments")
policies = typer.Typer(
    no_args_is_help=True, help="Version decision policies and inspect retained gate outcomes."
)
app.add_typer(policies, name="policies")


def call(method: str, *args: Any, **kwargs: Any) -> None:
    try:
        output(getattr(Workbench(), method)(*args, **kwargs))
    except (ValueError, KeyError, OSError) as exc:
        fail(exc)


@app.command()
def tui(
    worker: Annotated[bool, typer.Option("--worker/--no-worker")] = True,
    source: Annotated[
        str | None, typer.Option(help="Prefill the repository connection form.")
    ] = None,
) -> None:
    """Open the interactive terminal workbench."""
    from cvals.runtime import ensure_worker
    from cvals.tui.app import WorkbenchApp

    try:
        settings = Settings.from_env()
        if worker:
            ensure_worker(settings)
        WorkbenchApp(settings, workbench=Workbench(settings), source=source).run()
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def tmux(attach: Annotated[bool, typer.Option("--attach/--no-attach")] = True) -> None:
    """Create or resume a styled workspace-specific tmux session."""
    from cvals.runtime import tmux_session

    try:
        name = tmux_session(Settings.from_env(), attach=attach)
        if not attach:
            output({"session": name, "attach": f"tmux attach-session -t {name}"})
    except (ValueError, OSError) as exc:
        fail(exc)


@projects.command("connect")
def connect(source: str) -> None:
    """Connect owner/repository, a GitHub URL, or a local repository path."""
    call("connect", source)


@projects.command("list")
def project_list() -> None:
    call("projects")


@projects.command()
def discover(
    project_id: str,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
    cursor: str | None = None,
) -> None:
    """Discover candidates; the job continues if the terminal disconnects."""
    call("discover", project_id, limit, cursor)


@projects.command()
def candidates(project_id: str) -> None:
    call("candidates", project_id)


@projects.command("review")
def review_candidate(
    project_id: str, candidate_id: str, statement: Path, reviewer: str, rationale: str
) -> None:
    """Approve an independently written issue statement from a UTF-8 file."""
    try:
        if statement.stat().st_size > 100000:
            raise ValueError("Statement file exceeds 100 KiB")
        call(
            "review_candidate", project_id, candidate_id, statement.read_text(), reviewer, rationale
        )
    except (ValueError, OSError) as exc:
        fail(exc)


@projects.command()
def validate(project_id: str, candidate_id: str) -> None:
    """Repeat fail-before/pass-after checks and baseline regression controls."""
    call("validate", project_id, candidate_id)


@projects.command("profile")
def profile(
    project_id: str,
    image: str,
    timeout: Annotated[int, typer.Option(min=1, max=120)] = 60,
    plugin: Annotated[list[str] | None, typer.Option("--plugin")] = None,
    language: str = "python",
    runtime: str = "python-pytest",
) -> None:
    """Pin an already-local Python or Node test runtime and explicit language."""
    call(
        "add_project_profile",
        project_id,
        image,
        timeout,
        plugin,
        language=language,
        runtime=runtime,
    )


@environments.command("plan")
def plan_environment(
    project_id: str,
    package: Annotated[list[str] | None, typer.Option("--package")] = None,
    language: str = "python",
    runner: str = "node-test",
    artifact: Annotated[list[str] | None, typer.Option("--artifact")] = None,
    base_image: str | None = None,
    revision: str | None = None,
) -> None:
    """Review Python wheels or committed npm lock dependencies before isolated preparation."""
    call(
        "environment_plan",
        project_id,
        package,
        revision,
        base_image,
        language=language,
        runner=runner,
        artifacts=artifact,
    )


@environments.command("prepare")
def prepare_environment(
    project_id: str,
    package: Annotated[list[str] | None, typer.Option("--package")] = None,
    language: str = "python",
    runner: str = "node-test",
    artifact: Annotated[list[str] | None, typer.Option("--artifact")] = None,
    base_image: str | None = None,
    revision: str | None = None,
) -> None:
    """Prepare reviewed wheels or npm archives in Docker; repository install scripts stay disabled."""
    call(
        "prepare_environment",
        project_id,
        package,
        revision,
        base_image,
        language=language,
        runner=runner,
        artifacts=artifact,
    )


@benchmarks.command("release")
def release(project_id: str, name: str) -> None:
    """Seal admitted tasks into stable development and holdout partitions."""
    call("release", project_id, name)


@benchmarks.command("list")
def releases(project_id: str | None = None) -> None:
    call("releases", project_id)


@benchmarks.command("diff")
def release_diff(before_id: str, after_id: str) -> None:
    from cvals.benchmarks import Benchmarks

    try:
        output(Benchmarks(Settings.from_env()).diff(before_id, after_id))
    except (ValueError, KeyError, OSError) as exc:
        fail(exc)


@benchmarks.command("export")
def release_export(release_id: str, private_grader: bool = False) -> None:
    """Export a solver bundle, or explicitly include private grading material."""
    from cvals.benchmarks import Benchmarks
    from cvals.models import Collection, content_hash, new_id

    try:
        settings = Settings.from_env()
        bundle = Benchmarks(settings).export(release_id)
        if not private_grader:
            bundle = {
                "schema_version": 1,
                "release_id": release_id,
                "contains_private_grader_data": False,
                "collections": {
                    partition: {
                        "collection_id": value["id"],
                        "name": value["name"],
                        "collection_hash": content_hash(value),
                        "scenarios": [
                            scenario.agent_view()
                            for scenario in Collection.model_validate(value).scenarios
                        ],
                    }
                    for partition, value in bundle["collections"].items()
                },
            }
        bundle["bundle_hash"] = content_hash(bundle)
        path = settings.artifacts_dir / f"release-{release_id}-{new_id()}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(bundle, stream, indent=2, ensure_ascii=False, allow_nan=False)
        from cvals.audit import AuditLog

        AuditLog(settings).record(
            "release.exported", release_id, {"path": str(path), "private_grader": private_grader}
        )
        output(
            {
                "path": str(path),
                "private_grader": private_grader,
                "bundle_hash": bundle["bundle_hash"],
            }
        )
    except (ValueError, KeyError, OSError) as exc:
        fail(exc)


@jobs.command("list")
def job_list() -> None:
    from cvals.jobs import Jobs

    output(Jobs(Settings.from_env()).list())


@jobs.command("show")
def job_show(identifier: str) -> None:
    from cvals.jobs import Jobs

    try:
        output(Jobs(Settings.from_env()).get(identifier))
    except KeyError as exc:
        fail(exc)


@audit.command("list")
def audit_list(limit: Annotated[int, typer.Option(min=1, max=10000)] = 100) -> None:
    call("audit", limit)


@audit.command("verify")
def audit_verify(expected_head: str | None = None) -> None:
    from cvals.audit import AuditLog

    result = AuditLog(Settings.from_env()).verify(expected_head)
    output(result)
    if not result["valid"]:
        raise typer.Exit(1)


@app.command()
def compare(before_id: str, after_id: str) -> None:
    """Compare finalized evidence on a matching frozen cohort."""
    call("diff", before_id, after_id)


@app.command()
def gate(
    experiment_id: str,
    policy: Path | None = None,
    baseline: str | None = None,
    policy_id: str | None = None,
) -> None:
    """Evaluate and retain a CI quality decision; nonpassing evidence exits nonzero."""
    try:
        if policy and policy_id:
            raise ValueError("Choose a policy file or saved policy ID")
        if policy and policy.stat().st_size > 65536:
            raise ValueError("Policy exceeds 64 KiB")
        configured = json.loads(policy.read_text()) if policy else None
        result = Workbench().gates(experiment_id, configured, baseline, policy_id)
        output(result)
    except (ValueError, KeyError, OSError) as exc:
        fail(exc)
    if result.get("status") != "passed":
        raise typer.Exit(1)


@policies.command("save")
def policy_save(source: Path) -> None:
    """Save an immutable named policy version from JSON."""
    from cvals.policies import PolicyStore

    try:
        if source.stat().st_size > 65536:
            raise ValueError("Policy exceeds 64 KiB")
        output(PolicyStore(Settings.from_env()).save(json.loads(source.read_text())))
    except (ValueError, KeyError, OSError) as exc:
        fail(exc)


@policies.command("list")
def policy_list() -> None:
    from cvals.policies import PolicyStore

    output(PolicyStore(Settings.from_env()).list())


@policies.command("decisions")
def policy_decisions(experiment_id: str) -> None:
    from cvals.policies import PolicyStore

    output(PolicyStore(Settings.from_env()).decisions(experiment_id))
