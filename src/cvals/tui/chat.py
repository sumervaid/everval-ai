"""A conversational terminal front door to the shared benchmark service."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

from rich.panel import Panel
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, RichLog, Select, Static, TextArea

from cvals.config import Settings
from cvals.tui.dialogs import EvidenceDialog
from cvals.tui.presentation import plain


def safe_text(value: Any) -> str:
    """Keep paragraphs while removing terminal controls from stored text."""
    return "\n".join(plain(line, "") for line in str(value).splitlines())


def cadence(minutes: Any) -> str:
    if minutes is None:
        return "Not set yet"
    value = int(minutes)
    for divisor, unit in ((10080, "week"), (1440, "day"), (60, "hour")):
        if value and value % divisor == 0:
            count = value // divisor
            return f"Every {count} {unit}{'' if count == 1 else 's'}"
    return f"Every {value} minute{'' if value == 1 else 's'}"


def plan_text(session: dict[str, Any], catalog: dict[str, Any] | None = None) -> str:
    """A small readable plan, never a second source of execution rules."""
    draft = session.get("draft", {})
    catalog = catalog or {}
    collections = {
        item["id"]: item.get("name", item["id"]) for item in catalog.get("collections", [])
    }
    systems = {item["id"]: item.get("name", item["id"]) for item in catalog.get("systems", [])}
    collection_id = draft.get("collection_id")
    selected = draft.get("system_ids", [])
    budget = draft.get("budget_usd")
    lifetime = draft.get("lifetime_budget_usd")
    formats = draft.get("report_formats", [])
    lines = [
        str(draft.get("name") or "Your benchmark"),
        "",
        "MODELS",
        ", ".join(str(systems.get(key, key)) for key in selected) or "Not selected yet",
        "",
        "TASKS",
        str(collections.get(collection_id, collection_id) or "Not selected yet"),
        "",
        "SCHEDULE",
        cadence(draft.get("interval_minutes")),
        "",
        "RESULTS",
        ", ".join(str(value).upper() for value in formats) or "Not selected yet",
        "",
        "SPENDING LIMIT",
        (f"${float(budget):g} per run" if budget is not None else "Per-run limit not set")
        + " · "
        + (f"${float(lifetime):g} total" if lifetime is not None else "Total limit not set"),
    ]
    if session.get("schedule_id"):
        lines.extend(["", "Schedule: " + str(session["schedule_id"])])
    return safe_text("\n".join(lines))


class ChatDetails(EvidenceDialog):
    """Open a long plan at its beginning, including in small terminals."""

    def on_mount(self) -> None:
        log = self.query_one("#evidence-log", RichLog)
        log.auto_scroll = False
        super().on_mount()
        log.scroll_home(animate=False)


class ChatInput(TextArea):
    """Enter sends; Shift+Enter keeps a paragraph inside the message."""

    BINDINGS = [
        Binding("enter", "send_message", show=False, priority=True),
        Binding("shift+enter", "new_line", show=False, priority=True),
    ]

    class Submitted(Message):
        pass

    def action_send_message(self) -> None:
        self.post_message(self.Submitted())

    def action_new_line(self) -> None:
        self.insert("\n")


class ChatScreen(Screen[None]):
    """A saved conversation with one input and a live, inspectable plan."""

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("ctrl+j", "close", "Back", show=False, priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
    ]
    CSS = """
    ChatScreen { background: #0d1722; color: #dce5ea; }
    ChatScreen #chat-masthead { height: 3; background: #132230; padding: 0 2; align-vertical: middle; }
    ChatScreen #chat-brand { width: 1fr; color: #83d7c7; text-style: bold; }
    ChatScreen #chat-state { width: auto; color: #a9b8c7; }
    ChatScreen #chat-toolbar { height: 3; margin: 1 2 0 2; }
    ChatScreen #chat-sessions { width: 1fr; height: 3; margin-right: 1; }
    ChatScreen Button { min-width: 10; height: 3; margin: 0 1 0 0; background: #203344; border: tall #203344; color: #dce5ea; }
    ChatScreen Button:focus { border: tall #83d7c7; }
    ChatScreen Button.-primary { background: #28584b; border: tall #28584b; color: #e1f6ed; }
    ChatScreen Button:disabled { opacity: 40%; }
    ChatScreen #chat-body { height: 1fr; margin: 1 2 0 2; min-height: 4; }
    ChatScreen #chat-transcript { width: 3fr; height: 100%; background: #0d1722; scrollbar-size: 1 1; }
    ChatScreen #chat-plan-pane { width: 34; height: 100%; margin-left: 2; border-left: solid #2b4050; padding: 0 1; overflow-y: auto; }
    ChatScreen #chat-plan-title { height: 2; color: #83d7c7; text-style: bold; }
    ChatScreen #chat-plan { height: auto; color: #a9b8c7; }
    ChatScreen #chat-error { height: auto; max-height: 4; margin: 0 2; color: #f1b2a3; }
    ChatScreen #chat-composer { height: 5; margin: 0 2; border: solid #2b4050; }
    ChatScreen #chat-input { width: 1fr; height: 100%; border: none; background: #132230; padding: 0 1; }
    ChatScreen #chat-send { width: 10; margin: 0; height: 3; }
    ChatScreen #chat-actions { height: 3; margin: 0 2; overflow-x: auto; overflow-y: hidden; }
    ChatScreen #chat-hint { height: 1; margin: 0 2; color: #9aaebd; }
    ChatScreen Footer { background: #09111a; color: #9aaebd; }
    ChatScreen SelectCurrent { background: #132230; border: tall #2b4050; }
    ChatScreen.compact-chat #chat-plan-pane { display: none; }
    ChatScreen.compact-chat #chat-transcript { width: 100%; }
    ChatScreen.short-chat #chat-body { margin-top: 0; }
    ChatScreen.short-chat #chat-toolbar { margin-top: 0; }
    ChatScreen.short-chat #chat-composer { height: 4; }
    ChatScreen.short-chat #chat-hint { display: none; }
    """

    def __init__(
        self,
        settings: Settings,
        *,
        service: Any = None,
        session_id: str | None = None,
        standalone: bool = False,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.service = service
        self.session_id = session_id
        self.standalone = standalone
        self.session: dict[str, Any] = {}
        self.catalog: dict[str, Any] = {}
        self._busy = False
        self._refreshing = False
        self._revision = 0
        self._transcript: list[dict[str, Any]] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="chat-masthead"):
            yield Static("EVERVAL  /  BENCHMARK CHAT", id="chat-brand", markup=False)
            yield Static("LOCAL WORKSPACE", id="chat-state", markup=False)
        with Horizontal(id="chat-toolbar"):
            yield Select([], prompt="Saved conversations", id="chat-sessions")
            yield Button("New", id="chat-new")
            yield Button("Back" if not self.standalone else "Close", id="chat-close")
        with Horizontal(id="chat-body"):
            yield RichLog(
                id="chat-transcript", wrap=True, min_width=1, markup=False, highlight=False
            )
            with Vertical(id="chat-plan-pane"):
                yield Static("YOUR PLAN", id="chat-plan-title")
                yield Static("", id="chat-plan", markup=False)
        yield Static("", id="chat-error", markup=False)
        with Horizontal(id="chat-composer"):
            yield ChatInput(id="chat-input", soft_wrap=True, show_line_numbers=False)
            yield Button("Send", id="chat-send", variant="primary")
        yield Static(
            "Enter sends · Shift+Enter adds a line · Your conversation is saved locally",
            id="chat-hint",
            markup=False,
        )
        with Horizontal(id="chat-actions"):
            yield Button("Models & tasks", id="chat-catalog")
            yield Button("Plan", id="chat-details")
            yield Button("Start", id="chat-start", variant="primary")
            yield Button("Pause", id="chat-toggle")
            yield Button("Results", id="chat-results")
        yield Footer()

    def on_mount(self) -> None:
        self._resize()
        self.query_one("#chat-error").display = False
        self._perform(self._load)
        self.set_interval(5, self._poll)

    def _load(self) -> dict[str, Any]:
        if self.service is None:
            from cvals.conversation import ConversationService
            from cvals.store import Store

            self.settings.prepare()
            store = Store(self.settings)
            store.init()
            self.service = ConversationService(self.settings, store)
        self.catalog = self.service.catalog()
        return cast(
            dict[str, Any],
            self.service.get(self.session_id) if self.session_id else self.service.create(),
        )

    def on_resize(self, event: Resize) -> None:
        self._resize()

    def _resize(self) -> None:
        self.set_class(self.size.width < 105, "compact-chat")
        self.set_class(self.size.height < 32, "short-chat")

    def _controls(self) -> None:
        status = self.session.get("status", "draft")
        for identity in ("send", "new", "catalog"):
            self.query_one("#chat-" + identity, Button).disabled = self._busy
        self.query_one("#chat-input", ChatInput).disabled = self._busy
        self.query_one("#chat-sessions", Select).disabled = self._busy
        self.query_one("#chat-start", Button).disabled = (
            self._busy or status != "draft" or not self.session.get("ready", False)
        )
        toggle = self.query_one("#chat-toggle", Button)
        toggle.disabled = self._busy or status not in {"active", "paused"}
        toggle.label = "Resume" if status == "paused" else "Pause"
        self.query_one("#chat-results", Button).disabled = not bool(self.session.get("reports"))
        self.query_one("#chat-state", Static).update(
            "SAVING…"
            if self._busy
            else {"active": "SCHEDULED", "paused": "PAUSED"}.get(status, "DRAFT")
        )

    def _error(self, message: str) -> None:
        notice = self.query_one("#chat-error", Static)
        notice.update(safe_text(message))
        notice.display = bool(message)

    def _perform(
        self,
        operation: Callable[[], dict[str, Any]],
        *,
        clear_input: bool = False,
        history: bool = True,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self._revision += 1
        self._controls()
        self._error("")

        async def execute() -> None:
            try:
                session = await asyncio.to_thread(operation)
                sessions = await asyncio.to_thread(self.service.list) if history else None
                if not self.is_mounted:
                    return
                self.session = session
                self.session_id = session["id"]
                self._render_session(sessions)
                if clear_input:
                    self.query_one("#chat-input", ChatInput).clear()
            except Exception as exc:
                if self.is_mounted:
                    self._error(str(exc))
            finally:
                self._busy = False
                if self.is_mounted:
                    self._controls()
                    if history:
                        self.query_one("#chat-input", ChatInput).focus()

        self.run_worker(execute(), group="benchmark-chat", exit_on_error=False)

    def _render_session(self, sessions: list[dict[str, Any]] | None) -> None:
        if sessions is not None:
            selector = self.query_one("#chat-sessions", Select)
            options = [
                (
                    Text(
                        plain(item.get("draft", {}).get("name") or "Untitled benchmark")
                        + " · "
                        + plain(item.get("status", "draft"))
                        + " · "
                        + item["id"][:8]
                    ),
                    item["id"],
                )
                for item in sessions
            ]
            selector.set_options(options)
            selector.value = self.session_id
        messages = self.session.get("messages", [])
        if messages != self._transcript:
            self._transcript = list(messages)
            log = self.query_one("#chat-transcript", RichLog)
            log.clear()
            if not messages:
                log.write(
                    Panel(
                        Text(
                            "Tell me what to benchmark and how often.\n\n"
                            "“Compare my two models on support tickets every 2 weeks. "
                            "Save HTML and CSV. Limit spending to $5 per run and $50 total.”\n\n"
                            "Use your saved models and task collections. I'll ask for anything missing."
                        ),
                        title="EVERVAL",
                        title_align="left",
                        border_style="#355d5b",
                        padding=(1, 2),
                    )
                )
            for message in messages:
                user = message.get("role") == "user"
                log.write(
                    Panel(
                        Text(safe_text(message.get("content", ""))),
                        title="YOU" if user else "EVERVAL",
                        title_align="left",
                        border_style="#2b4050" if user else "#355d5b",
                        padding=(1, 2),
                    )
                )
        self.query_one("#chat-plan", Static).update(plan_text(self.session, self.catalog))

    @on(ChatInput.Submitted)
    @on(Button.Pressed, "#chat-send")
    def send(self) -> None:
        value = self.query_one("#chat-input", ChatInput).text.strip()
        if not value or self._busy or self.session_id is None:
            return
        if len(value) > 8000:
            self._error("Keep each message under 8,000 characters.")
            return
        self._perform(lambda: self.service.send(self.session_id, value), clear_input=True)

    @on(Button.Pressed, "#chat-catalog")
    def show_catalog(self) -> None:
        if self.session_id:
            self._perform(lambda: self.service.send(self.session_id, "help"))

    @on(Button.Pressed, "#chat-new")
    def new_conversation(self) -> None:
        self._perform(self.service.create)

    @on(Select.Changed, "#chat-sessions")
    def choose_session(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK and event.value != self.session_id and not self._busy:
            self._perform(lambda: self.service.get(str(event.value)))

    @on(Button.Pressed, "#chat-start")
    def start(self) -> None:
        if self.session_id:
            self._perform(lambda: self.service.activate(self.session_id))

    @on(Button.Pressed, "#chat-toggle")
    def toggle(self) -> None:
        if self.session_id:
            action = (
                self.service.resume
                if self.session.get("status") == "paused"
                else self.service.pause
            )
            self._perform(lambda: action(self.session_id))

    @on(Button.Pressed, "#chat-details")
    def details(self) -> None:
        self.app.push_screen(
            ChatDetails("Your benchmark plan", plan_text(self.session, self.catalog))
        )

    @on(Button.Pressed, "#chat-results")
    def results(self) -> None:
        receipts = self.session.get("reports", [])
        lines = ["Results stay in your local workspace.\n"]
        for receipt in receipts:
            lines.extend(
                [
                    plain(receipt.get("format", "report")).upper()
                    + " · "
                    + plain(receipt.get("created_at", "")),
                    safe_text(receipt.get("path", "")),
                    "",
                ]
            )
        self.app.push_screen(EvidenceDialog("Saved results", "\n".join(lines)))

    def _poll(self) -> None:
        if self.session.get("status") in {"active", "paused"} and self.app.screen is self:
            self.action_refresh()

    def action_refresh(self) -> None:
        if not self.session_id or self.service is None or self._busy or self._refreshing:
            return
        self._refreshing = True
        revision, identity = self._revision, self.session_id

        async def refresh_session() -> None:
            try:
                session = await asyncio.to_thread(self.service.get, identity)
                if self.is_mounted and revision == self._revision and not self._busy:
                    self.session = session
                    self._render_session(None)
                    self._controls()
            except Exception as exc:
                if self.is_mounted and revision == self._revision:
                    self._error(str(exc))
            finally:
                self._refreshing = False

        self.run_worker(refresh_session(), group="benchmark-chat-refresh", exit_on_error=False)

    @on(Button.Pressed, "#chat-close")
    def action_close(self) -> None:
        if self._busy:
            self.notify("Saving your request. Please wait a moment.")
        elif self.standalone:
            self.app.exit()
        else:
            self.app.pop_screen()


class ChatApp(App[None]):
    TITLE = "Everval · Benchmark chat"
    CSS_PATH = "workbench.tcss"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(
        self, settings: Settings, *, session_id: str | None = None, service: Any = None
    ) -> None:
        super().__init__()
        self.settings = settings
        self.session_id = session_id
        self.service = service

    def on_mount(self) -> None:
        self.push_screen(
            ChatScreen(
                self.settings, service=self.service, session_id=self.session_id, standalone=True
            )
        )
