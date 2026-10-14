"""Filesystem-owner provisioning for opt-in local team authentication."""

from __future__ import annotations

import json

import typer

from cvals.config import Settings
from cvals.identity import IdentityStore

app = typer.Typer(help="Provision local identities and revocable personal access tokens.")


def store() -> IdentityStore:
    return IdentityStore(Settings.from_env())


@app.command("bootstrap")
def bootstrap(name: str = typer.Option(..., help="Initial administrator display name")) -> None:
    """Enable team authentication atomically with a usable administrator token."""
    typer.echo(json.dumps(store().bootstrap(name), indent=2))
    typer.echo(
        "Save this token securely. It is shown once; team mode now requires individual tokens.",
        err=True,
    )


@app.command("users")
def users() -> None:
    typer.echo(json.dumps(store().users(), indent=2))


@app.command("add-user")
def add_user(name: str, role: str = "reviewer") -> None:
    typer.echo(json.dumps(store().create_user(name, role, "local-console"), indent=2))


@app.command("issue-token")
def issue_token(user_id: str, label: str = "Personal access", days: int = 30) -> None:
    """Issue or recover a token through trusted filesystem access."""
    typer.echo(
        json.dumps(store().issue_token(user_id, label, days, actor="local-console"), indent=2)
    )
    typer.echo("This token is shown once. Existing tokens remain valid until revoked.", err=True)


@app.command("revoke-token")
def revoke_token(token_id: str) -> None:
    store().revoke(token_id, "local-console")
    typer.echo("Token revoked.")


@app.command("disable-user")
def disable_user(user_id: str) -> None:
    store().set_active(user_id, False, "local-console")
    typer.echo("Identity disabled; its tokens and assignments no longer grant access.")


@app.command("enable-user")
def enable_user(user_id: str) -> None:
    store().set_active(user_id, True, "local-console")
    typer.echo("Identity enabled.")


def register_team_cli(parent: typer.Typer) -> None:
    parent.add_typer(app, name="team")
