"""Canonical command entry point with a lightweight version query."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from cvals import __version__

if TYPE_CHECKING:
    from cvals.cli import app as app


def main() -> None:
    if sys.argv[1:] == ["--version"]:
        print(__version__)
        return
    from cvals.cli import app

    app()


def __getattr__(name: str) -> Any:
    if name == "app":
        from cvals.cli import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
