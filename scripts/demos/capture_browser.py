"""Capture actual browser workflows using isolated, provider-free fixtures.

Build the web assets, then run:
    python scripts/demos/capture_browser.py --output .local/browser-demos

Requires Playwright and locally installed Chrome. Raw artifacts stay outside Git.
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
from playwright.sync_api import Page, expect, sync_playwright

from cvals.config import Settings
from cvals.engine import Engine
from cvals.identity import IdentityStore
from cvals.models import Check, Collection, ExperimentCreate, Scenario
from cvals.store import Store

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT = 1280, 800


class Clip:
    def __init__(self, root: Path, name: str, title: str, poster_frame: int = 0):
        self.path = root / name
        self.path.mkdir()
        self.value: dict[str, Any] = {
            "name": name,
            "title": title,
            "width": WIDTH,
            "height": HEIGHT,
            "poster_frame": poster_frame,
            "fixture_note": "Actual Everval interface with original installation controls. No model-quality claim.",
            "frames": [],
        }

    def frame(self, page: Page, caption: str, duration_ms: int = 1300) -> None:
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(100)
        filename = f"{len(self.value['frames']):03}.png"
        page.screenshot(path=str(self.path / filename), animations="disabled", caret="hide")
        self.value["frames"].append(
            {"file": filename, "duration_ms": duration_ms, "caption": caption}
        )

    def finish(self) -> None:
        (self.path / "manifest.json").write_text(json.dumps(self.value, indent=2) + "\n")


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


def sign_in(browser: Any, base: str, token: str, route: str, errors: list[str]) -> Page:
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1, accept_downloads=True
    )
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/#" + route, wait_until="networkidle")
    page.get_by_label("Access token", exact=True).fill(token)
    page.get_by_role("button", name="Connect to workspace", exact=True).click()
    expect(page.get_by_role("dialog", name="Unlock your workspace")).not_to_be_visible()
    expect(page.locator(".connection-dot.connected")).to_be_visible()
    return page


def top(page: Page) -> None:
    page.evaluate("window.scrollTo({top:0,behavior:'instant'})")


def show(page: Page, locator: Any) -> None:
    locator.scroll_into_view_if_needed()
    page.wait_for_timeout(100)


def capture_chat(page: Page, root: Path, base: str, token: str) -> None:
    clip = Clip(root, "chat-and-reports", "Describe a recurring benchmark", poster_frame=4)
    page.goto(base + "/#/chat", wait_until="networkidle")
    expect(page.get_by_role("heading", name="What would you like to benchmark?")).to_be_visible()
    clip.frame(page, "Start with a plain-language request.")
    textbox = page.get_by_role("textbox", name="Your benchmark request")
    text = (
        "Compare Reference fixture · control and Empty submission · control on Support routing every two weeks. "
        "Save HTML and CSV reports. Budget $0 per run and $0 total."
    )
    for length, caption in [
        (35, "Choose the saved model configurations."),
        (84, "Choose the tasks and how often to run."),
        (len(text), "Set the output formats and spending limits."),
    ]:
        textbox.fill(text[:length])
        clip.frame(page, caption, 900)
    page.get_by_role("button", name="Send", exact=True).click()
    expect(page.get_by_role("link", name="View results", exact=True)).to_be_visible(timeout=30000)
    top(page)
    clip.frame(page, "Everval freezes the plan and schedules the first run.", 1700)
    with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + token}) as client:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            sessions = client.get("/api/conversations").json()
            conversation = client.get("/api/conversations/" + sessions[0]["id"]).json()
            if len(conversation.get("reports", [])) >= 2:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("The fixture run did not produce its reports.")
        run = client.get("/api/experiments/" + conversation["experiment_id"]).json()
        assert run["status"] == "completed"
    reports = page.get_by_role("heading", name="Your reports", exact=True)
    expect(reports).to_be_visible(timeout=15000)
    show(page, reports)
    clip.frame(page, "The worker produces local reports automatically.", 1700)
    with page.expect_download() as download:
        page.get_by_role("button", name="HTML", exact=False).click()
    download.value.save_as(root / "conversation-report.html")
    clip.frame(page, "Download HTML or CSV. The evidence stays local.", 1300)
    pause = page.get_by_role("button", name="Pause future runs", exact=True)
    show(page, pause)
    pause.click()
    expect(page.get_by_role("button", name="Resume", exact=True)).to_be_visible()
    clip.frame(page, "Pause future runs whenever you need.", 1600)
    page.get_by_role("link", name="View results", exact=True).click()
    expect(page.get_by_role("heading", name="System comparison", exact=True)).to_be_visible(
        timeout=10000
    )
    top(page)
    clip.frame(page, "Inspect the completed run and every attempt.", 1200)
    page.evaluate("window.scrollTo({top:440,behavior:'instant'})")
    clip.frame(page, "Keep passing controls, failures and accounting together.", 1700)
    clip.finish()


def capture_rankings(page: Page, root: Path, base: str) -> None:
    clip = Clip(root, "rankings", "Compare your benchmarks side by side", poster_frame=1)
    page.goto(base + "/#/rankings", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Model rankings", exact=True)).to_be_visible()
    expect(page.locator(".rankings-cell").first).to_be_visible()
    clip.frame(page, "Every frozen benchmark becomes a comparison column.", 1500)
    page.evaluate("window.scrollTo({top:220,behavior:'instant'})")
    clip.frame(page, "See quality on the same tasks. Controls remain clearly labeled.", 1800)
    page.get_by_role("button", name="Cost per success", exact=True).click()
    clip.frame(page, "Switch to cost per successful attempt.", 1600)
    page.get_by_role("button", name="P95 latency", exact=True).click()
    clip.frame(page, "Then inspect recorded P95 latency.", 1600)
    page.get_by_role("button", name="Quality", exact=True).click()
    page.locator(".rankings-cell").first.click()
    show(page, page.locator("#ranking-result-detail"))
    clip.frame(page, "Select a cell to inspect coverage, uncertainty and provenance.", 1800)
    page.evaluate("window.scrollBy({top:210,behavior:'instant'})")
    clip.frame(page, "Every number links to its frozen source experiment.", 1600)
    top(page)
    search = page.get_by_label("Find a model or configuration", exact=True)
    search.fill("Reference")
    page.evaluate("window.scrollTo({top:220,behavior:'instant'})")
    clip.frame(page, "Filter configurations without blending benchmark scores.", 1700)
    search.fill("")
    top(page)
    clip.frame(page, "A private comparison matrix for your own workloads.", 1600)
    clip.finish()


def capture_authoring(page: Page, root: Path, base: str) -> None:
    clip = Clip(
        root, "tasks-and-comparisons", "Turn a task into a repeatable comparison", poster_frame=8
    )
    page.goto(base + "/#/collections", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Collections", exact=True)).to_be_visible()
    clip.frame(page, "Organize documents, data and workflows into task collections.")
    page.get_by_role("link", name="New collection", exact=True).click()
    page.get_by_role("textbox", name="Collection name", exact=True).fill("Invoice totals · demo")
    page.get_by_label("Scenario domain", exact=True).select_option("data")
    page.get_by_label("Description", exact=True).fill(
        "Check the public output contract for invoice totals."
    )
    clip.frame(page, "Give the benchmark a purpose and a domain.", 1400)
    page.get_by_label("Scenario name", exact=True).fill("Add invoice amounts")
    page.get_by_label("Task instruction", exact=True).fill(
        "Add the invoice amounts. Return a JSON object with one numeric field: total."
    )
    page.get_by_label("Public inputs · JSON object", exact=True).fill('{"amounts": [20, 22]}')
    show(page, page.get_by_label("Task instruction", exact=True))
    clip.frame(page, "Define what the model sees: the task and public inputs.", 1700)
    page.get_by_label("Reference output · JSON object", exact=True).fill('{"total": 42}')
    page.get_by_label("Output path", exact=True).fill("total")
    page.get_by_label("Expected value · JSON", exact=True).fill("42")
    show(page, page.get_by_role("heading", name="Define success", exact=True))
    clip.frame(page, "Keep the accepted answer and grading checks evaluator-only.", 1700)
    page.get_by_role("button", name="Create collection", exact=True).click()
    expect(page.get_by_role("heading", name="Invoice totals · demo", exact=True)).to_be_visible()
    top(page)
    clip.frame(page, "Save a versioned collection with an explicit contract.", 1300)
    page.get_by_role("button", name="Validate collection", exact=True).click()
    expect(page.get_by_text("Validation passed", exact=False)).to_be_visible(timeout=10000)
    clip.frame(page, "Validate the reference and negative controls before comparing.", 1600)
    page.get_by_role("link", name="Create experiment", exact=True).click()
    page.get_by_label("Experiment name", exact=True).fill("Invoice totals · fixture comparison")
    page.get_by_role("checkbox", name="Reference fixture", exact=False).check()
    page.get_by_role("checkbox", name="Empty submission", exact=False).check()
    page.get_by_label("Repetitions per scenario", exact=True).fill("2")
    show(page, page.get_by_role("heading", name="3. Set the protocol", exact=True))
    clip.frame(page, "Select systems, repetitions and a bounded provider budget.", 1700)
    page.get_by_role("button", name="Start experiment", exact=True).click()
    expect(page.get_by_role("heading", name="System comparison", exact=True)).to_be_visible(
        timeout=30000
    )
    top(page)
    clip.frame(page, "Compare recorded outcomes on exactly the same frozen work.", 1100)
    page.evaluate("window.scrollTo({top:440,behavior:'instant'})")
    clip.frame(page, "Retain failures alongside passing controls.", 1500)
    page.get_by_role("tab", name="Trial evidence", exact=False).click()
    clip.frame(page, "Inspect each attempt and its evidence.", 1700)
    clip.finish()


def capture_production(page: Page, importer: Page, root: Path, base: str) -> None:
    clip = Clip(
        root, "production-cases", "Turn reviewed incidents into regression tests", poster_frame=7
    )
    fixture = [
        {
            "name": "Invoice addition regression",
            "domain": "data",
            "instruction": "Add the supplied amounts and return JSON with the numeric total.",
            "inputs": {"amounts": [20, 22], "customer_email": "fixture@example.invalid"},
            "observed_output": {"total": 22},
        }
    ]
    source = root / "original-incident.json"
    source.write_text(json.dumps(fixture))
    importer.goto(base + "/#/cases", wait_until="networkidle")
    expect(importer.get_by_role("heading", name="Production examples", exact=True)).to_be_visible()
    clip.frame(importer, "Start with an authorized incident, not a guessed benchmark.")
    importer.get_by_label("Examples file", exact=True).set_input_files(source)
    clip.frame(importer, "Stage the example and redact selected fields.", 1400)
    importer.get_by_role("button", name="Redact and stage examples", exact=True).click()
    expect(
        importer.get_by_role("heading", name="original-incident.json", exact=True)
    ).to_be_visible()
    importer.get_by_role("button", name="Invoice addition regression", exact=False).click()
    top(importer)
    clip.frame(importer, "Inspect the redacted case. Observed output is not ground truth.", 1700)
    page.goto(importer.url, wait_until="networkidle")
    page.get_by_role("button", name="Invoice addition regression", exact=False).click()
    page.get_by_label("What should the correct behavior be?", exact=True).fill(
        "The total must include both amounts and equal 42."
    )
    page.get_by_label("Why is this expectation correct?", exact=True).fill(
        "Independent arithmetic check: 20 + 22 = 42."
    )
    page.get_by_label("Output field 1", exact=True).fill("total")
    page.get_by_label("Comparison 1", exact=True).select_option("numeric")
    page.get_by_label("Approved expected value 1", exact=True).fill("42")
    show(page, page.get_by_label("What should the correct behavior be?", exact=True))
    clip.frame(page, "A separate reviewer defines the correct behavior.", 1600)
    show(
        page, page.get_by_role("button", name="Validate and approve expected behavior", exact=True)
    )
    clip.frame(page, "Require checks that reject known incorrect outputs.", 1500)
    page.get_by_role("button", name="Validate and approve expected behavior", exact=True).click()
    expect(page.get_by_text("This case has an immutable approval.", exact=True)).to_be_visible()
    clip.frame(page, "Record an immutable approval with its rationale.", 1700)
    page.get_by_label("Collection name", exact=True).fill("Reviewed invoice incidents")
    show(page, page.get_by_role("button", name="Publish 1 approved case", exact=True))
    clip.frame(page, "Publish only approved cases with validated controls.", 1400)
    page.get_by_role("button", name="Publish 1 approved case", exact=True).click()
    page.get_by_role("link", name="Open the validated collection.", exact=True).click()
    expect(
        page.get_by_role("heading", name="Reviewed invoice incidents", exact=True)
    ).to_be_visible()
    top(page)
    clip.frame(page, "The incident is now a reusable regression benchmark.", 1900)
    clip.finish()


def capture(output: Path, channel: str) -> Path:
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
    result: dict[str, Any] = {"model_api_calls": 0, "fixture_only": True, "clips": []}
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Local capture server exited; inspect the private server log.")
            try:
                if httpx.get(base + "/api/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Local capture server did not become healthy.")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=channel,
                headless=True,
                args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
            )
            page = sign_in(browser, base, fixture["admin"], "/chat", errors)
            importer = sign_in(browser, base, fixture["importer"], "/cases", errors)
            for name, operation in [
                ("chat-and-reports", lambda: capture_chat(page, root, base, fixture["admin"])),
                ("rankings", lambda: capture_rankings(page, root, base)),
                ("tasks-and-comparisons", lambda: capture_authoring(page, root, base)),
                ("production-cases", lambda: capture_production(page, importer, root, base)),
            ]:
                operation()
                result["clips"].append(name)
                print("Captured " + name, flush=True)
            browser.close()
        if errors:
            raise RuntimeError("JavaScript errors occurred during capture.")
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
    parser.add_argument("--output", type=Path, default=ROOT / ".local/browser-demos")
    parser.add_argument("--channel", default="chrome")
    arguments = parser.parse_args()
    print(capture(arguments.output.resolve(), arguments.channel))


if __name__ == "__main__":
    main()
