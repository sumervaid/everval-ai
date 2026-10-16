"""Real terminal regression for the npm process handoff; no application data."""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from contextlib import suppress
from pathlib import Path


def child(mode):
    counts = {"interrupts": 0, "terminations": 0}
    inputs = []
    dimensions = None

    def interrupt(_signum, _frame):
        counts["interrupts"] += 1
        # A duplicate forwarded signal can arrive while this handler is active.
        time.sleep(0.15)
        print("INT " + str(counts["interrupts"]), flush=True)

    def terminate(_signum, _frame):
        counts["terminations"] += 1

    def resized(_signum, _frame):
        nonlocal dimensions
        size = os.get_terminal_size(0)
        dimensions = [size.lines, size.columns]
        print("RESIZED", flush=True)

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGWINCH, resized)
    tty_access = False
    if mode == "application":
        descriptor = os.open("/dev/tty", os.O_RDWR)
        os.close(descriptor)
        tty_access = os.isatty(0)
    print("READY", flush=True)
    deadline = time.monotonic() + 12
    while counts["terminations"] == 0 and time.monotonic() < deadline:
        if mode == "application" and select.select([0], [], [], 0.02)[0]:
            line = sys.stdin.readline().rstrip("\n")
            inputs.append(line)
            print("INPUT " + line, flush=True)
        else:
            time.sleep(0.01)
    print(
        "COMPLETE "
        + json.dumps(
            {
                "pid": os.getpid(),
                "tty": tty_access,
                "input": inputs,
                "dimensions": dimensions,
                **counts,
            }
        ),
        flush=True,
    )
    return 0 if counts["terminations"] == 1 else 1


def probe(node, launcher, mode):
    helper = str(Path(__file__).absolute())
    command = sys.executable
    args = ["-I", helper, "--child", mode]
    if mode == "application":
        statement = "execApplication(" + json.dumps(command) + ", " + json.dumps(args) + ");"
    else:
        statement = (
            "const r = await runChild("
            + json.dumps(command)
            + ", "
            + json.dumps(args)
            + "); process.exitCode = r.code;"
        )
    javascript = (
        "import {execApplication, runChild} from "
        + json.dumps(Path(launcher).as_uri())
        + "; "
        + statement
    )
    pid, terminal = pty.fork()
    if pid == 0:
        os.execv(node, [node, "--input-type=module", "-e", javascript])
    output = bytearray()
    finished = False

    def until(text):
        deadline = time.monotonic() + 8
        while text not in output and time.monotonic() < deadline:
            if select.select([terminal], [], [], 0.1)[0]:
                try:
                    chunk = os.read(terminal, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
        if text not in output:
            raise AssertionError("Missing terminal marker " + repr(text) + ": " + repr(output))

    try:
        until(b"READY")
        if mode == "application":
            fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 43, 121, 0, 0))
            until(b"RESIZED")
            os.write(terminal, b"hello terminal\n")
            until(b"INPUT hello terminal")
        os.write(terminal, b"\x03")
        until(b"INT ")
        # SIGTERM targets only the original Node PID, not its process group.
        time.sleep(0.2)
        os.kill(pid, signal.SIGTERM)
        until(b"COMPLETE ")
        deadline = time.monotonic() + 8
        status = None
        while time.monotonic() < deadline:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                finished = True
                break
            time.sleep(0.02)
        if not finished:
            raise AssertionError("Launcher did not finish after directed SIGTERM")
        lines = output.decode().splitlines()
        record = next(
            json.loads(line.split("COMPLETE ", 1)[1])
            for line in lines
            if line.startswith("COMPLETE ")
        )
        record["launcher_pid"] = pid
        record["exit_code"] = os.waitstatus_to_exitcode(status)
        record["experimental_warning"] = "ExperimentalWarning" in output.decode()
        print(json.dumps(record))
    finally:
        if not finished:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(terminal)


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        raise SystemExit(child(sys.argv[2]))
    probe(*sys.argv[1:])
