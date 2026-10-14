"""Guided first runs, benchmark health and recurring decision workflows."""

from __future__ import annotations

from functools import partial
from typing import Any

from cvals.benchmark_health import BenchmarkHealth
from cvals.change_runs import ChangeRuns
from cvals.onboarding import Onboarding
from cvals.schedules import ScheduleStore
from cvals.store import Store
from cvals.tui.dialogs import EvidenceDialog, FieldSpec
from cvals.tui.presentation import human_text, plain, system_label


class GrowthMixin:
    """UI actions remain thin clients of the shared durable services."""

    guide_id: str | None

    def growth_fetch(self: Any, page: str) -> Any:
        if page == "artifacts":
            from cvals.private_artifacts import PrivateArtifacts

            return PrivateArtifacts(self.settings).list()
        if page == "guides":
            return Onboarding(self.settings).list()
        if page == "journey":
            return Onboarding(self.settings).status(self.guide_id)
        if page == "health":
            return BenchmarkHealth(self.settings).project(self.project_id)
        if page == "changes":
            return ChangeRuns(self.settings).list()
        raise KeyError(page)

    def human_evidence(self: Any, title: str, result: Any) -> None:
        self.push_screen(EvidenceDialog(title, human_text(result)))

    def command_guide(self: Any) -> None:
        self.form(
            "Start a guided repository evaluation",
            "Progress is retained in this workspace. Every completed step is backed by a saved project, review, validation, release or experiment; recommendations never approve a task.",
            [
                FieldSpec("name", "Guide name", value="First repository evaluation"),
                FieldSpec(
                    "source", "GitHub source or local repository path · optional", required=False
                ),
            ],
            "Start guide",
            lambda values: self.request(
                "Creating guided run",
                lambda: Onboarding(self.settings).start(values["name"], values["source"] or None),
                lambda value: self.open_guide(value["id"]),
                mutation=True,
            ),
        )

    def command_resume(self: Any) -> None:
        if self.selected:
            self.open_guide(self.selected["id"])

    def open_guide(self: Any, identifier: str) -> None:
        self.guide_id = identifier
        self.show_page("journey")

    def guide_choice(self: Any, **choices: str) -> None:
        self.request(
            "Saving guide progress",
            lambda: Onboarding(self.settings).select(self.guide_id, **choices),
            lambda value: self.show_page("journey"),
            mutation=True,
        )

    def guide_action(self: Any, label: str, operation: Any, selection: str | None = None) -> None:
        guide_id = self.guide_id

        def execute() -> Any:
            result = operation()
            if selection:
                Onboarding(self.settings).select(guide_id, **{selection: result["id"]})
            return result

        self.request(label, execute, lambda value: self.show_page("journey"), mutation=True)

    def command_continue(self: Any) -> None:
        value = self.payload
        if self.page != "journey" or not isinstance(value, dict):
            return
        step = self.selected["id"] if self.selected else value.get("next_step")
        if step == "preflight":
            self.request(
                "Checking local readiness",
                self.service.diagnostics,
                lambda result: self.human_evidence("Local execution readiness", result),
            )
        elif step == "connect":
            self.form(
                "Connect the guide's repository",
                "Connection reads authorized repository history. Returning to this guide resumes all saved choices.",
                [
                    FieldSpec(
                        "source", "GitHub source or local path", value=value.get("source") or ""
                    )
                ],
                "Connect",
                lambda values: self.guide_action(
                    "Connecting repository",
                    partial(self.service.connect, values["source"]),
                    "project_id",
                ),
            )
        elif not value.get("project_id"):
            self.notify("Connect a repository first.", severity="warning")
        elif step == "discover":
            self.request(
                "Reading candidate recommendations",
                lambda: (
                    Onboarding(self.settings).recommendations(value["project_id"]),
                    self.service.candidates(value["project_id"]),
                ),
                lambda result: self.guide_candidates(value, result),
            )
        elif step == "review":
            if not value.get("candidate_id"):
                self.notify("Choose a candidate first.", severity="warning")
                return
            self.form(
                "Review the independent problem",
                "Describe observable requirements available before the fix. Do not copy solution details or hidden checks. Your reviewer label is self-reported.",
                [
                    FieldSpec("statement", "Agent-visible problem statement", kind="multiline"),
                    FieldSpec("reviewer", "Reviewer label"),
                    FieldSpec(
                        "rationale", "Why this is reconstructable and fair", kind="multiline"
                    ),
                ],
                "Save review",
                lambda values: self.guide_action(
                    "Saving independent review",
                    partial(
                        self.service.review_candidate,
                        value["project_id"],
                        value["candidate_id"],
                        **values,
                    ),
                ),
            )
        elif step == "prepare":
            self.project_id = value["project_id"]
            self.command_prepare()
        elif step == "validate":
            if not value.get("candidate_id"):
                self.notify("Choose and review a candidate first.", severity="warning")
                return
            self.guide_action(
                "Validating historical controls",
                partial(self.service.validate, value["project_id"], value["candidate_id"]),
            )
        elif step == "freeze":
            self.request(
                "Reading project releases",
                partial(self.service.releases, value["project_id"]),
                lambda releases: self.guide_release(value, releases),
            )
        elif step == "system":
            self.request(
                "Reading live systems",
                self.service.systems,
                lambda systems: self.guide_system(value, systems),
            )
        elif step == "run":
            if value.get("experiment_id"):
                self.experiment_id = value["experiment_id"]
                self.show_page("trials")
            elif not value.get("collection_id") or not value.get("system_id"):
                self.notify("Choose a release partition and live system first.", severity="warning")
            else:
                self.form(
                    "Run the frozen evaluation",
                    "This queues real system calls on the local worker. Hosted endpoints receive public task material. Budget admission reserves estimated maximum spend; missing provider billing remains unknown.",
                    [
                        FieldSpec("name", "Experiment name", value=value["name"]),
                        FieldSpec(
                            "budget",
                            "Maximum provider spend · USD",
                            kind="number",
                            value="0",
                            minimum=0,
                        ),
                        FieldSpec(
                            "repetitions",
                            "Repetitions",
                            kind="integer",
                            value="1",
                            minimum=1,
                            maximum=30,
                        ),
                    ],
                    "Queue evaluation",
                    lambda values: self.guide_action(
                        "Queuing evaluation",
                        partial(
                            self.service.run, value["collection_id"], [value["system_id"]], **values
                        ),
                        "experiment_id",
                    ),
                )

    def guide_candidates(self: Any, guide: dict[str, Any], result: Any) -> None:
        recommendations, candidates = result
        if not candidates:
            self.form(
                "Discover historical changes",
                "Read the latest bounded history window. Unsupported candidates remain in the rejection ledger.",
                [
                    FieldSpec(
                        "limit",
                        "Maximum changes",
                        value="20",
                        kind="integer",
                        minimum=1,
                        maximum=100,
                    )
                ],
                "Discover",
                lambda values: self.guide_action(
                    "Discovering candidates",
                    partial(self.service.discover, guide["project_id"], limit=values["limit"]),
                ),
            )
            return
        suggested = {item["candidate_id"]: item for item in recommendations}
        choices = sorted(
            [item for item in candidates if item["status"] == "discovered"],
            key=lambda item: -suggested.get(item["id"], {}).get("score", 0),
        )
        if not choices:
            self.human_evidence(
                "No currently supported candidates",
                {
                    "next_action": "Inspect the Projects candidate ledger; load older work or address reconstruction exclusions.",
                    "candidates": [
                        {"title": item.get("title"), "reasons": item.get("reasons")}
                        for item in candidates
                    ],
                },
            )
            return
        self.form(
            "Choose a candidate to review",
            "Sorted by available problem text, tests and bounded change size. These signals do not establish validity or representativeness; inspect the ledger for the original evidence.",
            [
                FieldSpec(
                    "candidate_id",
                    "Candidate",
                    kind="choice",
                    options=[
                        (
                            plain(item.get("title"))
                            + f" · suggestion score {suggested.get(item['id'], {}).get('score', 0)}",
                            item["id"],
                        )
                        for item in choices
                    ],
                )
            ],
            "Choose candidate",
            lambda values: self.guide_choice(**values),
        )

    def guide_release(self: Any, guide: dict[str, Any], releases: list[dict[str, Any]]) -> None:
        if not releases:
            self.form(
                "Freeze the first release",
                "Only currently reviewed and repeatedly validated candidates can enter the release.",
                [FieldSpec("name", "Release name", value="Initial validated benchmark")],
                "Freeze release",
                lambda values: self.guide_action(
                    "Freezing benchmark",
                    partial(self.service.release, guide["project_id"], values["name"]),
                    "release_id",
                ),
            )
            return
        self.form(
            "Choose a frozen release partition",
            "Development supports iteration. Preserve holdout for independent evaluation. Empty partitions cannot run.",
            [
                FieldSpec(
                    "selection",
                    "Release and partition",
                    kind="choice",
                    options=[
                        (
                            plain(release["name"]) + " · " + partition,
                            release["id"] + ":" + collection_id,
                        )
                        for release in releases
                        for partition, collection_id in release["collection_ids"].items()
                    ],
                )
            ],
            "Use partition",
            lambda values: self.guide_choice(
                release_id=values["selection"].split(":", 1)[0],
                collection_id=values["selection"].split(":", 1)[1],
            ),
        )

    def guide_system(self: Any, guide: dict[str, Any], systems: list[dict[str, Any]]) -> None:
        live = [system for system in systems if system["kind"] == "http"]
        if not live:
            self.new_system()
            return
        self.form(
            "Choose the system under evaluation",
            "Fixture controls and imported replays are excluded from this first real run. Configure another endpoint in Systems if needed.",
            [
                FieldSpec(
                    "system_id",
                    "Live system",
                    kind="choice",
                    options=[(system_label(system), system["id"]) for system in live],
                )
            ],
            "Use system",
            lambda values: self.guide_choice(**values),
        )

    def command_health(self: Any) -> None:
        project = (
            self.project_id
            if self.page in {"candidates", "journey"}
            else (self.selected or {}).get("id")
        )
        if project:
            self.project_id = project
            self.show_page("health")

    def command_digest(self: Any) -> None:
        if self.selected:
            identifier = self.selected["id"]
            self.request(
                "Freezing weekly decision digest",
                lambda: ScheduleStore(self.settings, Store(self.settings)).digest(identifier),
                lambda result: self.human_evidence("Frozen weekly decision digest", result),
                mutation=True,
            )

    def command_schedule_budget(self: Any) -> None:
        if not self.selected:
            return
        identifier = self.selected["id"]
        self.form(
            "Set cumulative schedule budget",
            "Every new occurrence reserves its full per-run budget. Settled unused reservations are released; uncertain billing remains reserved. Increasing the cap does not enable a paused schedule.",
            [
                FieldSpec(
                    "lifetime_budget_usd",
                    "Lifetime cap · USD",
                    kind="number",
                    minimum=0,
                    value=str(self.selected.get("lifetime_budget_usd") or 0),
                )
            ],
            "Save cap",
            lambda values: self.request(
                "Updating cumulative budget",
                lambda: ScheduleStore(self.settings, Store(self.settings)).set_budget(
                    identifier, **values
                ),
                lambda result: self.mutation_done("Cumulative schedule budget saved"),
                mutation=True,
            ),
        )

    def command_change_link(self: Any) -> None:
        if not self.selected:
            return
        experiment_id = self.selected["id"]
        self.request(
            "Reading connected projects",
            self.service.projects,
            lambda projects: self.change_link_form(experiment_id, projects),
        )

    def change_link_form(self: Any, experiment_id: str, projects: list[dict[str, Any]]) -> None:
        self.form(
            "Link evaluation to repository changes",
            "Runs retain the frozen benchmark and capture current saved system configurations. Commits are provenance; this does not build or deploy an agent. Polling only reads GitHub commit metadata and never posts statuses.",
            [
                FieldSpec("name", "Baseline name"),
                FieldSpec(
                    "project_id",
                    "Project",
                    kind="choice",
                    options=[(plain(project["name"]), project["id"]) for project in projects],
                ),
                FieldSpec(
                    "enabled",
                    "Trigger admission",
                    kind="choice",
                    value="paused",
                    options=[
                        ("Paused until reviewed", "paused"),
                        ("Enable local triggers", "enabled"),
                    ],
                ),
                FieldSpec("config_revision", "Agent/configuration revision label", required=False),
            ],
            "Save baseline",
            lambda values: self.request(
                "Saving change baseline",
                lambda: ChangeRuns(self.settings).register(
                    values["name"],
                    values["project_id"],
                    experiment_id,
                    {"revision": values["config_revision"]},
                    enabled=values["enabled"] == "enabled",
                ),
                lambda result: self.show_page("changes"),
                mutation=True,
            ),
        )

    def command_change_poll(self: Any) -> None:
        if not self.selected:
            return
        identifier = self.selected["id"]

        def execute() -> Any:
            from cvals.runtime import ensure_worker

            ensure_worker(self.settings)
            return ChangeRuns(self.settings).poll(identifier)

        self.request(
            "Reading change and admitting evaluation",
            execute,
            lambda result: self.human_evidence("Change evaluation", result),
            mutation=True,
        )

    def command_change_toggle(self: Any) -> None:
        if self.selected:
            identifier, enabled = self.selected["id"], not self.selected.get("enabled", False)
            self.request(
                "Updating change admission",
                lambda: ChangeRuns(self.settings).revise(identifier, enabled=enabled),
                lambda result: self.mutation_done("Change baseline updated"),
                mutation=True,
            )

    def command_change_history(self: Any) -> None:
        if self.selected:
            identifier = self.selected["id"]
            self.request(
                "Reading change history",
                lambda: ChangeRuns(self.settings).evaluations(identifier),
                lambda result: self.human_evidence("Change-linked evaluations", result),
            )

    def command_triage(self: Any) -> None:
        self.request(
            "Categorizing incomplete and failed attempts",
            partial(self.service.triage, self._experiment()),
            lambda result: self.human_evidence("Failure triage", result),
        )

    def command_economics(self: Any) -> None:
        self.request(
            "Reading cost and latency evidence",
            partial(self.service.economics, self._experiment()),
            lambda result: self.human_evidence("Cost and latency", result),
        )

    def command_brief(self: Any) -> None:
        self.request(
            "Preparing decision evidence",
            partial(self.service.decision_brief, self._experiment()),
            lambda result: self.human_evidence("Decision brief", result),
        )

    def command_diagnose(self: Any) -> None:
        if self.selected:
            self.request(
                "Reading attempt diagnosis",
                partial(self.service.diagnose, self.experiment_id, self.selected["id"]),
                lambda result: self.human_evidence("Attempt diagnosis", result),
            )

    def command_regrade(self: Any) -> None:
        if self.selected:
            self.request(
                "Regrading saved submission locally",
                partial(self.service.regrade, self.experiment_id, self.selected["id"]),
                lambda result: self.human_evidence("Linked regrade evidence", result),
                mutation=True,
            )

    def command_artifacts(self: Any) -> None:
        self.show_page("artifacts")

    def command_artifact_import(self: Any) -> None:
        from cvals.private_artifacts import PrivateArtifacts

        self.form(
            "Import a reviewed dependency artifact",
            "Select a local wheel or npm tarball on this machine. Everval validates and stores its bytes in the private workspace without running package code. The registry URL records reviewed provenance; never include credentials.",
            [
                FieldSpec("path", "Local artifact path"),
                FieldSpec(
                    "kind",
                    "Archive kind",
                    kind="choice",
                    value="wheel",
                    options=[("Python wheel", "wheel"), ("Npm package tarball", "npm")],
                ),
                FieldSpec(
                    "registry", "Original registry URL", placeholder="https://registry.example"
                ),
            ],
            "Validate and import",
            lambda values: self.request(
                "Importing dependency artifact",
                lambda: PrivateArtifacts(self.settings).import_file(**values),
                lambda result: self.show_page("artifacts"),
                mutation=True,
            ),
        )
