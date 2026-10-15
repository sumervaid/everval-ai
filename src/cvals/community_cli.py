"""Explicit local import, review, and export commands for community packs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import Field

from cvals.community import MAX_PACK_BYTES, CommunityPacks, pack_summary, read_pack, write_pack
from cvals.demo import builtin_catalog, builtin_pack
from cvals.models import Manifest


class PackReview(Manifest):
    rationale: str = Field(min_length=1, max_length=10000)
    expected_behaviors: dict[str, str] = Field(min_length=1, max_length=100)


def register(parent: typer.Typer) -> None:
    from cvals.cli import fail, output, workspace

    packs = typer.Typer(
        no_args_is_help=True, help="Inspect, review, and exchange data-only task packs locally."
    )

    @packs.command("catalog")
    def catalog() -> None:
        """List the versioned bundled index. No registry network requests."""
        output(builtin_catalog())

    @packs.command("inspect")
    def inspect(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
        """Validate a file and show its identity without importing or executing it."""
        try:
            output(pack_summary(read_pack(path)))
        except (ValueError, OSError) as exc:
            fail(exc)

    @packs.command("stage")
    def stage(
        path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
        reviewer: Annotated[str, typer.Option()],
    ) -> None:
        """Retain a pack for review; it cannot enter experiments until approved."""
        try:
            settings, _ = workspace()
            with path.open("rb") as stream:
                raw = stream.read(MAX_PACK_BYTES + 1)
            output(CommunityPacks(settings).stage(raw, actor=reviewer))
        except (ValueError, OSError) as exc:
            fail(exc)

    @packs.command("list")
    def list_packs() -> None:
        settings, _ = workspace()
        output(CommunityPacks(settings).list())

    @packs.command("show")
    def show(
        identifier: str,
        include_hidden: Annotated[
            bool,
            typer.Option(
                help="Deliberately reveal private checks, references, controls, and review evidence."
            ),
        ] = False,
    ) -> None:
        try:
            settings, _ = workspace()
            output(CommunityPacks(settings).get(identifier, include_hidden=include_hidden))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @packs.command("approve")
    def approve(
        identifier: str,
        review: Annotated[
            Path,
            typer.Option(
                exists=True,
                dir_okay=False,
                help="JSON with rationale and expected_behaviors keyed by scenario ID.",
            ),
        ],
        reviewer: Annotated[str, typer.Option()],
    ) -> None:
        """Record human review and admit only passing reference/negative controls."""
        try:
            settings, _ = workspace()
            with review.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Review file exceeds 1 MiB")
            decision = PackReview.model_validate_json(raw)
            result = CommunityPacks(settings).approve(
                identifier,
                actor=reviewer,
                rationale=decision.rationale,
                expected_behaviors=decision.expected_behaviors,
            )
            output(
                {k: v for k, v in result.items() if k not in {"validation", "expected_behaviors"}}
            )
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @packs.command("export")
    def export(
        identifier: str,
        path: Path,
        include_hidden: Annotated[
            bool,
            typer.Option(
                help="Acknowledge hidden grader and reference disclosure in this local file."
            ),
        ] = False,
    ) -> None:
        try:
            settings, _ = workspace()
            output(CommunityPacks(settings).export(identifier, path, include_hidden=include_hidden))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @packs.command("builtin")
    def builtin(
        example: str,
        path: Path,
        include_hidden: Annotated[
            bool, typer.Option(help="Acknowledge hidden grader and reference disclosure.")
        ] = False,
    ) -> None:
        """Write one bundled coding, documents, or data fixture pack locally."""
        try:
            output(write_pack(builtin_pack(example), path, include_hidden=include_hidden))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)

    @packs.command("schema")
    def schema() -> None:
        """Print the versioned pack JSON Schema for authoring tools."""
        from cvals.community import CommunityPack

        output(CommunityPack.model_json_schema())

    parent.add_typer(packs, name="packs")
