import argparse
import hashlib
import json
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

parser = argparse.ArgumentParser(description="Download and verify the frozen Django wheel set.")
parser.add_argument("--work-dir", required=True, type=Path)
args = parser.parse_args()
project = Path(__file__).resolve().parents[3]
workspace = args.work_dir.resolve()
if workspace == project or project in workspace.parents:
    raise ValueError("Preparation data must stay outside the source checkout")
workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
root = args.work_dir.resolve() / "native-django"
root.mkdir(parents=True, exist_ok=True, mode=0o700)
lock_path = Path(__file__).with_name("django-wheels.lock.json")
expected = json.loads(lock_path.with_name("manifest-sha256.json").read_text())[lock_path.name]
if hashlib.sha256(lock_path.read_bytes()).hexdigest() != expected:
    raise ValueError("Frozen wheel lock changed")
locked = json.loads(lock_path.read_text())
by_name = {item["name"]: item for item in locked}
wheels = root / "wheels"
wheels.mkdir(exist_ok=True)
versions = {item["name"]: item["version"] for item in locked}


def read_url(url, limit):
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Everval-research/0.4"}), timeout=60
    ) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Artifact exceeds bound")
    return data


def download(item):
    name, version = item
    row = by_name[name]
    if row["version"] != version or not row["url"].startswith("https://files.pythonhosted.org/"):
        raise ValueError("Untrusted frozen wheel metadata")
    row = {**row, "digests": {"sha256": row["sha256"]}}

    out = wheels / row["filename"]
    data = out.read_bytes() if out.exists() else read_url(row["url"], 80 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != row["digests"]["sha256"]:
        raise ValueError("Wheel digest mismatch")
    if not out.exists():
        out.write_bytes(data)
    return {
        "name": name,
        "version": version,
        "filename": out.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "url": row["url"],
        "bytes": len(data),
    }


with ThreadPoolExecutor(max_workers=4) as pool:
    artifacts = list(pool.map(download, versions.items()))
(root / "artifacts.json").write_text(json.dumps(artifacts, indent=2) + "\n")
environment = default_environment()
environment.update(
    python_version="3.9",
    python_full_version="3.9.20",
    sys_platform="linux",
    platform_machine="aarch64",
    platform_system="Linux",
    extra="",
)
available = {canonicalize_name(k): v for k, v in versions.items()}
gaps = []
for artifact in artifacts:
    with zipfile.ZipFile(wheels / artifact["filename"]) as z:
        m = BytesParser().parsebytes(
            z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA")))
        )
    for line in m.get_all("Requires-Dist", []):
        r = Requirement(line)
        if r.marker and not r.marker.evaluate(environment):
            continue
        got = available.get(canonicalize_name(r.name))
        if got is None or got not in r.specifier:
            gaps.append({"from": artifact["name"], "requirement": line, "provided": got})
record = {
    "source": "Django requirements at pinned environment_setup_commit values in source-plan.json",
    "official_recipe_reference": "https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/constants/python.py",
    "python": "3.9.20",
    "architecture": "aarch64",
    "versions": versions,
    "omitted_optional_backends": [
        "pylibmc/libmemcached",
        "pywatchman/watchman",
        "selenium/browser",
        "geoip2",
    ],
    "omission_rule": "No external services/browser/native source builds; required skipped or missing tests exclude a task during controls.",
    "dependency_gaps": gaps,
    "total_bytes": sum(x["bytes"] for x in artifacts),
    "preparation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}
(root / "dependency-plan.json").write_text(json.dumps(record, indent=2) + "\n")
print(
    json.dumps(
        {"wheels": len(artifacts), "bytes": record["total_bytes"], "dependency_gaps": gaps},
        indent=2,
    )
)
if gaps:
    raise RuntimeError("Unresolved wheel dependencies")
