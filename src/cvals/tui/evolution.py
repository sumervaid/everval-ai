"""Terminal workflows for continuous evaluation and controlled research."""

from __future__ import annotations

from functools import partial
from typing import Any

from cvals.tui.dialogs import FieldSpec
from cvals.tui.presentation import plain, system_label


class EvolutionMixin:
    def evolution_fetch(self: Any, page: str) -> Any:
        if page == "packs":
            from cvals.community import CommunityPacks

            return CommunityPacks(self.settings).list()
        if page == "autopilot":
            from cvals.autopilot import Autopilot

            return Autopilot(self.settings).list()
        if page == "lab":
            from cvals.optimization import Optimizer
            from cvals.research import ResearchLab

            studies = [
                {**r, "record_kind": "optimization"} for r in Optimizer(self.settings).list()
            ]
            evidence = [
                {**r, "record_kind": "investigation"}
                for r in ResearchLab(self.settings).list_records()
                if r.get("kind") != "optimization"
            ]
            return studies + evidence
        raise KeyError(page)

    def command_budget(self: Any) -> None:
        from cvals.budgeting import BudgetPlanner

        self.request(
            "Reading spending and reservations",
            lambda: BudgetPlanner(self.settings).status(),
            lambda result: self.human_evidence("Budget ledger", result),
        )

    def command_forecast(self: Any) -> None:
        if self.page != "experiments" or not self.selected:
            return
        from cvals.budgeting import BudgetPlanner

        identifier = self.selected["id"]
        self.form(
            "Project recurring evaluation costs",
            "Uses this frozen experiment's observed live costs and configured admission budget. Missing accounting stays unknown. Forecasting does not start any calls.",
            [
                FieldSpec(
                    "runs",
                    "Number of future runs",
                    kind="integer",
                    value="30",
                    minimum=1,
                    maximum=100000,
                ),
                FieldSpec(
                    "cap_usd",
                    "Additional budget available · USD",
                    kind="number",
                    value="200",
                    minimum=0,
                ),
            ],
            "Project budget",
            lambda values: self.request(
                "Projecting spend",
                lambda: BudgetPlanner(self.settings).project(identifier, **values),
                lambda result: self.human_evidence("Budget projection", result),
            ),
        )

    def command_autopilot_new(self: Any) -> None:
        self.request(
            "Reading baseline experiments",
            self.service.experiments,
            self.autopilot_form,
        )

    def autopilot_form(self: Any, experiments: list[dict[str, Any]]) -> None:
        from cvals.autopilot import Autopilot

        choices = [(plain(r["name"]), r["id"]) for r in experiments if r["status"] == "completed"]
        if len(choices) < 2:
            self.human_evidence(
                "Prepare two baseline runs",
                {
                    "next_step": "Complete a full development run and a smaller screening run with the same systems.",
                    "screening_helper": "everval autopilot screen FULL_EXPERIMENT --size N",
                    "contract": "The screening tasks must be an exact subset of the full workload. Holdout tasks are excluded.",
                },
            )
            return
        self.form(
            "Create a continuous evaluation policy",
            "A paused policy starts with frozen full and screening baselines. Enabling it admits periodic runs within its cumulative cap. Unknown charges halt new admissions.",
            [
                FieldSpec("name", "Policy name"),
                FieldSpec("experiment_id", "Full baseline", kind="choice", options=choices),
                FieldSpec(
                    "screening_experiment_id", "Screening baseline", kind="choice", options=choices
                ),
                FieldSpec(
                    "interval_minutes",
                    "Interval · minutes",
                    kind="integer",
                    value="60",
                    minimum=5,
                    maximum=525600,
                ),
                FieldSpec(
                    "lifetime_budget_usd",
                    "Cumulative provider cap · USD",
                    kind="number",
                    value="0",
                    minimum=0,
                ),
                FieldSpec(
                    "escalate",
                    "Escalation rule",
                    kind="choice",
                    value="regression",
                    options=[
                        ("On observed regression", "regression"),
                        ("Every screen", "always"),
                        ("On configuration change", "change"),
                    ],
                ),
            ],
            "Save paused policy",
            lambda values: self.request(
                "Saving evaluation policy",
                lambda: Autopilot(self.settings).create(**values),
                lambda result: self.show_page("autopilot"),
                mutation=True,
            ),
        )

    def command_autopilot_toggle(self: Any) -> None:
        if self.page != "autopilot" or not self.selected:
            return
        from cvals.autopilot import Autopilot
        from cvals.runtime import ensure_worker

        identifier, enabled = self.selected["id"], not self.selected.get("enabled", False)

        def execute() -> Any:
            if enabled:
                ensure_worker(self.settings)
            return Autopilot(self.settings).configure(identifier, enabled=enabled)

        self.request(
            "Updating continuous evaluation",
            execute,
            lambda result: self.mutation_done("Continuous evaluation updated"),
            mutation=True,
        )

    def command_autopilot_history(self: Any) -> None:
        if self.page != "autopilot" or not self.selected:
            return
        from cvals.autopilot import Autopilot

        identifier = self.selected["id"]
        self.request(
            "Reading screening and escalation evidence",
            lambda: {
                "budget": Autopilot(self.settings).budget(identifier),
                "occurrences": Autopilot(self.settings).history(identifier),
                "investigations": Autopilot(self.settings).investigations(identifier),
            },
            lambda result: self.human_evidence("Continuous evaluation evidence", result),
        )

    def command_investigate(self: Any) -> None:
        if self.page != "trials" or not self.selected:
            return
        from cvals.research import ResearchLab

        identifier = self.selected["id"]
        self.form(
            "Reproduce recorded grading",
            "Rechecks the saved output against frozen checks. This tests grader consistency; it does not repeat a model call or prove a root cause.",
            [
                FieldSpec(
                    "repetitions", "Regrades", kind="integer", value="3", minimum=2, maximum=10
                )
            ],
            "Investigate",
            lambda values: self.request(
                "Reproducing grading evidence",
                lambda: ResearchLab(self.settings).repeat_regrade(
                    self.experiment_id, identifier, **values
                ),
                lambda result: self.human_evidence("Investigation evidence", result),
                mutation=True,
            ),
        )

    def command_reduce(self: Any) -> None:
        if self.page != "trials" or not self.selected:
            return
        from cvals.research import ResearchLab

        identifier = self.selected["id"]
        self.request(
            "Reducing recorded output",
            lambda: ResearchLab(self.settings).minimize_output(
                self.experiment_id, identifier, max_evaluations=40
            ),
            lambda result: self.human_evidence("Reduced grading reproduction", result),
            mutation=True,
        )

    def command_quality(self: Any) -> None:
        self.request("Reading collections", self.service.collections, self.quality_form)

    def quality_form(self: Any, collections: list[dict[str, Any]]) -> None:
        from cvals.research import ResearchLab

        self.form(
            "Check benchmark quality",
            "Runs bounded structural negative controls and duplicate checks. Surviving mutations are review signals. These checks do not certify a grader or reveal hidden tasks to an agent.",
            [
                FieldSpec(
                    "collection_id",
                    "Collection",
                    kind="choice",
                    options=[(plain(c["name"]), c["id"]) for c in collections],
                )
            ],
            "Inspect quality",
            lambda values: self.request(
                "Inspecting grader controls",
                lambda: ResearchLab(self.settings).quality(**values),
                lambda result: self.human_evidence("Benchmark quality evidence", result),
                mutation=True,
            ),
        )

    def command_optimize(self: Any) -> None:
        self.request(
            "Reading collections and systems",
            lambda: (self.service.collections(), self.service.systems()),
            self.optimization_form,
        )

    def optimization_form(self: Any, choices: Any) -> None:
        from cvals.optimization import Optimizer
        from cvals.runtime import ensure_worker

        collections, systems = choices
        dev = [
            (plain(c["name"]), c["id"])
            for c in collections
            if c.get("benchmark_partition") not in {"holdout", "all"}
        ]
        holdout = [
            (plain(c["name"]), c["id"])
            for c in collections
            if c.get("benchmark_partition") == "holdout"
        ]
        live = [(system_label(s), s["id"]) for s in systems if s["kind"] == "http"]
        if not dev or not holdout or not live:
            self.human_evidence(
                "Prepare an optimization study",
                {
                    "required": "A development collection, a disjoint unused holdout collection, and saved live systems.",
                    "next_step": "Register candidate model, prompt, or harness configurations as Systems; freeze a separate holdout before starting.",
                    "scope": "The study evaluates explicit candidates and produces a proposal for review. It does not deploy changes.",
                },
            )
            return

        def create(values: dict[str, Any]) -> Any:
            ensure_worker(self.settings)
            return Optimizer(self.settings).create(**values)

        self.form(
            "Find a lower-cost eligible configuration",
            "Freeze candidate systems and reserve an unused holdout. The worker evaluates development first, then validates the selected candidate once on holdout. No deployment is automatic.",
            [
                FieldSpec("name", "Study name"),
                FieldSpec(
                    "development_collection_id",
                    "Development collection",
                    kind="choice",
                    options=dev,
                ),
                FieldSpec(
                    "holdout_collection_id", "Reserved holdout", kind="choice", options=holdout
                ),
                FieldSpec("system_ids", "Candidate systems", kind="multi", options=live),
                FieldSpec(
                    "budget_usd",
                    "Total study provider cap · USD",
                    kind="number",
                    value="0",
                    minimum=0,
                ),
                FieldSpec(
                    "min_pass_rate",
                    "Minimum pass fraction",
                    kind="number",
                    value="0.95",
                    minimum=0,
                    maximum=1,
                ),
                FieldSpec(
                    "max_p95_ms",
                    "Maximum p95 attempt duration · ms",
                    kind="number",
                    value="60000",
                    minimum=1,
                ),
            ],
            "Queue study",
            lambda values: self.request(
                "Queuing optimization study",
                partial(create, values),
                lambda result: self.show_page("lab"),
                mutation=True,
            ),
        )

    def command_lab_record(self: Any) -> None:
        if self.page != "lab" or not self.selected:
            return
        from cvals.optimization import Optimizer
        from cvals.research import ResearchLab

        selected = self.selected
        self.request(
            "Reading retained research evidence",
            lambda: (
                Optimizer(self.settings).get(selected["id"])
                if selected["record_kind"] == "optimization"
                else ResearchLab(self.settings).get_record(selected["id"])
            ),
            lambda result: self.human_evidence("Research evidence", result),
        )

    def command_catalog(self: Any) -> None:
        from cvals.community import CommunityPacks

        self.request(
            "Reading local pack catalog",
            lambda: CommunityPacks(self.settings).catalog(),
            lambda result: self.human_evidence("Community pack catalog", result),
        )

    def command_packs(self: Any) -> None:
        self.show_page("packs")

    def command_pack_stage(self: Any) -> None:
        from pathlib import Path

        from cvals.community import CommunityPacks

        def stage(values: dict[str, Any]) -> Any:
            path = Path(values["path"]).expanduser()
            if not path.is_file():
                raise ValueError("Choose a local pack JSON file")
            with path.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            return CommunityPacks(self.settings).stage(raw, actor=values["actor"])

        self.form(
            "Stage a community pack",
            "Imported tasks stay outside the evaluation library until behavior review and successful controls. This reads bounded JSON only; package code is not executed.",
            [
                FieldSpec("path", "Pack JSON path on this machine"),
                FieldSpec("actor", "Local reviewer label · self-reported"),
            ],
            "Inspect and stage",
            lambda values: self.request(
                "Staging pack",
                partial(stage, values),
                lambda result: self.show_page("packs"),
                mutation=True,
            ),
        )

    def command_pack_review(self: Any) -> None:
        if self.page != "packs" or not self.selected:
            return
        from cvals.community import CommunityPacks

        identifier = self.selected["id"]
        self.request(
            "Reading staged public tasks",
            lambda: CommunityPacks(self.settings).get(identifier),
            self.pack_review_form,
        )

    def pack_review_form(self: Any, pack: dict[str, Any]) -> None:
        from cvals.community import CommunityPacks

        tasks = pack["public_tasks"]
        if len(tasks) > 10:
            self.human_evidence(
                "Review a larger pack",
                {
                    "pack_id": pack["id"],
                    "next_step": "Use everval packs approve with a reviewed expected-behavior JSON map.",
                    "reason": "The guided terminal form supports up to ten tasks; the CLI supports the complete bounded pack.",
                },
            )
            return

        def approve(values: dict[str, Any]) -> Any:
            expected = {task["id"]: values[f"behavior_{index}"] for index, task in enumerate(tasks)}
            return CommunityPacks(self.settings).approve(
                pack["id"],
                actor=values["actor"],
                rationale=values["rationale"],
                expected_behaviors=expected,
            )

        self.form(
            "Review expected behavior",
            "Read the public task records first. Describe independently what each task should do; approval reruns controls and retains your review without altering the imported pack.",
            [
                FieldSpec("actor", "Local reviewer label · self-reported"),
                FieldSpec("rationale", "Review rationale", kind="multiline"),
                *[
                    FieldSpec(
                        f"behavior_{i}",
                        "Expected behavior · " + plain(task["name"]),
                        kind="multiline",
                    )
                    for i, task in enumerate(tasks)
                ],
            ],
            "Validate and admit",
            lambda values: self.request(
                "Validating reviewed pack",
                partial(approve, values),
                lambda result: self.show_page("packs"),
                mutation=True,
            ),
        )

    def command_pack_record(self: Any) -> None:
        if self.page != "packs" or not self.selected:
            return
        from cvals.community import CommunityPacks

        identifier = self.selected["id"]
        self.request(
            "Reading private pack evidence",
            lambda: CommunityPacks(self.settings).get(identifier, include_hidden=True),
            lambda result: self.human_evidence("Private pack review evidence", result),
        )

    def command_variant(self: Any) -> None:
        if self.page != "systems" or not self.selected:
            return
        from cvals.store import Store
        from cvals.variants import variant

        source = self.selected
        if source["kind"] != "http" or source.get("harness") == "external-agent":
            self.notify(
                "Use a live single-turn or tool system. Adapter changes require their own revision.",
                severity="warning",
            )
            return
        self.form(
            "Save a configuration candidate",
            "Creates a separate system with source provenance. Its model, prompt and tool budget are frozen when evaluated; the original system stays available.",
            [
                FieldSpec("name", "Candidate name"),
                FieldSpec("model", "Model", value=source["model"]),
                FieldSpec(
                    "input_cost_per_million",
                    "Reviewed input price per million tokens · USD",
                    kind="number",
                    minimum=0,
                ),
                FieldSpec(
                    "output_cost_per_million",
                    "Reviewed output price per million tokens · USD",
                    kind="number",
                    minimum=0,
                ),
                FieldSpec("pricing_version", "Pricing source and date"),
                FieldSpec(
                    "additional_instructions",
                    "Additional instructions",
                    kind="multiline",
                    value=source.get("additional_instructions", ""),
                    required=False,
                ),
                FieldSpec(
                    "max_tokens",
                    "Output token ceiling",
                    kind="integer",
                    value=str(source["max_tokens"]),
                    minimum=1,
                    maximum=32768,
                ),
                FieldSpec(
                    "max_steps",
                    "Tool step ceiling",
                    kind="integer",
                    value=str(source["max_steps"]),
                    minimum=1,
                    maximum=30,
                ),
            ],
            "Save candidate",
            lambda values: self.request(
                "Saving candidate",
                lambda: variant(Store(self.settings), source["id"], **values),
                lambda result: self.mutation_done("Candidate saved"),
                mutation=True,
            ),
        )
