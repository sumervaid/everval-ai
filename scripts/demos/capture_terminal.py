"""Capture original fixture workflows from the real terminal workbench.

Run from a source checkout with development dependencies installed:
    python scripts/demos/capture_terminal.py --output /private/path/terminal

The output contains a new isolated workspace, SVG/PNG keyframes and manifests.
No provider is called. Repository demonstration only reads original local Git
history; it does not claim a validated repository benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import html
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from textual.widgets import DataTable, Input, Select

from cvals.autopilot import Autopilot
from cvals.config import Settings
from cvals.demo import run_demo
from cvals.engine import Engine
from cvals.models import ExperimentCreate, System
from cvals.projects import Projects
from cvals.store import Store
from cvals.tui import WorkbenchApp
from cvals.workbench import Workbench

FIXTURE_NOTE = (
    "Original installation controls; no model calls or model-performance claims. "
    "Frames show the actual terminal application."
)


async def settle(app: WorkbenchApp, pilot: Any) -> None:
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause(0.12)


class Recorder:
    def __init__(self, root: Path):
        self.root = root
        self.clips: list[dict[str, Any]] = []
        self.current: dict[str, Any] = {}

    def start(self, name: str, title: str, note: str = FIXTURE_NOTE) -> None:
        self.current = {"name": name, "title": title, "fixture_note": note, "frames": []}
        if name == "terminal-workbench":
            self.current["poster_frame"] = 0
        self.clips.append(self.current)
        (self.root / name).mkdir(parents=True)

    async def capture(
        self, app: WorkbenchApp, pilot: Any, caption: str, duration_ms: int = 1400
    ) -> None:
        await settle(app, pilot)
        name = self.current["name"]
        number = len(self.current["frames"]) + 1
        stem = f"{number:02d}"
        svg = self.root / name / (stem + ".svg")
        rendered = app.export_screenshot(title="Everval AI · fixture walkthrough")
        account = html.escape(getpass.getuser())
        if account in rendered:
            rendered = rendered.replace(account, "operator")
            self.current["redactions"] = [
                "The local OS account name is replaced with operator; no evidence is changed."
            ]
        svg.write_text(rendered)
        self.current["frames"].append(
            {"file": f"{name}/{stem}.png", "duration_ms": duration_ms, "caption": caption}
        )

    async def render(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                channel="chrome",
                headless=True,
                args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 1000})
            await page.route("**/*", lambda route: route.abort())
            try:
                for clip in self.clips:
                    for frame in clip["frames"]:
                        png = self.root / frame["file"]
                        svg = png.with_suffix(".svg").read_text()
                        # Use a local monospaced font; screenshots require no CDN.
                        svg = re.sub(r"@font-face\s*\{.*?\}", "", svg, flags=re.S)
                        svg = svg.replace("Fira Code, monospace", "Menlo, monospace")
                        await page.set_content(
                            "<style>html,body{margin:0;background:#0d1722}"
                            "svg{display:block;width:1280px;height:auto}</style>" + svg
                        )
                        await page.locator("svg").screenshot(path=str(png))
                    (self.root / (clip["name"] + ".json")).write_text(
                        json.dumps(clip, indent=2) + "\n"
                    )
            finally:
                await browser.close()
        (self.root / "manifest.json").write_text(
            json.dumps({"clips": self.clips, "provider_calls": 0}, indent=2) + "\n"
        )


def select(app: WorkbenchApp, identifier: str) -> None:
    index = next(n for n, row in enumerate(app.rows) if row["id"] == identifier)
    app.query_one("#ledger", DataTable).move_cursor(row=index)


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def original_repository(repository: Path) -> None:
    repository.mkdir()
    git(repository, "init", "--quiet", "-b", "main")
    git(repository, "config", "user.name", "Everval fixture")
    git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "support-toolkit-fixture"\nversion = "0.1.0"\n'
    )
    (repository / "cart.py").write_text(
        'def total(items):\n    return sum(item["price"] for item in items)\n'
    )
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Initial cart fixture")
    (repository / "cart.py").write_text(
        'def total(items):\n    return sum(item["price"] * item["quantity"] for item in items)\n'
    )
    (repository / "test_cart.py").write_text(
        "from cart import total\n\ndef test_quantities():\n"
        '    assert total([{"price": 5, "quantity": 3}]) == 15\n'
    )
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Handle item quantities in cart totals")


async def capture(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    base = Settings(output / "base", allow_remote_providers=False)
    demo = run_demo(base, workspace=output / "workspace")
    settings = Settings(output / "workspace", allow_remote_providers=False)
    store = Store(settings)
    engine = Engine(store, settings)
    full = store.create_experiment(
        ExperimentCreate(
            name="Fixture · full baseline",
            collection_id="demo-workload",
            system_ids=["demo-control"],
            budget_usd=2,
        )
    )
    engine.run_experiment(full["id"])
    Workbench(settings).add_schedule(
        full["id"], "Weekly fixture review", 10080, enabled=False, lifetime_budget_usd=12
    )
    autopilot = Autopilot(settings, store)
    screening = autopilot.screen(full["id"], size=1)
    engine.run_experiment(screening["id"])
    policy = autopilot.create(
        "Fortnightly fixture checks",
        full["id"],
        screening["id"],
        interval_minutes=20160,
        lifetime_budget_usd=12,
        enabled=False,
    )
    recorder = Recorder(output)
    app = WorkbenchApp(settings)
    with tempfile.TemporaryDirectory(prefix="everval-demo-", dir="/tmp") as temporary:
        repository = Path(temporary) / "support-toolkit"
        original_repository(repository)
        async with app.run_test(size=(120, 38)) as pilot:
            await settle(app, pilot)
            recorder.start("terminal-workbench", "One workspace. Every piece of evidence.")
            await pilot.press("f4")
            await recorder.capture(
                app, pilot, "Start with original, no-key installation controls.", 1700
            )
            app.command("trials")
            await recorder.capture(
                app, pilot, "Every attempt retains its output and frozen checks."
            )
            app.command("back")
            await settle(app, pilot)
            select(app, demo["runs"][1]["id"])
            await recorder.capture(
                app, pilot, "Passing, changed and restored fixtures remain inspectable."
            )
            await pilot.press("f5")
            await recorder.capture(app, pilot, "Configure systems without storing API secrets.")
            await pilot.press("ctrl+p")
            await recorder.capture(
                app, pilot, "Use the command palette to move through the workbench."
            )
            await pilot.press("escape", "f7")
            await recorder.capture(
                app, pilot, "The audit trail retains actions and evidence links."
            )
            await pilot.press("f6")
            await recorder.capture(
                app, pilot, "Keep recurring checks paused until their scope and cap are ready."
            )
            await pilot.press("f1")
            await recorder.capture(
                app, pilot, "The terminal and shared services use the same local records.", 1800
            )

            recorder.start(
                "repository-onboarding",
                "Connect history. Keep every admission decision.",
                FIXTURE_NOTE
                + " Local original Git history only. Isolated validation remains pending.",
            )
            app.command("guide")
            await recorder.capture(
                app, pilot, "Start a resumable guide for an authorized repository."
            )
            app.screen.query_one("#field-name", Input).value = "Support toolkit · local fixture"
            await recorder.capture(app, pilot, "Give this evaluation a clear purpose.")
            await pilot.click("#form-submit")
            await recorder.capture(
                app, pilot, "The guide tracks evidence-backed steps; pending work stays pending."
            )
            guide_id = app.guide_id
            select(app, "connect")
            await settle(app, pilot)
            app.command("continue")
            await recorder.capture(
                app, pilot, "Connect GitHub history or an absolute local repository."
            )
            app.screen.query_one("#field-source", Input).value = str(repository)
            await recorder.capture(
                app, pilot, "This walkthrough uses an original local fixture repository."
            )
            await pilot.click("#form-submit")
            await recorder.capture(
                app, pilot, "Connection reads Git metadata without running repository code."
            )
            project = Projects(settings).list()[0]
            Projects(settings).discover(project["id"])
            app.project_id = project["id"]
            app.show_page("candidates")
            await recorder.capture(
                app, pilot, "Inspect discovered changes and retained rejection reasons."
            )
            await pilot.press("down")
            await recorder.capture(
                app, pilot, "Root commits remain quarantined; discovery is not validation."
            )
            app.open_guide(guide_id)
            await recorder.capture(
                app,
                pilot,
                "Resume where you stopped. Review and isolated validation are still required.",
                2200,
            )

            recorder.start("autopilot-budget", "Continuous checks with an explicit budget.")
            app.show_page("autopilot")
            await recorder.capture(
                app, pilot, "Start paused with frozen screening and full baselines.", 1600
            )
            app.command("autopilot_new")
            await recorder.capture(
                app, pilot, "Set the interval, cumulative cap and escalation rule."
            )
            await pilot.press("escape")
            autopilot.configure(policy["id"], enabled=True)
            app.action_refresh()
            await recorder.capture(
                app, pilot, "Enable the policy inside the isolated fixture workspace."
            )
            store.save_system(
                System(
                    id="demo-control",
                    name="Installation fixture",
                    kind="incomplete",
                    provenance={"purpose": "installation-control", "demo_phase": "regression"},
                )
            )
            due = autopilot.get(policy["id"])["next_due_epoch"] + 1
            screen_run = autopilot.tick(due)[0]
            engine.run_experiment(screen_run)
            app.action_refresh()
            await recorder.capture(
                app, pilot, "A due fixture cycle runs the smaller screening workload."
            )
            app.command("autopilot_history")
            await recorder.capture(
                app, pilot, "The policy retains screening evidence and reserved budget."
            )
            await pilot.press("escape")
            full_run = autopilot.tick(due + 61)[0]
            engine.run_experiment(full_run)
            autopilot.tick(due + 122)
            autopilot.configure(policy["id"], enabled=False)
            app.action_refresh()
            app.command("autopilot_history")
            await recorder.capture(
                app, pilot, "The observed fixture regression escalates to the full workload.", 1900
            )
            await pilot.press("pagedown")
            await recorder.capture(
                app, pilot, "History records both stages instead of hiding the failed screen."
            )
            await pilot.press("escape")
            app.command("budget")
            await recorder.capture(
                app,
                pilot,
                "Configured caps and settled usage stay separate. These fixtures cost $0.",
                2000,
            )
            await pilot.press("escape")
            app.show_page("autopilot")
            await recorder.capture(
                app, pilot, "Pause future checks while retaining their evidence."
            )

            recorder.start("failure-research", "Turn a failure into inspectable evidence.")
            app.show_page("experiments")
            await settle(app, pilot)
            changed = demo["runs"][1]["id"]
            select(app, changed)
            await recorder.capture(app, pilot, "Open a retained failing fixture run.")
            app.command("triage")
            await recorder.capture(
                app, pilot, "Triage groups recorded failures without another model call."
            )
            await pilot.press("escape")
            app.command("trials")
            await recorder.capture(app, pilot, "Every attempt keeps its output and frozen checks.")
            app.command("diagnose")
            await recorder.capture(
                app, pilot, "Inspect the failed checks and reproduction context."
            )
            await pilot.press("escape")
            app.command("investigate")
            await recorder.capture(
                app, pilot, "Repeat grading on the saved output; inference is not repeated."
            )
            await pilot.click("#form-submit")
            await recorder.capture(
                app, pilot, "Retain the observed grading consistency and its limits.", 1800
            )
            await pilot.press("escape", "f12")
            await recorder.capture(app, pilot, "Investigations accumulate in the research lab.")
            app.command("quality")
            await settle(app, pilot)
            app.screen.query_one("#field-collection_id", Select).value = "demo-workload"
            await recorder.capture(
                app, pilot, "Probe structural grader weaknesses and duplicate signals."
            )
            await pilot.click("#form-submit")
            await recorder.capture(
                app,
                pilot,
                "Quality diagnostics remain review signals, not proof of a perfect grader.",
                2100,
            )
            await pilot.press("escape")
            app.show_page("lab")
            await recorder.capture(
                app, pilot, "Keep investigations alongside the original experiment."
            )
    await recorder.render()
    print(
        json.dumps(
            {
                "output": str(output),
                "clips": [c["name"] for c in recorder.clips],
                "provider_calls": 0,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(capture(args.output.expanduser().resolve()))


if __name__ == "__main__":
    main()
