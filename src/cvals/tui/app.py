"""Keyboard-first workbench over the shared durable evaluation service."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import partial
from time import monotonic
from typing import Any

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, LoadingIndicator, RichLog, Select, Static

from cvals.config import Settings
from cvals.tui.dialogs import EvidenceDialog, FieldSpec, FormDialog, PlanDialog
from cvals.tui.evolution import EvolutionMixin
from cvals.tui.growth import GrowthMixin
from cvals.tui.presentation import (
    human_text,
    money,
    percent,
    plain,
    record_text,
    status,
    system_label,
)
from cvals.tui.rankings import RankingsMixin
from cvals.tui.training import TrainingMixin

PAGES = (
    ("overview", "Overview", "F1"),
    ("rankings", "Rankings", "Ctrl+L"),
    ("training", "Training", "Ctrl+T"),
    ("projects", "Projects", "F2"),
    ("releases", "Releases", "F3"),
    ("experiments", "Experiments", "F4"),
    ("systems", "Systems", "F5"),
    ("schedules", "Schedules", "F6"),
    ("audit", "Audit trail", "F7"),
    ("diagnostics", "Workspace", "F8"),
    ("guides", "Guided runs", "F9"),
    ("changes", "Change runs", "F10"),
    ("autopilot", "Autopilot", "F11"),
    ("lab", "Research lab", "F12"),
)
TITLES = {key: title for key, title, _ in PAGES} | {
    "candidates": "Candidate ledger",
    "trials": "Trial evidence",
    "collections": "Collections",
    "journey": "First real evaluation",
    "health": "Benchmark health",
    "artifacts": "Dependency artifacts",
    "packs": "Community packs",
}
DESCRIPTIONS = {
    "training": "Checkpoint trajectories, coverage and evidence for post-training decisions.",
    "rankings": "Rank within each frozen benchmark. Exact model configurations stay separate.",
    "packs": "Inspect provenance, review expected behavior and retain validation before admission.",
    "autopilot": "Continuous screening, bounded escalation and retained change evidence.",
    "lab": "Investigate outcomes, challenge graders and validate configuration proposals.",
    "artifacts": "Reviewed local wheels and npm archives; package code is never run during import.",
    "guides": "Resume the path from a real repository to a frozen evaluation.",
    "journey": "Every completed step points to saved evidence. Enter opens the selected step.",
    "health": "Admissions, coverage, exclusions and observed control consistency.",
    "changes": "Exact source and configuration fingerprints linked to local evaluations.",
    "overview": "Build a benchmark from work that actually happened.",
    "projects": "Connect history. Preserve why each candidate was admitted or rejected.",
    "candidates": "A discovered change is a draft until its reference and controls are verified.",
    "releases": "Frozen benchmark collections, with provenance and validation evidence.",
    "collections": "Code, documents, data, and workflows share one evaluation contract.",
    "experiments": "Compare systems on the same frozen tasks. Inspect every attempt.",
    "trials": "Outputs, checks, costs, and human judgments remain tied to this attempt.",
    "systems": "Endpoint configuration stores environment references, never API secrets.",
    "schedules": "Frozen runs share a cumulative cap; uncertain billing stays reserved.",
    "audit": "A local record of workbench actions and their persisted outcomes.",
    "diagnostics": "Storage, execution, and provider readiness for this local workspace.",
}
TOOLS = {
    "training_details": "Checkpoint details ↵",
    "training_protocol": "Frozen protocol",
    "rankings": "Model rankings",
    "ranking_quality": "1 Quality",
    "ranking_cost": "2 Cost / pass",
    "ranking_latency": "3 P95 latency",
    "ranking_details": "Details ↵",
    "ranking_run": "Open run · O",
    "packs": "Staged packs",
    "pack_stage": "Stage pack",
    "pack_review": "Review pack",
    "pack_record": "Pack evidence",
    "variant": "Save variant",
    "budget": "Budget ledger",
    "forecast": "Forecast budget",
    "autopilot_new": "New policy",
    "autopilot_toggle": "Pause / enable",
    "autopilot_history": "Run history",
    "investigate": "Investigate",
    "reduce": "Reduce output",
    "quality": "Check quality",
    "optimize": "New study",
    "lab_record": "Research evidence",
    "catalog": "Community packs",
    "artifacts": "Dependency artifacts",
    "artifact_import": "Import artifact",
    "guide": "Start guide",
    "resume": "Resume guide",
    "continue": "Open step",
    "health": "Benchmark health",
    "digest": "Weekly digest",
    "schedule_budget": "Lifetime cap",
    "change_link": "Link changes",
    "change_poll": "Poll and evaluate",
    "change_toggle": "Pause / enable",
    "change_history": "Change evidence",
    "triage": "Triage",
    "economics": "Cost / latency",
    "brief": "Decision brief",
    "diagnose": "Diagnose attempt",
    "regrade": "Regrade saved output",
    "connect": "Connect repository",
    "discover": "Discover",
    "older": "Load older work",
    "prepare": "Prepare runtime",
    "candidates": "Candidates",
    "validate": "Validate",
    "release": "Create release",
    "run": "New experiment",
    "trials": "Trial evidence",
    "cancel": "Cancel run",
    "export": "Export",
    "diff": "Compare runs",
    "gates": "Check gates",
    "review": "Add review",
    "reviews": "Review history",
    "schedule": "Schedule",
    "toggle": "Pause / enable",
    "profile": "Environment",
    "review_candidate": "Review task",
    "system": "Add system",
    "record": "Full record",
    "back": "Back",
}
PAGE_TOOLS: dict[str, list[str]] = {
    "training": ["training_details", "training_protocol"],
    "rankings": [
        "ranking_quality",
        "ranking_cost",
        "ranking_latency",
        "ranking_details",
        "ranking_run",
    ],
    "packs": ["pack_stage", "pack_review", "pack_record", "catalog"],
    "autopilot": ["autopilot_new", "autopilot_toggle", "autopilot_history", "budget", "record"],
    "lab": ["optimize", "quality", "catalog", "packs", "lab_record", "budget"],
    "overview": ["rankings", "guide", "connect", "run", "budget"],
    "guides": ["guide", "resume", "record"],
    "journey": ["continue", "health", "record"],
    "health": ["back", "record"],
    "changes": ["change_poll", "change_toggle", "change_history", "record"],
    "projects": [
        "connect",
        "discover",
        "candidates",
        "prepare",
        "profile",
        "health",
        "release",
        "record",
    ],
    "candidates": [
        "back",
        "discover",
        "older",
        "prepare",
        "review_candidate",
        "profile",
        "validate",
        "release",
        "record",
    ],
    "releases": ["run", "record"],
    "collections": ["run", "record"],
    "experiments": [
        "rankings",
        "run",
        "forecast",
        "trials",
        "triage",
        "economics",
        "brief",
        "cancel",
        "export",
        "diff",
        "gates",
        "schedule",
        "change_link",
        "record",
    ],
    "trials": [
        "back",
        "diagnose",
        "investigate",
        "reduce",
        "regrade",
        "review",
        "reviews",
        "export",
        "record",
    ],
    "systems": ["system", "variant", "record"],
    "schedules": ["toggle", "schedule_budget", "digest", "record"],
    "audit": ["record"],
    "diagnostics": ["artifacts", "artifact_import", "record"],
    "artifacts": ["artifact_import", "record"],
}


class WorkbenchApp(TrainingMixin, RankingsMixin, EvolutionMixin, GrowthMixin, App[None]):
    """A terminal client; no evaluation or storage rules are duplicated here."""

    TITLE = "Everval AI"
    SUB_TITLE = "Local evaluation workbench"
    CSS_PATH = "workbench.tcss"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+n", "connect", "Connect", priority=True),
        Binding("ctrl+j", "chat", "Chat", priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
        Binding("1", "ranking_metric('quality')", "Quality", show=False),
        Binding("2", "ranking_metric('cost')", "Cost / pass", show=False),
        Binding("3", "ranking_metric('latency')", "P95 latency", show=False),
        Binding("o", "ranking_run", "Open run", show=False),
        *[
            Binding(key.lower(), f"navigate('{name}')", label, show=False, priority=True)
            for name, label, key in PAGES
        ],
    ]

    def __init__(
        self, settings: Settings, *, workbench: Any = None, source: str | None = None
    ) -> None:
        super().__init__()
        if workbench is None:
            from cvals.workbench import Workbench

            workbench = Workbench(settings)
        self.settings = settings
        self.service = workbench
        self.initial_source = source
        self.page = "overview"
        self.guide_id: str | None = None
        self.project_id: str | None = None
        self._project_cursors: dict[str, str | None] = {}
        self.experiment_id: str | None = None
        self.rows: list[dict[str, Any]] = []
        self.selected: dict[str, Any] | None = None
        self.payload: Any = None
        self._generation = 0
        self._loading = False
        self._focus_after_load = True
        self._mutating = False
        self._pending: dict[int, str] = {}
        self._pending_started: dict[int, float] = {}
        self._request_id = 0
        self._refresh_timers: list[Timer] = []
        self._render_revision = 0
        self._detail_context: tuple[int, int, str] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("E / V", id="monogram")
            yield Static("EVERVAL AI", id="brand")
            yield Static("LOCAL WORKSPACE  •  NO TELEMETRY", id="local-badge")
        with Horizontal(id="body"):
            with Vertical(id="rail"):
                yield Static("WORKBENCH", classes="rail-label")
                yield Button("Ctrl+J  Benchmark chat", id="open-chat", classes="nav-button")
                for key, title, hotkey in PAGES:
                    yield Button(f"{hotkey}  {title}", id="nav-" + key, classes="nav-button")
                yield Static(
                    "HISTORY → EVIDENCE\n\nGitHub & local Git\nPrivate by default",
                    id="rail-note",
                    markup=False,
                )
                yield Button("+ Connect", id="rail-connect", variant="primary")
            with Vertical(id="workspace"):
                yield Static("Overview", id="page-title", markup=False)
                yield Static(DESCRIPTIONS["overview"], id="page-description", markup=False)
                yield Static("", id="notice", markup=False)
                yield Static("", id="metrics", markup=False)
                yield Static("", id="intro", markup=False)
                yield Static("", id="ranking-summary", markup=False)
                yield Static(
                    "← ↑ ↓ → select · Enter details · O open run · 1/2/3 metrics · Ctrl+R refresh",
                    id="ranking-help",
                    markup=False,
                )
                yield Select([], prompt="Select a training run", id="training-run")
                yield Static("", id="training-summary", markup=False)
                yield Static(
                    "↑ ↓ checkpoint · Enter evidence · Shift+Tab runs · Ctrl+R refresh",
                    id="training-help",
                    markup=False,
                )
                with Horizontal(id="panes"):
                    with Vertical(id="ledger-pane"):
                        yield Static("", id="empty", markup=False)
                        yield DataTable(id="ledger", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="detail-pane"):
                        yield Static("INSPECT", id="detail-heading", markup=False)
                        yield RichLog(
                            id="detail",
                            wrap=True,
                            min_width=1,
                            markup=False,
                            highlight=False,
                            auto_scroll=False,
                        )
                with Horizontal(id="actions"):
                    for key, label in TOOLS.items():
                        yield Button(
                            label,
                            id="tool-" + key,
                            variant="primary"
                            if key in {"connect", "validate", "run"}
                            else "default",
                        )
        with Horizontal(id="statusbar"):
            yield LoadingIndicator(id="busy")
            yield Static("Ready", id="activity", markup=False)
            yield Static("Ctrl+P  commands", id="palette-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._resize(self.size.width, self.size.height)
        self.show_page("overview")
        self._refresh_timers = [
            self.set_interval(2, self._poll),
            self.set_interval(1, self._status),
        ]
        if self.initial_source:
            self.call_after_refresh(self.action_connect)

    def on_unmount(self) -> None:
        for timer in self._refresh_timers:
            timer.stop()
        self._refresh_timers.clear()
        self._pending.clear()
        self._pending_started.clear()
        self._mutating = False
        self._loading = False

    def on_resize(self, event: Resize) -> None:
        self._resize(event.size.width, event.size.height)

    def _resize(self, width: int, height: int) -> None:
        self.set_class(width < 110, "compact")
        self.set_class(height < 32, "short")

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Benchmark chat", "Describe models, tasks, schedule and results.", self.action_chat
        )
        for page, title, _ in PAGES:
            yield SystemCommand(title, DESCRIPTIONS[page], partial(self.show_page, page))
        yield SystemCommand(
            "Collections", DESCRIPTIONS["collections"], partial(self.show_page, "collections")
        )
        for key, label in TOOLS.items():
            yield SystemCommand(
                label,
                "Work on the selected record" if key not in {"connect", "run", "system"} else label,
                partial(self.command, key),
            )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "chat":
            return len(self.screen_stack) == 1
        if action in {"ranking_metric", "ranking_run"}:
            return self.page == "rankings" and len(self.screen_stack) == 1
        return super().check_action(action, parameters)

    def _status(self) -> None:
        # Textual stops the app before unmounting its screens; a queued timer
        # can still arrive during that teardown. Active DOM errors stay visible.
        if not self.is_running:
            return
        pending = [
            f"{label} · {int(monotonic() - self._pending_started[identity])}s"
            for identity, label in self._pending.items()
        ]
        self.query_one("#busy").display = bool(pending)
        self.query_one("#activity", Static).update(
            " · ".join(pending) if pending else "Ready · Changes are stored in the local workspace"
        )

    def request(
        self,
        label: str,
        fn: Callable[[], Any],
        done: Callable[[Any], None],
        *,
        mutation: bool = False,
    ) -> None:
        if mutation and self._mutating:
            self.notify(
                "Another change is still in progress. You can continue browsing.",
                severity="warning",
            )
            return
        self._request_id += 1
        request_id = self._request_id
        self._pending[request_id] = label
        self._pending_started[request_id] = monotonic()
        if mutation:
            self._mutating = True
        self._status()

        def execute() -> None:
            result: Any = None
            error_message: str | None = None
            try:
                result = fn()
            except Exception as error:
                error_message = plain(str(error)) or type(error).__name__
            if self.is_running:
                try:
                    self.call_from_thread(finish, result, error_message)
                except RuntimeError:
                    # The app may stop between the check and dispatch. Backend
                    # operations finish independently of the terminal client.
                    if self.is_running:
                        raise

        def finish(result: Any, error: str | None) -> None:
            self._pending.pop(request_id, None)
            self._pending_started.pop(request_id, None)
            if mutation:
                self._mutating = False
            if not self.is_running:
                return
            self._status()
            if error:
                self._loading = False
                self.query_one("#notice", Static).update(f"{label}: {error}")
                self.query_one("#notice").display = True
                self.notify(error, title=label, severity="error", timeout=8)
            else:
                done(result)

        self.run_worker(execute, thread=True, exit_on_error=False, name=label)

    def show_page(self, page: str) -> None:
        self.page = page
        self.selected = None
        self._focus_after_load = True
        self._generation += 1
        self.query_one("#page-title", Static).update(TITLES[page])
        self.query_one("#page-description", Static).update(DESCRIPTIONS[page])
        self.query_one("#notice").display = False
        self.query_one("#intro").display = page == "overview"
        self.query_one("#metrics").display = page == "overview"
        self.set_class(page == "rankings", "rankings-page")
        self.set_class(page == "training", "training-page")
        for identity in ("training-run", "training-summary", "training-help"):
            self.query_one("#" + identity).display = page == "training"
        self.query_one("#ranking-summary").display = page == "rankings"
        self.query_one("#ranking-help").display = page == "rankings"
        table = self.query_one("#ledger", DataTable)
        table.cursor_type = "cell" if page == "rankings" else "row"
        table.fixed_columns = 1 if page == "rankings" else 0
        table.header_height = 2 if page == "rankings" else 1
        for name, _, _ in PAGES:
            self.query_one("#nav-" + name).set_class(
                page == name
                or (page in {"candidates", "health"} and name == "projects")
                or (page == "journey" and name == "guides")
                or (page == "trials" and name == "experiments"),
                "active",
            )
        for key in TOOLS:
            self.query_one("#tool-" + key).display = key in PAGE_TOOLS[page]
        self.query_one("#detail", RichLog).clear()
        self.query_one("#ledger", DataTable).clear(columns=True)
        self.rows = []
        self.reload()

    def action_navigate(self, page: str) -> None:
        if len(self.screen_stack) == 1:
            self.show_page(page)

    def action_refresh(self) -> None:
        if len(self.screen_stack) == 1:
            self.reload()

    def _poll(self) -> None:
        if (
            self.is_running
            and self.page in {"experiments", "trials", "journey", "training"}
            and len(self.screen_stack) == 1
            and not self._loading
            and (
                any(row.get("status") in {"queued", "running", "cancelling"} for row in self.rows)
                or self.page == "training"
                or self.page == "journey"
                and isinstance(self.payload, dict)
                and self.payload.get("experiment_status") in {"queued", "running"}
            )
        ):
            self.reload()

    def reload(self) -> None:
        generation = self._generation
        page = self.page
        project_id, experiment_id = self.project_id, self.experiment_id
        self._loading = True

        def fetch() -> Any:
            if page == "training":
                return self.training_fetch()
            if page in {"autopilot", "lab", "packs"}:
                return self.evolution_fetch(page)
            if page in {"guides", "journey", "health", "changes", "artifacts"}:
                return self.growth_fetch(page)
            if page == "candidates":
                project: dict[str, Any] = next(
                    (p for p in self.service.projects() if p["id"] == project_id), {}
                )
                return {"items": self.service.candidates(project_id), "project": project}
            if page == "trials":
                return self.service.experiment(experiment_id)
            return getattr(self.service, page)()

        def loaded(result: Any) -> None:
            if generation != self._generation:
                return
            self._loading = False
            self.payload = result
            self.render_page(result)

        self.request("Loading " + TITLES[page].lower(), fetch, loaded)

    def render_page(self, result: Any) -> None:
        self._render_revision += 1
        selected_id = self.selected.get("id") if self.selected else None
        page = self.page
        if page == "training":
            self.render_training(result)
            return
        if page == "rankings":
            self.render_rankings(result)
            return
        if page == "candidates" and isinstance(result, dict) and self.project_id:
            self._project_cursors[self.project_id] = (
                result.get("project", {}).get("last_discovery", {}).get("next_cursor")
            )
        columns: list[str]
        table_rows: list[tuple[Any, ...]]
        if page == "overview":
            summary = result if isinstance(result, dict) else {}
            counts = summary.get("counts", summary)
            self.query_one("#metrics", Static).update(
                "   ".join(
                    f"{plain(counts.get(key, '—'))} {label}"
                    for key, label in [
                        ("projects", "PROJECTS"),
                        ("candidates", "CANDIDATES"),
                        ("releases", "RELEASES"),
                        ("experiments", "EXPERIMENTS"),
                    ]
                )
            )
            self.query_one("#intro", Static).update(
                ("FROM HISTORY TO A BENCHMARK\n\n" if self.size.height >= 32 else "")
                + "01  Connect a GitHub URL or a local repository.\n"
                + "02  Discover candidates. Review an independent issue statement.\n"
                + "03  Prepare runtime. Validate repair and regression controls.\n"
                + "04  Freeze a release. Compare systems. Read the evidence."
            )
            recent_projects = summary.get("recent_projects", summary.get("projects", []))
            self.rows = recent_projects if isinstance(recent_projects, list) else []
            columns = ["Project", "State", "Source"]
            table_rows = [
                (plain(r.get("name")), status(r.get("status", "connected")), plain(r.get("source")))
                for r in self.rows
            ]
        elif page == "journey":
            self.rows = result["steps"]
            self.project_id = result.get("project_id")
            columns = ["Step", "Progress", "Evidence"]
            table_rows = [
                (plain(row["title"]), status(row["status"]), plain(row.get("evidence")))
                for row in self.rows
            ]
            if self._focus_after_load:
                selected_id = result.get("next_step")
        elif page == "health":
            self.rows = [
                {"id": key, "name": key.replace("_", " "), "value": value}
                for key, value in result.items()
                if key not in {"project_id", "generated_at"}
            ]
            columns = ["Measure", "Evidence"]
            table_rows = [
                (
                    plain(row["name"]),
                    str(len(row["value"])) + " records"
                    if isinstance(row["value"], (dict, list))
                    else plain(row["value"]),
                )
                for row in self.rows
            ]
        elif page == "trials":
            self.rows = result.get("trials", [])
            if self.ranking_trial_system_id:
                selected_id = next(
                    (
                        row["id"]
                        for row in self.rows
                        if row.get("system_id") == self.ranking_trial_system_id
                    ),
                    selected_id,
                )
                self.ranking_trial_system_id = None
            columns = ["Task", "System", "Attempt", "State", "Pass", "Cost"]
            table_rows = [
                (
                    plain(r.get("scenario_name", r.get("scenario_id"))),
                    plain(r.get("system_name", r.get("system_id"))),
                    plain(r.get("repetition", 1)),
                    status(r.get("status")),
                    plain(r.get("passed")),
                    money(r.get("cost_usd")),
                )
                for r in self.rows
            ]
        elif page == "diagnostics":
            self.rows = (
                result
                if isinstance(result, list)
                else [
                    {"id": key, "name": key.replace("_", " "), "value": value}
                    for key, value in result.items()
                ]
            )
            columns = ["Check", "Observation"]
            table_rows = [
                (plain(r.get("name", r.get("id"))), plain(r.get("value", r.get("status"))))
                for r in self.rows
            ]
        else:
            self.rows = (
                result if isinstance(result, list) else result.get("items", result.get(page, []))
            )
            if page == "autopilot":
                columns = ["Policy", "State", "Interval", "Lifetime cap", "Last error"]
                table_rows = [
                    (
                        plain(r.get("name")),
                        status("enabled" if r.get("enabled") else "paused"),
                        f"{r.get('interval_minutes', '—')} min",
                        money(r.get("lifetime_budget_usd")),
                        plain(r.get("last_error")),
                    )
                    for r in self.rows
                ]
            elif page == "packs":
                columns = ["Pack", "Version", "State", "License"]
                table_rows = [
                    (
                        plain(r.get("name")),
                        plain(r.get("version")),
                        status(r.get("status")),
                        plain(r.get("license")),
                    )
                    for r in self.rows
                ]
            elif page == "lab":
                columns = ["Research", "Kind", "State", "Created"]
                table_rows = [
                    (
                        plain(r.get("name", r.get("kind", r.get("id")))),
                        plain(r.get("record_kind")),
                        status(r.get("status", "retained")),
                        plain(r.get("created_at")),
                    )
                    for r in self.rows
                ]
            elif page == "artifacts":
                columns = ["Package", "Version", "Kind", "Registry", "Bytes"]
                table_rows = [
                    (
                        plain(row["name"]),
                        plain(row["version"]),
                        plain(row["kind"]),
                        plain(row["registry"]),
                        plain(row["bytes"]),
                    )
                    for row in self.rows
                ]
            elif page == "guides":
                columns = ["Guide", "Project", "Experiment", "Updated"]
                table_rows = [
                    (
                        plain(row["name"]),
                        plain(row.get("project_id")),
                        plain(row.get("experiment_id")),
                        plain(row["updated_at"]),
                    )
                    for row in self.rows
                ]
            elif page == "changes":
                columns = ["Baseline", "State", "Version", "Source", "Configuration"]
                table_rows = [
                    (
                        plain(row["name"]),
                        status("enabled" if row["enabled"] else "paused"),
                        plain(row["version"]),
                        plain(row["source_commit"][:12]),
                        plain(row["config_hash"][:12]),
                    )
                    for row in self.rows
                ]
            elif page == "projects":
                columns = ["Project", "State", "Candidates", "Source"]
                table_rows = [
                    (
                        plain(r.get("name")),
                        status(r.get("status", "connected")),
                        plain(r.get("candidate_count", r.get("counts", {}).get("candidates"))),
                        plain(r.get("source")),
                    )
                    for r in self.rows
                ]
            elif page == "candidates":
                columns = ["Change", "State", "Validation", "Reason"]
                table_rows = [
                    (
                        plain(r.get("title", r.get("summary", r.get("commit", r.get("id"))))),
                        status(r.get("status", r.get("state"))),
                        status(
                            r.get(
                                "validation_status",
                                (r.get("validation") or {}).get("status", "not run"),
                            )
                        ),
                        plain(
                            r.get(
                                "reason", r.get("rejection_reason", "; ".join(r.get("reasons", [])))
                            )
                        ),
                    )
                    for r in self.rows
                ]
            elif page in {"releases", "collections"}:
                columns = ["Name", "Tasks", "Created", "Identity"]
                table_rows = [
                    (
                        plain(r.get("name")),
                        plain(
                            r.get(
                                "scenario_count",
                                r.get("task_count", len(r.get("members", r.get("scenarios", [])))),
                            )
                        ),
                        plain(r.get("created_at")),
                        plain(r.get("id")),
                    )
                    for r in self.rows
                ]
            elif page == "experiments":
                columns = ["Experiment", "State", "Finalized", "Budget", "Created"]
                table_rows = [
                    (
                        plain(r.get("name")),
                        status(r.get("status")),
                        f"{r.get('completed_trials', 0)}/{r.get('total_trials', '—')}",
                        money(r.get("budget_usd")),
                        plain(r.get("created_at")),
                    )
                    for r in self.rows
                ]
            elif page == "systems":
                columns = ["System", "Adapter", "Model", "Endpoint"]
                table_rows = [
                    (
                        system_label(r),
                        plain(r.get("kind")),
                        plain(r.get("model")),
                        plain(r.get("base_url")),
                    )
                    for r in self.rows
                ]
            elif page == "schedules":
                columns = [
                    "Schedule",
                    "State",
                    "Lifetime cap",
                    "Interval",
                    "Next run",
                    "Last error",
                ]
                table_rows = [
                    (
                        plain(r.get("name")),
                        status("enabled" if r.get("enabled") else "paused"),
                        money(r.get("lifetime_budget_usd")),
                        f"{r.get('interval_minutes', '—')} min",
                        plain(r.get("next_run_at")),
                        plain(r.get("last_error")),
                    )
                    for r in self.rows
                ]
            else:
                columns = ["When", "Action", "Subject", "Outcome"]
                table_rows = [
                    (
                        plain(r.get("created_at", r.get("timestamp"))),
                        plain(r.get("action", r.get("event"))),
                        plain(r.get("subject_id", r.get("entity_id"))),
                        plain(r.get("status", r.get("outcome"))),
                    )
                    for r in self.rows
                ]
        table = self.query_one("#ledger", DataTable)
        table.clear(columns=True)
        table.add_columns(*columns)
        for index, values in enumerate(table_rows):
            table.add_row(
                *(value if isinstance(value, Text) else Text(str(value)) for value in values),
                key=str(index),
            )
        table.display = bool(self.rows)
        self.query_one("#empty").display = not self.rows
        self.query_one("#empty", Static).update(self.empty_message())
        if self.rows:
            index = next((i for i, row in enumerate(self.rows) if row.get("id") == selected_id), 0)
            table.move_cursor(row=index)
            self.select_row(index)
        else:
            self.selected = None
            self.query_one("#detail", RichLog).clear()
            self.query_one("#detail", RichLog).write(
                Text(
                    "Your workspace has no records here yet.\n\nUse the actions below or Ctrl+P to continue.",
                    style="#a9b8c7",
                )
            )
        self._update_actions()
        if self._focus_after_load and len(self.screen_stack) == 1:
            self._focus_after_load = False
            if self.rows:
                table.focus()
            else:
                for key in PAGE_TOOLS[self.page]:
                    button = self.query_one("#tool-" + key, Button)
                    if not button.disabled:
                        button.focus()
                        break

    def empty_message(self) -> str:
        return {
            "autopilot": "Create a paused policy from full and screening development runs. Enable it after reviewing its budget.",
            "lab": "Investigate a trial, check a collection, or start a controlled optimization study.",
            "overview": "Connect your first repository to start reconstructing real tasks.\nCtrl+N opens the connection form.",
            "projects": "No repositories connected.\nConnect a GitHub URL or an existing local repository.",
            "candidates": "No candidates discovered for this project.\nDiscover reads history and records both acceptance and rejection reasons.",
            "releases": "No benchmark releases.\nValidate candidates in Projects, then create a frozen release.",
            "experiments": "No experiments yet.\nRelease a validated benchmark or select an existing collection.",
            "systems": "No systems configured.\nAdd a compatible endpoint using an environment-variable key reference.",
            "schedules": "No recurring runs.\nSelect an experiment, then choose Schedule.",
            "guides": "Start a guided run to build your first real evaluation. Progress resumes after closing the terminal.",
            "changes": "Select a completed experiment, then Link changes to create a reviewed baseline.",
            "audit": "No workbench actions recorded yet.",
        }.get(self.page, "No records available.")

    def _update_actions(self) -> None:
        for key in {
            "variant",
            "pack_review",
            "pack_record",
            "autopilot_toggle",
            "autopilot_history",
            "lab_record",
            "investigate",
            "reduce",
            "forecast",
            "resume",
            "continue",
            "health",
            "digest",
            "schedule_budget",
            "change_link",
            "change_poll",
            "change_toggle",
            "change_history",
            "triage",
            "economics",
            "brief",
            "diagnose",
            "regrade",
            "discover",
            "candidates",
            "validate",
            "release",
            "trials",
            "cancel",
            "export",
            "diff",
            "gates",
            "review",
            "reviews",
            "schedule",
            "toggle",
            "record",
            "review_candidate",
            "profile",
            "older",
            "prepare",
        }:
            enabled = bool(self.selected)
            if key in {"discover", "release", "profile", "prepare"} and self.page == "candidates":
                enabled = bool(self.project_id)
            if key in {"export", "reviews"} and self.page == "trials":
                enabled = bool(self.experiment_id)
            if key == "health" and self.page == "journey":
                enabled = bool(self.project_id)
            if key == "regrade" and self.selected:
                enabled = self.selected.get("status") not in {"queued", "running"}
            if key == "older":
                enabled = bool(self.project_id and self._project_cursors.get(self.project_id))
            if key == "cancel" and self.selected:
                enabled = self.selected.get("status") in {"queued", "running", "cancelling"}
            self.query_one("#tool-" + key, Button).disabled = not enabled

    @on(Select.Changed, "#training-run")
    def training_changed(self, event: Select.Changed) -> None:
        self.training_run_changed(event)

    @on(DataTable.CellHighlighted, "#ledger")
    def cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        self.ranking_highlighted(event)

    @on(DataTable.CellSelected, "#ledger")
    def cell_opened(self, event: DataTable.CellSelected) -> None:
        self.ranking_opened(event)

    @on(DataTable.RowHighlighted, "#ledger")
    def highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.page != "rankings" and event.row_key.value is not None:
            self.select_row(int(event.row_key.value))

    @on(DataTable.RowSelected, "#ledger")
    def opened(self, event: DataTable.RowSelected) -> None:
        if self.page == "training" and event.row_key.value is not None:
            self.select_training_checkpoint(int(event.row_key.value))
            self.training_command("training_details")
            return
        if self.page != "rankings" and event.row_key.value is not None:
            self.select_row(int(event.row_key.value))
            self.command(
                "resume"
                if self.page == "guides"
                else "continue"
                if self.page == "journey"
                else "candidates"
                if self.page in {"projects", "overview"}
                else "trials"
                if self.page == "experiments"
                else "record"
            )

    def select_row(self, index: int) -> None:
        if self.page == "training":
            self.select_training_checkpoint(index)
            return
        if index >= len(self.rows):
            return
        self.selected = self.rows[index]
        self._update_actions()
        self.render_detail(self.selected)
        if self.page == "experiments":
            identity = self.selected.get("id")
            generation = self._generation
            context = (generation, self._render_revision, str(identity))
            if self._detail_context == context:
                return
            self._detail_context = context

            def loaded(result: Any) -> None:
                if (
                    generation == self._generation
                    and self.selected
                    and self.selected.get("id") == identity
                ):
                    self.render_detail(result)

            self.request("Reading comparison", partial(self.service.experiment, identity), loaded)

    def render_detail(self, record: dict[str, Any]) -> None:
        log = self.query_one("#detail", RichLog)
        log.clear()
        title = plain(
            record.get("name", record.get("title", record.get("scenario_name", record.get("id")))),
            "Record",
        )
        self.query_one("#detail-heading", Static).update("INSPECT  /  " + title)
        if self.page in {"journey", "health", "guides", "changes", "artifacts"}:
            log.write(Text(human_text(record)))
            return
        report = record.get("report")
        if report:
            comparison = Table(box=None, padding=(0, 1), expand=True)
            comparison.add_column("System")
            comparison.add_column("Pass", justify="right")
            comparison.add_column("95% interval", justify="right")
            comparison.add_column("Cost", justify="right")
            for system in report.get("systems", []):
                comparison.add_row(
                    Text(system_label(system)),
                    percent(system.get("pass_rate")),
                    f"{percent(system.get('ci_low'))}–{percent(system.get('ci_high'))}",
                    money(system.get("cost_usd")),
                )
            log.write(comparison)
            log.write(
                Text(
                    "\nPass rates include all scheduled attempts. Missing cost remains unknown.",
                    style="#a9b8c7",
                )
            )
            for warning in report.get("warnings", []):
                log.write(Text("• " + plain(warning), style="#e6bd76"))
        else:
            if self.page == "trials":
                checks = record.get("checks", [])
                if checks:
                    check_table = Table(box=None, padding=(0, 1), expand=True)
                    check_table.add_column("Check")
                    check_table.add_column("Result")
                    for check in checks:
                        check_table.add_row(
                            Text(plain(check.get("name", check.get("id")))),
                            status("passed" if check.get("passed") else "failed"),
                        )
                    log.write(check_table)
                    for check in checks:
                        log.write(Text(plain(check.get("message")), style="#a9b8c7"))
                    log.write(Text(""))
            summary = Text()
            hidden = {
                "scenarios",
                "trials",
                "systems",
                "output",
                "checks",
                "reference",
                "collection",
                "experiment",
                "report",
                "scenario",
            }
            for key, value in record.items():
                if key in hidden:
                    continue
                summary.append(key.replace("_", " ").upper() + "\n", style="#83d7c7")
                summary.append(plain(value) + "\n\n", style="#dce5ea")
            if self.page == "trials":
                summary.append("OUTPUT & CHECKS\n", style="#83d7c7")
                summary.append(
                    "Open Full record for complete output, check evidence, usage, and errors.\n"
                )
            log.write(summary)
        if self.page == "candidates":
            log.write(
                Text(
                    "\nValidation runs the historical reference and controls in an isolated environment. Rejected candidates remain in this ledger.",
                    style="#a9b8c7",
                )
            )

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        identity = event.button.id or ""
        if identity == "open-chat":
            self.action_chat()
        elif identity.startswith("nav-"):
            self.show_page(identity[4:])
        elif identity.startswith("tool-"):
            self.command(identity[5:])
        elif identity == "rail-connect":
            self.action_connect()

    def action_chat(self) -> None:
        from cvals.tui.chat import ChatScreen

        self.push_screen(ChatScreen(self.settings))

    def command(self, name: str) -> None:
        if self.page == "training" and name.startswith("training_"):
            self.training_command(name)
            return
        if name == "rankings":
            self.show_page("rankings")
            return
        if self.page == "rankings" and name.startswith("ranking_"):
            self.ranking_command(name)
            return
        if (
            name
            not in {
                "connect",
                "run",
                "system",
                "guide",
                "artifacts",
                "artifact_import",
                "budget",
                "quality",
                "optimize",
                "catalog",
                "autopilot_new",
                "packs",
                "pack_stage",
            }
            and name not in PAGE_TOOLS[self.page]
        ):
            self.notify("Open the relevant page and select a record first.", severity="warning")
            return
        if name in {
            "artifacts",
            "artifact_import",
            "budget",
            "quality",
            "optimize",
            "catalog",
            "autopilot_new",
            "packs",
            "pack_stage",
        }:
            getattr(self, "command_" + name)()
        elif name == "guide":
            self.command_guide()
        elif name == "connect":
            self.action_connect()
        elif name == "run":
            self.new_run()
        elif name == "system":
            self.new_system()
        elif name == "back":
            self.show_page("projects" if self.page in {"candidates", "health"} else "experiments")
        elif (
            name in {"discover", "release", "profile", "older", "prepare"}
            and self.page == "candidates"
            and self.project_id
            or self.selected
        ):
            getattr(self, "command_" + name)()
        else:
            self.notify("Select a record first.", severity="warning")

    def form(
        self,
        title: str,
        description: str,
        fields: list[FieldSpec],
        submit: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        def submitted(values: dict[str, Any] | None) -> None:
            if values is not None:
                callback(values)

        self.push_screen(FormDialog(title, description, fields, submit), submitted)

    def action_connect(self) -> None:
        if len(self.screen_stack) > 1:
            return
        source = self.initial_source or ""
        self.initial_source = None
        self.form(
            "Connect a repository",
            "Start with a GitHub repository URL or an existing local Git checkout. Connection reads history; discovery and validation are separate steps.",
            [
                FieldSpec(
                    "source",
                    "GitHub URL or local path",
                    value=source,
                    placeholder="https://github.com/owner/repository",
                )
            ],
            "Connect repository",
            lambda values: self.request(
                "Connecting repository",
                partial(self.service.connect, values["source"]),
                self.connected,
                mutation=True,
            ),
        )

    def connected(self, project: dict[str, Any]) -> None:
        self.project_id = project["id"]
        self.notify(
            "Repository connected. Discover candidate work next.", title=plain(project.get("name"))
        )
        self.show_page("projects")

    def command_candidates(self) -> None:
        assert self.selected
        self.project_id = self.selected["id"]
        self.show_page("candidates")

    def command_discover(self) -> None:
        project_id = (
            self.project_id
            if self.page in {"candidates", "journey"}
            else (self.selected or {}).get("id")
        )
        if not isinstance(project_id, str):
            self.notify("Select a project first.", severity="warning")
            return
        self.form(
            "Discover historical work",
            "Read a bounded slice of repository history. Unsupported changes are retained with a rejection reason; discovery does not execute repository code.",
            [
                FieldSpec(
                    "limit", "Commit limit", value="20", kind="integer", minimum=1, maximum=100
                )
            ],
            "Discover",
            lambda values: self.request(
                "Discovering candidates",
                partial(self.service.discover, project_id, limit=values["limit"]),
                lambda result: self.discovered(project_id, result),
                mutation=True,
            ),
        )

    def discovered(self, project_id: str, result: Any) -> None:
        self.project_id = project_id
        self.notify("Discovery complete. Inspect the candidate ledger.")
        self.show_page("candidates")

    def command_older(self) -> None:
        if not self.project_id or not self._project_cursors.get(self.project_id):
            self.notify(
                "No next page is recorded. Discover the latest work first.", severity="warning"
            )
            return
        project_id = self.project_id
        self.request(
            "Discovering older work",
            partial(
                self.service.discover,
                project_id,
                limit=20,
                cursor=self._project_cursors[project_id],
            ),
            lambda result: self.discovered(project_id, result),
            mutation=True,
        )

    def command_prepare(self) -> None:
        from cvals.private_artifacts import PrivateArtifacts

        project_id = (
            self.project_id
            if self.page in {"candidates", "journey"}
            else (self.selected or {}).get("id")
        )

        def loaded(artifacts: list[dict[str, Any]]) -> None:
            fields = [
                FieldSpec(
                    "language",
                    "Repository language",
                    kind="choice",
                    value="python",
                    options=[
                        ("Python", "python"),
                        ("JavaScript", "javascript"),
                        ("TypeScript", "typescript"),
                    ],
                ),
                FieldSpec(
                    "runner",
                    "Node runner · ignored for Python",
                    kind="choice",
                    value="node-test",
                    options=[
                        ("Node built-in tests", "node-test"),
                        ("Jest", "jest"),
                        ("Vitest", "vitest"),
                    ],
                ),
                FieldSpec(
                    "mode",
                    "Dependency source",
                    kind="choice",
                    value="detected",
                    options=[
                        ("Read committed declarations or npm lockfile", "detected"),
                        ("Explicit Python package list", "explicit"),
                    ],
                ),
                FieldSpec(
                    "packages",
                    "Explicit Python packages · one per line",
                    kind="multiline",
                    required=False,
                ),
                FieldSpec(
                    "base_image",
                    "Already-local base image · optional",
                    required=False,
                    placeholder="Default: configured Python image or node:24-slim",
                ),
                FieldSpec(
                    "artifacts",
                    "Imported dependency artifacts · Space selects · optional",
                    kind="multi",
                    required=False,
                    options=[
                        (
                            f"{item['kind']} · {item['name']} {item['version']} · {item['registry']}",
                            item["id"],
                        )
                        for item in artifacts
                    ],
                ),
            ]

            def submit(values: dict[str, Any]) -> None:
                packages = (
                    [line.strip() for line in values["packages"].splitlines() if line.strip()]
                    if values["mode"] == "explicit"
                    else None
                )
                options: dict[str, Any] = {}
                if values["language"] != "python":
                    options.update(language=values["language"], runner=values["runner"])
                if values["artifacts"]:
                    options["artifacts"] = values["artifacts"]
                plan_options = dict(options)
                if values["base_image"]:
                    plan_options["base_image"] = values["base_image"]

                def planned(plan: dict[str, Any]) -> None:
                    def confirmed(accepted: bool | None) -> None:
                        if accepted:
                            self.request(
                                "Preparing isolated runtime",
                                partial(
                                    self.service.prepare_environment,
                                    project_id,
                                    packages=packages,
                                    revision=plan["source_commit"],
                                    base_image=plan["base_image_digest"],
                                    **options,
                                ),
                                lambda result: self.mutation_done("Execution environment prepared"),
                                mutation=True,
                            )

                    self.push_screen(PlanDialog(plan), confirmed)

                self.request(
                    "Planning execution environment",
                    partial(
                        self.service.environment_plan,
                        project_id,
                        packages=packages,
                        **plan_options,
                    ),
                    planned,
                )

            self.form(
                "Plan an execution runtime",
                "Review exact dependencies and image before preparation. Python uses wheels; Node uses a committed npm lockfile with scripts disabled. Imported wheels must supply the offline dependency closure. Import artifacts from Workspace first.",
                fields,
                "Review plan",
                submit,
            )

        self.request(
            "Reading imported dependency artifacts",
            lambda: PrivateArtifacts(self.settings).list(),
            loaded,
        )

    def command_review_candidate(self) -> None:
        assert self.selected
        candidate_id = self.selected["id"]
        self.form(
            "Review the task statement",
            "Write the problem using information available before the repair. Do not reveal the historical solution, hidden checks, or future outcomes. This review is required before release.",
            [
                FieldSpec(
                    "statement",
                    "Agent-visible problem statement",
                    kind="multiline",
                    value=plain(
                        (self.selected.get("review") or {}).get(
                            "statement", self.selected.get("issue_statement", "")
                        ),
                        "",
                    ),
                ),
                FieldSpec("reviewer", "Reviewer label"),
                FieldSpec(
                    "rationale", "Why the task is reconstructable and fair", kind="multiline"
                ),
            ],
            "Save task review",
            lambda values: self.request(
                "Saving task review",
                partial(self.service.review_candidate, self.project_id, candidate_id, **values),
                lambda result: self.mutation_done("Task review saved"),
                mutation=True,
            ),
        )

    def command_profile(self) -> None:
        project_id = (
            self.project_id
            if self.page in {"candidates", "journey"}
            else (self.selected or {}).get("id")
        )
        fields = [
            FieldSpec(
                "profile",
                "Language and test runner",
                kind="choice",
                value="python:python-pytest",
                options=[
                    ("Python · pytest", "python:python-pytest"),
                    *[
                        (language.title() + " · " + runner, language + ":" + runner)
                        for language in ("javascript", "typescript")
                        for runner in ("node-test", "jest", "vitest")
                    ],
                ],
            ),
            FieldSpec(
                "image",
                "Prepared test image · already installed",
                placeholder="local/repository-tests:reviewed",
            ),
            FieldSpec(
                "timeout_seconds",
                "Test deadline · seconds",
                kind="integer",
                value="60",
                minimum=1,
                maximum=120,
            ),
            FieldSpec(
                "plugins",
                "Explicit pytest plugin modules · Python only · comma separated",
                required=False,
            ),
        ]

        def submit(values: dict[str, Any]) -> None:
            language, runtime = values.pop("profile").split(":", 1)
            values["plugins"] = [
                item.strip() for item in values["plugins"].split(",") if item.strip()
            ]
            self.request(
                "Recording execution environment",
                partial(
                    self.service.add_project_profile,
                    project_id,
                    language=language,
                    runtime=runtime,
                    **values,
                ),
                lambda result: self.mutation_done("Prepared environment recorded"),
                mutation=True,
            )

        self.form(
            "Pin repository validation runtime",
            "Choose an already prepared image matching the language and runner. The core pins its immutable identity. Tests run with candidate code inside the sandbox; deliberate grader manipulation remains outside this integrity guarantee.",
            fields,
            "Save environment",
            submit,
        )

    def command_validate(self) -> None:
        assert self.selected
        candidate_id = self.selected["id"]
        self.request(
            "Validating candidate in sandbox",
            partial(self.service.validate, self.project_id, candidate_id),
            lambda result: self.mutation_done("Validation evidence saved"),
            mutation=True,
        )

    def command_release(self) -> None:
        project_id = (
            self.project_id
            if self.page in {"candidates", "journey"}
            else (self.selected or {}).get("id")
        )
        self.form(
            "Create benchmark release",
            "Freeze the project's reviewed, validated tasks. The core assigns stable development and holdout partitions. Select the intended partition explicitly when starting an experiment.",
            [FieldSpec("name", "Release name", placeholder="Repository benchmark · v1")],
            "Create release",
            lambda values: self.request(
                "Creating benchmark release",
                partial(self.service.release, project_id, values["name"]),
                lambda result: self.released(result),
                mutation=True,
            ),
        )

    def released(self, result: Any) -> None:
        self.notify("Benchmark release saved. Its task snapshot is frozen.")
        self.show_page("releases")

    def new_run(self) -> None:
        def loaded(result: Any) -> None:
            collections, systems = result
            systems = [s for s in systems if s.get("kind") != "replay"]
            if not collections or not systems:
                self.notify(
                    "A collection and at least one configured system are required.",
                    severity="warning",
                )
                return
            preselected = ""
            collection_label = "Task collection"
            if self.page == "releases" and self.selected:
                partition_ids = self.selected.get("collection_ids", {})
                collections = [c for c in collections if c["id"] in partition_ids.values()]
                collection_label = "Release partition · choose explicitly"
                if not collections:
                    self.notify(
                        "This release has no available runnable collection.", severity="warning"
                    )
                    return
            elif self.page == "collections" and self.selected:
                preselected = self.selected["id"]
            fields = [
                FieldSpec("name", "Experiment name", placeholder="Baseline comparison"),
                FieldSpec(
                    "collection_id",
                    collection_label,
                    kind="choice",
                    value=preselected,
                    options=[(plain(c.get("name")), c["id"]) for c in collections],
                ),
                FieldSpec(
                    "system_ids",
                    "Systems · Space selects",
                    kind="multi",
                    options=[(system_label(s), s["id"]) for s in systems],
                ),
                FieldSpec(
                    "repetitions",
                    "Repetitions per task",
                    kind="integer",
                    value="1",
                    minimum=1,
                    maximum=30,
                ),
                FieldSpec(
                    "budget", "Maximum provider spend · USD", kind="number", value="0", minimum=0
                ),
            ]
            self.form(
                "Design an experiment",
                "All systems receive the same frozen public tasks. Reserve holdout partitions for independent evaluation. Fixture controls verify the pipeline, not model performance. Remote providers require opt-in, pricing, and a positive budget.",
                fields,
                "Start experiment",
                lambda values: self.request(
                    "Starting experiment",
                    partial(self.service.run, **values),
                    self.started,
                    mutation=True,
                ),
            )

        self.request(
            "Reading collections and systems",
            lambda: (self.service.collections(), self.service.systems()),
            loaded,
        )

    def started(self, result: dict[str, Any]) -> None:
        self.experiment_id = result.get("id", result.get("experiment", {}).get("id"))
        self.notify("Experiment saved. Execution status updates as attempts finish.")
        self.show_page("experiments")

    def command_trials(self) -> None:
        self.ranking_trial_system_id = None
        assert self.selected
        self.experiment_id = self.selected["id"]
        self.show_page("trials")

    def command_cancel(self) -> None:
        assert self.selected
        identity = self.selected["id"]
        self.form(
            "Cancel experiment",
            "Completed evidence is retained. An in-flight provider call may finish before cancellation takes effect; recorded usage remains attached to the attempt.",
            [],
            "Request cancellation",
            lambda values: self.request(
                "Requesting cancellation",
                partial(self.service.cancel, identity),
                lambda result: self.mutation_done("Cancellation requested"),
            ),
        )

    def _experiment(self) -> str | None:
        return self.experiment_id if self.page == "trials" else (self.selected or {}).get("id")

    def command_export(self) -> None:
        identity = self._experiment()
        self.form(
            "Export experiment",
            "Write a local artifact containing task, result, and evidence records. Keep exports private when the benchmark includes confidential material.",
            [
                FieldSpec(
                    "format",
                    "Format",
                    kind="choice",
                    value="html",
                    options=[
                        ("Readable HTML report", "html"),
                        ("Complete JSON evidence", "json"),
                        ("Trial CSV", "csv"),
                    ],
                )
            ],
            "Export",
            lambda values: self.request(
                "Exporting evidence",
                partial(self.service.export, identity, values["format"]),
                lambda result: self.show_evidence("Export saved", result),
            ),
        )

    def command_diff(self) -> None:
        assert self.selected
        identity = self.selected["id"]
        others = [r for r in self.rows if r.get("id") != identity]
        if not others:
            self.notify("Create another experiment before comparing runs.", severity="warning")
            return
        self.form(
            "Compare experiments",
            "Cohort compatibility and uncertainty are evaluated by the shared core. A changed benchmark must remain distinguishable from a changed system.",
            [
                FieldSpec(
                    "right",
                    "Compare selected run with",
                    kind="choice",
                    options=[(plain(r.get("name")), r["id"]) for r in others],
                )
            ],
            "Compare",
            lambda values: self.request(
                "Comparing experiments",
                partial(self.service.diff, identity, values["right"]),
                lambda result: self.show_evidence("Experiment comparison", result),
            ),
        )

    def command_gates(self) -> None:
        identity = self._experiment()
        others = [r for r in self.rows if r.get("id") != identity]
        fields = [
            FieldSpec("name", "Policy name", value="Execution readiness"),
            FieldSpec("version", "Policy version", value="1"),
            FieldSpec(
                "min_completion_rate",
                "Minimum completion · 0 to 1",
                kind="number",
                value="1",
                minimum=0,
                maximum=1,
            ),
            FieldSpec(
                "max_error_rate",
                "Maximum error rate · 0 to 1",
                kind="number",
                value="0",
                minimum=0,
                maximum=1,
            ),
            FieldSpec(
                "min_pass_rate",
                "Minimum pass rate · optional",
                kind="number",
                required=False,
                minimum=0,
                maximum=1,
            ),
            FieldSpec(
                "min_pass_rate_lower_bound",
                "Minimum 95% pass-rate lower bound · optional",
                kind="number",
                required=False,
                minimum=0,
                maximum=1,
            ),
            FieldSpec(
                "max_cost_usd",
                "Maximum cost per system · USD · optional",
                kind="number",
                required=False,
                minimum=0,
            ),
            FieldSpec(
                "max_p95_duration_ms",
                "Maximum p95 duration · milliseconds · optional",
                kind="number",
                required=False,
                minimum=0,
            ),
            FieldSpec(
                "baseline_id",
                "Baseline experiment · optional",
                kind="choice",
                required=False,
                options=[(plain(r.get("name")), r["id"]) for r in others],
            ),
            FieldSpec(
                "max_pass_rate_regression",
                "Maximum tolerated pass-rate loss · optional",
                kind="number",
                required=False,
                minimum=0,
                maximum=1,
            ),
        ]

        def submit(values: dict[str, Any]) -> None:
            baseline = values.pop("baseline_id") or None
            policy = {key: value for key, value in values.items() if value != ""}
            quality = {
                "min_pass_rate",
                "min_pass_rate_lower_bound",
                "max_cost_usd",
                "max_p95_duration_ms",
                "max_pass_rate_regression",
            }
            if quality & policy.keys() and policy["name"] == "Execution readiness":
                policy["name"] = "Custom decision gates"
            self.request(
                "Evaluating and recording decision gates",
                partial(self.service.gates, identity, policy=policy, baseline_id=baseline),
                lambda result: self.show_evidence("Recorded decision gates", result),
                mutation=True,
            )

        self.form(
            "Declare decision gates",
            "Defaults check complete execution without infrastructure errors, not model quality. Add thresholds deliberately. Unknown metrics and inconclusive intervals cannot pass dependent gates. Each decision is retained locally.",
            fields,
            "Evaluate gates",
            submit,
        )

    def command_record(self) -> None:
        if self.page == "experiments" and self.selected:
            self.request(
                "Reading experiment evidence",
                partial(self.service.experiment, self.selected["id"]),
                lambda result: self.show_evidence("Complete experiment evidence", result),
            )
        else:
            self.show_evidence("Complete record · " + TITLES[self.page], self.selected)

    def show_evidence(self, title: str, result: Any) -> None:
        self.push_screen(EvidenceDialog(title, record_text(result), json=True))

    def command_review(self) -> None:
        assert self.selected
        trial_id = self.selected["id"]
        if self.selected.get("status") in {"queued", "running"}:
            self.notify("Only finalized attempts can receive a review.", severity="warning")
            return
        fields = [
            FieldSpec("reviewer", "Reviewer label", placeholder="Your name or team label"),
            FieldSpec("dimension", "Dimension", placeholder="Correctness, clarity, or usefulness"),
            FieldSpec("score", "Score · 0 to 1", kind="number", minimum=0, maximum=1),
            FieldSpec("rationale", "Rationale", kind="multiline"),
        ]
        self.form(
            "Add human judgment",
            "Reviewer labels are self-reported, not verified identities. Judgments are append-only and remain separate from automated scores.",
            fields,
            "Save review",
            lambda values: self.request(
                "Saving human judgment",
                partial(self.service.add_review, self.experiment_id, trial_id, **values),
                lambda result: self.mutation_done("Review saved"),
                mutation=True,
            ),
        )

    def command_reviews(self) -> None:
        self.request(
            "Reading review history",
            partial(self.service.reviews, self._experiment()),
            lambda result: self.show_evidence(
                "Human review history · self-reported identities", result
            ),
        )

    def command_schedule(self) -> None:
        assert self.selected
        identity = self.selected["id"]
        self.form(
            "Schedule a recurring evaluation",
            "Each occurrence repeats the frozen experiment. Its full per-run budget is reserved against your lifetime cap. Unknown billing holds that reservation and pauses future admission. Imported replays cannot be scheduled.",
            [
                FieldSpec("name", "Schedule name"),
                FieldSpec(
                    "lifetime_budget_usd",
                    "Cumulative schedule cap · USD",
                    kind="number",
                    minimum=0,
                    value=str(self.selected.get("budget_usd", 0)),
                ),
                FieldSpec(
                    "interval_minutes",
                    "Interval · minutes",
                    kind="integer",
                    value="1440",
                    minimum=5,
                    maximum=525600,
                ),
                FieldSpec(
                    "enabled",
                    "Initial state",
                    kind="choice",
                    value="paused",
                    options=[
                        ("Paused · enable after review", "paused"),
                        ("Enabled · permit recurring runs", "enabled"),
                    ],
                ),
            ],
            "Save schedule",
            lambda values: self.request(
                "Creating schedule",
                partial(
                    self.service.add_schedule,
                    identity,
                    values["name"],
                    values["interval_minutes"],
                    enabled=values["enabled"] == "enabled",
                    lifetime_budget_usd=values["lifetime_budget_usd"],
                ),
                lambda result: self.schedule_saved(),
                mutation=True,
            ),
        )

    def schedule_saved(self) -> None:
        self.notify("Schedule saved. Inspect its next run and state below.")
        self.show_page("schedules")

    def command_toggle(self) -> None:
        assert self.selected
        identity = self.selected["id"]
        enabled = not self.selected.get("enabled", False)
        self.form(
            "Enable schedule" if enabled else "Pause schedule",
            "Enabling permits recurring runs, each with the frozen experiment's provider budget. Pausing stops future admissions; an already queued occurrence remains visible in Experiments.",
            [],
            "Enable" if enabled else "Pause",
            lambda values: self.request(
                "Updating schedule",
                partial(self.service.toggle_schedule, identity, enabled),
                lambda result: self.mutation_done("Schedule updated"),
                mutation=True,
            ),
        )

    def new_system(self) -> None:
        fields = [
            FieldSpec("name", "System name"),
            FieldSpec("base_url", "Provider base URL", placeholder="http://127.0.0.1:8000/v1"),
            FieldSpec("model", "Model identifier"),
            FieldSpec(
                "harness",
                "Execution harness",
                kind="choice",
                value="single-turn",
                options=[
                    ("Single structured-output request", "single-turn"),
                    ("Bounded tool-using harness", "tools"),
                    ("External agent adapter", "external-agent"),
                ],
            ),
            FieldSpec(
                "adapter_revision",
                "Adapter revision · external agent only",
                value="1",
                required=False,
            ),
            FieldSpec(
                "max_steps",
                "Maximum harness steps",
                value="8",
                kind="integer",
                minimum=1,
                maximum=30,
            ),
            FieldSpec(
                "api_key_env",
                "API-key environment variable · optional",
                placeholder="PROVIDER_API_KEY",
                required=False,
            ),
            FieldSpec(
                "input_cost_per_million",
                "Input price · USD per million tokens",
                value="0",
                kind="number",
                minimum=0,
            ),
            FieldSpec(
                "output_cost_per_million",
                "Output price · USD per million tokens",
                value="0",
                kind="number",
                minimum=0,
            ),
            FieldSpec(
                "max_tokens",
                "Maximum output tokens",
                value="4096",
                kind="integer",
                minimum=1,
                maximum=32768,
            ),
        ]

        def submit(values: dict[str, Any]) -> None:
            payload = dict(values, kind="http")
            if values["harness"] != "external-agent":
                payload.pop("adapter_revision", None)
            self.request(
                "Saving system",
                partial(self.service.add_system, payload),
                lambda result: self.system_saved(),
                mutation=True,
            )

        self.form(
            "Connect a model system",
            "Choose a single structured-output request or the bounded tool harness. Tools remain inside the configured execution policy. Supply an environment variable name, never its secret value; hosted systems require opt-in and pricing.",
            fields,
            "Save system",
            submit,
        )

    def system_saved(self) -> None:
        self.notify("System configuration saved.")
        self.show_page("systems")

    def mutation_done(self, message: str) -> None:
        self.notify(message)
        if self.page == "journey":
            self._focus_after_load = True
        self.reload()


def run_tui(settings: Settings, source: str | None = None) -> None:
    """Launch the terminal application against a configured local workspace."""
    WorkbenchApp(settings, source=source).run()
