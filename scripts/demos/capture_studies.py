"""Record uninterrupted study workflows with live pointer, typing and scrolling.

Run after building the browser:
    python scripts/demos/capture_studies.py --output .local/continuous-studies

Each clip is a native 25 fps browser recording. Source videos, credentials and
fixture workspaces remain private. Segment times are offsets in the source WebM.
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

from motion import Motion
from playwright.sync_api import Browser, Page, expect, sync_playwright

from cvals.blind_studies import BlindStudies, StudyRequest
from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.review_queues import Dimension, Judgment, ReviewQueues, Rubric
from cvals.store import Store
from cvals.training_demo import run_training_demo

VIEWPORT = {"width": 1280, "height": 800}
ROOT = Path(__file__).resolve().parents[2]


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


class Recording:
    def __init__(
        self,
        browser: Browser,
        output: Path,
        name: str,
        title: str,
        note: str,
        errors: list[str],
        url: str,
        route: str,
        token: str | None = None,
    ):
        self.browser = browser
        self.directory = output / name
        self.directory.mkdir()
        self.context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=self.directory,
            record_video_size=VIEWPORT,
            accept_downloads=True,
            reduced_motion="no-preference",
        )
        if token:
            # Authentication is loaded before navigation; no token is shown.
            self.context.add_init_script(
                "if(location.origin === "
                + json.dumps(url)
                + "){sessionStorage.setItem('custom-vals-session-token',"
                + json.dumps(token)
                + ");}"
            )
        self.clock = time.monotonic()
        self.page: Page = self.context.new_page()
        self.page.on("pageerror", lambda error: errors.append(str(error)))
        self.motion = Motion(self.page)
        self.page.goto(url + "/#" + route, wait_until="networkidle")
        expect(self.page.locator(".connection-dot.connected")).to_be_visible()
        self.motion.install()
        self.value: dict[str, Any] = {
            "name": name,
            "title": title,
            "fixture_note": note,
            **VIEWPORT,
            "fps": 25,
            "continuous": True,
            "video": "source.webm",
            "capture": "Native continuous browser recording; no keyframe reconstruction.",
        }

    def begin(self) -> None:
        self.motion.pause(250)
        self.value["start_s"] = round(time.monotonic() - self.clock, 3)

    def poster(self) -> None:
        self.value["poster_s"] = round(time.monotonic() - self.clock, 3)

    def finish(self) -> dict[str, Any]:
        self.value["end_s"] = round(time.monotonic() - self.clock, 3)
        self.value.setdefault("poster_s", self.value["start_s"] + 1)
        video = self.page.video
        assert video is not None
        self.context.close()
        source = Path(video.path())
        source = source.replace(self.directory / "source.webm")
        probe = self.browser.new_page()
        try:
            probe.goto(source.resolve().as_uri(), wait_until="load")
            probe.wait_for_function(
                "document.querySelector('video') && Number.isFinite(document.querySelector('video').duration)"
            )
            duration = float(probe.locator("video").evaluate("(video) => video.duration"))
        finally:
            probe.close()
        # Chromium starts its video clock after page creation. Align markers
        # with the flushed source duration rather than extending beyond EOF.
        offset = self.value["end_s"] - duration
        for field in ("start_s", "poster_s"):
            self.value[field] = round(max(0, self.value[field] - offset), 3)
        self.value["end_s"] = round(duration, 3)
        self.value["capture_clock_offset_s"] = round(offset, 3)
        (self.directory / "manifest.json").write_text(json.dumps(self.value, indent=2) + "\n")
        return self.value


def seed_blind(settings: Settings) -> dict[str, Any]:
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
    queues = ReviewQueues(settings)
    rubric = queues.create_rubric(
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
    study = BlindStudies(settings).create(
        StudyRequest(
            name="Invoice review · fixture control",
            experiment_id=experiment["id"],
            rubric_id=rubric["id"],
            system_ids=["fixture-reference", "fixture-incomplete"],
            reviewer_ids=[user["id"] for user in reviewers],
        ),
        actor,
    )
    return {
        "admin": admin,
        "tokens": tokens,
        "study": study,
        "identities": identities,
        "queues": queues,
    }


def record_reviewer(
    browser: Browser, output: Path, errors: list[str], url: str, fixture: dict[str, Any]
) -> dict[str, Any]:
    identities, queues = fixture["identities"], fixture["queues"]
    who = identities.authenticate(fixture["tokens"][0]["token"])
    assert who is not None
    assignment = queues.assigned(who)[0]
    item = queues.item(assignment["id"], who)
    correct = next(
        label
        for label, response in item["responses"].items()
        if response["output"].get("total") == 5
    )
    recording = Recording(
        browser,
        output,
        "blind-reviewer",
        "Review without model names",
        "Original fixture outputs and a scripted reviewer account; not an independent human study.",
        errors,
        url,
        "/reviews/items/" + assignment["id"],
        fixture["tokens"][0]["token"],
    )
    page, motion = recording.page, recording.motion
    expect(page.get_by_role("heading", name="Invoice total", exact=True)).to_be_visible()
    assert "Reference fixture" not in page.locator("main").inner_text()
    recording.begin()
    motion.pause(1300)
    motion.move(page.get_by_role("heading", name="Response A", exact=True))
    motion.pause(900)
    motion.move(page.get_by_role("heading", name="Response B", exact=True))
    motion.pause(900)
    motion.show(page.get_by_role("heading", name="Task correctness", exact=True))
    motion.pause(650)
    for label in ("A", "B"):
        selector = page.get_by_label("Response " + label + " · Correctness", exact=True)
        motion.click(selector)
        selector.select_option("1" if label == correct else "0")
        page.keyboard.press("Escape")
        motion.pause(600)
    preference = page.get_by_label("Which response better satisfies the task?", exact=True)
    motion.click(preference)
    preference.select_option(correct)
    page.keyboard.press("Escape")
    disposition = page.get_by_label("Overall judgment", exact=True)
    motion.click(disposition)
    disposition.select_option("acceptable")
    page.keyboard.press("Escape")
    motion.type(
        page.get_by_label("Explain your judgment", exact=True),
        "The response returns the correct total. Scripted fixture review.",
        delay=35,
    )
    recording.poster()
    motion.pause(1000)
    motion.click(page.get_by_role("button", name="Record judgment", exact=True))
    expect(page.get_by_text("Review recorded.", exact=False)).to_be_visible()
    motion.pause(1700)
    motion.scroll_to(0)
    motion.pause(1800)
    return recording.finish()


def blind_study(browser: Browser, output: Path, errors: list[str]) -> list[dict[str, Any]]:
    directory = output / "blind-runtime"
    directory.mkdir(mode=0o700)
    settings = Settings(directory / "workspace")
    fixture = seed_blind(settings)
    with local_server(settings.data_dir, directory) as url:
        reviewer = record_reviewer(browser, output, errors, url, fixture)
        # The second independent account submits a real service judgment before
        # the coordinator recording begins; no outcomes are fabricated.
        identities, queues = fixture["identities"], fixture["queues"]
        who = identities.authenticate(fixture["tokens"][1]["token"])
        assert who is not None
        assignment = queues.assigned(who)[0]
        item = queues.item(assignment["id"], who)
        preferred = next(
            label
            for label, response in item["responses"].items()
            if response["output"].get("total") == 5
        )
        queues.judge(
            assignment["id"],
            who,
            Judgment(
                scores={label: {"quality": float(label == preferred)} for label in ("A", "B")},
                preference=preferred,
                disposition="acceptable",
                rationale="The response gives the correct total. Scripted second-account review.",
            ),
        )
        recording = Recording(
            browser,
            output,
            "double-blind",
            "Lock judgments. Then reveal results.",
            "Uninterrupted coordinator workflow over completed scripted fixture reviews. Trusted custodian; no hardware confidentiality or model-quality claim.",
            errors,
            url,
            "/blind/" + fixture["study"]["id"],
            fixture["admin"]["token"],
        )
        page, motion = recording.page, recording.motion
        lock = page.get_by_role("button", name="Lock completed judgments", exact=True)
        expect(lock).to_be_enabled()
        recording.begin()
        motion.pause(1600)
        motion.move(page.get_by_role("heading", name="Review coverage", exact=True))
        motion.pause(800)
        motion.move(page.get_by_role("heading", name="Study phase", exact=True))
        motion.pause(800)
        motion.show(lock)
        motion.pause(900)
        motion.click(lock)
        expect(page.get_by_role("heading", name="Judgments are locked", exact=True)).to_be_visible()
        motion.pause(1500)
        reason = page.get_by_label("Reason for revealing results", exact=True)
        motion.type(reason, "All scripted reviews are complete and locked.", delay=45)
        motion.pause(700)
        reveal = page.get_by_role("button", name="Reveal identities and results", exact=True)
        motion.click(reveal)
        expect(
            page.get_by_role("heading", name="Released study report", exact=True)
        ).to_be_visible()
        motion.scroll_to(0)
        motion.pause(1600)
        recording.poster()
        motion.show(page.get_by_role("heading", name="Released study report", exact=True))
        motion.pause(1200)
        motion.show(page.get_by_role("heading", name="Recorded preferences", exact=True))
        motion.pause(1900)
        details = page.get_by_text("Frozen protocol and released evidence", exact=True)
        motion.click(details)
        motion.pause(1400)
        motion.click(details)
        motion.show(page.get_by_role("button", name="HTML report", exact=True))
        motion.pause(600)
        with page.expect_download() as download:
            motion.click(page.get_by_role("button", name="HTML report", exact=True))
        download.value.save_as(directory / "released-report.html")
        motion.pause(1300)
        motion.scroll_to(0)
        motion.pause(1500)
        coordinator = recording.finish()
    return [coordinator, reviewer]


def checkpoint_study(browser: Browser, output: Path, errors: list[str]) -> dict[str, Any]:
    directory = output / "training-runtime"
    directory.mkdir(mode=0o700)
    workspace = directory / "workspace"
    result = run_training_demo(Settings(directory / "unused"), workspace=workspace)
    assert result["model_calls"] == result["provider_spend_usd"] == 0
    with local_server(workspace, directory) as url:
        recording = Recording(
            browser,
            output,
            "checkpoint-monitoring",
            "Follow a training run continuously",
            "Synthetic installation controls, including invented mix observations; no actual training or model-quality claim.",
            errors,
            url,
            "/training",
        )
        page, motion = recording.page, recording.motion
        trajectory = page.get_by_role("heading", name="Checkpoint trajectory", exact=True)
        expect(trajectory).to_be_visible()
        recording.begin()
        motion.pause(1400)
        motion.scroll_to(
            trajectory.evaluate(
                "(element) => window.scrollY + element.getBoundingClientRect().top - 120"
            )
        )
        motion.pause(1200)
        recording.poster()
        motion.click(page.get_by_role("button", name="Step 10", exact=True))
        motion.pause(1100)
        domain = page.get_by_label("Evaluation domain", exact=True)
        motion.click(domain)
        domain.select_option("documents")
        page.keyboard.press("Escape")
        motion.pause(1300)
        motion.show(page.get_by_role("heading", name="Evaluation coverage", exact=True))
        motion.pause(1400)
        motion.click(page.get_by_role("button", name="Inspect step 10", exact=True))
        motion.show(page.get_by_role("heading", name="Checkpoint evidence · step 10", exact=True))
        motion.pause(1400)
        motion.show(page.get_by_role("heading", name="Regression decisions", exact=True))
        motion.pause(1200)
        motion.show(page.get_by_role("heading", name="Investigations", exact=True))
        motion.pause(1400)
        motion.show(page.get_by_role("heading", name="Evaluation qualifications", exact=True))
        motion.pause(1600)
        motion.show(page.get_by_role("heading", name="Evaluation mix studies", exact=True))
        motion.pause(1500)
        motion.click(page.get_by_text("Inspect evaluation mix studies record 1", exact=True))
        motion.pause(1700)
        motion.click(page.get_by_text("Inspect evaluation mix studies record 1", exact=True))
        motion.show(page.get_by_role("heading", name="Frozen evaluation protocol", exact=True))
        motion.click(page.get_by_text("Inspect frozen protocol", exact=True))
        motion.pause(1700)
        motion.click(page.get_by_text("Inspect frozen protocol", exact=True))
        motion.scroll_to(0)
        motion.pause(1600)
        return recording.finish()


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
            blind, reviewer = blind_study(browser, output, errors)
            training = checkpoint_study(browser, output, errors)
        finally:
            browser.close()
    if errors:
        raise RuntimeError("The browser emitted JavaScript errors.")
    summary = {
        "clips": [blind, training],
        "additional_recordings": [reviewer],
        "model_api_calls": 0,
        "javascript_errors": errors,
        "servers_stopped": True,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "clips": len(summary["clips"]),
                "additional_recordings": 1,
                "model_api_calls": 0,
                "javascript_errors": errors,
                "servers_stopped": True,
            }
        )
    )


if __name__ == "__main__":
    main()
