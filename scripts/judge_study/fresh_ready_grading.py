"""Grade immutable completed responses while serial generation continues."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from scripts.judge_study import fresh_dispatch as dispatch
from scripts.judge_study import fresh_repositories as study


def ready(root: Path, job: dict[str, Any], adapter_sha256: str) -> bool:
    folder = root / "requests" / job["id"]
    try:
        validation = dispatch.strict_loads((folder / "dispatch-validation.json").read_bytes())
        result = dispatch.strict_loads((folder / "result.json").read_bytes())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    if (
        validation.get("job_id") != job["id"]
        or validation.get("body_sha256") != job["body_sha256"]
        or validation.get("adapter_sha256") != adapter_sha256
        or validation.get("result_sha256") != study.digest(result)
        or result.get("instance_id") != job["instance_id"]
        or result.get("model") != job["model"]
    ):
        raise ValueError("Completed-response identity or hash differs")
    return True


def available_jobs(root: Path, jobs: list[dict[str, Any]], worker: int, adapter_sha256: str):
    if worker not in (0, 1):
        raise ValueError("Invalid worker partition")
    for index, job in enumerate(jobs):
        if index % 2 != worker:
            continue
        while True:
            if (root / "pause-requested").exists() or (root / "grading-STOP.json").exists():
                return
            if ready(root, job, adapter_sha256):
                yield job
                break
            if (root / "dispatch-STOP.json").exists():
                return
            time.sleep(0.5)


@contextlib.contextmanager
def lock(root: Path):
    descriptor = os.open(root / "grading.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    finally:
        os.close(descriptor)


def verify_lease(root: Path, descriptor: int) -> None:
    observed = os.fstat(descriptor)
    expected = (root / "grading.lock").stat()
    if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("Grader lease does not match the campaign lock")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def worker(root: Path, partition: int, descriptor: int) -> None:
    verify_lease(root, descriptor)
    _, manifest = dispatch.verified_plan(root)
    original = study.inference_plan

    def own_jobs(path):
        checked = original(path)
        return {
            **checked,
            "jobs": available_jobs(root, checked["jobs"], partition, manifest["adapter_sha256"]),
        }

    study.inference_plan = own_jobs
    try:
        study.grade_inference(root)
    except BaseException as error:
        target = root / "grading-STOP.json"
        if not target.exists():
            study.private_json(target, {"worker": partition, "error_type": type(error).__name__})
        raise
    finally:
        study.inference_plan = original


def run(root: Path) -> None:
    plan, manifest = dispatch.verified_plan(root)
    operation = {
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "inference_sha256": study.digest(plan),
        "dispatch_freeze_sha256": study.digest(manifest),
        "workers": 2,
        "new_provider_calls": 0,
        "policy": (
            "Two fixed disjoint job partitions wait for complete dispatch-validation markers "
            "bound to immutable results. Original grader, patches, images and test scripts "
            "are unchanged. Never read an unfinished response as a failed submission."
        ),
    }
    dispatch.save_once(root / "grading-execution.json", operation)
    with lock(root) as descriptor:

        def execute(partition):
            log = root / ("grading-worker-" + str(partition) + ".log")
            with log.open("ab") as stream:
                log.chmod(0o600)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-u",
                        "-m",
                        "scripts.judge_study.fresh_ready_grading",
                        "--root",
                        str(root),
                        "--worker",
                        str(partition),
                        "--lease",
                        str(descriptor),
                    ],
                    pass_fds=(descriptor,),
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            return {"worker": partition, "returncode": result.returncode}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for result in pool.map(execute, (0, 1)):
                print(json.dumps(result), flush=True)
                if result["returncode"]:
                    raise RuntimeError("Grading stopped; retained evidence requires review")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--lease", type=int)
    args = parser.parse_args()
    root = study.private_root(args.root)

    def pause(signum, frame):
        (root / "pause-requested").write_text(
            "Drain the current provider request and containers.\n"
        )

    signal.signal(signal.SIGINT, pause)
    if args.worker is None:
        if args.lease is not None:
            parser.error("--lease requires --worker")
        run(root)
    elif args.lease is None:
        parser.error("--worker requires the parent-held --lease")
    else:
        worker(root, args.worker, args.lease)


if __name__ == "__main__":
    main()
