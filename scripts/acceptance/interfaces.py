#!/usr/bin/env python3
"""Installed-wheel interface acceptance using loopback HTTP, Chromium, and Textual.

Run with the development environment (for httpx/Playwright), passing a separate
wheel-installed Python. All generated material stays under --output. No model
calls, external browser requests, customer repositories, or hosted services.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import socket
import subprocess
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRIVATE_MARKER = "EVALUATOR_ONLY_ACCEPTANCE_CANARY"
ROOT = Path(__file__).resolve().parents[2]


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def installed_identity() -> dict[str, Any]:
    import cvals

    path = Path(cvals.__file__).resolve()
    assert "site-packages" in path.parts, "Acceptance must import the installed wheel"
    return {
        "version": importlib.metadata.version("everval-ai"),
        "installed_package": "site-packages/cvals",
        "package_init_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "tui_styles_sha256": hashlib.sha256(
            (path.parent / "tui" / "workbench.tcss").read_bytes()
        ).hexdigest(),
    }


def seed() -> dict[str, Any]:
    from cvals.autopilot import Autopilot
    from cvals.config import Settings
    from cvals.engine import Engine
    from cvals.identity import IdentityStore
    from cvals.models import Check, Collection, ExperimentCreate, Scenario, System
    from cvals.research import ResearchLab
    from cvals.store import Store

    identity = installed_identity()
    settings = Settings.from_env()
    identities = IdentityStore(settings)
    admin = identities.bootstrap("Acceptance administrator")
    store = Store(settings)
    store.ensure_fixture_systems()
    scenarios = [
        Scenario(
            id=f"acceptance-total-{i}",
            name=f"Invoice total {i + 1} · installation control",
            domain="data",
            instruction="Return the sum of the invoice amounts in a total field.",
            inputs={"amounts": amounts},
            expected={"private_review_note": PRIVATE_MARKER},
            checks=[
                Check(
                    id="total",
                    name=PRIVATE_MARKER,
                    kind="numeric",
                    path="total",
                    expected=sum(amounts),
                )
            ],
            reference_output={"total": sum(amounts)},
            provenance={"private_note": PRIVATE_MARKER},
            tags=["installation-control"],
        )
        for i, amounts in enumerate(([20, 22], [30, 12], [15, 27]))
    ]
    store.save_collection(
        Collection(
            id="acceptance-dev",
            name="Invoice contracts · installation controls",
            benchmark_partition="dev",
            scenarios=scenarios,
        )
    )
    store.save_collection(
        Collection(
            id="acceptance-screen",
            name="Screening · installation controls",
            benchmark_partition="dev",
            scenarios=scenarios[:1],
        )
    )
    holdout = scenarios[0].model_copy(
        update={
            "id": "acceptance-holdout",
            "name": "Reserved total · installation control",
            "inputs": {"amounts": [11, 31]},
        }
    )
    store.save_collection(
        Collection(
            id="acceptance-holdout",
            name="Reserved tasks · installation controls",
            benchmark_partition="holdout",
            scenarios=[holdout],
        )
    )
    store.save_system(
        System(
            id="acceptance-config-only",
            name="Configuration form control · never called",
            kind="http",
            model="installation-control",
            base_url="http://127.0.0.1:9",
            input_cost_per_million=0,
            output_cost_per_million=0,
            pricing_version="free form control",
        )
    )
    runs = {}
    for name, collection_id in (("full", "acceptance-dev"), ("screen", "acceptance-screen")):
        experiment = store.create_experiment(
            ExperimentCreate(
                name=("Invoice comparison" if name == "full" else "Screening baseline")
                + " · installation controls",
                collection_id=collection_id,
                system_ids=["fixture-reference", "fixture-incomplete"],
            )
        )
        runs[name] = Engine(store, settings).run_experiment(experiment["id"])
    policy = Autopilot(settings).create(
        "Invoice regression watch · paused control",
        runs["full"]["id"],
        runs["screen"]["id"],
        lifetime_budget_usd=0,
        enabled=False,
    )
    failed = next(t for t in runs["full"]["trials"] if t["passed"] is False)
    research = ResearchLab(settings).repeat_regrade(runs["full"]["id"], failed["id"], 2)
    return {
        "identity": identity,
        "admin": admin,
        "experiment_id": runs["full"]["id"],
        "screen_id": runs["screen"]["id"],
        "failed_trial_id": failed["id"],
        "policy_id": policy["id"],
        "research_id": research["id"],
        "collection_id": "acceptance-dev",
    }


async def tui(output: Path) -> dict[str, Any]:
    from textual.widgets import Button, Input, Select, Static

    from cvals.config import Settings
    from cvals.tui import WorkbenchApp
    from cvals.tui.app import PAGES
    from cvals.tui.dialogs import EvidenceDialog, FormDialog

    installed_identity()
    results: list[dict[str, Any]] = []

    async def settle(app: Any, pilot: Any) -> None:
        # A mutation callback may enqueue the next page fetch after its first worker finishes.
        for _ in range(20):
            await pilot.pause(0.05)
            await app.workers.wait_for_complete()
            await pilot.pause(0.05)
            if not app._loading and not app._mutating:
                return
        raise TimeoutError("Textual workbench did not reach an idle rendered state")

    for width, height in ((136, 46), (80, 24)):
        app = WorkbenchApp(Settings.from_env())
        async with app.run_test(size=(width, height)) as pilot:
            await settle(app, pilot)
            for key, title, shortcut in PAGES:
                await pilot.press(shortcut.lower())
                await settle(app, pilot)
                assert app.page == key, (shortcut, app.page)
                assert str(app.query_one("#page-title", Static).render()) == title
                assert not app._loading
                screenshot = f"tui-{key}-{width}.svg"
                (output / screenshot).write_text(
                    app.export_screenshot(
                        title="Everval AI · " + title + " · installation controls"
                    )
                )
                results.append(
                    {
                        "kind": "view",
                        "name": key,
                        "width": width,
                        "height": height,
                        "screenshot": screenshot,
                        "passed": True,
                    }
                )
            for command in (
                "connect",
                "guide",
                "autopilot_new",
                "quality",
                "pack_stage",
                "optimize",
            ):
                app.command(command)
                await settle(app, pilot)
                assert isinstance(app.screen, FormDialog), command
                submit = app.screen.query_one("#form-submit", Button)
                assert submit.region.x >= 0 and submit.region.right <= width
                assert submit.region.y >= 0 and submit.region.bottom <= height, (
                    command,
                    submit.region,
                )
                first = app.screen.query_one("#field-" + app.screen.fields[0].key)
                if isinstance(first, Input):
                    first.value = ""
                await pilot.click("#form-submit")
                await pilot.pause()
                assert isinstance(app.screen, FormDialog), command
                assert str(app.screen.query_one("#form-error", Static).render()), command
                filename = f"tui-form-{command}-{width}.svg"
                (output / filename).write_text(
                    app.export_screenshot(
                        title="Everval AI · " + app.screen.dialog_title + " · installation controls"
                    )
                )
                results.append(
                    {
                        "kind": "form_validation",
                        "name": command,
                        "width": width,
                        "height": height,
                        "screenshot": filename,
                        "passed": True,
                    }
                )
                await pilot.press("escape")
                await settle(app, pilot)
            # A successful form mutation verifies persistence through the shared service.
            app.command("guide")
            await settle(app, pilot)
            app.screen.query_one("#field-name", Input).value = f"Acceptance guided run {width}"
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert app.page == "journey"
            assert app.guide_id
            results.append(
                {"kind": "form_submit", "name": "guided_run", "width": width, "passed": True}
            )
            await pilot.press("f11")
            await settle(app, pilot)
            app.command("budget")
            await settle(app, pilot)
            assert isinstance(app.screen, EvidenceDialog)
            assert "provider" in app.screen.text.lower()
            await pilot.press("escape")
            app.command("autopilot_new")
            await settle(app, pilot)
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-name", Input).value = f"Acceptance paused policy {width}"
            from cvals.store import Store

            experiments = Store(Settings.from_env()).list_experiments()
            full = next(e for e in experiments if e["collection_id"] == "acceptance-dev")
            screening = next(e for e in experiments if e["collection_id"] == "acceptance-screen")
            app.screen.query_one("#field-experiment_id", Select).value = full["id"]
            app.screen.query_one("#field-screening_experiment_id", Select).value = screening["id"]
            await pilot.click("#form-submit")
            await settle(app, pilot)
            assert app.page == "autopilot"
            assert any(
                r["name"] == f"Acceptance paused policy {width}" and not r["enabled"]
                for r in app.rows
            )
            results.append(
                {"kind": "form_submit", "name": "paused_autopilot", "width": width, "passed": True}
            )
    report = {
        "checks": results,
        "identity": installed_identity(),
        "scope": "Real Textual application via its event-driven Pilot renderer; separate tmux smoke checks the installed terminal entry point.",
    }
    save(output / "tui-report.json", report)
    return report


async def choice_tui(output: Path) -> dict[str, Any]:
    """Verify selected values remain visible in the installed optimization form."""
    from textual.widgets import Input, Select

    from cvals.config import Settings
    from cvals.tui import WorkbenchApp
    from cvals.tui.dialogs import FormDialog

    output.mkdir(parents=True, exist_ok=True)
    identity = installed_identity()
    checks: list[dict[str, Any]] = []
    for width, height in ((136, 46), (80, 24)):
        app = WorkbenchApp(Settings.from_env())
        async with app.run_test(size=(width, height)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("f12")
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.command("optimize")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            app.screen.query_one("#field-name", Input).value = "Invoice configuration proposal"
            for select in app.screen.query(Select):
                select.focus()
                select.scroll_visible()
                await pilot.pause()
                await pilot.press("enter", "down", "enter")
                await pilot.pause()
                assert select.value is not Select.BLANK
                assert select.content_region.contains_region(
                    select.query_one("SelectCurrent").content_region
                )
            selected = app.screen.query_one("#field-development_collection_id", Select)
            selected.focus()
            selected.scroll_visible()
            await pilot.pause()
            field = next(f for f in app.screen.fields if f.key == "development_collection_id")
            label = next(label for label, value in field.options if value == selected.value)
            svg = app.export_screenshot(
                title="Everval AI · Configuration proposal · installation controls"
            )
            assert label.replace(" ", "&#160;") in svg
            filename = f"tui-choice-fixed-{width}.svg"
            (output / filename).write_text(svg)
            checks.append(
                {
                    "name": "optimization choice text visible",
                    "status": "passed",
                    "width": width,
                    "height": height,
                    "screenshot": filename,
                }
            )
    report = {"status": "passed", "checks": checks, "installed_identity": identity}
    save(output / "results.json", report)
    return report


class Acceptance:
    def __init__(
        self,
        python: Path,
        output: Path,
        skip_browser: bool,
        browser_only: bool = False,
        browser_regrade_only: bool = False,
    ):
        self.python = python.absolute()
        self.output = output.absolute()
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.skip_browser = skip_browser
        self.browser_only = browser_only
        self.browser_regrade_only = browser_regrade_only
        self.environment = dict(os.environ)
        for key in ("PYTHONPATH", "EVERVAL_TOKEN", "CVALS_TOKEN", "NO_COLOR"):
            self.environment.pop(key, None)
        self.environment.update(
            {
                "EVERVAL_DATA_DIR": str(self.output / "workspace"),
                "CVALS_DATA_DIR": str(self.output / "workspace"),
                "EVERVAL_ALLOW_REMOTE_PROVIDERS": "0",
                "CVALS_ALLOW_REMOTE_PROVIDERS": "0",
                "COLORTERM": "truecolor",
                "TERM": "xterm-256color",
            }
        )
        self.secrets: list[str] = []
        self.report: dict[str, Any] = {
            "schema_version": 1,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "checks": [],
            "browser_page_errors": [],
            "browser_external_requests": [],
            "browser_failed_requests": [],
            "provider_calls": 0,
            "fixture_scope": "Original installation controls; no customer/model performance claims",
            "limits": [
                "One local workspace and installed wheel on this host; not a cross-platform certification.",
                "Textual views use its real event renderer plus an actual tmux launch; no manual usability study.",
                "No paid inference, external agent performance, Docker execution, or external identity provider tested here.",
                "Browser and API role checks do not restrict trusted filesystem or terminal administrators.",
            ],
        }
        self.server: subprocess.Popen[Any] | None = None
        self.base = ""

    def check(self, category: str, name: str, **details: Any) -> None:
        self.report["checks"].append(
            {
                "category": category,
                "name": name,
                "status": "passed",
                "passed": True,
                "evidence": details,
            }
        )
        print(f"PASS {category}: {name}", flush=True)

    def child(self, *args: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(self.python), *args],
            cwd=self.output,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(
                f"Installed process failed ({result.returncode}): {self.scrub(result.stderr[-4000:])}"
            )
        return result

    def scrub(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[TOKEN REDACTED]")
        return value.replace(str(self.output), "[ACCEPTANCE_ARTIFACTS]")

    def cli(self) -> None:
        for arguments in (
            ["--help"],
            ["research", "--help"],
            ["optimize", "--help"],
            ["autopilot", "--help"],
            ["packs", "--help"],
            ["budget", "status"],
        ):
            result = self.child("-m", "cvals", *arguments)
            assert result.stdout.strip()
            self.check("cli", " ".join(arguments))
        result = self.child(
            "-m", "cvals", "demo", "--workspace", str(self.output / "cli-demo"), "--json"
        )
        demo = json.loads(result.stdout)
        save(self.output / "cli-demo-result.json", demo)
        self.check("cli", "free demo", result="completed original installation controls")

    def start_server(self) -> None:
        import httpx

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        log = (self.output / "server.log").open("w")
        self.server = subprocess.Popen(
            [
                str(self.python),
                "-m",
                "cvals",
                "serve",
                "--no-worker",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=self.output,
            env=self.environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError("Installed API server exited before readiness")
            try:
                response = httpx.get(self.base + "/api/session", timeout=1, trust_env=False)
                if response.status_code == 401:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise TimeoutError("Installed API server did not become ready")
        response = httpx.get(self.base + "/", timeout=10, trust_env=False)
        assert response.status_code == 200 and "<html" in response.text.lower()
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', response.text)
        assert assets
        for asset in assets:
            assert httpx.get(self.base + asset, timeout=10, trust_env=False).status_code == 200
        self.check("http", "installed wheel serves API and bundled frontend", assets=len(assets))

    def http(self, seed_data: dict[str, Any]) -> dict[str, Any]:
        import httpx

        admin_token = seed_data["admin"]["token"]
        self.secrets.append(admin_token)
        client = httpx.Client(base_url=self.base, timeout=15, trust_env=False)

        def request(method: str, path: str, token: str | None, expected: int, **kwargs: Any) -> Any:
            headers = {"Authorization": "Bearer " + token} if token else {}
            response = client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == expected, (
                f"{method} {path}: expected {expected}, got {response.status_code}"
            )
            return response.json() if response.content else None

        tokens = {"admin": admin_token}
        identities = {}
        token_ids = {}
        for role in ("operator", "reviewer", "viewer"):
            user = request(
                "POST",
                "/api/team/users",
                admin_token,
                201,
                json={
                    "name": "Acceptance " + role,
                    "role": role,
                },
            )
            issued = request(
                "POST",
                f"/api/team/users/{user['id']}/tokens",
                admin_token,
                201,
                json={"label": "Interface acceptance", "days": 1},
            )
            identities[role] = user
            tokens[role] = issued["token"]
            self.secrets.append(issued["token"])
            token_ids[role] = issued.get("id", issued.get("token_id"))
        request("GET", "/api/session", None, 401)
        request("GET", "/api/session", "invalid-token", 401)
        self.check("http", "unauthenticated and invalid tokens rejected")
        private_paths = (
            "/api/collections",
            "/api/experiments",
            "/api/systems",
            "/api/settings",
            "/api/autopilot",
            "/api/research",
            "/api/optimization",
            "/api/budget/status",
            "/api/community/packs",
            f"/api/experiments/{seed_data['experiment_id']}",
        )
        for role, token in tokens.items():
            session = request("GET", "/api/session", token, 200)
            assert session["role"] == role
            for path in private_paths:
                request("GET", path, token, 200 if role in {"admin", "operator"} else 403)
            request("GET", "/api/team/users", token, 200 if role == "admin" else 403)
            request(
                "POST",
                "/api/autopilot",
                token,
                422 if role in {"admin", "operator"} else 403,
                json={},
            )
            self.check("http", role + " capability matrix", assertions=len(private_paths) + 3)
        summary = request("GET", "/api/team/summary", tokens["viewer"], 200)
        assert PRIVATE_MARKER not in json.dumps(summary)
        assert "reference_output" not in json.dumps(summary)
        self.check("http", "viewer summary excludes task and grader content")
        for role in ("viewer", "reviewer"):
            request("POST", "/api/community/packs", tokens[role], 403, content=b"invalid")
            request(
                "POST",
                f"/api/collections/{seed_data['collection_id']}/quality",
                tokens[role],
                403,
                json={},
            )
        self.check("http", "authorization precedes mutation payload parsing", assertions=4)
        request(
            "PATCH",
            f"/api/team/users/{identities['viewer']['id']}",
            admin_token,
            200,
            json={"active": False},
        )
        request("GET", "/api/session", tokens["viewer"], 401)
        request(
            "PATCH",
            f"/api/team/users/{identities['viewer']['id']}",
            admin_token,
            200,
            json={"active": True},
        )
        request("GET", "/api/session", tokens["viewer"], 200)
        self.check("http", "disabled identity denied and explicitly reactivated")
        client.close()
        return {"tokens": tokens, "users": identities, "token_ids": token_ids}

    def tmux(self) -> None:
        executable = shutil.which("tmux")
        if not executable:
            raise RuntimeError("tmux is required for this acceptance profile")
        socket_path = self.output / "tmux.sock"
        prefix = [executable, "-S", str(socket_path)]
        try:
            subprocess.run(
                prefix
                + [
                    "new-session",
                    "-d",
                    "-s",
                    "acceptance",
                    "-x",
                    "136",
                    "-y",
                    "46",
                    str(self.python),
                    "-m",
                    "cvals",
                    "tui",
                    "--no-worker",
                ],
                cwd=self.output,
                env=self.environment,
                check=True,
                capture_output=True,
                timeout=20,
            )
            text = ""
            for _ in range(60):
                captured = subprocess.run(
                    prefix + ["capture-pane", "-p", "-t", "acceptance"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                text = captured.stdout
                if "Overview" in text and "everval" in text.lower():
                    break
                time.sleep(0.5)
            (self.output / "tmux-overview.txt").write_text(text)
            assert "Overview" in text and "everval" in text.lower(), (
                "Installed tmux TUI did not render"
            )
            subprocess.run(
                prefix + ["send-keys", "-t", "acceptance", "F12"],
                check=True,
                capture_output=True,
                timeout=5,
            )
            for _ in range(30):
                text = subprocess.run(
                    prefix + ["capture-pane", "-p", "-t", "acceptance"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout
                if "Research lab" in text:
                    break
                time.sleep(0.25)
            assert "Research lab" in text
            (self.output / "tmux-research.txt").write_text(text)
            self.check("tmux", "installed terminal launch and F12 navigation", size="136x46")
        finally:
            subprocess.run(prefix + ["kill-server"], capture_output=True, timeout=10)

    def browser(self, seed_data: dict[str, Any], team: dict[str, Any]) -> None:
        import httpx
        from playwright.sync_api import expect, sync_playwright

        expect.set_options(timeout=30000)
        expected_responses: list[dict[str, Any]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="chromium", headless=True, args=["--disable-gpu"]
            )
            contexts = []

            def context(token: str, route: str = "/") -> Any:
                current = browser.new_context(
                    viewport={"width": 1440, "height": 1050}, color_scheme="light"
                )
                contexts.append(current)

                def network(route_obj: Any) -> None:
                    if route_obj.request.url.startswith(self.base + "/"):
                        route_obj.continue_()
                    else:
                        self.report["browser_external_requests"].append("blocked external request")
                        route_obj.abort()

                current.route("**/*", network)
                page = current.new_page()
                page.set_default_timeout(20000)
                page.on(
                    "pageerror",
                    lambda error: self.report["browser_page_errors"].append(self.scrub(str(error))),
                )
                page.on(
                    "requestfailed",
                    lambda request: self.report["browser_failed_requests"].append(
                        {
                            "resource_type": request.resource_type,
                            "path": request.url.split(self.base, 1)[-1].split("?", 1)[0],
                            "failure": request.failure,
                        }
                    ),
                )
                page.goto(self.base + "/#" + route)
                page.get_by_label("Access token", exact=True).fill(token)
                page.get_by_role("button", name="Connect to workspace", exact=True).click()
                expect(page.get_by_role("dialog")).to_have_count(0)
                return page

            def capture(page: Any, filename: str, mobile: bool = False) -> None:
                expect(page.get_by_text(re.compile(r"^Loading workspace"))).to_have_count(0)
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path=str(self.output / filename), full_page=not mobile, animations="disabled"
                )
                if mobile:
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    ), "Mobile horizontal overflow"
                self.report.setdefault("screenshots", []).append(filename)

            def investigate(operator: Any) -> None:
                operator.goto(self.base + "/#/experiments/" + seed_data["experiment_id"])
                expect(
                    operator.get_by_role(
                        "heading", name="Invoice comparison · installation controls", exact=True
                    )
                ).to_be_visible()
                operator.get_by_role("tab", name="Value analysis", exact=True).click()
                expect(
                    operator.get_by_role("heading", name="Quality, cost, and time", exact=True)
                ).to_be_visible()
                expect(
                    operator.get_by_text("Insufficient eligible evidence", exact=True)
                ).to_have_count(2)
                capture(operator, "browser-value-analysis.png")
                operator.get_by_role("tab", name="Failure inbox", exact=True).click()
                operator.get_by_role(
                    "button", name="Inspect this attempt", exact=True
                ).first.click()
                regrade_button = operator.get_by_role(
                    "button", name="Regrade recorded output · no model call", exact=True
                )
                expect(regrade_button).to_be_visible()
                retained = (
                    operator.get_by_role("heading", name="Retained regrades", exact=True)
                    .locator("..")
                    .locator("p")
                )
                before = retained.count()
                with operator.expect_response(
                    lambda response: (
                        response.url.endswith("/regrade") and response.request.method == "POST"
                    )
                ) as regraded:
                    regrade_button.click()
                assert regraded.value.status == 201, f"Regrade HTTP status {regraded.value.status}"
                expect(retained).to_have_count(before + 1)
                expect(regrade_button).to_be_enabled()
                capture(operator, "browser-failure-investigation.png")
                operator.set_viewport_size({"width": 390, "height": 844})
                capture(operator, "browser-failure-investigation-mobile.png", mobile=True)
                self.check(
                    "browser",
                    "installation evidence is excluded from economics; recorded output regrades without calls",
                    retained_regrades_before=before,
                    retained_regrades_after=before + 1,
                    mutation_http_status=regraded.value.status,
                )

            try:
                if self.browser_regrade_only:
                    self.report["limits"].append(
                        "Focused browser run covers economics, recorded-output regrade completion, and desktop/mobile rendering; other browser journeys omitted."
                    )
                    investigate(
                        context(
                            team["tokens"]["operator"], "/experiments/" + seed_data["experiment_id"]
                        )
                    )
                    assert not self.report["browser_page_errors"]
                    assert not self.report["browser_external_requests"]
                    self.check(
                        "browser",
                        "focused regrade has no JavaScript exceptions or external requests",
                    )
                    return
                admin = context(team["tokens"]["admin"], "/team")
                expect(admin.get_by_role("heading", name="Team access", exact=True)).to_be_visible()
                admin.get_by_label("Display name", exact=True).fill("Browser reviewer")
                admin.get_by_role("button", name="Create identity", exact=True).click()
                expect(
                    admin.get_by_label("Identity", exact=True).locator(
                        "option", has_text="Browser reviewer"
                    )
                ).to_have_count(1)
                admin.get_by_label("Identity", exact=True).select_option(
                    label="Browser reviewer · reviewer"
                )
                admin.get_by_label("Token label", exact=True).fill("Browser acceptance token")
                admin.get_by_role("button", name="Issue token once", exact=True).click()
                field = admin.get_by_label("New personal token", exact=True)
                expect(field).to_be_visible()
                reviewer_token = field.input_value()
                self.secrets.append(reviewer_token)
                admin.get_by_role("button", name="I saved the token securely", exact=True).click()
                expect(field).to_have_count(0)
                capture(admin, "browser-team-access.png")
                self.check("browser", "administrator creates identity and handles one-time token")

                operator = context(team["tokens"]["operator"], "/reviews/queues/new")
                expect(operator.get_by_label("Queue name", exact=True)).to_be_visible()
                expect(operator.get_by_role("link", name="Team access", exact=True)).to_have_count(
                    0
                )
                operator.get_by_label("Queue name", exact=True).fill(
                    "Invoice correctness · installation control review"
                )
                operator.get_by_label("Completed experiment", exact=True).select_option(
                    label="Invoice comparison · installation controls"
                )
                operator.get_by_role(
                    "checkbox", name="Reference fixture · control", exact=True
                ).check()
                operator.get_by_role(
                    "checkbox", name="Empty submission · control", exact=True
                ).check()
                operator.get_by_role("button", name="Create a rubric", exact=True).click()
                operator.get_by_label("Rubric name", exact=True).fill("Numerical correctness")
                operator.get_by_label("Reviewer instructions", exact=True).fill(
                    "Check whether each response solves the task using its public inputs."
                )
                operator.get_by_label("Dimension 1 name", exact=True).fill("Correctness")
                operator.get_by_label("Dimension 1 scoring guidance", exact=True).fill(
                    "Zero means incorrect or missing; one means the exact total."
                )
                operator.get_by_role("button", name="Save frozen rubric", exact=True).click()
                expect(
                    operator.get_by_label("Frozen rubric", exact=True).locator(
                        "option", has_text="Numerical correctness"
                    )
                ).to_have_count(1)
                operator.get_by_role("checkbox", name=re.compile("Browser reviewer")).check()
                operator.get_by_role("button", name="Freeze and assign queue", exact=True).click()
                expect(
                    operator.get_by_role(
                        "heading",
                        name="Invoice correctness · installation control review",
                        exact=True,
                    )
                ).to_be_visible()
                queue_id = operator.url.rsplit("/", 1)[1]
                self.check("browser", "operator freezes rubric and assigns blind comparison queue")

                reviewer = context(reviewer_token, "/reviews")
                expect(
                    reviewer.get_by_role("heading", name="Your review desk", exact=True)
                ).to_be_visible()
                expect(reviewer.get_by_role("link", name="Collections", exact=True)).to_have_count(
                    0
                )
                reviewer.get_by_role("link", name=re.compile("Invoice total 1")).click()
                expect(
                    reviewer.get_by_role(
                        "heading", name="Invoice total 1 · installation control", exact=True
                    )
                ).to_be_visible()
                item_id = reviewer.url.rsplit("/", 1)[1]
                with httpx.Client(base_url=self.base, timeout=10, trust_env=False) as client:
                    response = client.get(
                        "/api/review-items/" + item_id,
                        headers={"Authorization": "Bearer " + reviewer_token},
                    )
                    assert response.status_code == 200
                    payload = response.text
                    for forbidden in (
                        PRIVATE_MARKER,
                        "fixture-reference",
                        "fixture-incomplete",
                        "reference_output",
                        "system_id",
                        "provider_metadata",
                    ):
                        assert forbidden not in payload, (
                            "Blind response exposed a private field or identity"
                        )
                    for role in ("reviewer", "viewer"):
                        denied = client.get(
                            "/api/review-items/" + item_id,
                            headers={"Authorization": "Bearer " + team["tokens"][role]},
                        )
                        assert denied.status_code == (404 if role == "reviewer" else 403), (
                            "Unassigned identity accessed private review item"
                        )
                    expected_responses.append(
                        {"check": "blind payload private boundaries", "status": "passed"}
                    )
                text = reviewer.locator("body").inner_text()
                assert (
                    PRIVATE_MARKER not in text
                    and "Reference fixture" not in text
                    and "Empty submission" not in text
                )
                capture(reviewer, "browser-blind-review-desktop.png")
                panels = reviewer.locator(".review-responses .panel").all_inner_texts()
                selected = "A" if "42" in panels[0] else "B"
                for label in ("A", "B"):
                    reviewer.get_by_label(
                        f"Response {label} · Correctness", exact=True
                    ).select_option("1" if label == selected else "0")
                reviewer.get_by_label(
                    "Which response better satisfies the task?", exact=True
                ).select_option(selected)
                reviewer.get_by_label("Overall judgment", exact=True).select_option("acceptable")
                reviewer.get_by_label("Explain your judgment", exact=True).fill(
                    "The selected response returns the verified sum of 20 and 22. This is an installation-control review."
                )
                reviewer.get_by_role("button", name="Record judgment", exact=True).click()
                expect(reviewer.get_by_text("Review recorded.", exact=False)).to_be_visible()
                reviewer.set_viewport_size({"width": 390, "height": 844})
                capture(reviewer, "browser-blind-review-mobile.png", mobile=True)
                self.check(
                    "browser",
                    "assigned reviewer submits blind judgment; server strips private material",
                    assertions=8,
                )

                operator.goto(self.base + "/#/reviews/queues/" + queue_id)
                operator.reload()
                expect(operator.get_by_text("1 / 3", exact=True)).to_be_visible()
                operator.get_by_label("Decision rationale", exact=True).fill(
                    "Installation acceptance retained one human judgment; representative customer validation remains required."
                )
                operator.get_by_role(
                    "button", name="Record decision with evidence", exact=True
                ).click()
                expect(operator.locator(".decision-record")).to_have_count(1)
                capture(operator, "browser-review-decision.png")
                self.check("browser", "operator records decision against displayed evidence")

                viewer = context(team["tokens"]["viewer"], "/reviews/queues/" + queue_id)
                expect(
                    viewer.get_by_role(
                        "heading",
                        name="Invoice correctness · installation control review",
                        exact=True,
                    )
                ).to_be_visible()
                expect(viewer.get_by_label("Decision rationale", exact=True)).to_have_count(0)
                assert PRIVATE_MARKER not in viewer.locator("body").inner_text()
                viewer.goto(self.base + "/#/collections")
                expect(
                    viewer.get_by_text("This page is unavailable for your role.", exact=False)
                ).to_be_visible()
                self.check(
                    "browser", "viewer sees summaries and cannot open private workspace routes"
                )

                investigate(operator)

                admin.goto(self.base + "/#/team")
                row = admin.locator(
                    ".team-account-row",
                    has=admin.get_by_text("Browser acceptance token", exact=True),
                )
                row.get_by_role("button", name="Revoke token", exact=True).click()
                expect(row.get_by_text("revoked", exact=True)).to_be_visible()
                reviewer.reload()
                expect(reviewer.get_by_role("dialog")).to_be_visible()
                with httpx.Client(base_url=self.base, timeout=10, trust_env=False) as client:
                    revoked = client.get(
                        "/api/session", headers={"Authorization": "Bearer " + reviewer_token}
                    )
                    assert revoked.status_code == 401
                self.check("browser", "revoked reviewer loses browser and HTTP access")

                assert not self.report["browser_page_errors"], "Browser JavaScript exception"
                assert not self.report["browser_external_requests"], (
                    "Unexpected external browser request"
                )
                self.check(
                    "browser",
                    "no JavaScript exceptions or external requests",
                    requests=expected_responses,
                )
                render_context = browser.new_context()
                render_context.route("**/*", lambda route: route.abort())
                render = render_context.new_page()
                for filename in (
                    "tui-autopilot-136.svg",
                    "tui-lab-136.svg",
                    "tui-form-optimize-136.svg",
                    "tui-autopilot-80.svg",
                ):
                    source = self.output / filename
                    if not source.exists():
                        continue
                    svg = re.sub(r"@font-face\s*\{.*?\}", "", source.read_text(), flags=re.DOTALL)
                    svg = svg.replace(
                        "font-family: Fira Code, monospace;", "font-family: Menlo, monospace;"
                    )
                    dimensions = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
                    assert dimensions
                    width, height = [int(float(part) + 1) for part in dimensions.groups()]
                    render.set_viewport_size({"width": width, "height": height})
                    render.set_content(
                        "<style>body{margin:0;background:#0b1118}svg{display:block}</style>" + svg
                    )
                    destination = filename.replace(".svg", ".png")
                    render.screenshot(
                        path=str(self.output / destination), full_page=True, animations="disabled"
                    )
                    self.report.setdefault("screenshots", []).append(destination)
                render_context.close()
                self.check(
                    "visual",
                    "Textual SVGs rendered with local Menlo; original SVGs retained",
                    external_fonts_downloaded=False,
                )
            except BaseException:
                for index, current in enumerate(contexts):
                    for page in current.pages:
                        try:
                            page.screenshot(
                                path=str(self.output / f"browser-failure-{index}.png"),
                                full_page=True,
                                mask=[
                                    page.locator('input[type="password"]'),
                                    page.get_by_label("New personal token", exact=True),
                                    page.get_by_label("Access token", exact=True),
                                ],
                                timeout=5000,
                            )
                            (self.output / f"browser-failure-{index}.txt").write_text(
                                self.scrub(page.locator("body").inner_text(timeout=5000))
                            )
                        except Exception:
                            pass
                raise
            finally:
                browser.close()

    def run(self) -> dict[str, Any]:
        try:
            if not self.browser_only:
                self.cli()
            seeded = json.loads(
                self.child(str(Path(__file__).resolve()), "--internal", "seed").stdout
            )
            self.secrets.append(seeded["admin"]["token"])
            self.report["installed_identity"] = seeded["identity"]
            self.check("fixture", "seed original controls and private grader canary")
            if not self.browser_only:
                self.child(
                    str(Path(__file__).resolve()),
                    "--internal",
                    "tui",
                    "--output",
                    str(self.output),
                    timeout=300,
                )
                tui_report = json.loads((self.output / "tui-report.json").read_text())
                self.report["tui_checks"] = tui_report["checks"]
                self.check(
                    "tui",
                    "12 views and six forms at two terminal sizes plus submitted workflows",
                    checks=len(tui_report["checks"]),
                )
                self.tmux()
            else:
                self.report["limits"].append(
                    "Browser-only run: CLI, Textual, and tmux checks omitted; HTTP identity setup and boundaries retained."
                )
            self.start_server()
            team = self.http(seeded)
            if not self.skip_browser:
                self.browser(seeded, team)
            else:
                self.report["limits"].append("Browser stage intentionally omitted in this run.")
            import httpx

            ledger = httpx.get(
                self.base + "/api/budget/status",
                timeout=10,
                trust_env=False,
                headers={"Authorization": "Bearer " + team["tokens"]["admin"]},
            )
            assert ledger.status_code == 200
            assert ledger.json()["provider_admissions"] == 0
            assert ledger.json()["known_provider_cost_usd"] == 0
            self.check(
                "accounting",
                "workspace ledger confirms zero provider admissions",
                provider_admissions=0,
                known_provider_cost_usd=0,
            )
            self.report["status"] = "passed"
        except BaseException as error:
            self.report["status"] = "failed"
            self.report["error_type"] = type(error).__name__
            self.report["error"] = self.scrub(str(error))
            (self.output / "failure-traceback.txt").write_text(self.scrub(traceback.format_exc()))
            raise
        finally:
            if self.server is not None:
                self.server.terminate()
                try:
                    self.server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.server.kill()
                    self.server.wait(timeout=5)
            self.report["finished_at"] = datetime.now(UTC).isoformat()
            self.report["check_count"] = len(self.report["checks"])
            save(self.output / "result.json", self.report)
            save(self.output / "results.json", self.report)
        return self.report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-python", type=Path)
    parser.add_argument("--output", type=Path, required=False)
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument("--skip-browser", action="store_true")
    stages.add_argument("--browser-only", action="store_true")
    stages.add_argument("--browser-regrade-only", action="store_true")
    parser.add_argument("--internal", choices=("seed", "tui", "choice-tui"))
    args = parser.parse_args()
    if args.internal == "seed":
        print(json.dumps(seed()))
        return
    if args.internal == "tui":
        assert args.output is not None
        print(json.dumps(asyncio.run(tui(args.output))))
        return
    if args.internal == "choice-tui":
        assert args.output is not None
        print(json.dumps(asyncio.run(choice_tui(args.output))))
        return
    if args.installed_python is None or args.output is None:
        parser.error("--installed-python and --output are required")
    result = Acceptance(
        args.installed_python,
        args.output,
        args.skip_browser,
        args.browser_only or args.browser_regrade_only,
        args.browser_regrade_only,
    ).run()
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": result["check_count"],
                "browser": not args.skip_browser,
                "provider_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
