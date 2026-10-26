"""Conversational setup for a saved, recurring benchmark."""

from __future__ import annotations

from typing import Annotated

import typer


def register(app: typer.Typer) -> None:
    @app.command("chat")
    def chat(
        message: Annotated[
            str | None,
            typer.Option("--message", "-m", help="Send one message and print the reply."),
        ] = None,
        session: Annotated[
            str | None, typer.Option("--session", "-s", help="Resume a saved conversation.")
        ] = None,
        list_sessions: Annotated[
            bool, typer.Option("--list", help="List saved conversations.")
        ] = False,
        json_output: Annotated[
            bool,
            typer.Option(
                "--json", help="Print structured output for a one-message request or list."
            ),
        ] = False,
        worker: Annotated[
            bool,
            typer.Option(
                "--worker/--no-worker",
                help="Keep scheduled evaluations running in the local worker.",
            ),
        ] = True,
    ) -> None:
        """Describe models, tasks, schedule and results in plain language."""
        from cvals.cli import fail, output, workspace
        from cvals.conversation import ConversationService
        from cvals.runtime import ensure_worker
        from cvals.tui.chat import ChatApp, plan_text, safe_text

        try:
            if list_sessions and (message is not None or session is not None):
                raise ValueError("Use --list by itself, or send a message to a conversation.")
            if json_output and message is None and not list_sessions:
                raise ValueError("--json needs --message or --list.")
            settings, store = workspace()
            service = ConversationService(settings, store)
            if list_sessions:
                sessions = service.list()
                if json_output:
                    output(sessions)
                elif not sessions:
                    typer.echo("No saved conversations. Start one with everval chat.")
                else:
                    for item in sessions:
                        typer.echo(
                            safe_text(
                                item["id"]
                                + "  "
                                + item.get("status", "draft")
                                + "  "
                                + str(item.get("draft", {}).get("name") or "Untitled benchmark")
                            )
                        )
                return
            if message is not None:
                if not message.strip():
                    raise ValueError("Write a message to start or update the benchmark.")
                conversation = service.get(session) if session else service.create()
                conversation = service.send(conversation["id"], message)
                if worker and conversation.get("status") == "active":
                    ensure_worker(settings)
                if json_output:
                    output(conversation)
                else:
                    reply = next(
                        (
                            item["content"]
                            for item in reversed(conversation.get("messages", []))
                            if item.get("role") == "assistant"
                        ),
                        "Your conversation has been saved.",
                    )
                    typer.echo(safe_text(reply))
                    typer.echo("\n" + plan_text(conversation, service.catalog()))
                    typer.echo("\nResume: everval chat --session " + conversation["id"])
                return
            if worker:
                ensure_worker(settings)
            ChatApp(settings, session_id=session, service=service).run()
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)
