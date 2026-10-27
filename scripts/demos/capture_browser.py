"""Record continuous browser workflows with original, provider-free controls.

Each clip is one uninterrupted Chrome video. Pointer travel, sequential typing,
smooth scrolling and real UI updates are retained; no screenshots are assembled.
Credentials are inserted before navigation and never displayed in the recording.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from motion import Motion
from playwright.sync_api import Browser, Page, expect, sync_playwright

from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.store import Store

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT = 1280, 800


def seed(settings: Settings) -> dict[str, Any]:
    store = Store(settings)
    store.init()
    store.ensure_fixture_systems()
    definitions = [
        ("Support routing", "workflow", "route", ["billing", "technical", "returns"]),
        ("Invoice checks", "data", "total", [42, 125, 380]),
        ("Policy extraction", "documents", "approval", ["manager", "finance", "none"]),
    ]
    collections = []
    for name, domain, field, answers in definitions:
        scenarios = [
            Scenario(
                name=f"{name} · case {index}",
                domain=domain,
                instruction=(
                    "Add the supplied amounts and return JSON with the total field."
                    if domain == "data"
                    else f"Extract the {field} value from the supplied policy and return it as JSON."
                ),
                inputs={"amounts": [answer - 2, 2]}
                if domain == "data"
                else {"policy": {field: answer}},
                reference_output={field: answer},
                checks=[Check(name="Expected result", kind="equals", path=field, expected=answer)],
            )
            for index, answer in enumerate(answers, 1)
        ]
        collection = Collection(
            name=name,
            domain=domain,
            description="Original installation examples for a repeatable workflow demonstration.",
            scenarios=scenarios,
        )
        store.save_collection(collection)
        run = store.create_experiment(
            ExperimentCreate(
                name=name + " · fixture comparison",
                collection_id=collection.id,
                system_ids=["fixture-reference", "fixture-incomplete"],
                repetitions=2,
            )
        )
        Engine(store, settings).run_experiment(run["id"])
        collections.append(collection)
    identities = IdentityStore(settings)
    admin = identities.bootstrap("Evaluation lead")
    actor = admin["user"]["id"]
    importer = identities.create_user("Incident curator", "operator", actor)
    importer_token = identities.issue_token(importer["id"], "Local demo", actor=actor)
    return {
        "admin": admin["token"],
        "importer": importer_token["token"],
        "collections": collections,
    }


def serve(workspace: Path, port: int) -> None:
    import uvicorn

    from cvals.api import create_app

    uvicorn.run(
        create_app(Settings(workspace), start_worker=True),
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="warning",
    )


def align_recording_clock(value: dict[str, Any], media_duration: float) -> None:
    """Translate action-clock markers into the actual recorded media timeline."""
    value.setdefault("wallclock_start_s", value["start_s"])
    value.setdefault("wallclock_poster_s", value["poster_s"])
    offset = max(0.0, value["wallclock_end_s"] - media_duration)
    value["recording_clock_offset_s"] = round(offset, 3)
    value["end_s"] = round(media_duration, 3)
    value["start_s"] = round(max(0.0, min(media_duration, value["wallclock_start_s"] - offset)), 3)
    value["poster_s"] = round(
        max(
            value["start_s"],
            min(media_duration - 0.04, value["wallclock_poster_s"] - offset),
        ),
        3,
    )
    for mark in value["marks"]:
        mark.setdefault("wallclock_at_s", mark["at_s"])
        mark["at_s"] = round(
            max(value["start_s"], min(media_duration, mark["wallclock_at_s"] - offset)), 3
        )
    value["duration_s"] = round(value["end_s"] - value["start_s"], 3)


class Recording:
    """One real page, one video, and explicit trim markers around the workflow."""

    def __init__(
        self,
        browser: Browser,
        root: Path,
        name: str,
        title: str,
        base: str,
        token: str,
        route: str,
        errors: list[str],
    ):
        self.browser = browser
        self.directory = root / name
        self.directory.mkdir()
        self.context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=self.directory / "raw",
            record_video_size={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            accept_downloads=True,
            reduced_motion="no-preference",
        )
        self.context.add_init_script(
            "sessionStorage.setItem('custom-vals-session-token'," + json.dumps(token) + ");"
        )
        self.origin = time.monotonic()
        self.page = self.context.new_page()
        self.video = self.page.video
        self.page.on("pageerror", lambda error: errors.append(str(error)))
        self.motion = Motion(self.page)
        self.page.goto(base + "/#" + route, wait_until="networkidle")
        expect(self.page.locator(".connection-dot.connected")).to_be_visible()
        self.page.evaluate("document.fonts.ready")
        self.motion.install()
        self.motion.pause(500)
        self.value: dict[str, Any] = {
            "name": name,
            "title": title,
            "width": WIDTH,
            "height": HEIGHT,
            "fps": 25,
            "continuous": True,
            "fixture_note": (
                "Actual interface driven continuously with original installation controls. "
                "No model-quality or latency claim."
            ),
            "start_s": self.elapsed(),
            "marks": [],
        }
        self.motion.pause(700)

    def elapsed(self) -> float:
        return round(time.monotonic() - self.origin, 3)

    def mark(self, label: str, poster: bool = False) -> None:
        self.value["marks"].append({"at_s": self.elapsed(), "label": label})
        if poster:
            self.value["poster_s"] = self.elapsed()

    def finish(self) -> None:
        self.motion.pause(1000)
        self.value["wallclock_end_s"] = self.elapsed()
        self.value.setdefault(
            "poster_s", max(self.value["start_s"], self.value["wallclock_end_s"] - 1)
        )
        self.context.close()
        if self.video is None:
            raise RuntimeError("Chrome did not attach a video recorder")
        video = Path(self.video.path())
        destination = self.directory / "capture.webm"
        video.replace(destination)
        self.value["video"] = destination.name
        metadata = self.directory / "video-metadata.html"
        metadata.write_text('<video preload="metadata" src="capture.webm"></video>')
        probe = self.browser.new_context()
        try:
            page = probe.new_page()
            page.goto(metadata.as_uri())
            page.wait_for_function("document.querySelector('video').readyState >= 1")
            self.value["end_s"] = round(page.locator("video").evaluate("(v) => v.duration"), 3)
        finally:
            probe.close()
        align_recording_clock(self.value, self.value["end_s"])
        (self.directory / "manifest.json").write_text(json.dumps(self.value, indent=2) + "\n")

    def close(self) -> None:
        self.context.close()


def nav(record: Recording, name: str) -> None:
    record.motion.click(record.page.locator(".sidebar").get_by_role("link", name=name, exact=True))


def choose(record: Recording, locator: Any, value: str) -> None:
    record.motion.show(locator)
    record.motion.move(locator)
    locator.select_option(value)
    record.motion.pause(350)


def wait_reports(page: Page) -> None:
    expect(page.get_by_role("heading", name="Your reports", exact=True)).to_be_visible(
        timeout=30000
    )


def chat_request(record: Recording) -> None:
    page, motion = record.page, record.motion
    expect(page.get_by_role("heading", name="What would you like to benchmark?")).to_be_visible()
    text = (
        "Compare Reference fixture · control and Empty submission · control on Support routing "
        "every two weeks. Save HTML and CSV reports. Budget $0 per run and $0 total."
    )
    motion.type(page.get_by_role("textbox", name="Your benchmark request"), text, delay=23)
    record.mark("The request is typed character by character")
    motion.pause(600)
    motion.click(page.get_by_role("button", name="Send", exact=True))
    expect(page.get_by_role("link", name="View results", exact=True)).to_be_visible(timeout=30000)
    motion.pause(1500)
    record.mark("The actual recurring plan is active", poster=True)
    wait_reports(page)


def pause_schedule(record: Recording) -> None:
    page, motion = record.page, record.motion
    pause = page.get_by_role("button", name="Pause future runs", exact=True)
    motion.show(pause)
    motion.click(pause)
    expect(page.get_by_role("button", name="Resume", exact=True)).to_be_visible()
    motion.pause(700)
    record.mark("Future runs paused")


def chat(record: Recording) -> None:
    page, motion = record.page, record.motion
    chat_request(record)
    motion.show(page.get_by_role("heading", name="Your reports", exact=True))
    motion.pause(1200)
    with page.expect_download() as download:
        motion.click(page.get_by_role("button", name="HTML", exact=False))
    download.value.save_as(record.directory / "report.html")
    motion.pause(800)
    record.mark("Actual HTML report downloaded")
    pause_schedule(record)
    motion.click(page.get_by_role("link", name="View results", exact=True))
    expect(page.get_by_role("heading", name="System comparison", exact=True)).to_be_visible(
        timeout=15000
    )
    motion.scroll_to(410)
    motion.pause(2000)
    record.mark("Completed outcomes retained", poster=True)


def hero(record: Recording) -> None:
    page, motion = record.page, record.motion
    chat_request(record)
    motion.show(page.get_by_role("heading", name="Your reports", exact=True))
    motion.pause(1200)
    pause_schedule(record)
    motion.click(page.get_by_role("link", name="View results", exact=True))
    expect(page.get_by_role("heading", name="System comparison", exact=True)).to_be_visible(
        timeout=15000
    )
    motion.scroll_to(410)
    motion.pause(1700)
    record.mark("The completed comparison is inspected")
    nav(record, "Rankings")
    expect(page.get_by_role("heading", name="Model rankings", exact=True)).to_be_visible()
    motion.scroll_to(220)
    motion.pause(1800)
    record.mark("The same workspace's benchmark matrix", poster=True)
    motion.click(page.get_by_role("button", name="Cost per success", exact=True))
    motion.pause(1200)
    motion.click(page.get_by_role("button", name="P95 latency", exact=True))
    motion.pause(1200)
    motion.click(page.get_by_role("button", name="Quality", exact=True))
    motion.pause(1700)
    motion.click(page.locator(".rankings-cell").first)
    motion.show(page.locator("#ranking-result-detail"))
    motion.pause(2000)
    record.mark("The matrix opens its actual supporting evidence")


def rankings(record: Recording) -> None:
    page, motion = record.page, record.motion
    expect(page.get_by_role("heading", name="Model rankings", exact=True)).to_be_visible()
    expect(page.locator(".rankings-cell").first).to_be_visible()
    motion.scroll_to(220)
    motion.pause(1500)
    record.mark("Frozen benchmark matrix", poster=True)
    motion.click(page.get_by_role("button", name="Cost per success", exact=True))
    motion.pause(1400)
    motion.click(page.get_by_role("button", name="P95 latency", exact=True))
    motion.pause(1400)
    motion.click(page.get_by_role("button", name="Quality", exact=True))
    motion.pause(1000)
    motion.click(page.locator(".rankings-cell").first)
    motion.show(page.locator("#ranking-result-detail"))
    motion.pause(2200)
    motion.scroll_to(100)
    motion.type(
        page.get_by_label("Find a model or configuration", exact=True), "Reference", delay=75
    )
    motion.pause(1200)
    motion.scroll_to(220)
    motion.pause(1400)
    record.mark("Configuration filter applied")
    search = page.get_by_label("Find a model or configuration", exact=True)
    motion.show(search)
    motion.click(search)
    search.press("ControlOrMeta+A")
    search.press("Backspace")
    motion.scroll_to(220)
    motion.pause(1500)


def authoring(record: Recording) -> None:
    page, motion = record.page, record.motion
    expect(page.get_by_role("heading", name="Collections", exact=True)).to_be_visible()
    motion.click(page.get_by_role("link", name="New collection", exact=True))
    motion.type(
        page.get_by_role("textbox", name="Collection name", exact=True), "Invoice totals", delay=35
    )
    choose(record, page.get_by_label("Scenario domain", exact=True), "data")
    motion.type(page.get_by_label("Scenario name", exact=True), "Add amounts", delay=30)
    motion.type(
        page.get_by_label("Task instruction", exact=True),
        "Add the amounts. Return JSON with a numeric total.",
        delay=20,
    )
    motion.type(
        page.get_by_label("Public inputs · JSON object", exact=True),
        '{"amounts":[20,22]}',
        delay=35,
    )
    motion.pause(800)
    record.mark("The task's public contract is entered")
    motion.type(
        page.get_by_label("Reference output · JSON object", exact=True), '{"total":42}', delay=40
    )
    motion.type(page.get_by_label("Output path", exact=True), "total", delay=55)
    motion.type(page.get_by_label("Expected value · JSON", exact=True), "42", delay=80)
    motion.pause(700)
    motion.click(page.get_by_role("button", name="Create collection", exact=True))
    expect(page.get_by_role("heading", name="Invoice totals", exact=True)).to_be_visible()
    motion.scroll_to(0)
    motion.pause(1000)
    motion.click(page.get_by_role("button", name="Validate collection", exact=True))
    expect(page.get_by_text("Validation passed", exact=False)).to_be_visible(timeout=10000)
    motion.pause(1000)
    record.mark("Real original controls pass validation")
    motion.click(page.get_by_role("link", name="Create experiment", exact=True))
    motion.type(
        page.get_by_label("Experiment name", exact=True),
        "Invoice totals · fixture comparison",
        delay=20,
    )
    for name in ("Reference fixture", "Empty submission"):
        control = page.get_by_role("checkbox", name=name, exact=False)
        if not control.is_checked():
            motion.click(control)
    motion.click(page.get_by_role("button", name="Start experiment", exact=True))
    expect(page.get_by_role("heading", name="System comparison", exact=True)).to_be_visible(
        timeout=30000
    )
    motion.scroll_to(410)
    motion.pause(2200)
    record.mark("Both recorded outcomes are visible", poster=True)
    motion.click(page.get_by_role("tab", name="Trial evidence", exact=False))
    motion.pause(1600)


def stage_case(base: str, token: str, root: Path) -> str:
    # Use the real importer UI before recording the separate approver's journey.
    from playwright.sync_api import sync_playwright

    source = root / "original-incident.json"
    source.write_text(
        json.dumps(
            [
                {
                    "name": "Invoice addition regression",
                    "domain": "data",
                    "instruction": "Add the supplied amounts and return JSON with the numeric total.",
                    "inputs": {"amounts": [20, 22], "customer_email": "fixture@example.invalid"},
                    "observed_output": {"total": 22},
                }
            ]
        )
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
        context.add_init_script(
            "sessionStorage.setItem('custom-vals-session-token'," + json.dumps(token) + ");"
        )
        page = context.new_page()
        page.goto(base + "/#/cases", wait_until="networkidle")
        page.get_by_label("Examples file", exact=True).set_input_files(source)
        page.get_by_role("button", name="Redact and stage examples", exact=True).click()
        expect(
            page.get_by_role("heading", name="original-incident.json", exact=True)
        ).to_be_visible()
        route = page.url.split("#", 1)[1]
        browser.close()
        return route


def production(record: Recording) -> None:
    page, motion = record.page, record.motion
    expect(page.get_by_role("heading", name="original-incident.json", exact=True)).to_be_visible()
    motion.click(page.get_by_role("button", name="Invoice addition regression", exact=False))
    motion.pause(1200)
    record.mark("The independently staged, redacted incident is inspected")
    motion.type(
        page.get_by_label("What should the correct behavior be?", exact=True),
        "The total must include both amounts and equal 42.",
        delay=27,
    )
    motion.type(
        page.get_by_label("Why is this expectation correct?", exact=True),
        "Independent arithmetic check: 20 + 22 = 42.",
        delay=27,
    )
    motion.type(page.get_by_label("Output field 1", exact=True), "total", delay=60)
    choose(record, page.get_by_label("Comparison 1", exact=True), "numeric")
    motion.type(page.get_by_label("Approved expected value 1", exact=True), "42", delay=100)
    motion.pause(900)
    motion.click(
        page.get_by_role("button", name="Validate and approve expected behavior", exact=True)
    )
    expect(page.get_by_text("This case has an immutable approval.", exact=True)).to_be_visible()
    motion.pause(1400)
    record.mark("Actual approval and control validation completed")
    motion.type(
        page.get_by_label("Collection name", exact=True), "Reviewed invoice incidents", delay=32
    )
    motion.pause(600)
    motion.click(page.get_by_role("button", name="Publish 1 approved case", exact=True))
    motion.click(page.get_by_role("link", name="Open the validated collection.", exact=True))
    expect(
        page.get_by_role("heading", name="Reviewed invoice incidents", exact=True)
    ).to_be_visible()
    motion.scroll_to(0)
    motion.pause(2400)
    record.mark("Approved incident published as a validated collection", poster=True)


def capture(output: Path, channel: str, only: str | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="capture-", dir=output))
    settings = Settings(root / "workspace")
    fixture = seed(settings)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    errors: list[str] = []
    log = (root / "server.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            str(settings.data_dir),
            str(port),
        ],
        cwd=ROOT,
        stdout=log,
        stderr=log,
    )
    result: dict[str, Any] = {
        "model_api_calls": 0,
        "fixture_only": True,
        "continuous": True,
        "clips": [],
    }
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Local capture server exited; inspect its private log.")
            try:
                if httpx.get(base + "/api/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Local capture server did not become healthy")
        case_route = (
            stage_case(base, fixture["importer"], root)
            if only in {None, "production-cases"}
            else None
        )
        specs = [
            ("everval-tour", "A question becomes a recurring benchmark", "/chat", hero),
            ("chat-and-reports", "Describe a recurring benchmark", "/chat", chat),
            ("rankings", "Compare quality, cost and latency", "/rankings", rankings),
            (
                "tasks-and-comparisons",
                "Create a task and inspect the comparison",
                "/collections",
                authoring,
            ),
            (
                "production-cases",
                "Approve an incident as a regression task",
                case_route,
                production,
            ),
        ]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel=channel,
                headless=True,
                args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
            )
            try:
                for name, title, route, action in specs:
                    if only is not None and only != name:
                        continue
                    if route is None:
                        raise RuntimeError("The production case was not staged")
                    record = Recording(
                        browser, root, name, title, base, fixture["admin"], route, errors
                    )
                    try:
                        action(record)
                        record.finish()
                        result["clips"].append(name)
                        print("Recorded " + name, flush=True)
                    finally:
                        record.close()
            finally:
                browser.close()
        if errors:
            raise RuntimeError("JavaScript errors occurred during capture")
        result.update(status="passed", javascript_errors=errors)
    except Exception:
        result.update(status="failed", javascript_errors=errors)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        result["server_stopped"] = process.poll() is not None
        (root / "capture-results.json").write_text(json.dumps(result, indent=2) + "\n")
    return root


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/continuous-browser-demos")
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--only")
    arguments = parser.parse_args()
    print(capture(arguments.output.resolve(), arguments.channel, arguments.only))


if __name__ == "__main__":
    main()
