"""Explicit coordinator actions for blinded, locked human evaluation studies."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from cvals.config import Settings


def register(app: typer.Typer) -> None:
    from cvals.cli import fail, output

    group = typer.Typer(
        no_args_is_help=True,
        help="Coordinate double-blind studies with locked judgments and a controlled reveal.",
    )
    app.add_typer(group, name="blind")

    def service() -> Any:
        from cvals.blind_studies import BlindStudies

        return BlindStudies(Settings.from_env())

    def call(operation: Any) -> None:
        try:
            output(operation())
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @group.command("list")
    def list_studies() -> None:
        """List studies without revealing concealed model identities."""
        call(lambda: service().list())

    @group.command("show")
    def show(study_id: str) -> None:
        """Read review coverage, state, and the current evidence fingerprint."""
        call(lambda: service().get(study_id))

    @group.command("create")
    def create(
        name: Annotated[str, typer.Option(help="Study name; concealed until reveal.")],
        experiment: Annotated[str, typer.Option(help="Completed experiment identifier.")],
        rubric: Annotated[str, typer.Option(help="Saved scoring rubric identifier.")],
        systems: Annotated[
            list[str],
            typer.Option("--system", help="Select exactly two systems; repeat this option."),
        ],
        reviewers: Annotated[
            list[str],
            typer.Option(
                "--reviewer",
                help="Select at least two reviewer-only identities; repeat this option.",
            ),
        ],
    ) -> None:
        """Freeze a trusted-custodian study over existing outputs; no model calls."""
        from cvals.blind_studies import StudyRequest

        call(
            lambda: service().create(
                StudyRequest(
                    name=name,
                    experiment_id=experiment,
                    rubric_id=rubric,
                    system_ids=systems,
                    reviewer_ids=reviewers,
                    mode="trusted_custodian",
                ),
                actor="local-console",
            )
        )

    @group.command("lock")
    def lock(
        study_id: str,
        expected_hash: Annotated[
            str, typer.Option("--expected-hash", help="Evidence fingerprint from blind show.")
        ],
    ) -> None:
        """Permanently close judgments after every assigned review is complete."""
        call(lambda: service().lock(study_id, actor="local-console", expected_hash=expected_hash))

    @group.command("reveal")
    def reveal(
        study_id: str,
        expected_hash: Annotated[
            str, typer.Option("--expected-hash", help="Evidence fingerprint of the locked study.")
        ],
        reason: Annotated[
            str, typer.Option("--reason", help="Retained reason for revealing identities.")
        ],
    ) -> None:
        """Reveal identities and results from a locked study."""
        call(
            lambda: service().reveal(
                study_id, actor="local-console", expected_hash=expected_hash, rationale=reason
            )
        )

    @group.command("report")
    def report(study_id: str) -> None:
        """Print the revealed report; a concealed study cannot be exported."""
        call(lambda: service().report(study_id))

    @group.command("tui")
    def tui() -> None:
        """Open the coordinator's terminal screen without starting a worker."""
        from cvals.tui.blind_studies import BlindStudyApp

        try:
            BlindStudyApp(Settings.from_env()).run()
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)
