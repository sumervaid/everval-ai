"""Bounded subprocess I/O for trusted local parsers and read-only metadata tools."""

from __future__ import annotations

import os
import selectors
import subprocess
import threading
import time
from typing import Any


def run_bounded(
    args: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    limit_bytes: int,
    payload: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Kill and reap a process before its output can exhaust host memory."""
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert process.stdout is not None and process.stderr is not None
    writer = None
    if payload is not None:
        assert process.stdin is not None

        def write() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(payload)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Local parser exceeded its time limit")
                for key, _ in selector.select(min(remaining, 0.25)):
                    stream: Any = key.fileobj
                    chunk = os.read(stream.fileno(), min(65536, limit_bytes - total + 1))
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    total += len(chunk)
                    if total > limit_bytes:
                        raise ValueError("Local parser output exceeds the size limit")
                    buffers[key.data].extend(chunk)
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return subprocess.CompletedProcess(
            args,
            process.returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        if writer is not None:
            writer.join(timeout=1)
