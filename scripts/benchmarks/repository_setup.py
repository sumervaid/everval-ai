"""Restore exact Sphinx starting points and retain verified upstream grader setup."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

REVISION = "sphinx-upstream-configuration-v1"
ARTIFACTS = ("repository-setup.json", "repository-setup.diff")
IMAGE_HELPER = "/opt/everval-repository-setup.py"
PROVENANCE = Path("/opt/everval-provenance")
MAX_CONFIG_BYTES = 128 * 1024

_TOX = ("sed -i 's/pytest/pytest -rA/' tox.ini", "tox.ini", b"pytest", b"pytest -rA")
_CONSTRAINTS = (
    ("sed -i 's/Jinja2>=2.3/Jinja2<3.0/' setup.py", "setup.py", b"Jinja2>=2.3", b"Jinja2<3.0"),
    (
        "sed -i 's/sphinxcontrib-applehelp/sphinxcontrib-applehelp<=1.0.7/' setup.py",
        "setup.py",
        b"sphinxcontrib-applehelp",
        b"sphinxcontrib-applehelp<=1.0.7",
    ),
    (
        "sed -i 's/sphinxcontrib-devhelp/sphinxcontrib-devhelp<=1.0.5/' setup.py",
        "setup.py",
        b"sphinxcontrib-devhelp",
        b"sphinxcontrib-devhelp<=1.0.5",
    ),
    (
        "sed -i 's/sphinxcontrib-qthelp/sphinxcontrib-qthelp<=1.0.6/' setup.py",
        "setup.py",
        b"sphinxcontrib-qthelp",
        b"sphinxcontrib-qthelp<=1.0.6",
    ),
    (
        "sed -i 's/alabaster>=0.7,<0.8/alabaster>=0.7,<0.7.12/' setup.py",
        "setup.py",
        b"alabaster>=0.7,<0.8",
        b"alabaster>=0.7,<0.7.12",
    ),
    (
        "sed -i \"s/'packaging',/'packaging', 'markupsafe<=2.0.1',/\" setup.py",
        "setup.py",
        b"'packaging',",
        b"'packaging', 'markupsafe<=2.0.1',",
    ),
    (
        "sed -i 's/sphinxcontrib-htmlhelp/sphinxcontrib-htmlhelp<=2.0.4/' setup.py",
        "setup.py",
        b"sphinxcontrib-htmlhelp",
        b"sphinxcontrib-htmlhelp<=2.0.4",
    ),
    (
        "sed -i 's/sphinxcontrib-serializinghtml/sphinxcontrib-serializinghtml<=1.1.9/' setup.py",
        "setup.py",
        b"sphinxcontrib-serializinghtml",
        b"sphinxcontrib-serializinghtml<=1.1.9",
    ),
)
_PROFILES = {
    "sphinx-doc__sphinx-11445": (
        "71db08c05197545944949d5aa76cd340e7143627",
        "75b15b2ae9cd5b1545b899376d455f5e6224c1f6ac328eb9eef8c9ba09657ad0",
        (_TOX,),
    ),
    "sphinx-doc__sphinx-10451": (
        "195e911f1dab04b8ddeacbe04b7d214aaf81bb0b",
        "7ba3df5baf2cdfe7670766896952d9fa462b26936ad03a020ef591256da70fb3",
        (_TOX,),
    ),
    "sphinx-doc__sphinx-10325": (
        "7bdc11e87c7d86dcc2a087eccb7a7c129a473415",
        "78c00d93c09f79f05e7d8e2d6e9b5b3e5fbe064114d66c118e92418fc9213395",
        (_TOX,),
    ),
    "sphinx-doc__sphinx-8801": (
        "7ca279e33aebb60168d35e6be4ed059f4a68f2c1",
        "c0f20ceadae4cbf36b2f62253cf257b2bb592c27e12234b7ee767521e9379af8",
        (_TOX, *_CONSTRAINTS),
    ),
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def profile(instance: str, base_commit: str | None = None) -> dict | None:
    selected = _PROFILES.get(instance)
    if selected is None:
        return None
    base, recipe, commands = selected
    if base_commit is not None and base_commit != base:
        raise ValueError("Repository setup base differs from the reviewed profile")
    return {
        "revision": REVISION,
        "instance_id": instance,
        "base_commit": base,
        "source_dockerfile_sha256": recipe,
        "commands": [entry[0] for entry in commands],
        "files": sorted({entry[1] for entry in commands}),
    }


def verify_recipe(instance: str, dockerfile: str) -> dict:
    selected = profile(instance)
    if selected is None:
        raise ValueError("Repository setup has no reviewed profile")
    commands = [line for line in dockerfile.splitlines() if line.startswith("sed ")]
    if (
        digest(dockerfile.encode()) != selected["source_dockerfile_sha256"]
        or commands != selected["commands"]
    ):
        raise ValueError("Repository setup recipe differs from its pinned source")
    return selected


def transformed(instance: str, base_files: dict[str, bytes]) -> dict[str, bytes]:
    selected = profile(instance)
    if selected is None or set(base_files) != set(selected["files"]):
        raise ValueError("Repository setup files differ from the reviewed profile")
    result = dict(base_files)
    # These reviewed patterns use only literals and '.'; Python and basic sed
    # regexes agree. sed replaces the first occurrence independently on each line.
    for _, path, pattern, replacement in _PROFILES[instance][2]:
        before = result[path]
        after = b"".join(
            re.sub(pattern, replacement, line, count=1) for line in before.splitlines(keepends=True)
        )
        if after == before:
            raise ValueError("Reviewed repository setup transformation did not match")
        result[path] = after
    return result


def configuration_patch(base_files: dict[str, bytes], configured: dict[str, bytes]) -> bytes:
    result = []
    for path in sorted(base_files):
        if base_files[path] == configured[path]:
            raise ValueError("Repository setup file has no verified change")
        name = path.encode()
        result.append(b"diff --git a/" + name + b" b/" + name + b"\n")
        lines = difflib.diff_bytes(
            difflib.unified_diff,
            base_files[path].splitlines(keepends=True),
            configured[path].splitlines(keepends=True),
            fromfile=b"a/" + name,
            tofile=b"b/" + name,
        )
        for line in lines:
            result.append(
                line if line.endswith(b"\n") else line + b"\n\\ No newline at end of file\n"
            )
    return b"".join(result)


def _git(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fileMode=true",
            "-c",
            "core.autocrlf=false",
            *arguments,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        timeout=30,
    )
    if len(completed.stdout) > 512 * 1024:
        raise ValueError("Repository setup Git output exceeds its bound")
    return completed.stdout


def _base_files(repository: Path, selected: dict) -> dict[str, bytes]:
    files = {}
    base = selected["base_commit"]
    for path in selected["files"]:
        entry = _git(repository, "ls-tree", base, "--", path).split()
        if len(entry) != 4 or entry[0] != b"100644" or entry[3].decode() != path:
            raise ValueError(
                "Reviewed repository configuration must be a regular nonexecutable file"
            )
        value = _git(repository, "show", base + ":" + path)
        if len(value) > MAX_CONFIG_BYTES:
            raise ValueError("Repository configuration exceeds its bound")
        files[path] = value
    return files


def _current_files(repository: Path, selected: dict) -> dict[str, bytes]:
    files = {}
    for path in selected["files"]:
        target = repository / path
        info = target.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o111
            or info.st_size > MAX_CONFIG_BYTES
        ):
            raise ValueError("Repository configuration has unexpected type, mode or size")
        files[path] = target.read_bytes()
    return files


def _manifest(
    selected: dict, base_files: dict[str, bytes], configured: dict[str, bytes], patch: bytes
) -> dict:
    return {
        **selected,
        "helper_sha256": digest(Path(__file__).read_bytes()),
        "patch_sha256": digest(patch),
        "configuration": {
            path: {
                "base_sha256": digest(base_files[path]),
                "configured_sha256": digest(configured[path]),
            }
            for path in sorted(base_files)
        },
    }


def _write_private(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _read_private(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Repository setup artifact ownership or permissions differ")
        value = stream.read(512 * 1024 + 1)
    if len(value) > 512 * 1024:
        raise ValueError("Repository setup artifact exceeds its bound")
    return value


def capture(instance: str, base_commit: str, repository: Path, provenance: Path) -> dict:
    selected = profile(instance, base_commit)
    if selected is None:
        raise ValueError("Repository setup has no reviewed profile")
    base_files = _base_files(repository, selected)
    configured = transformed(instance, base_files)
    changed = set(
        _git(
            repository,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            base_commit,
            "--",
        )
        .rstrip(b"\0")
        .decode()
        .split("\0")
    )
    if changed != set(selected["files"]) or _current_files(repository, selected) != configured:
        raise ValueError(
            "Tracked repository changes differ from the exact reviewed transformations"
        )
    patch = configuration_patch(base_files, configured)
    manifest = _manifest(selected, base_files, configured, patch)
    provenance.mkdir(parents=True, exist_ok=True)
    _write_private(provenance / ARTIFACTS[1], patch)
    _write_private(
        provenance / ARTIFACTS[0], (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    )
    # Restore only verified files first. Unrelated tracked divergence must still
    # fail the complete guard before the final HEAD reset.
    _git(
        repository,
        "restore",
        "--source",
        base_commit,
        "--staged",
        "--worktree",
        "--",
        *selected["files"],
    )
    _git(repository, "diff", "--no-ext-diff", "--no-textconv", "--exit-code", base_commit)
    _git(repository, "reset", "--hard", base_commit)
    return manifest


def apply(instance: str, base_commit: str, repository: Path, provenance: Path) -> dict:
    selected = profile(instance, base_commit)
    if selected is None:
        raise ValueError("Repository setup has no reviewed profile")
    base_files = _base_files(repository, selected)
    if _current_files(repository, selected) != base_files:
        raise ValueError("Grader configuration is not at the exact frozen base")
    configured = transformed(instance, base_files)
    patch = configuration_patch(base_files, configured)
    manifest_bytes = _read_private(provenance / ARTIFACTS[0])
    manifest = json.loads(manifest_bytes)
    if manifest != _manifest(selected, base_files, configured, patch):
        raise ValueError("Image repository setup manifest differs from verified inputs")
    if _read_private(provenance / ARTIFACTS[1]) != patch:
        raise ValueError("Image repository setup patch differs from exact transformations")
    patch_path = str((provenance / ARTIFACTS[1]).resolve())
    _git(repository, "apply", "--check", "--whitespace=nowarn", patch_path)
    _git(repository, "apply", "--whitespace=nowarn", patch_path)
    if _current_files(repository, selected) != configured:
        raise ValueError("Applied repository setup differs from verified configuration")
    return {
        "revision": REVISION,
        "helper_sha256": manifest["helper_sha256"],
        "patch_sha256": digest(patch),
        "manifest_sha256": digest(manifest_bytes),
        "source_dockerfile_sha256": selected["source_dockerfile_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture-reset", "apply"))
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--base-commit", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Repository setup CLI runs only as root inside its isolated image")
    action = capture if args.command == "capture-reset" else apply
    result = action(args.instance_id, args.base_commit, Path("/testbed"), PROVENANCE)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
