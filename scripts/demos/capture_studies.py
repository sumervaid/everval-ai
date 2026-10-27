"""Record the real study interfaces with isolated, provider-free installation controls.

Run after building the optional browser interface:
    python scripts/demos/capture_studies.py --output .local/demo-captures/studies

The output contains private temporary workspaces and frame manifests. Publish only
reviewed images/manifests, never the runtime directories. Chrome and the development
Playwright dependency must already be installed.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, expect, sync_playwright

from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.review_queues import Dimension, ReviewQueues, Rubric
from cvals.store import Store
from cvals.training_demo import run_training_demo

VIEWPORT = {"width": 1280, "height": 800}
ROOT = Path(__file__).resolve().parents[2]


class Clip:
    def __init__(self, output: Path, name: str, title: str, note: str, poster_frame: int = 0):
        self.directory = output / name
        self.directory.mkdir(parents=True)
        self.manifest: dict[str, Any] = {
            "name": name,
            "title": title,
            "fixture_note": note,
            "poster_frame": poster_frame,
            **VIEWPORT,
            "frames": [],
        }

    def frame(self, page: Page, caption: str, duration: int = 1200) -> None:
        page.wait_for_timeout(140)
        name = f"{len(self.manifest['frames']) + 1:02d}.png"
        page.screenshot(
            path=str(self.directory / name),
            full_page=False,
            animations="disabled",
        )
        self.manifest["frames"].append({"file": name, "duration_ms": duration, "caption": caption})

    def save(self) -> dict[str, Any]:
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest, indent=2) + "\n")
        return {
            "name": self.manifest["name"],
            "frames": len(self.manifest["frames"]),
            "duration_ms": sum(frame["duration_ms"] for frame in self.manifest["frames"]),
        }


@contextmanager
def local_server(workspace: Path, output: Path):
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    code = (
        "from pathlib import Path; import sys,uvicorn; "
        "from cvals.api import create_app; from cvals.config import Settings; "
        "uvicorn.run(create_app(Settings(Path(sys.argv[1])),start_worker=False),"
        "host='127.0.0.1',port=int(sys.argv[2]),access_log=False,log_level='warning')"
    )
    url = f"http://127.0.0.1:{port}"
    with (output / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(workspace), str(port)],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Capture server exited; inspect the private log.")
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Capture server did not become healthy.")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def sign_in(browser: Browser, url: str, token: str, route: str, errors: list[str]) -> Page:
    context = browser.new_context(viewport=VIEWPORT, accept_downloads=True)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "/#" + route, wait_until="networkidle")
    page.get_by_label("Access token", exact=True).fill(token)
    page.get_by_role("button", name="Connect to workspace", exact=True).click()
    expect(page.get_by_role("dialog", name="Unlock your workspace")).not_to_be_visible()
    expect(page.locator(".connection-dot.connected")).to_be_visible()
    return page


def show(page: Page, heading: str) -> None:
    target = page.get_by_role("heading", name=heading, exact=True)
    expect(target).to_be_visible()
    target.evaluate(
        "(element) => { element.scrollIntoView({block: 'start'}); window.scrollBy(0, -104); }"
    )
    page.wait_for_timeout(120)


def blind_study(browser: Browser, output: Path, errors: list[str]) -> dict[str, Any]:
    directory = output / "blind-runtime"
    directory.mkdir(mode=0o700)
    settings = Settings(directory / "workspace")
    identities = IdentityStore(settings)
    admin = identities.bootstrap("Study coordinator")
    actor = admin["user"]["id"]
    reviewers = [
        identities.create_user(name, "reviewer", actor) for name in ("Reviewer One", "Reviewer Two")
    ]
    tokens = [
        identities.issue_token(user["id"], "Local demonstration", actor=actor) for user in reviewers
    ]
    store = Store(settings)
    store.ensure_fixture_systems()
    collection = Collection(
        name="Invoice review · installation control",
        scenarios=[
            Scenario(
                name="Invoice total",
                domain="data",
                instruction="Add the invoice amounts and return the total.",
                inputs={"amounts": [2, 3]},
                reference_output={"total": 5},
                checks=[Check(name="Total", kind="equals", path="total", expected=5)],
            )
        ],
    )
    store.save_collection(collection)
    experiment = store.create_experiment(
        ExperimentCreate(
            name="Invoice comparison · fixture controls",
            collection_id=collection.id,
            system_ids=["fixture-reference", "fixture-incomplete"],
        )
    )
    Engine(store, settings).run_experiment(experiment["id"])
    rubric = ReviewQueues(settings).create_rubric(
        Rubric(
            name="Task correctness",
            version="1",
            instructions="Assess whether the answer completes the task.",
            dimensions=[
                Dimension(
                    id="quality",
                    name="Correctness",
                    guidance="One for correct; zero for incorrect.",
                )
            ],
        ),
        actor,
    )
    clip = Clip(
        output,
        "double-blind",
        "Judge first. Reveal later.",
        "Original fixture outputs and scripted reviewers. Trusted-custodian blinding; not confidential hardware or a model-quality study.",
        poster_frame=11,
    )
    with local_server(settings.data_dir, directory) as url:
        coordinator = sign_in(browser, url, admin["token"], "/blind", errors)
        clip.frame(coordinator, "Start a dedicated double-blind study")
        coordinator.get_by_role("link", name="New study", exact=True).click()
        expect(coordinator.get_by_label("Study name", exact=True)).to_be_visible()
        clip.frame(coordinator, "Set the rules before judging", 1000)
        coordinator.get_by_label("Study name", exact=True).fill("Invoice review · fixture control")
        coordinator.get_by_label("Completed experiment", exact=True).select_option(experiment["id"])
        coordinator.get_by_label("Reference fixture · control", exact=True).check()
        coordinator.get_by_label("Empty submission · control", exact=True).check()
        coordinator.get_by_label("Scoring rubric", exact=True).select_option(rubric["id"])
        for person in reviewers:
            coordinator.get_by_label(person["name"], exact=True).check()
        coordinator.get_by_role(
            "button", name="Freeze and assign study", exact=True
        ).scroll_into_view_if_needed()
        clip.frame(coordinator, "Freeze the pair, rubric and independent assignments")
        coordinator.get_by_role("button", name="Freeze and assign study", exact=True).click()
        expect(
            coordinator.get_by_role("heading", name="Judgments come first", exact=True)
        ).to_be_visible()
        coordinator.evaluate("window.scrollTo(0, 0)")
        clip.frame(coordinator, "Model identities and results stay sealed", 1400)
        reviewer_pages = []
        for number, token in enumerate(tokens):
            page = sign_in(browser, url, token["token"], "/reviews", errors)
            reviewer_pages.append(page)
            if number == 0:
                clip.frame(page, "Each reviewer receives their own assignment", 1000)
            page.locator("a.review-inbox-row").filter(has_text="Invoice total").click()
            expect(page.get_by_role("heading", name="Invoice total", exact=True)).to_be_visible()
            assert "Reference fixture" not in page.locator("main").inner_text()
            if number == 0:
                clip.frame(page, "Compare anonymous responses against the same task", 1500)
            preference = None
            for label in ("A", "B"):
                panel = page.locator("section.panel").filter(
                    has=page.get_by_role("heading", name="Response " + label, exact=True)
                )
                correct = "total" in panel.inner_text().lower()
                page.get_by_label("Response " + label + " · Correctness", exact=True).select_option(
                    "1" if correct else "0"
                )
                if correct:
                    preference = label
            assert preference is not None
            page.get_by_label(
                "Which response better satisfies the task?", exact=True
            ).select_option(preference)
            page.get_by_label("Overall judgment", exact=True).select_option("acceptable")
            page.get_by_label("Explain your judgment", exact=True).fill(
                "The answer returns the correct total. Scripted fixture review."
            )
            if number == 0:
                show(page, "Task correctness")
                clip.frame(page, "Score the answers and retain a reason", 1400)
            page.get_by_role("button", name="Record judgment", exact=True).click()
            expect(page.get_by_text("Review recorded.", exact=False)).to_be_visible()
            if number == 0:
                clip.frame(page, "Record judgments without seeing other reviewers' scores", 1000)
        coordinator.get_by_role("button", name="Refresh", exact=True).click()
        expect(
            coordinator.get_by_role("button", name="Lock completed judgments", exact=True)
        ).to_be_enabled()
        coordinator.evaluate("window.scrollTo(0, 0)")
        clip.frame(coordinator, "All required judgments are complete", 1300)
        coordinator.get_by_role("button", name="Lock completed judgments", exact=True).click()
        expect(
            coordinator.get_by_role("heading", name="Judgments are locked", exact=True)
        ).to_be_visible()
        coordinator.evaluate("window.scrollTo(0, 0)")
        clip.frame(coordinator, "Lock the evidence before revealing identities", 1400)
        coordinator.get_by_label("Reason for revealing results", exact=True).fill(
            "All scripted fixture reviews are complete and locked."
        )
        show(coordinator, "Judgments are locked")
        clip.frame(coordinator, "Release is a separate, recorded decision", 1000)
        coordinator.get_by_role("button", name="Reveal identities and results", exact=True).click()
        expect(
            coordinator.get_by_role("heading", name="Released study report", exact=True)
        ).to_be_visible()
        coordinator.evaluate("window.scrollTo(0, 0)")
        clip.frame(coordinator, "Reveal the named results", 1400)
        show(coordinator, "Released study report")
        clip.frame(coordinator, "Inspect preferences and export local evidence", 1800)
        with coordinator.expect_download() as download:
            coordinator.get_by_role("button", name="HTML report", exact=True).click()
        report_file = directory / "released-report.html"
        download.value.save_as(report_file)
        report = browser.new_page(viewport=VIEWPORT)
        report.on("pageerror", lambda error: errors.append(str(error)))
        report.goto(report_file.resolve().as_uri(), wait_until="load")
        clip.frame(report, "Share a self-contained HTML report", 1700)
        for page in [coordinator, *reviewer_pages]:
            page.context.close()
        report.close()
    return clip.save()


def checkpoint_study(browser: Browser, output: Path, errors: list[str]) -> dict[str, Any]:
    directory = output / "training-runtime"
    directory.mkdir(mode=0o700)
    workspace = directory / "workspace"
    result = run_training_demo(Settings(directory / "unused"), workspace=workspace)
    assert result["model_calls"] == result["provider_spend_usd"] == 0
    clip = Clip(
        output,
        "checkpoint-monitoring",
        "Follow quality as training changes.",
        "Synthetic installation controls, including invented mix observations. No model training, inference, human calibration or real checkpoint performance.",
        poster_frame=1,
    )
    with local_server(workspace, directory) as url:
        page = browser.new_page(viewport=VIEWPORT)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/#/training", wait_until="networkidle")
        expect(
            page.get_by_role("heading", name="Checkpoint trajectory", exact=True)
        ).to_be_visible()
        clip.frame(page, "Monitor one frozen training protocol", 1400)
        show(page, "Checkpoint trajectory")
        clip.frame(page, "Follow checkpoint quality with explicit provenance", 1800)
        page.get_by_role("button", name="Step 10", exact=True).click()
        clip.frame(page, "Select a checkpoint without hiding uncertainty", 1400)
        page.get_by_label("Evaluation domain", exact=True).select_option("documents")
        clip.frame(page, "Switch domains on the same training trajectory", 1400)
        show(page, "Evaluation coverage")
        clip.frame(page, "Keep coverage, errors and cost alongside each score", 1600)
        page.get_by_role("button", name="Inspect step 10", exact=True).click()
        show(page, "Checkpoint evidence · step 10")
        clip.frame(page, "Inspect the frozen execution identity and evidence", 1500)
        show(page, "Regression decisions")
        clip.frame(page, "Review paired decisions and practical thresholds", 1600)
        show(page, "Investigations")
        clip.frame(page, "Connect a surprising result to a retained investigation", 1700)
        show(page, "Evaluation qualifications")
        clip.frame(page, "See measurement gaps before trusting a number", 1700)
        show(page, "Evaluation mix studies")
        clip.frame(page, "Select an evaluation mix within a declared budget", 1400)
        show(page, "Held-out mix validation")
        clip.frame(page, "Validate the selected mix on held-out controls", 1500)
        show(page, "Frozen evaluation protocol")
        page.get_by_text("Inspect frozen protocol", exact=True).click()
        clip.frame(page, "Keep the measurement policy inspectable", 1600)
        page.close()
    return clip.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("Choose a new or empty output directory.")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (ROOT / "src/cvals/static/index.html").exists():
        raise SystemExit("Build the browser interface first with scripts/build_web.py.")
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        try:
            clips = [
                blind_study(browser, output, errors),
                checkpoint_study(browser, output, errors),
            ]
        finally:
            browser.close()
    if errors:
        raise RuntimeError("The browser emitted JavaScript errors.")
    summary = {
        "clips": clips,
        "model_api_calls": 0,
        "javascript_errors": errors,
        "servers_stopped": True,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
