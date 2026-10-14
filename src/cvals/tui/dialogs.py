"""Small, accessible dialogs shared by terminal workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Select, SelectionList, Static, TextArea


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    value: str = ""
    placeholder: str = ""
    kind: str = "text"
    options: list[tuple[str, str]] = field(default_factory=list)
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None


class FormDialog(ModalScreen[dict[str, Any] | None]):
    """Return validated values; domain admission remains in the shared core."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self, title: str, description: str, fields: list[FieldSpec], submit: str = "Continue"
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.description = description
        self.fields = fields
        self.submit_label = submit

    def compose(self) -> ComposeResult:
        with Vertical(id="form-dialog", classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title", markup=False)
            yield Static(self.description, classes="dialog-description", markup=False)
            with VerticalScroll(id="form-fields"):
                for spec in self.fields:
                    yield Label(spec.label, classes="field-label", markup=False)
                    widget_id = "field-" + spec.key
                    if spec.kind == "choice":
                        yield Select[str](
                            spec.options,
                            value=spec.value if spec.value else Select.BLANK,
                            prompt=spec.placeholder or "Choose…",
                            allow_blank=True,
                            id=widget_id,
                        )
                    elif spec.kind == "multi":
                        yield SelectionList[str](
                            *(
                                (Text(label), value, value in spec.value.split(","))
                                for label, value in spec.options
                            ),
                            id=widget_id,
                        )
                    elif spec.kind == "multiline":
                        yield TextArea(spec.value, id=widget_id, soft_wrap=True)
                    else:
                        yield Input(value=spec.value, placeholder=spec.placeholder, id=widget_id)
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="form-cancel")
                yield Button(self.submit_label, variant="primary", id="form-submit")

    def on_resize(self, event: Resize) -> None:
        if self.is_mounted:
            self._size_dialog()

    def _size_dialog(self) -> None:
        field_height = sum(
            9 if spec.kind == "multi" else 8 if spec.kind == "multiline" else 5
            for spec in self.fields
        )
        self.query_one("#form-dialog").styles.height = min(
            self.app.size.height - 2, 42, 16 + field_height
        )

    def on_mount(self) -> None:
        self._size_dialog()
        if self.fields:
            self.query_one("#field-" + self.fields[0].key).focus()

    @on(Button.Pressed, "#form-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#form-submit")
    def submit(self) -> None:
        values: dict[str, Any] = {}
        try:
            for spec in self.fields:
                widget = self.query_one("#field-" + spec.key)
                value: Any
                if isinstance(widget, Select):
                    value = widget.value if widget.value is not Select.BLANK else ""
                elif isinstance(widget, SelectionList):
                    value = widget.selected
                elif isinstance(widget, TextArea):
                    value = widget.text.strip()
                else:
                    assert isinstance(widget, Input)
                    value = widget.value.strip()
                if spec.required and not value:
                    raise ValueError(f"{spec.label} is required.")
                if spec.kind in {"number", "integer"} and value:
                    try:
                        value = int(value) if spec.kind == "integer" else float(value)
                    except ValueError as error:
                        raise ValueError(f"{spec.label} must be a valid {spec.kind}.") from error
                    if not math.isfinite(value):
                        raise ValueError(f"{spec.label} must be finite.")
                    if spec.minimum is not None and value < spec.minimum:
                        raise ValueError(f"{spec.label} must be at least {spec.minimum:g}.")
                    if spec.maximum is not None and value > spec.maximum:
                        raise ValueError(f"{spec.label} must be at most {spec.maximum:g}.")
                values[spec.key] = value
        except ValueError as error:
            self.query_one("#form-error", Static).update(str(error))
            return
        self.dismiss(values)


class EvidenceDialog(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, title: str, text: str, *, json: bool = False) -> None:
        super().__init__()
        self.dialog_title = title
        self.text = text
        self.is_json = json

    def compose(self) -> ComposeResult:
        with Vertical(id="evidence-dialog", classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title", markup=False)
            yield Static("Scroll to inspect the complete record · Esc closes", classes="muted")
            yield RichLog(id="evidence-log", wrap=True, min_width=1, markup=False, highlight=False)
            yield Button("Close", id="evidence-close", variant="primary")

    def on_mount(self) -> None:
        log = self.query_one(RichLog)
        log.write(
            Syntax(self.text, "json", theme="ansi_dark", word_wrap=True)
            if self.is_json
            else Text(self.text)
        )
        log.focus()

    @on(Button.Pressed, "#evidence-close")
    def close(self) -> None:
        self.dismiss(None)


class PlanDialog(ModalScreen[bool]):
    """Review an immutable environment plan before permitting dependency retrieval."""

    BINDINGS = [("escape", "dismiss(False)", "Cancel")]

    def __init__(self, plan: dict[str, Any]) -> None:
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Vertical(id="evidence-dialog", classes="dialog"):
            yield Static("Review execution environment", classes="dialog-title", markup=False)
            yield Static(
                "Preparation is bound to this source commit and immutable base image. Review requirements and notices before downloading packages.",
                classes="dialog-description",
                markup=False,
            )
            yield RichLog(id="plan-log", wrap=True, min_width=1, markup=False, highlight=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="plan-cancel")
                yield Button("Prepare runtime", id="plan-prepare", variant="primary")

    def on_mount(self) -> None:
        from cvals.tui.presentation import record_text

        self.query_one("#plan-log", RichLog).write(
            Syntax(record_text(self.plan), "json", theme="ansi_dark", word_wrap=True)
        )
        self.query_one("#plan-log").focus()

    @on(Button.Pressed, "#plan-cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#plan-prepare")
    def prepare(self) -> None:
        self.dismiss(True)
