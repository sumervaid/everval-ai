# /// script
# requires-python = ">=3.12"
# dependencies = ["pyarrow==26.0.0"]
# ///
"""Fetch frozen benchmark data and prepare isolated native ARM runtimes.

This tool makes no provider calls. Downloads remain in the explicitly selected
private directory. Imported setup commands execute only inside Docker.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

ASSETS_SHA = "ab87f56df58169343cfe61322f537b358590b305c3ffa0790c4a4f87fd1a5c1a"
SELECTION_SHA = "4698b8cd1b4eb0637018690c594c9ab81477920adc7d05fc91dba46f242b64bb"
DATASET_REVISION = "b0dde1093fe417d83b7184254edf8199c1f0dff5"
DATASET_SHA = "438e281d80587aa7be470896ce410557002fde02d2ceee3e099331d308f62dd3"
TASK_REVISION = "3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d"
DATASET_NAME = "SWE-bench_Lite-test-00000-of-00001.parquet"
DATASET_URL = (
    "https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite/resolve/"
    + DATASET_REVISION
    + "/data/test-00000-of-00001.parquet"
)
PLATFORM_PACKAGES = {
    "_libgcc_mutex",
    "_openmp_mutex",
    "ca-certificates",
    "ld_impl_linux-64",
    "libffi",
    "libgcc-ng",
    "libgomp",
    "libstdcxx-ng",
    "ncurses",
    "openssl",
    "readline",
    "sqlite",
    "tk",
    "tzdata",
    "xz",
    "zlib",
}
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_BUILD_LOG = 64 * 1024 * 1024
WHEELHOUSE_SCRIPT = r'''"""Prepare exact-version offline build wheels inside the image only."""

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys

REQUIRED = ("setuptools", "wheel")
OPTIONAL = (
    "setuptools-scm",
    "flit-core",
    "packaging",
    "tomli",
    "typing-extensions",
    "importlib-metadata",
    "zipp",
)
NEEDED_BACKENDS = ()
FALLBACKS = {
    "setuptools-scm": {
        "version": "7.1.0",
        "filename": "setuptools_scm-7.1.0-py3-none-any.whl",
        "sha256": "73988b6d848709e2af142aa48c986ea29592bbcfca5375678064708205253d8e",
        "url": "https://files.pythonhosted.org/packages/1d/66/8f42c941be949ef2b22fe905d850c794e7c170a526023612aad5f3a121ad/setuptools_scm-7.1.0-py3-none-any.whl",
        "metadata_url": "https://pypi.org/pypi/setuptools-scm/7.1.0/json",
    },
    "flit-core": {
        "version": "3.9.0",
        "filename": "flit_core-3.9.0-py3-none-any.whl",
        "sha256": "7aada352fb0c7f5538c4fafeddf314d3a6a92ee8e2b1de70482329e42de70301",
        "url": "https://files.pythonhosted.org/packages/38/45/618e84e49a6c51e5dd15565ec2fcd82ab273434f236b8f108f065ded517a/flit_core-3.9.0-py3-none-any.whl",
        "metadata_url": "https://pypi.org/pypi/flit_core/3.9.0/json",
    },
    "typing-extensions": {
        "version": "4.12.2",
        "filename": "typing_extensions-4.12.2-py3-none-any.whl",
        "sha256": "04e5ca0351e0f3f85c6853954072df659d0d13fac324d0072316b67d7794700d",
        "url": "https://files.pythonhosted.org/packages/26/9f/ad63fc0248c5379346306f8668cda6e2e2e9c95e01216d2b8ffd9ff037d0/typing_extensions-4.12.2-py3-none-any.whl",
        "metadata_url": "https://pypi.org/pypi/typing-extensions/4.12.2/json",
    },
}


def selected_requirements():
    required = [*REQUIRED, *NEEDED_BACKENDS]
    if "setuptools-scm" in NEEDED_BACKENDS:
        required.extend(("packaging", "typing-extensions"))
        if sys.version_info < (3, 11):
            required.append("tomli")
    requirements = []
    for name in dict.fromkeys([*required, *OPTIONAL]):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            if name not in required:
                continue
            if name not in FALLBACKS:
                raise
            version = FALLBACKS[name]["version"]
        requirements.append(name + "==" + version)
    return requirements


def main():
    directory = Path("/opt/everval-wheels")
    directory.mkdir(parents=True, exist_ok=True)
    requirements = selected_requirements()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "download",
            "--only-binary=:all:",
            "--no-deps",
            "--dest",
            str(directory),
            *requirements,
        ],
        check=True,
    )
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != len(requirements):
        raise RuntimeError("Offline wheel count differs from exact package requests")
    verified_fallbacks = []
    for name, pin in FALLBACKS.items():
        if name + "==" + pin["version"] not in requirements:
            continue
        path = directory / pin["filename"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]:
            raise RuntimeError("Pinned build-backend wheel hash differs: " + name)
        verified_fallbacks.append({"name": name, **pin})
    manifest = {
        "requirements": requirements,
        "required_backends": list(NEEDED_BACKENDS),
        "verified_pinned_backend_artifacts": verified_fallbacks,
        "artifacts": [
            {
                "filename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
            for path in wheels
        ],
        "download_phase": "image_preparation",
        "evaluation_network": "none",
    }
    provenance = Path("/opt/everval-provenance")
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / "offline-wheels.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
'''

PROVENANCE_COMMAND = (
    "\nRUN mkdir -p /opt/everval-provenance && conda list -n testbed --explicit --md5 "
    "> /opt/everval-provenance/conda-explicit.txt && conda list -n testbed --json "
    "> /opt/everval-provenance/conda-list.json && "
    "/opt/miniconda3/envs/testbed/bin/python -m pip freeze --all "
    "> /opt/everval-provenance/pip-freeze.txt && sha256sum /miniconda.sh "
    "> /opt/everval-provenance/miniconda-sha256.txt && "
    '/opt/miniconda3/envs/testbed/bin/python -c "import glob,json; '
    "print(json.dumps([json.load(open(p)) for p in sorted(glob.glob("
    "'/opt/miniconda3/envs/testbed/conda-meta/*.json'))],sort_keys=True))\" "
    "> /opt/everval-provenance/conda-package-records.json\n"
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def immutable(path: Path, value: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink artifact: {path}")
    if path.exists():
        if path.read_bytes() != value:
            raise ValueError(f"Existing artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(value)


def atomic_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink manifest: {path}")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(json_bytes(value))
    os.replace(temporary, path)


def private_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if root == project or root.is_relative_to(project):
        raise ValueError("The output directory must resolve outside the Git checkout")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


def asset_manifest() -> dict[str, Any]:
    raw = Path(__file__).with_name("preparation-assets.json").read_bytes()
    if digest(raw) != ASSETS_SHA:
        raise ValueError("Build asset manifest hash differs")
    spec = json.loads(raw)
    if spec.get("revision") != TASK_REVISION:
        raise ValueError("Build asset revision differs")
    names = [entry["path"] for entry in spec["files"]]
    if len(names) != len(set(names)):
        raise ValueError("Build asset paths must be unique")
    return spec


def verified_build_assets(root: Path, instance: str) -> Path:
    spec = asset_manifest()
    required = {f"tasks/{instance}/Dockerfile", f"tasks/{instance}/environment.yml"}
    entries = [item for item in spec["files"] if item["path"] in required]
    if {item["path"] for item in entries} != required or len(entries) != 2:
        raise ValueError("Both required build assets must be present exactly once")
    base = root / "sources/swe-bench-tasks" / TASK_REVISION
    for entry in entries:
        path = base / entry["path"]
        if path.is_symlink() or digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Build asset hash mismatch")
    return base / "tasks" / instance


@contextlib.contextmanager
def preparation_lock(root: Path) -> Iterator[None]:
    descriptor = os.open(
        root / ".preparation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Preparation lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another preparation process owns this private directory") from error
        yield
    finally:
        os.close(descriptor)


def require_unfrozen(root: Path) -> None:
    if (root / "preparation-blocked.json").exists():
        raise ValueError("Preparation cleanup is unresolved or active; further work is blocked")
    if (root / "campaign.json").exists():
        raise ValueError("Campaign is frozen; preparation requires a new private directory")


def validate_cached(record: dict[str, Any], task: dict[str, Any]) -> None:
    if (
        record.get("selection_sha256") != SELECTION_SHA
        or record.get("task_sha256") != task["task_sha256"]
    ):
        raise ValueError("Cached preparation does not match the frozen selection/task")
    if record.get("preparation_status") not in {"ready", "failed"}:
        raise ValueError("Unknown cached preparation status")
    if record.get("cleanup_complete") is not True:
        raise ValueError("Preparation cleanup is unresolved; further work is blocked")
    if record["preparation_status"] == "ready" and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", record.get("image_id") or ""
    ):
        raise ValueError("Cached preparation has no immutable image identifier")


def remove_owned_container(
    docker: list[str],
    environment: dict[str, str],
    name: str,
) -> bool:
    try:
        result = subprocess.run(
            docker + ["rm", "--force", name],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 or "no such container" in result.stderr.lower()


def settle_preparation(root: Path, record: dict[str, Any]) -> None:
    marker = root / "preparation-blocked.json"
    if not record.get("ownership_token"):
        return
    current = json.loads(marker.read_text())
    if current.get("ownership_token") != record["ownership_token"]:
        raise ValueError("Preparation ownership marker changed; refusing to clear it")
    if record["cleanup_complete"]:
        marker.unlink()
    else:
        current.update(
            state="cleanup_unresolved",
            cleanup_complete=False,
            reason=record["reason"],
            reason_code=record.get("reason_code"),
        )
        atomic_json(marker, current)


def tooling_environment(root: Path) -> dict[str, str]:
    temporary = root / "tooling/tmp"
    temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "TMPDIR": str(temporary),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "UV_NO_CONFIG": "1",
        "UV_CACHE_DIR": str(root / "tooling/cache"),
        "UV_PYTHON_INSTALL_DIR": str(root / "tooling/python"),
    }


def verify_tooling_reuse(root: Path, record: dict[str, Any]) -> None:
    relative = Path(record["artifacts"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid tooling artifact path")
    original = root / relative / "installed-packages.txt"
    expected = record["artifact_sha256"]["installed-packages.txt"]
    if digest(original.read_bytes()) != expected:
        raise ValueError("Retained tooling dependency record differs")
    target = root / "tooling" / ("reuse-" + uuid.uuid4().hex + ".txt")
    code = bounded_process(
        ["uv", "pip", "freeze", "--python", str(root / "tooling-env/bin/python")],
        tooling_environment(root),
        target,
        120,
        2 * 1024 * 1024,
    )
    if code or digest(target.read_bytes()) != expected:
        raise ValueError("Installed tooling packages drifted from retained provenance")
    version_file = root / relative / "python-version.txt"
    version_hash = record["artifact_sha256"]["python-version.txt"]
    if digest(version_file.read_bytes()) != version_hash:
        raise ValueError("Retained tooling Python record differs")
    current_version = target.with_suffix(".python.txt")
    code = bounded_process(
        [str(root / "tooling-env/bin/python"), "--version"],
        tooling_environment(root),
        current_version,
        30,
        4096,
    )
    if code or digest(current_version.read_bytes()) != version_hash:
        raise ValueError("Tooling Python version drifted from retained provenance")


def selection(root: Path) -> dict[str, Any]:
    project = Path(__file__).resolve().parents[2]
    path = project / "docs/benchmarks/public-repositories-2026-10-15/selection.json"
    raw = path.read_bytes()
    if digest(raw) != SELECTION_SHA:
        raise ValueError("The preregistered selection hash does not match")
    data = json.loads(raw)
    immutable(root / "selection/selection-public.json", raw)
    return data


def download(url: str, destination: Path, expected_sha: str) -> None:
    if destination.is_symlink():
        raise ValueError(f"Refusing symlink download: {destination}")
    if destination.exists():
        if digest(destination.read_bytes()) != expected_sha:
            raise ValueError(f"Existing download hash differs: {destination}")
        return
    request = urllib.request.Request(url, headers={"User-Agent": "Everval-benchmark-preparation"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD or digest(data) != expected_sha:
        raise ValueError(f"Downloaded artifact failed size/hash verification: {destination.name}")
    immutable(destination, data)


def fetch(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    import pyarrow.parquet as pq

    selected = root / "selection"
    download(DATASET_URL, selected / DATASET_NAME, DATASET_SHA)
    rows = pq.read_table(selected / DATASET_NAME).to_pylist()
    if len(rows) != 300:
        raise ValueError("Unexpected pinned dataset row count")
    rows_by_id = {row["instance_id"]: row for row in rows}
    index = []
    for entry in manifest["tasks"]:
        row = rows_by_id[entry["instance_id"]]
        raw = canonical(row) + b"\n"
        if digest(raw) != entry["task_sha256"]:
            raise ValueError(f"Task hash mismatch: {entry['instance_id']}")
        target = selected / "tasks-private" / (entry["instance_id"] + ".json")
        immutable(target, raw)
        index.append(
            {
                "instance_id": entry["instance_id"],
                "split": entry["split"],
                "task_path": str(target.resolve()),
                "task_sha256": digest(raw),
            }
        )
    immutable(
        selected / "selection-private-index.json",
        json_bytes({"selection_sha256": SELECTION_SHA, "tasks": index}),
    )
    spec = asset_manifest()
    if spec["revision"] != TASK_REVISION:
        raise ValueError("Build assets revision differs")
    source = root / "sources/swe-bench-tasks" / TASK_REVISION
    for asset in spec["files"]:
        name = asset["path"]
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid build asset path")
        url = f"https://raw.githubusercontent.com/SWE-bench/swe-bench-tasks/{TASK_REVISION}/{name}"
        download(url, source / name, asset["sha256"])
    return {"status": "ready", "tasks": len(index), "assets": len(spec["files"])}


SHARED_RESOLVER = {
    "name": "micromamba",
    "release": "2.9.0-0",
    "sha256": "9f93b974adcb4d166996af969b6cd371287d1a3e52733704727884d9b74cb7a7",
    "configuration": {
        "use_sharded_repodata": True,
        "repodata_shards_threads": 1,
        "channel_priority": "flexible",
    },
}
SHARED_ROOT_PREFIX = "/opt/miniconda3"
SHARED_TARGET_PREFIX = SHARED_ROOT_PREFIX + "/envs/testbed"
SHARED_CREATE = (
    "micromamba create --yes --rc-file /opt/everval-mambarc "
    "--root-prefix /opt/miniconda3 --prefix /opt/miniconda3/envs/testbed "
    "--file /root/environment.yml"
)
SHARED_HEADER = "#!/bin/bash\nset -euxo pipefail\nsource /opt/miniconda3/bin/activate\n"
SHARED_FIRST_ANCHOR = (
    "COPY prepare-1.sh /opt/everval-prepare-1.sh\nRUN bash /opt/everval-prepare-1.sh\n"
)
SHARED_PIP_ANCHOR = (
    "COPY prepare-pip.sh /opt/everval-prepare-pip.sh\nRUN bash /opt/everval-prepare-pip.sh\n"
)
SHARED_PIP_COMMAND = (
    "/opt/miniconda3/envs/testbed/bin/python -m pip install "
    "-r /root/everval-pip-requirements.txt --no-input"
)
SHARED_PIP_SOURCE = "https://github.com/mamba-org/mamba/blob/2.9.0/libmamba/src/api/utils.cpp"
SHARED_PARSER_SOURCE = "https://github.com/mamba-org/mamba/blob/2.9.0/libmamba/src/api/install.cpp"
SHARED_ACTIVATION_SOURCE = (
    "https://github.com/mamba-org/mamba/blob/2.9.0/libmamba/src/core/util.cpp"
)


def _environment(pins: Sequence[str], pip_lines: Sequence[str], channels: Sequence[str]) -> str:
    return (
        "name: testbed\nchannels:\n"
        + "".join("  - " + channel + "\n" for channel in channels)
        + "dependencies:\n"
        + "".join("  - " + pin + "\n" for pin in pins)
        + ("  - pip:\n" + "\n".join(pip_lines) + "\n" if pip_lines else "")
        + "prefix: /opt/miniconda3/envs/testbed\n"
    )


def _pip_requirements(pip_lines: Sequence[str]) -> bytes:
    """Accept only the plain exact-version YAML entries in the reviewed assets."""
    requirements = []
    for line in pip_lines:
        if not isinstance(line, str):
            raise ValueError("Pip requirements must be source-derived strings")
        match = re.fullmatch(
            r"      - ([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?"
            r"==[A-Za-z0-9][A-Za-z0-9.+!_-]*)",
            line,
        )
        if not match:
            raise ValueError(
                "Unsupported pip YAML syntax; review before extending this shared-stage adapter"
            )
        requirements.append(match.group(1))
    return "".join(requirement + "\n" for requirement in requirements).encode()


def _split_environment(
    adapted: tuple[str, dict[str, str], dict[str, Any]],
    *,
    pins: Sequence[str],
    pip_lines: Sequence[str],
    channels: Sequence[str] = ("defaults", "conda-forge"),
    platform: str = "linux/arm64",
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Split one strictly recognized recipe without executing or mutating inputs."""
    dockerfile, original_scripts, original_provenance = adapted
    pins = tuple(pins)
    pip_lines = tuple(pip_lines)
    channels = tuple(channels)
    if platform != "linux/arm64" or original_provenance.get("platform") != platform:
        raise ValueError("Only the reviewed native ARM recipe is supported")
    if channels != ("defaults", "conda-forge"):
        raise ValueError("Channel order differs from the reviewed recipe")
    if original_provenance.get("resolver") != SHARED_RESOLVER:
        raise ValueError("Resolver release, checksum, or configuration differs")
    if original_provenance.get("preserved_version_pins") != list(pins):
        raise ValueError("Pins differ from source-derived adaptation evidence")
    if not pins or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*=[A-Za-z0-9][A-Za-z0-9.+!_-]*", pin)
        for pin in pins
    ):
        raise ValueError("Only reviewed exact-version Conda pins are supported")
    names = [pin.split("=", 1)[0] for pin in pins]
    if (
        len(names) != len(set(names))
        or "python" not in names
        or "pip" not in names
        or "uv" in names
    ):
        raise ValueError("Unique Python and pip pins, without uv, are required")
    if original_provenance.get("adapted_dockerfile_sha256") != digest(dockerfile.encode()):
        raise ValueError("Adapted Dockerfile differs from its recorded hash")
    expected_hashes = {name: digest(value.encode()) for name, value in original_scripts.items()}
    if original_provenance.get("build_scripts") != expected_hashes:
        raise ValueError("Build scripts differ from their recorded hashes")
    if "prepare-pip.sh" in original_scripts or dockerfile.count(SHARED_FIRST_ANCHOR) != 1:
        raise ValueError("First-stage boundary is absent, repeated, or already split")
    first = original_scripts.get("prepare-1.sh", "")
    match = re.match(
        re.escape(SHARED_HEADER) + r"cat <<'(EOF_[a-z0-9]+)' > /root/environment.yml\n", first
    )
    if match is None:
        raise ValueError("First stage does not match the reviewed activation/heredoc template")
    original_environment = _environment(pins, pip_lines, channels) + "\n"
    expected_first = (
        SHARED_HEADER
        + f"cat <<'{match.group(1)}' > /root/environment.yml\n"
        + original_environment
        + match.group(1)
        + "\n"
        + SHARED_CREATE
        + "\nconda activate testbed\n"
    )
    if first != expected_first:
        raise ValueError("First stage contains unreviewed commands or different environment data")

    requirements = _pip_requirements(pip_lines)
    conda_yaml = (_environment(pins, (), channels) + "\n").encode()
    common_script = (
        SHARED_HEADER
        + "cat <<'EVERVAL_CONDA_ENVIRONMENT' > /root/environment.yml\n"
        + conda_yaml.decode()
        + "EVERVAL_CONDA_ENVIRONMENT\n"
        + SHARED_CREATE
        + "\nconda activate testbed\n"
    )
    group = {
        "schema_version": 1,
        "platform": platform,
        "channels": list(channels),
        "pins": list(pins),
        "root_prefix": SHARED_ROOT_PREFIX,
        "target_prefix": SHARED_TARGET_PREFIX,
        "resolver": copy.deepcopy(SHARED_RESOLVER),
        "conda_yaml_sha256": digest(conda_yaml),
    }
    group_digest = digest(canonical(group))
    scripts = original_scripts.copy()
    scripts["prepare-1.sh"] = common_script
    new_dockerfile = dockerfile
    if requirements:
        # utils.cpp writes one requirements line per YAML string, then invokes
        # target Python after activation, with the YAML parent as cwd. No -U or
        # --no-deps applies to an ordinary YAML create (install.cpp Update::No).
        scripts["prepare-pip.sh"] = (
            SHARED_HEADER
            + "cat <<'EVERVAL_FULL_ENVIRONMENT' > /root/environment.yml\n"
            + original_environment
            + "EVERVAL_FULL_ENVIRONMENT\n"
            + "cat <<'EVERVAL_PIP_REQUIREMENTS' > /root/everval-pip-requirements.txt\n"
            + requirements.decode()
            + "EVERVAL_PIP_REQUIREMENTS\n"
            + "cd /root\n"
            + "export MAMBA_EXE=/usr/local/bin/micromamba\n"
            + 'eval "$("$MAMBA_EXE" shell hook -s bash -r /opt/miniconda3)"\n'
            + "micromamba activate /opt/miniconda3/envs/testbed\n"
            + SHARED_PIP_COMMAND
            + "\n"
        )
        new_dockerfile = dockerfile.replace(
            SHARED_FIRST_ANCHOR, SHARED_FIRST_ANCHOR + SHARED_PIP_ANCHOR, 1
        )

    # The resolver/configuration prefix and every later repository-install byte
    # remain untouched; this shared-stage adapter only splits the reviewed first boundary.
    before, after = dockerfile.split(SHARED_FIRST_ANCHOR)
    assert (
        new_dockerfile
        == before + SHARED_FIRST_ANCHOR + (SHARED_PIP_ANCHOR if requirements else "") + after
    )
    assert all(
        scripts[name] == value for name, value in original_scripts.items() if name != "prepare-1.sh"
    )
    provenance = copy.deepcopy(original_provenance)
    provenance["unsplit_adapted_dockerfile_sha256"] = digest(dockerfile.encode())
    provenance["unsplit_first_script_sha256"] = digest(first.encode())
    provenance["unsplit_recipe_bundle_sha256"] = original_provenance["recipe_bundle_sha256"]
    provenance["adapted_dockerfile_sha256"] = digest(new_dockerfile.encode())
    provenance["build_scripts"] = {name: digest(value.encode()) for name, value in scripts.items()}
    provenance["recipe_bundle_sha256"] = digest(
        canonical(
            {
                "dockerfile_sha256": provenance["adapted_dockerfile_sha256"],
                "scripts": provenance["build_scripts"],
            }
        )
    )
    provenance["shared_conda_stage"] = {
        **group,
        "group_sha256": group_digest,
        "script_sha256": digest(common_script.encode()),
        "pip_request_normalization": (
            "Micromamba adds an unversioned pip request for a YAML pip section. "
            "Every reviewed group already requires one exact pip version; the bare "
            "request is logically redundant and is omitted consistently for shared "
            "Conda stages. No version constraint is relaxed. Resolved package "
            "equivalence still requires manifest comparison and controls."
        ),
        "normalization_source": SHARED_PARSER_SOURCE + "#L192-L249",
    }
    provenance["separate_pip_stage"] = {
        "requirements_count": len(pip_lines),
        "requirements_sha256": digest(requirements),
        "original_order_preserved": True,
        "working_directory": "/root",
        "python": SHARED_TARGET_PREFIX + "/bin/python",
        "argv": [
            SHARED_TARGET_PREFIX + "/bin/python",
            "-m",
            "pip",
            "install",
            "-r",
            "/root/everval-pip-requirements.txt",
            "--no-input",
        ],
        "original_environment_sha256": digest(original_environment.encode()),
        "command_source": SHARED_PIP_SOURCE + "#L50-L119",
        "requirements_source": SHARED_PIP_SOURCE + "#L213-L281",
        "activation_source": SHARED_ACTIVATION_SOURCE + "#L1406-L1457",
    }
    provenance["changes"] = [
        *provenance.get("changes", []),
        "Separate canonical Conda stage from unchanged ordered per-task pip requirements",
        "Preserve the original resolver prefix and all repository-install script bytes",
    ]
    provenance["recipe_sharing_verification_limit"] = (
        "Pure specification/script checks only; no split image or resolved package "
        "equivalence has been executed or established."
    )
    return new_dockerfile, scripts, provenance


def required_backends(instance: str) -> tuple[str, ...]:
    # Read-only review of the fixed cohort's base-commit pyproject.toml declarations.
    if instance in {
        "pytest-dev__pytest-11148",
        "pytest-dev__pytest-11143",
        "pytest-dev__pytest-9359",
        "pytest-dev__pytest-8906",
    }:
        return ("setuptools-scm",)
    if instance == "sphinx-doc__sphinx-11445":
        return ("flit-core",)
    return ()


def wheelhouse_script(instance: str) -> str:
    return WHEELHOUSE_SCRIPT.replace(
        "NEEDED_BACKENDS = ()", "NEEDED_BACKENDS = " + repr(required_backends(instance)), 1
    )


def adapt(source: Path, *, shared_conda: bool = True) -> tuple[str, dict[str, str], dict[str, Any]]:
    original = (source / "Dockerfile").read_text()
    old_environment = (source / "environment.yml").read_text()
    pins = []
    removed = []
    pip_lines = []
    in_pip = False
    for line in old_environment.splitlines():
        stripped = line.strip()
        if stripped == "- pip:":
            in_pip = True
            continue
        if in_pip:
            if line.startswith("    "):
                pip_lines.append(line)
                continue
            in_pip = False
        if stripped.startswith("- ") and "=" in stripped:
            parts = stripped[2:].split("=")
            if parts[0] in PLATFORM_PACKAGES:
                removed.append(stripped[2:])
            else:
                pins.append(parts[0] + "=" + parts[1])
    if not any(pin.startswith("python=") for pin in pins):
        raise ValueError("No pinned Python version in the official environment")
    environment = (
        "name: testbed\nchannels:\n  - defaults\n  - conda-forge\ndependencies:\n"
        + "".join("  - " + pin + "\n" for pin in pins)
        + ("  - pip:\n" + "\n".join(pip_lines) + "\n" if pip_lines else "")
        + "prefix: /opt/miniconda3/envs/testbed\n"
    )
    text = original.replace(
        "FROM --platform=linux/amd64 ubuntu:jammy",
        "FROM --platform=linux/arm64 ubuntu:jammy",
    ).replace(
        "Miniconda3-py311_23.11.0-2-Linux-x86_64.sh",
        "Miniconda3-py311_23.11.0-2-Linux-aarch64.sh",
    )
    if text == original or old_environment not in text:
        raise ValueError("Official Dockerfile no longer matches the reviewed adaptation")
    text = text.replace(old_environment, environment).replace(
        "conda env create -f /root/environment.yml",
        "micromamba create --yes --rc-file /opt/everval-mambarc --root-prefix /opt/miniconda3 "
        "--prefix /opt/miniconda3/envs/testbed --file /root/environment.yml",
    )
    resolver = (
        "RUN wget -q https://github.com/mamba-org/micromamba-releases/releases/download/"
        "2.9.0-0/micromamba-linux-aarch64 -O /usr/local/bin/micromamba "
        "&& echo '9f93b974adcb4d166996af969b6cd371287d1a3e52733704727884d9b74cb7a7  "
        "/usr/local/bin/micromamba' | sha256sum -c - && chmod 755 /usr/local/bin/micromamba "
        "&& printf '%s\\n' 'use_sharded_repodata: true' 'repodata_shards_threads: 1' "
        "'channel_priority: flexible' > /opt/everval-mambarc\n"
    )
    permission_command = "chmod -R 777 /testbed"
    permission_replacements = text.count("\n" + permission_command + "\n")
    text = text.replace("\n" + permission_command + "\n", "\nchmod -R a+rwX /testbed\n")
    offset = text.index("RUN <<")
    text = text[:offset] + resolver + text[offset:]
    text += (
        "\nCOPY prepare-wheelhouse.py /opt/everval-prepare-wheelhouse.py\n"
        "RUN /opt/miniconda3/envs/testbed/bin/python /opt/everval-prepare-wheelhouse.py && "
        + PROVENANCE_COMMAND.removeprefix("\nRUN ")
        + "ENV PIP_FIND_LINKS=/opt/everval-wheels\n"
    )
    lines = iter(text.splitlines())
    output = []
    scripts = {}
    for line in lines:
        match = re.fullmatch(r"RUN <<(EOF_[a-z0-9]+)", line)
        if not match:
            output.append(line)
            continue
        body = []
        for inner in lines:
            if inner == match.group(1):
                break
            body.append(inner)
        else:
            raise ValueError("Unterminated upstream Dockerfile heredoc")
        name = f"prepare-{len(scripts) + 1}.sh"
        scripts[name] = "\n".join(body) + "\n"
        output.extend([f"COPY {name} /opt/everval-{name}", f"RUN bash /opt/everval-{name}"])
    scripts["prepare-wheelhouse.py"] = wheelhouse_script(source.name)
    result = "\n".join(output) + "\n"
    provenance = {
        "source_repository": "SWE-bench/swe-bench-tasks",
        "source_revision": TASK_REVISION,
        "platform": "linux/arm64",
        "official_image": False,
        "resolver": {
            "name": "micromamba",
            "release": "2.9.0-0",
            "configuration": {
                "use_sharded_repodata": True,
                "repodata_shards_threads": 1,
                "channel_priority": "flexible",
            },
            "sha256": "9f93b974adcb4d166996af969b6cd371287d1a3e52733704727884d9b74cb7a7",
        },
        "source_dockerfile_sha256": digest(original.encode()),
        "adapted_dockerfile_sha256": digest(result.encode()),
        "preserved_version_pins": pins,
        "relaxed_platform_or_runtime_pins": removed,
        "build_scripts": {name: digest(value.encode()) for name, value in scripts.items()},
        "offline_build_wheels": {
            "script_sha256": digest(scripts["prepare-wheelhouse.py"].encode()),
            "versions": "Preserve installed versions; required absent backends use pinned wheel fallbacks",
            "required_backends": list(required_backends(source.name)),
            "absent_backend_policy": "Explicit version and SHA256 verified, without installing into the target environment",
            "required": ["setuptools", "wheel"],
            "optional_if_installed": [
                "setuptools-scm",
                "flit-core",
                "packaging",
                "tomli",
                "typing-extensions",
                "importlib-metadata",
                "zipp",
            ],
            "find_links": "/opt/everval-wheels",
            "manifest": "/opt/everval-provenance/offline-wheels.json",
        },
        "worktree_permissions": {
            "upstream": permission_command,
            "adapted": "chmod -R a+rwX /testbed",
            "replacements": permission_replacements,
        },
        "recipe_bundle_sha256": digest(
            canonical(
                {
                    "dockerfile_sha256": digest(result.encode()),
                    "scripts": {name: digest(value.encode()) for name, value in scripts.items()},
                }
            )
        ),
        "changes": [
            "Native ARM Ubuntu and the same-release aarch64 Miniconda installer",
            "Remove x86 Conda build strings and explicit transitive platform-runtime locks",
            "Preserve declared Python, test and Python-library version pins",
            "Use SHA256-verified standalone micromamba 2.9.0-0 with one-thread sharded metadata",
            "Capture resolved packages, pip freeze and Miniconda installer hash",
            "Translate Dockerfile heredocs into copied shell scripts",
            "Preserve tracked executable bits while granting nonroot worktree write access",
            "Retain exact-version build backend wheels for unchanged offline isolated installation",
        ],
    }
    if shared_conda:
        return _split_environment((result, scripts, provenance), pins=pins, pip_lines=pip_lines)
    return result, scripts, provenance


def docker_prefix(host: str | None) -> list[str]:
    value = host or os.environ.get("CVALS_DOCKER_HOST") or os.environ.get("DOCKER_HOST")
    if value and (not value.startswith("unix:///") or "\n" in value):
        raise ValueError("This preparation protocol supports a local Unix Docker socket only")
    return ["docker"] + (["--host", value] if value else [])


def docker_environment(root: Path) -> dict[str, str]:
    temporary = root / "docker-tmp"
    temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = root / "docker-client"
    config.mkdir(mode=0o700, exist_ok=True)
    immutable(config / "config.json", b"{}\n")
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "DOCKER_CONFIG": str(config),
        "DOCKER_BUILDKIT": "0",
        "TMPDIR": str(temporary),
    }


def bounded_process(
    command: list[str],
    environment: dict[str, str],
    log: Path,
    timeout: int,
    limit: int,
) -> int:
    started = time.monotonic()
    size = 0
    with log.open("xb") as stream:
        os.chmod(log, 0o600)
        child = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        assert child.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Docker command exceeded the preparation deadline")
                for key, _ in selector.select(timeout=0.5):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Docker output exceeded the preparation limit")
                    stream.write(chunk)
                    stream.flush()
            return child.wait(timeout=10)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            raise
        finally:
            selector.close()
            child.stdout.close()


def build_one(
    root: Path,
    task: dict[str, Any],
    docker: list[str],
    timeout: int,
) -> dict[str, Any]:
    instance = task["instance_id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]+", instance):
        raise ValueError("Invalid instance identifier")
    preparer_source = Path(__file__).read_bytes()
    record: dict[str, Any] = {
        "preparation_status": "failed",
        "image_id": None,
        "cleanup_complete": True,
        "selection_sha256": SELECTION_SHA,
        "task_sha256": task["task_sha256"],
        "preparer_sha256": digest(preparer_source),
        "architecture": "arm64",
        "reason": None,
        "prepared_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    directory = root / "runtime-builds" / (instance + "-arm64")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    attempt = directory / dt.datetime.now(dt.UTC).strftime("attempt-%Y%m%dT%H%M%S%f")
    attempt.mkdir(mode=0o700)
    immutable(attempt / "preparer.py", preparer_source)
    record["artifacts"] = str(attempt.relative_to(root))
    environment = docker_environment(root)
    build_active = False
    try:
        source = verified_build_assets(root, instance)
        dockerfile, scripts, provenance = adapt(source)
        context = directory / "contexts" / provenance["recipe_bundle_sha256"]
        context.mkdir(parents=True, exist_ok=True, mode=0o700)
        immutable(context / "Dockerfile", dockerfile.encode())
        for name, value in scripts.items():
            immutable(context / name, value.encode())
        immutable(context / ".dockerignore", b"*.log\nimage-id.txt\n")
        immutable(attempt / "adaptation.json", json_bytes(provenance))
        record["recipe_sha256"] = provenance["adapted_dockerfile_sha256"]
        record["recipe_bundle_sha256"] = provenance["recipe_bundle_sha256"]
        record["recipe_path"] = str((context / "Dockerfile").relative_to(root))
        record["source_revision"] = TASK_REVISION
        image_file = attempt / "image-id.txt"
        tag = "everval/swe-lite-" + instance.replace("__", "-") + ":arm64-v1"
        command = docker + [
            "build",
            "--force-rm",
            "--platform",
            "linux/arm64",
            "--tag",
            tag,
            "--iidfile",
            str(image_file),
            str(context),
        ]
        record["ownership_token"] = uuid.uuid4().hex
        ownership = {
            "schema_version": 1,
            "state": "active",
            "cleanup_complete": False,
            "ownership_token": record["ownership_token"],
            "instance_id": instance,
            "selection_sha256": SELECTION_SHA,
            "artifacts": record["artifacts"],
            "reason": "Awaiting terminal Docker preparation and cleanup evidence",
        }
        immutable(attempt / "ownership.json", json_bytes(ownership))
        immutable(root / "preparation-blocked.json", json_bytes(ownership))
        build_active = True
        code = bounded_process(command, environment, attempt / "build.log", timeout, MAX_BUILD_LOG)
        if code:
            record["build_exit_code"] = code
            record["reason_code"] = "docker_build_client_failed_cleanup_unknown"
            raise RuntimeError(
                f"Docker build client exited with status {code}; daemon cleanup is unverified"
            )
        build_active = False
        image_id = image_file.read_text().strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Docker did not return an immutable image identifier")
        record["built_image_id"] = image_id
        for name in [
            "conda-explicit.txt",
            "conda-list.json",
            "pip-freeze.txt",
            "miniconda-sha256.txt",
            "conda-package-records.json",
            "offline-wheels.json",
        ]:
            container_name = "everval-preparation-" + uuid.uuid4().hex
            command = docker + [
                "run",
                "--name",
                container_name,
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--entrypoint",
                "/bin/cat",
                image_id,
                "/opt/everval-provenance/" + name,
            ]
            try:
                manifest_limit = (16 if name == "conda-package-records.json" else 2) * 1024 * 1024
                code = bounded_process(command, environment, attempt / name, 60, manifest_limit)
                if code:
                    raise RuntimeError(f"Failed to capture resolved dependency manifest: {name}")
            finally:
                if not remove_owned_container(docker, environment, container_name):
                    record["cleanup_complete"] = False
            if not record["cleanup_complete"]:
                raise RuntimeError("Owned provenance container cleanup is unresolved")
        code = bounded_process(
            docker + ["image", "inspect", image_id],
            environment,
            attempt / "image-inspect.json",
            60,
            1024 * 1024,
        )
        if code:
            raise RuntimeError("Could not inspect the resulting image")
        inspected = json.loads((attempt / "image-inspect.json").read_text())
        if inspected[0]["Architecture"] != "arm64" or inspected[0]["Id"] != image_id:
            raise ValueError("Resulting image architecture/identity differs")
        record.update(preparation_status="ready", image_id=image_id, image_tag=tag, reason=None)
        record["artifact_sha256"] = {
            file.name: digest(file.read_bytes()) for file in attempt.iterdir() if file.is_file()
        }
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        record["reason"] = type(error).__name__ + ": " + str(error)
        if build_active:
            record["cleanup_complete"] = False
            record.setdefault("reason_code", "docker_build_interrupted_cleanup_unknown")
        if isinstance(error, KeyboardInterrupt):
            record["cancelled"] = True
    immutable(attempt / "preparation.json", json_bytes(record))
    return record


def tooling(root: Path) -> dict[str, Any]:
    """Install the pinned trusted benchmark toolchain, never task repositories."""
    requirement = Path(__file__).with_name("tooling-requirements.lock")
    requirement_hash = digest(requirement.read_bytes())
    source_pins = [
        {
            "name": "mini-swe-agent",
            "revision": "a83fcae82d2a08f0ee0c688f9d137b3566c097f8",
            "sha256": "f6c724076f94a9c9a246d6375502386428d2e2ab8d85efdc78f578ecc6f973ad",
            "url": "https://codeload.github.com/SWE-agent/mini-swe-agent/tar.gz/a83fcae82d2a08f0ee0c688f9d137b3566c097f8",
        },
        {
            "name": "swe-bench",
            "revision": "02e7a74ffd0b707aab73d203fe87bdc7c76afc8e",
            "sha256": "d06181d5365b51fc7ff490171581b9ee87d77e19b3a739a8d574bf25d9ef9479",
            "url": "https://codeload.github.com/SWE-bench/SWE-bench/tar.gz/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e",
        },
    ]
    directory = root / "tooling"
    directory.mkdir(mode=0o700, exist_ok=True)
    environment_path = root / "tooling-env"
    provenance_path = directory / "provenance.json"
    identity = {
        "python": "3.12.14",
        "requirements_sha256": requirement_hash,
        "upstream_sources": source_pins,
    }
    if provenance_path.exists():
        existing = json.loads(provenance_path.read_text())
        if any(existing.get(key) != value for key, value in identity.items()):
            raise ValueError("Existing tooling provenance does not match the frozen inputs")
        if existing.get("status") == "ready" and (environment_path / "bin/python").exists():
            verify_tooling_reuse(root, existing)
            return existing
    elif environment_path.exists():
        raise ValueError(
            "Existing tooling-env has no matching provenance; use a fresh private root"
        )
    attempt = directory / dt.datetime.now(dt.UTC).strftime("attempt-%Y%m%dT%H%M%S%f")
    attempt.mkdir(mode=0o700)
    record = {**identity, "status": "preparing", "artifacts": str(attempt.relative_to(root))}
    atomic_json(provenance_path, record)
    process_env = tooling_environment(root)
    archives = []
    try:
        for pin in source_pins:
            target = directory / "sources" / (pin["name"] + "-" + pin["revision"] + ".tar.gz")
            download(pin["url"], target, pin["sha256"])
            archives.append(str(target))
        if not environment_path.exists():
            code = bounded_process(
                ["uv", "venv", "--python", "3.12.14", str(environment_path)],
                process_env,
                attempt / "create-environment.log",
                1800,
                MAX_BUILD_LOG,
            )
            if code:
                raise RuntimeError(f"Tooling environment creation exited with status {code}")
        python = str(environment_path / "bin/python")
        commands = [
            (
                "install-locked-dependencies.log",
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    python,
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    "--no-deps",
                    "-r",
                    str(requirement),
                ],
            ),
            (
                "install-verified-upstreams.log",
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    python,
                    "--no-deps",
                    "--no-build-isolation",
                    *archives,
                ],
            ),
            (
                "dependency-check.log",
                ["uv", "pip", "check", "--python", python],
            ),
            (
                "installed-packages.txt",
                ["uv", "pip", "freeze", "--python", python],
            ),
            (
                "python-version.txt",
                [python, "--version"],
            ),
            (
                "uv-version.txt",
                ["uv", "--version"],
            ),
        ]
        for name, command in commands:
            code = bounded_process(command, process_env, attempt / name, 1800, MAX_BUILD_LOG)
            if code:
                raise RuntimeError(f"Tooling stage {name} exited with status {code}")
        record.update(status="ready", environment=str(environment_path.relative_to(root)))
        record["artifact_sha256"] = {
            file.name: digest(file.read_bytes()) for file in attempt.iterdir() if file.is_file()
        }
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(status="failed", reason=type(error).__name__ + ": " + str(error))
        atomic_json(provenance_path, record)
        raise
    atomic_json(provenance_path, record)
    return record


def execute(arguments: argparse.Namespace, root: Path) -> int:
    require_unfrozen(root)
    manifest = selection(root)
    if arguments.command == "selection":
        print(json.dumps({"selection_sha256": SELECTION_SHA, "counts": manifest["counts"]}))
        return 0
    if arguments.command == "tooling":
        print(json.dumps(tooling(root)))
        return 0
    if arguments.command == "download":
        print(json.dumps(fetch(root, manifest)))
        return 0
    if not 60 <= arguments.timeout_seconds <= 7200:
        raise ValueError("Build timeout must be between 60 and 7200 seconds")
    requested = set(arguments.instance_id or [])
    known = {row["instance_id"] for row in manifest["tasks"]}
    if requested - known:
        raise ValueError("Requested task is not in the frozen selection")
    images_path = root / "runtime-images.json"
    images = json.loads(images_path.read_text()) if images_path.exists() else {}
    if any(row.get("cleanup_complete") is not True for row in images.values()):
        raise ValueError("Preparation cleanup is unresolved; further work is blocked")
    prefix = docker_prefix(arguments.docker_host)
    failed = False
    for task in manifest["tasks"]:
        instance = task["instance_id"]
        if requested and instance not in requested:
            continue
        existing = images.get(instance)
        if existing:
            validate_cached(existing, task)
        if existing and not (arguments.retry_failed and existing["preparation_status"] == "failed"):
            if existing["preparation_status"] == "ready":
                inspected = subprocess.run(
                    prefix
                    + [
                        "image",
                        "inspect",
                        "--format",
                        "{{.Id}} {{.Architecture}}",
                        existing["image_id"],
                    ],
                    env=docker_environment(root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if (
                    inspected.returncode
                    or inspected.stdout.strip() != existing["image_id"] + " arm64"
                ):
                    raise ValueError("Cached image is missing or differs; use a fresh private root")
            print(json.dumps({"instance_id": instance, **existing}), flush=True)
            failed |= existing["preparation_status"] != "ready"
            continue
        record = build_one(root, task, prefix, arguments.timeout_seconds)
        if existing:
            record["previous_preparations"] = existing.get("previous_preparations", []) + [
                {key: value for key, value in existing.items() if key != "previous_preparations"}
            ]
        images[instance] = record
        atomic_json(images_path, images)
        settle_preparation(root, record)
        print(json.dumps({"instance_id": instance, **record}), flush=True)
        if record.get("cleanup_complete") is not True or record.get("cancelled"):
            return 130 if record.get("cancelled") else 1
        failed |= record["preparation_status"] != "ready"
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", required=True, help="Private directory outside the checkout"
    )
    parser.add_argument("--docker-host", help="Explicit local unix:/// Docker socket")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selection", help="Verify and copy the frozen public selection")
    sub.add_parser("download", help="Fetch and verify data/build assets without executing them")
    sub.add_parser(
        "tooling", help="Install the pinned trusted benchmark toolchain in the private root"
    )
    build = sub.add_parser("build", help="Prepare selected images serially in Docker")
    build.add_argument("--instance-id", action="append", help="One selected task; repeatable")
    build.add_argument("--timeout-seconds", type=int, default=3600)
    build.add_argument(
        "--retry-failed", action="store_true", help="Retain evidence and retry failed builds"
    )
    arguments = parser.parse_args()
    root = private_root(arguments.output_dir)
    with preparation_lock(root):
        return execute(arguments, root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Preparation stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from error
