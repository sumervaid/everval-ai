import argparse
import hashlib
import json
import re
import shlex
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.benchmarks.executor import DockerSandbox, bounded_process

parser = argparse.ArgumentParser(
    description="Prepare Django offline in a restricted local container."
)
parser.add_argument("--work-dir", required=True, type=Path)
parser.add_argument("--base-image", required=True)
args = parser.parse_args()
project = Path(__file__).resolve().parents[3]
workspace = args.work_dir.resolve()
if workspace == project or project in workspace.parents:
    raise ValueError("Preparation data must stay outside the source checkout")
workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.base_image):
    raise ValueError("Supply an existing immutable Docker image ID")
root = args.work_dir.resolve()
prep = root / "native-django"
out = root / "direct-builds-final/django-rebuild"
if (out / "environment.json").exists():
    raise RuntimeError("An immutable Django environment already exists")
source = json.loads((prep / "fetch-complete.json").read_text())
cache = Path(source["cache"])
archive = cache.parent / "django-preparation.tar"
artifacts = json.loads((prep / "artifacts.json").read_text())
if not archive.exists():
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:

        def add_file(path, name):
            if path.is_symlink() or not path.is_file():
                raise ValueError("Only regular preparation files")
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.mtime = 0
            info.size = path.stat().st_size
            with path.open("rb") as stream:
                tar.addfile(info, stream)

        for path in sorted(cache.rglob("*")):
            if path.is_dir():
                continue
            add_file(path, "everval-django.git/" + path.relative_to(cache).as_posix())
        for item in artifacts:
            path = prep / "wheels" / item["filename"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Wheel changed")
            add_file(path, "everval-django-wheels/" + path.name)
    record = {
        "archive": str(archive),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "bytes": archive.stat().st_size,
    }
    (prep / "archive.json").write_text(json.dumps(record, indent=2) + "\n")
else:
    record = json.loads((prep / "archive.json").read_text())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("Archive changed")
plan = json.loads((prep / "source-plan.json").read_text())
task = next(t for t in plan["tasks"] if t["version"] == "4.2")
image = args.base_image
s = DockerSandbox(image=image, output_dir=out)


def good(result, reason):
    if result["returncode"] or result["exception_info"]:
        raise RuntimeError(reason)
    return result


try:
    s.start()
    limit = bounded_process(
        s._docker("update", "--memory", "384m", "--memory-swap", "384m", "--cpus", "0.25", s.name),
        timeout=30,
        max_bytes=8192,
    )
    good(limit, "Preparation resource cap failed")
    (out / "preparation-resource-cap.json").write_text(json.dumps(limit, indent=2))
    good(
        bounded_process(
            s._docker("cp", "-", s.name + ":/opt/"), timeout=120, input_path=archive, max_bytes=8192
        ),
        "Preparation archive copy failed",
    )
    commands = "\n".join(
        [
            "set -e",
            "python -c "
            + shlex.quote(
                "import sys,platform; assert sys.version_info[:3]==(3,9,20); assert platform.machine()=='aarch64'"
            ),
            "cd /",
            "rm -rf /testbed",
            "mkdir -p /testbed",
            "cp -a /opt/everval-django.git /testbed/.git",
            "git -C /testbed config core.bare false",
            "cd /testbed",
            "git config core.hooksPath /dev/null",
            "git checkout --detach " + shlex.quote(task["base_commit"]),
            "python -m pip install --no-index --no-build-isolation --no-deps /opt/everval-django-wheels/*.whl",
            "python -m pip install --no-index --no-build-isolation --no-deps -e .",
            "python -m pip check",
            "python -c "
            + shlex.quote(
                "import django,sys,sqlite3; print('Django',django.get_version(),'Python',sys.version.split()[0],'SQLite',sqlite3.sqlite_version)"
            ),
            "mkdir -p /tmp/everval-home",
            "chmod 0700 /tmp/everval-home",
        ]
    )
    result = s.shell(commands, user="0:0", timeout=600, cwd="/", max_bytes=131072)
    print("Preparation exit", result["returncode"], result["output"][-2500:], flush=True)
    good(result, "Django offline preparation failed; private output retained")
    commit = bounded_process(s._docker("commit", s.name), timeout=180, max_bytes=8192)
    good(commit, "Image commit failed")
    (out / "commit.json").write_text(json.dumps(commit, indent=2))
    record = {
        **task,
        "image_id": commit["output"].strip(),
        "source_preparation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "architecture": "arm64",
        "base_image_id": image,
        "historical_image_reproduced": False,
        "native_amendment": "Python3.9.20 native substitution for Django3.0-4.2; 204 exact base commits plus first parents; 32 pinned offline wheels from repository requirements. Optional browser/service backends omitted, required skips exclude tasks. Preparation only; task controls remain mandatory.",
        "dependency_plan_sha256": hashlib.sha256(
            (prep / "dependency-plan.json").read_bytes()
        ).hexdigest(),
        "source_plan_sha256": hashlib.sha256((prep / "source-plan.json").read_bytes()).hexdigest(),
    }
    (out / "environment.json").write_text(json.dumps(record, indent=2) + "\n")
    canonical = root / "direct-builds-final/django/environment.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if canonical.exists():
        raise RuntimeError("Canonical image record already exists")
    canonical.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2), flush=True)
finally:
    s.close()
