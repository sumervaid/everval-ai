#!/usr/bin/env python3
"""Run free installed-package acceptance in isolated workspaces, sequentially.

The development interpreter supplies Playwright; --installed-python must point
to a separate environment containing the wheel to be accepted. Docker images
must already be local. This command never runs the paid OpenAI pilot.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--installed-python", type=Path, required=True)
    parser.add_argument("--python-image", required=True, help="Approved Python/pytest image")
    parser.add_argument("--node-image", required=True, help="Approved Node image")
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    installed = args.installed_python.expanduser().absolute()
    if not installed.is_file():
        raise SystemExit("The installed Python executable does not exist")
    scripts = Path(__file__).resolve().parent
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
        and key != "PYTHONPATH"
    }
    env.update(EVERVAL_ALLOW_REMOTE_PROVIDERS="false", CVALS_ALLOW_REMOTE_PROVIDERS="false")
    commands = [
        (
            "repositories",
            [
                str(installed),
                str(scripts / "repositories.py"),
                "--output",
                str(output / "repositories"),
                "--python-image",
                args.python_image,
                "--node-image",
                args.node_image,
            ],
        ),
        (
            "automation",
            [
                str(installed),
                str(scripts / "automation.py"),
                "--output-dir",
                str(output / "automation"),
            ],
        ),
        (
            "interfaces",
            [
                sys.executable,
                str(scripts / "interfaces.py"),
                "--installed-python",
                str(installed),
                "--output",
                str(output / "interfaces"),
            ]
            + (["--skip-browser"] if args.skip_browser else []),
        ),
    ]
    result = {
        "schema": "everval.acceptance-run.v1",
        "status": "running",
        "paid_provider_calls": 0,
        "browser_requested": not args.skip_browser,
        "stages": [],
    }
    for name, command in commands:
        print("Running " + name + " acceptance", flush=True)
        started = time.monotonic()
        with (output / (name + ".log")).open("w") as stream:
            completed = subprocess.run(
                command, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
        result["stages"].append(
            {
                "name": name,
                "exit_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        )
        if completed.returncode:
            result["status"] = "failed"
            break
    else:
        result["status"] = "passed"
    (output / "run.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
