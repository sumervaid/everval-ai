"""Record continuous terminal sessions through a real PTY and local xterm renderer.

The application runs normally in an isolated fixture workspace. Its actual ANSI
output is streamed to a local browser terminal while Playwright records video.
No screenshots are sampled and no UI states are reconstructed for the animation.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hashlib
import io
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import termios
import time
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import Request
from motion import CURSOR_SCRIPT
from playwright.async_api import Page, async_playwright

from cvals.autopilot import Autopilot
from cvals.config import Settings
from cvals.demo import run_demo
from cvals.engine import Engine
from cvals.models import ExperimentCreate, System
from cvals.store import Store
from cvals.workbench import Workbench

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT, COLS, ROWS = 1280, 800, 120, 38
XTERM_URL = "https://registry.npmjs.org/@xterm/xterm/-/xterm-5.5.0.tgz"
XTERM_SHA256 = "bd954fa721872170188cc5d7e83e88db3c83c9a18a4e8d24c2783d26491f59d2"
FIXTURE_NOTE = (
    "Continuous recording of the actual terminal application with original controls. "
    "No model calls or model-performance claims. Repository validation remains pending."
)
TERMINAL_HTML = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/xterm.css"><style>
html,body{margin:0;width:100%;height:100%;background:#0d1722;overflow:hidden}
#terminal{position:absolute;left:26px;top:20px}
.xterm .xterm-viewport{overflow:hidden!important}
</style></head><body><div id="terminal"></div><script src="/xterm.js"></script><script>
const term = new Terminal({cols:120,rows:38,fontFamily:'Menlo, monospace',
fontSize:17,lineHeight:1,cursorBlink:true,scrollback:100,
theme:{background:'#0d1722',foreground:'#dce5ea',cursor:'#83d7c7'},
allowTransparency:false,convertEol:false});
window.captureTerminal=term;term.open(document.getElementById('terminal'));term.focus();
const session=crypto.randomUUID();
const events=new EventSource('/stream/'+session);
events.onmessage=e=>term.write(Uint8Array.from(atob(e.data),c=>c.charCodeAt(0)));
let pending=Promise.resolve();
term.onData(data=>{pending=pending.then(()=>fetch('/input/'+session,{method:'POST',body:data}));});
window.terminalText=()=>Array.from({length:term.rows},(_,i)=>
term.buffer.active.getLine(term.buffer.active.viewportY+i)?.translateToString(true)||'');
</script></body></html>"""


def prepare_assets(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if all((directory / name).is_file() for name in ("xterm.js", "xterm.css", "LICENSE")):
        return
    with urllib.request.urlopen(XTERM_URL, timeout=30) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != XTERM_SHA256:
        raise ValueError("The pinned terminal renderer archive did not match its digest")
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for name in ("package/lib/xterm.js", "package/css/xterm.css", "package/LICENSE"):
            source = bundle.extractfile(name)
            if source is None:
                raise ValueError("Missing terminal renderer asset")
            (directory / Path(name).name).write_bytes(source.read())


def serve(workspace: Path, port: int, assets: Path) -> None:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, HTMLResponse

    app = FastAPI()

    @app.get("/")
    def index():
        return HTMLResponse(TERMINAL_HTML)

    @app.get("/xterm.js")
    def javascript():
        return FileResponse(assets / "xterm.js", media_type="text/javascript")

    @app.get("/xterm.css")
    def stylesheet():
        return FileResponse(assets / "xterm.css", media_type="text/css")

    sessions: dict[str, int] = {}

    @app.post("/input/{identifier}")
    async def input_data(identifier: str, request: Request):
        master = sessions.get(identifier)
        if master is not None:
            os.write(master, await request.body())
        return {"accepted": master is not None}

    @app.get("/stream/{identifier}")
    async def terminal(identifier: str):
        from fastapi.responses import StreamingResponse

        async def stream():
            master, slave = os.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            environment = {
                **os.environ,
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
                "PYTHONUNBUFFERED": "1",
                "USER": "operator",
                "LOGNAME": "operator",
            }
            code = (
                "from pathlib import Path;import sys;"
                "from cvals.tui import WorkbenchApp;from cvals.config import Settings;"
                "WorkbenchApp(Settings(Path(sys.argv[1]),allow_remote_providers=False)).run()"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(workspace)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=ROOT,
                env=environment,
                start_new_session=True,
            )
            os.close(slave)
            os.set_blocking(master, False)
            sessions[identifier] = master
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()

            def readable() -> None:
                try:
                    data = os.read(master, 65536)
                except BlockingIOError:
                    return
                except OSError:
                    data = b""
                queue.put_nowait(data or None)
                if not data:
                    loop.remove_reader(master)

            loop.add_reader(master, readable)
            try:
                while (data := await queue.get()) is not None:
                    yield "data:" + base64.b64encode(data).decode() + chr(10) + chr(10)
            finally:
                loop.remove_reader(master)
                sessions.pop(identifier, None)
                os.close(master)
                if child.poll() is None:
                    child.terminate()
                    try:
                        await asyncio.to_thread(child.wait, timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        await asyncio.to_thread(child.wait)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


async def wait_text(page: Page, text: str, timeout: int = 10000) -> None:
    await page.wait_for_function(
        "(text)=>window.terminalText&&window.terminalText().join('\\n').includes(text)",
        arg=text,
        timeout=timeout,
    )


async def pause(page: Page, seconds: float = 0.6) -> None:
    await page.wait_for_timeout(seconds * 1000)


async def type_text(page: Page, text: str, delay: int = 52) -> None:
    await page.keyboard.type(text, delay=delay)


async def escape(page: Page) -> None:
    await page.keyboard.press("Escape")
    # ANSI Escape is ambiguous with an Alt prefix; let the real terminal parser
    # dispatch it before the next keystroke.
    await pause(page, 0.38)


async def command(page: Page, label: str) -> None:
    await page.keyboard.press("Control+p")
    await pause(page, 0.25)
    await type_text(page, label)
    await pause(page, 0.35)
    await page.keyboard.press("Enter")
    await pause(page, 0.65)


async def click_text(
    page: Page, text: str, *, last: bool = False, same_line: str | None = None
) -> None:
    matches = await page.evaluate(
        """({text,sameLine})=>window.terminalText().flatMap((line,row)=>{
          const col=line.indexOf(text);
          return col<0||(sameLine&&!line.includes(sameLine))?[]:[{row,col}];})""",
        {"text": text, "sameLine": same_line},
    )
    if not matches:
        raise AssertionError("Terminal text not visible: " + text)
    match = matches[-1] if last else matches[0]
    box = await page.locator(".xterm-screen").bounding_box()
    if box is None:
        raise AssertionError("Terminal screen is not visible")
    x = box["x"] + (match["col"] + len(text) / 2) * box["width"] / COLS
    y = box["y"] + (match["row"] + 0.5) * box["height"] / ROWS
    pointer = await page.locator("#demo-pointer").bounding_box()
    start = (pointer["x"], pointer["y"]) if pointer else (120, 76)
    for step in range(1, 25):
        t = step / 24
        eased = t * t * (3 - 2 * t)
        await page.mouse.move(start[0] + (x - start[0]) * eased, start[1] + (y - start[1]) * eased)
        await pause(page, 0.013)
    await pause(page, 0.12)
    await page.mouse.down()
    await pause(page, 0.07)
    await page.mouse.up()
    await pause(page, 0.5)


async def select_row(page: Page, text: str) -> None:
    await click_text(page, text)
    await pause(page, 0.45)


def seed(output: Path) -> tuple[Settings, dict[str, Any]]:
    demo = run_demo(Settings(output / "base"), workspace=output / "workspace")
    settings = Settings(output / "workspace", allow_remote_providers=False)
    store = Store(settings)
    engine = Engine(store, settings)
    full = store.create_experiment(
        ExperimentCreate(
            name="Fixture full baseline",
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
    autopilot.configure(policy["id"], enabled=True)
    store.save_system(
        System(
            id="demo-control",
            name="Installation fixture",
            kind="incomplete",
            provenance={"purpose": "installation-control", "demo_phase": "regression"},
        )
    )
    due = autopilot.get(policy["id"])["next_due_epoch"] + 1
    engine.run_experiment(autopilot.tick(due)[0])
    engine.run_experiment(autopilot.tick(due + 61)[0])
    autopilot.tick(due + 122)
    autopilot.configure(policy["id"], enabled=False)
    return settings, {"demo": demo, "policy": policy}


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


async def workbench(page: Page, _: Path) -> None:
    await page.keyboard.press("F4")
    await pause(page, 1)
    await command(page, "Trial evidence")
    await pause(page, 1.2)
    await page.keyboard.press("ArrowDown")
    await pause(page, 0.5)
    await page.keyboard.press("ArrowDown")
    await pause(page, 0.6)
    await command(page, "Back")
    await page.keyboard.press("F5")
    await pause(page, 1)
    await command(page, "Model rankings")
    await pause(page, 1)
    await page.keyboard.press("2")
    await pause(page, 0.8)
    await page.keyboard.press("3")
    await pause(page, 0.8)
    await page.keyboard.press("1")
    await pause(page, 0.8)
    await page.keyboard.press("F6")
    await pause(page, 1.2)
    await page.keyboard.press("F4")
    await pause(page, 1.5)


async def repository(page: Page, repository: Path) -> None:
    await command(page, "Connect repository")
    await wait_text(page, "GitHub URL or local path")
    await type_text(page, str(repository), delay=42)
    await pause(page, 0.5)
    await click_text(page, "Connect repository")
    await pause(page, 1.2)
    await command(page, "Discover")
    await pause(page, 0.7)
    await click_text(page, "Discover", same_line="Cancel")
    await wait_text(page, "Candidate ledger")
    await pause(page, 1.4)
    await page.keyboard.press("ArrowDown")
    await pause(page, 0.8)
    await command(page, "Start guide")
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Control+k")
    await type_text(page, "Support toolkit review", delay=60)
    await page.keyboard.press("Tab")
    await type_text(page, str(repository), delay=32)
    await pause(page, 0.6)
    await click_text(page, "Start guide", same_line="Cancel")
    await wait_text(page, "First real evaluation")
    await pause(page, 1.5)


async def automation(page: Page, _: Path) -> None:
    await page.keyboard.press("F11")
    await pause(page, 1)
    await command(page, "New policy")
    await wait_text(page, "Policy name")
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Control+k")
    await type_text(page, "Fortnightly support checks", delay=55)
    await pause(page, 0.5)
    await page.keyboard.press("Tab")
    await page.keyboard.press("Enter")
    await pause(page, 0.5)
    await escape(page)
    await escape(page)
    await pause(page, 0.6)
    await command(page, "Budget ledger")
    await pause(page, 1.2)
    await page.keyboard.press("PageDown")
    await pause(page, 1)
    await escape(page)
    await command(page, "Run history")
    await pause(page, 1.2)
    await escape(page)
    await pause(page, 1)


async def research(page: Page, _: Path) -> None:
    await page.keyboard.press("F4")
    await pause(page, 0.6)
    await select_row(page, "Demo · changed")
    await command(page, "Triage")
    await pause(page, 1)
    await escape(page)
    await command(page, "Trial evidence")
    await pause(page, 0.6)
    await command(page, "Diagnose attempt")
    await pause(page, 1)
    await page.keyboard.press("PageDown")
    await pause(page, 0.7)
    await escape(page)
    await command(page, "Investigate")
    await pause(page, 0.6)
    await click_text(page, "Investigate")
    await pause(page, 1.2)
    await escape(page)
    await page.keyboard.press("F12")
    await pause(page, 0.9)
    await command(page, "Check quality")
    await page.keyboard.press("Enter")
    await pause(page, 0.4)
    await page.keyboard.press("ArrowDown")
    await pause(page, 0.4)
    await page.keyboard.press("Enter")
    await pause(page, 0.5)
    await click_text(page, "Inspect quality", same_line="Cancel")
    await wait_text(page, "Benchmark quality evidence")
    await pause(page, 1.2)
    await page.keyboard.press("PageDown")
    await pause(page, 0.7)
    await escape(page)
    await page.keyboard.press("Control+r")
    await pause(page, 1.2)


async def capture(output: Path, assets: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    prepare_assets(assets)
    settings, fixture = seed(output)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="everval-fixture-", dir="/tmp") as temporary:
        repository_path = Path(temporary) / "support-toolkit"
        original_repository(repository_path)
        log = (output / "server.log").open("w")
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                str(settings.data_dir),
                str(port),
                str(assets),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        clips = []
        errors: list[str] = []
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1):
                        break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Terminal server did not start")
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
                )
                try:
                    for name, title, operation in [
                        ("terminal-workbench", "The terminal workbench", workbench),
                        ("repository-onboarding", "Connect repository history", repository),
                        ("autopilot-budget", "Set recurring checks and budgets", automation),
                        ("failure-research", "Investigate retained failures", research),
                    ]:
                        directory = output / name
                        directory.mkdir()
                        context = await browser.new_context(
                            viewport={"width": WIDTH, "height": HEIGHT},
                            record_video_dir=str(directory / "video"),
                            record_video_size={"width": WIDTH, "height": HEIGHT},
                        )
                        await context.add_init_script(CURSOR_SCRIPT)
                        epoch = time.monotonic()
                        page = await context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        await page.goto(f"http://127.0.0.1:{port}/")
                        await wait_text(page, "EVERVAL")
                        await pause(page, 0.5)
                        start = time.monotonic() - epoch
                        try:
                            await operation(page, repository_path)
                        except Exception:
                            await page.screenshot(path=str(directory / "failure.png"))
                            (directory / "failure-terminal.txt").write_text(
                                "\n".join(await page.evaluate("window.terminalText()"))
                            )
                            raise
                        end = time.monotonic() - epoch
                        await page.screenshot(path=str(directory / "poster.png"))
                        video = page.video
                        await context.close()
                        if video is None:
                            raise AssertionError("Continuous video was not recorded")
                        video_path = Path(await video.path())
                        manifest = {
                            "name": name,
                            "title": title,
                            "fixture_note": FIXTURE_NOTE,
                            "video": str(video_path.relative_to(directory)),
                            "width": WIDTH,
                            "height": HEIGHT,
                            "start_s": round(start, 3),
                            "end_s": round(end, 3),
                            "duration_seconds": round(end - start, 3),
                            "poster_s": round(end - 1.0, 3),
                            "recording": "Actual PTY ANSI stream rendered continuously in xterm.js.",
                            "continuous": True,
                            "fps": 25,
                            "model_api_calls": 0,
                            "redactions": [
                                "The fixture process uses operator as its local audit account label."
                            ],
                        }
                        (directory / "manifest.json").write_text(
                            json.dumps(manifest, indent=2) + "\n"
                        )
                        clips.append(manifest)
                        print(f"Recorded {name}: {end - start:.1f}s", flush=True)
                finally:
                    await browser.close()
            if errors:
                raise RuntimeError("Browser errors occurred: " + str(errors))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            log.close()
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "clips": clips,
                        "model_api_calls": 0,
                        "javascript_errors": errors,
                        "servers_stopped": process.poll() is not None,
                        "policy_enabled": Autopilot(settings).get(fixture["policy"]["id"])[
                            "enabled"
                        ],
                    },
                    indent=2,
                )
                + "\n"
            )


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=ROOT / ".local/demo-assets/xterm-5.5.0")
    args = parser.parse_args()
    asyncio.run(capture(args.output.expanduser().resolve(), args.assets.expanduser().resolve()))


if __name__ == "__main__":
    main()
