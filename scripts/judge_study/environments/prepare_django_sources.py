from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

parser = argparse.ArgumentParser(
    description="Fetch pinned Django Git objects without executing imported code."
)
parser.add_argument("--work-dir", required=True, type=Path)
parser.add_argument("--dataset", required=True, type=Path)
parser.add_argument("--cache", required=True, type=Path)
args = parser.parse_args()
project = Path(__file__).resolve().parents[3]
workspace = args.work_dir.resolve()
if workspace == project or project in workspace.parents:
    raise ValueError("Preparation data must stay outside the source checkout")
workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
root = args.work_dir.resolve() / "native-django"
root.mkdir(parents=True, exist_ok=True, mode=0o700)
dataset = args.dataset.resolve()
assert (
    hashlib.sha256(dataset.read_bytes()).hexdigest()
    == "030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25"
)
rows = [
    r
    for r in pq.read_table(dataset).to_pylist()
    if r["repo"] == "django/django" and r["version"] in {"3.0", "3.1", "3.2", "4.0", "4.1", "4.2"}
]
rows.sort(key=lambda r: r["instance_id"])
selected = [
    {k: r[k] for k in ("instance_id", "repo", "version", "base_commit", "environment_setup_commit")}
    for r in rows
]
bases = sorted({r["base_commit"] for r in rows})
setups = sorted({r["environment_setup_commit"] for r in rows})
plan = {
    "created_at": datetime.now(UTC).isoformat(),
    "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
    "selection": "All Django 3.0-4.2 metadata; no repair outputs or outcomes inspected.",
    "tasks": selected,
    "source_url": "https://github.com/django/django.git",
    "base_commits": bases,
    "environment_setup_commits": setups,
    "depth": 2,
}
plan_path = root / "source-plan.json"
if plan_path.exists():
    old = json.loads(plan_path.read_text())
    assert old["tasks"] == selected
else:
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
cache = args.cache.resolve()
if cache == project or project in cache.parents:
    raise ValueError("Git source cache must stay outside the source checkout")
cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
base = [
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "gc.auto=0",
    "-c",
    "pack.threads=1",
    "-c",
    "pack.windowMemory=32m",
    "-c",
    "fetch.fsckObjects=true",
]


def run(args, timeout=600):
    p = subprocess.run(base + args, env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        (root / "last-error.log").write_text(p.stdout + p.stderr)
        raise RuntimeError("Git preparation failed; retained private log")
    return p.stdout


if not cache.exists():
    run(["init", "--bare", str(cache)])
marker = root / "fetch-complete.json"
if not marker.exists():
    print(
        "Fetching",
        len(bases),
        "Django base commits and",
        len(setups),
        "environment revisions with first parents.",
        flush=True,
    )
    result = run(
        [
            "--git-dir",
            str(cache),
            "fetch",
            "--no-tags",
            "--depth=2",
            plan["source_url"],
            *sorted(set(bases + setups)),
        ]
    )
    (root / "fetch.log").write_text(result)
    verified = {}
    for sha in bases:
        raw = run(["--git-dir", str(cache), "cat-file", "-p", sha])
        parent = next(line.split()[1] for line in raw.splitlines() if line.startswith("parent "))
        run(["--git-dir", str(cache), "cat-file", "-e", parent + "^{commit}"])
        observed = run(["--git-dir", str(cache), "rev-parse", sha + "^1"]).strip()
        if parent != observed:
            raise RuntimeError("First parent identity differs")
        run(["--git-dir", str(cache), "update-ref", "refs/everval/" + sha, sha])
        verified[sha] = parent
    marker.write_text(
        json.dumps(
            {
                "cache": str(cache),
                "verified_first_parents": verified,
                "base_count": len(bases),
                "task_count": len(rows),
                "preparation_script_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
print("Django exact base/parent preparation complete.", flush=True)
for version in sorted({r["version"] for r in rows}):
    r = next(r for r in rows if r["version"] == version)
    commit = r["environment_setup_commit"]
    for relative in (
        "tests/requirements/py3.txt",
        "tests/requirements/base.txt",
        "setup.py",
        "pyproject.toml",
    ):
        p = subprocess.run(
            base + ["--git-dir", str(cache), "show", commit + ":" + relative],
            env=env,
            capture_output=True,
            text=True,
        )
        if p.returncode == 0:
            out = root / ("requirements-" + version + "-" + relative.replace("/", "_"))
            out.write_text(p.stdout)
print("Pinned repository requirement files retained without executing them.", flush=True)
