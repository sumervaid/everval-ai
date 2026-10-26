"""A coordinator screen for frozen blind studies and controlled result release."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, RichLog, Select, Static

from cvals.config import Settings
from cvals.tui.dialogs import EvidenceDialog, FieldSpec, FormDialog
from cvals.tui.presentation import plain


def study_text(study: dict[str, Any]) -> str:
    """Describe the released service view without consulting hidden identity maps."""
    state = study.get("status", "collecting")
    assigned = study.get("assigned_reviews", 0)
    completed = study.get("completed_reviews", 0)
    missing = study.get("missing_reviews", max(0, assigned - completed))
    lines = [
        plain(study.get("name", "Blinded study")),
        "",
        f"{completed} of {assigned} assigned reviews complete · {missing} remaining",
        "",
        "COLLECT → LOCK → REVEAL",
        "",
    ]
    if state == "collecting":
        lines.append(
            "Reviewers score anonymously labeled responses independently. Model identities and identified results remain concealed in study reports."
        )
        lines.append(
            "When every assigned review is complete, lock judgments before revealing results."
        )
    elif state == "locked":
        lines.append(
            "All judgments are locked. Further scoring and assignment changes are blocked."
        )
        lines.append("Reveal identities with a recorded reason to make the final report available.")
    else:
        lines.append(
            "Identities and results have been revealed. The locked judgments remain unchanged."
        )
    lines.extend(
        [
            "",
            "BOUNDARY",
            plain(
                study.get(
                    "boundary",
                    "The workspace owner and coordinator are trusted. Task and response wording may reveal identity. This study does not hide prompts from a hosted provider or protect model weights with confidential hardware.",
                )
            ),
        ]
    )
    lines.extend(
        [
            "",
            "Study: " + plain(study["id"]),
            "Review queue: " + plain(study.get("queue_id", "")),
            "Evidence: " + plain(study.get("evidence_hash", "")),
        ]
    )
    return "\n".join(lines)


class BlindStudyScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("ctrl+y", "close", "Back", show=False, priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
    ]
    CSS = """
    BlindStudyScreen { background: #0d1722; color: #dce5ea; }
    BlindStudyScreen #blind-header { height: 3; background: #132230; padding: 0 2; align-vertical: middle; }
    BlindStudyScreen #blind-title { width: 1fr; color: #83d7c7; text-style: bold; }
    BlindStudyScreen #blind-state { width: auto; color: #a9b8c7; }
    BlindStudyScreen #blind-toolbar { height: 3; margin: 1 2 0 2; }
    BlindStudyScreen #blind-selector { width: 1fr; margin-right: 1; }
    BlindStudyScreen Button { min-width: 10; height: 3; margin-right: 1; background: #203344; color: #dce5ea; border: tall #203344; }
    BlindStudyScreen Button:focus { border: tall #83d7c7; }
    BlindStudyScreen Button.-primary { background: #28584b; border: tall #28584b; color: #e1f6ed; }
    BlindStudyScreen Button:disabled { opacity: 40%; }
    BlindStudyScreen #blind-boundary { height: auto; max-height: 4; margin: 1 2 0 2; color: #a9b8c7; }
    BlindStudyScreen #blind-evidence { height: 1fr; min-height: 5; margin: 1 2; background: #0d1722; border: solid #2b4050; padding: 0 1; }
    BlindStudyScreen #blind-notice { height: auto; max-height: 4; color: #f1b2a3; margin: 0 2; }
    BlindStudyScreen #blind-actions { height: 3; margin: 0 2; overflow-x: auto; overflow-y: hidden; }
    BlindStudyScreen Footer { background: #09111a; color: #9aaebd; }
    BlindStudyScreen SelectCurrent { background: #132230; border: tall #2b4050; }
    """

    def __init__(
        self, settings: Settings, *, service: Any = None, standalone: bool = False
    ) -> None:
        super().__init__()
        self.settings = settings
        self.service = service
        self.standalone = standalone
        self.study: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []
        self._busy = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="blind-header"):
            yield Static("EVERVAL  /  DOUBLE-BLIND STUDIES", id="blind-title", markup=False)
            yield Static("COORDINATOR", id="blind-state", markup=False)
        with Horizontal(id="blind-toolbar"):
            yield Select([], prompt="Choose a study", id="blind-selector")
            yield Button("New study", id="blind-new", variant="primary")
            yield Button("Close" if self.standalone else "Back", id="blind-back")
        yield Static(
            "Trusted-custodian mode · Reviewers use their personal browser access. "
            "Local terminal access belongs to the trusted workspace owner.",
            id="blind-boundary",
            markup=False,
        )
        yield RichLog(
            id="blind-evidence",
            wrap=True,
            min_width=1,
            markup=False,
            highlight=False,
            auto_scroll=False,
        )
        yield Static("", id="blind-notice", markup=False)
        with Horizontal(id="blind-actions"):
            yield Button("Refresh", id="blind-refresh")
            yield Button("Lock judgments", id="blind-lock", disabled=True)
            yield Button("Reveal identities", id="blind-reveal", disabled=True)
            yield Button("Final report", id="blind-report", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#blind-notice").display = False
        self.action_refresh()
        self.set_interval(5, self._poll)

    def _service(self) -> Any:
        if self.service is None:
            from cvals.blind_studies import BlindStudies

            self.service = BlindStudies(self.settings)
        return self.service

    def _poll(self) -> None:
        if self.is_mounted and self.app.screen is self and not self._busy:
            self.action_refresh()

    def _controls(self) -> None:
        self.query_one("#blind-selector", Select).disabled = self._busy
        self.query_one("#blind-new", Button).disabled = self._busy
        self.query_one("#blind-refresh", Button).disabled = self._busy
        state = self.study or {}
        self.query_one("#blind-lock", Button).disabled = (
            self._busy
            or state.get("status") != "collecting"
            or state.get("assigned_reviews", 0) == 0
            or state.get("missing_reviews", 1) != 0
        )
        self.query_one("#blind-reveal", Button).disabled = (
            self._busy or state.get("status") != "locked"
        )
        self.query_one("#blind-report", Button).disabled = (
            self._busy or state.get("status") != "revealed"
        )

    def _error(self, message: str) -> None:
        notice = self.query_one("#blind-notice", Static)
        notice.update(plain(message))
        notice.display = True

    def _run(self, operation: Callable[[], Any], after: Callable[[Any], None]) -> None:
        if self._busy:
            return
        self._busy = True
        self.query_one("#blind-notice").display = False
        self._controls()

        async def execute() -> None:
            try:
                value = await asyncio.to_thread(operation)
                if self.is_mounted:
                    after(value)
            except Exception as exc:
                if self.is_mounted:
                    self._error(str(exc))
            finally:
                self._busy = False
                if self.is_mounted:
                    self._controls()

        self.run_worker(execute(), group="blind-studies", exit_on_error=False)

    def action_refresh(self) -> None:
        identifier = self.study["id"] if self.study else None

        def read() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            service = self._service()
            rows = service.list()
            chosen = identifier or (rows[0]["id"] if rows else None)
            return rows, service.get(chosen) if chosen else None

        self._run(read, self._render_study)

    def _render_study(self, value: tuple[list[dict[str, Any]], dict[str, Any] | None]) -> None:
        self.rows, self.study = value
        selector = self.query_one("#blind-selector", Select)
        selector.set_options(
            [
                (
                    Text(
                        plain(item.get("name", "Blinded study"))
                        + " · "
                        + plain(item["status"])
                        + " · "
                        + item["id"][:8]
                    ),
                    item["id"],
                )
                for item in self.rows
            ]
        )
        selector.value = self.study["id"] if self.study else Select.BLANK
        log = self.query_one("#blind-evidence", RichLog)
        log.clear()
        log.write(
            Text(
                study_text(self.study)
                if self.study
                else (
                    "A fair comparison, before the names are revealed.\n\n"
                    "1. Choose a completed experiment with two systems.\n"
                    "2. Freeze a rubric and assign at least two independent reviewers.\n"
                    "3. Collect all judgments, lock them, then reveal the results.\n\n"
                    "Create reviewer-only identities and a rubric in Team access and Review desk first.\n"
                    "This screen does not start model runs or make provider calls."
                )
            )
        )
        log.scroll_home(animate=False)
        self.query_one("#blind-state", Static).update(
            str(self.study["status"]).upper() if self.study else "COORDINATOR"
        )

    def _updated(self, study: dict[str, Any]) -> None:
        rows = [study, *(row for row in self.rows if row["id"] != study["id"])]
        self._render_study((rows, study))

    @on(Select.Changed, "#blind-selector")
    def selected(self, event: Select.Changed) -> None:
        if (
            self._busy
            or event.value is Select.BLANK
            or (self.study and event.value == self.study["id"])
        ):
            return
        identifier = str(event.value)
        self._run(lambda: self._service().get(identifier), self._updated)

    @on(Button.Pressed, "#blind-refresh")
    def refresh_pressed(self) -> None:
        self.action_refresh()

    @on(Button.Pressed, "#blind-new")
    def new_study(self) -> None:
        def choices() -> dict[str, Any]:
            from cvals.identity import IdentityStore
            from cvals.review_queues import ReviewQueues
            from cvals.store import Store

            store = Store(self.settings)
            store.init()
            return {
                "experiments": [
                    item for item in store.list_experiments() if item["status"] == "completed"
                ],
                "rubrics": ReviewQueues(self.settings).rubrics(),
                "reviewers": [
                    item
                    for item in IdentityStore(self.settings).users()
                    if item["active"] and item["role"] == "reviewer"
                ],
            }

        self._run(choices, self._new_form)

    def _new_form(self, choices: dict[str, Any]) -> None:
        if not choices["experiments"] or not choices["rubrics"] or len(choices["reviewers"]) < 2:
            self._error(
                "Create a completed experiment, a saved rubric, and at least two active reviewer-only identities first."
            )
            return
        self.app.push_screen(
            FormDialog(
                "Create a double-blind study",
                "Use a trusted coordinator account. Reviewers and result readers remain blinded until judgments are locked and identities are revealed.",
                [
                    FieldSpec("name", "Study name", value="Model comparison"),
                    FieldSpec(
                        "experiment_id",
                        "Completed experiment",
                        kind="choice",
                        options=[
                            (plain(item["name"]), item["id"]) for item in choices["experiments"]
                        ],
                    ),
                    FieldSpec(
                        "rubric_id",
                        "Frozen scoring rubric",
                        kind="choice",
                        options=[
                            (plain(item["name"]) + " · " + plain(item["version"]), item["id"])
                            for item in choices["rubrics"]
                        ],
                    ),
                    FieldSpec(
                        "reviewer_ids",
                        "Independent reviewers · choose at least two",
                        kind="multi",
                        options=[
                            (plain(item["name"]), item["id"]) for item in choices["reviewers"]
                        ],
                    ),
                ],
                submit="Choose models",
            ),
            self._choose_systems,
        )

    def _choose_systems(self, values: dict[str, Any] | None) -> None:
        if values is None:
            return

        def load() -> list[dict[str, Any]]:
            from cvals.store import Store

            return list(
                Store(self.settings).get_experiment(values["experiment_id"])["snapshot"]["systems"]
            )

        def form(systems: list[dict[str, Any]]) -> None:
            self.app.push_screen(
                FormDialog(
                    "Choose the two model configurations",
                    "The study freezes these recorded outputs and all reviewer assignments. No additional model calls are made.",
                    [
                        FieldSpec(
                            "system_ids",
                            "Systems from the completed experiment",
                            kind="multi",
                            value=",".join(item["id"] for item in systems)
                            if len(systems) == 2
                            else "",
                            options=[(plain(item["name"]), item["id"]) for item in systems],
                        )
                    ],
                    submit="Create study",
                ),
                lambda selected: self._create({**values, **selected}) if selected else None,
            )

        self._run(load, form)

    def _create(self, values: dict[str, Any]) -> None:
        def operation() -> dict[str, Any]:
            from cvals.blind_studies import StudyRequest

            return dict(
                self._service().create(
                    StudyRequest(**values, mode="trusted_custodian"), actor="local-console"
                )
            )

        self._run(operation, self._updated)

    @on(Button.Pressed, "#blind-lock")
    def lock_judgments(self) -> None:
        if self.study is None:
            return
        study = dict(self.study)
        self.app.push_screen(
            FormDialog(
                "Lock all judgments",
                f"All {study.get('assigned_reviews', 0)} assigned reviews are complete. Locking permanently closes scoring and assignment changes. Identities stay concealed until a separate reveal.",
                [],
                submit="Lock judgments",
            ),
            lambda values: (
                self._run(
                    lambda: self._service().lock(
                        study["id"], actor="local-console", expected_hash=study["evidence_hash"]
                    ),
                    self._updated,
                )
                if values is not None
                else None
            ),
        )

    @on(Button.Pressed, "#blind-reveal")
    def reveal_identities(self) -> None:
        if self.study is None:
            return
        study = dict(self.study)
        self.app.push_screen(
            FormDialog(
                "Reveal model identities",
                "This releases model identities and the final study report. The recorded judgments remain locked.",
                [FieldSpec("rationale", "Reason for revealing results", kind="multiline")],
                submit="Reveal identities",
            ),
            lambda values: (
                self._run(
                    lambda: self._service().reveal(
                        study["id"],
                        actor="local-console",
                        expected_hash=study["evidence_hash"],
                        rationale=values["rationale"],
                    ),
                    self._updated,
                )
                if values is not None
                else None
            ),
        )

    @on(Button.Pressed, "#blind-report")
    def report(self) -> None:
        if self.study is None:
            return
        identifier = self.study["id"]

        def show(value: dict[str, Any]) -> None:
            self.app.push_screen(
                EvidenceDialog(
                    "Revealed study report",
                    json.dumps(value, indent=2, ensure_ascii=False),
                    json=True,
                )
            )

        self._run(lambda: self._service().report(identifier), show)

    @on(Button.Pressed, "#blind-back")
    def action_close(self) -> None:
        if self.standalone:
            self.app.exit()
        else:
            self.app.pop_screen()


class BlindStudyApp(App[None]):
    TITLE = "Everval · Double-blind studies"
    CSS_PATH = "workbench.tcss"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self, settings: Settings, *, service: Any = None) -> None:
        super().__init__()
        self.settings, self.service = settings, service

    def on_mount(self) -> None:
        self.push_screen(BlindStudyScreen(self.settings, service=self.service, standalone=True))
