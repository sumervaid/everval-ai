"""Stage an auditable npm distribution without installing or publishing anything."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tomllib
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHIPPING_SOURCES = ("bin/everval.mjs", "lib/launcher.mjs")
PACKAGE_FILES = [*SHIPPING_SOURCES, "runtime/", "runtime.json", "README.md", "LICENSE", "NOTICE"]
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 1000
STATIC_REQUIRED = {
    "cvals/static/index.html",
    "cvals/static/favicon.svg",
    "cvals/static/assets/third-party-notices.txt",
}
STATIC_ASSET = re.compile(r"cvals/static/assets/index-[A-Za-z0-9_-]+\.(js|css)")
PRIVATE_PATTERNS = (
    re.compile(rb"/Users/[^/\s]+/"),
    re.compile(rb"/Volumes/[^/\r\n]+/"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    re.compile(rb"npm_[A-Za-z0-9]{30,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)


class BuildError(ValueError):
    """A source or package boundary failed validation."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, *, maximum: int = MAX_FILE_BYTES) -> bytes:
    if path.is_symlink():
        raise BuildError(f"Symbolic links cannot be packaged: {path.name}")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise BuildError(f"Not a regular, size-bounded file: {path.name}")
            data = stream.read(maximum + 1)
    except OSError as error:
        raise BuildError(f"Cannot read required file: {path.name}") from error
    if len(data) > maximum:
        raise BuildError(f"File exceeds the size limit: {path.name}")
    return data


def read_source(root: Path, relative: str) -> bytes:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ("", ".", "..") for part in relative.split("/")):
        raise BuildError("Invalid source path")
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise BuildError(f"Symbolic links cannot be packaged: {relative}")
    return read_regular(current)


def check_private(data: bytes, label: str) -> None:
    if any(pattern.search(data) for pattern in PRIVATE_PATTERNS):
        raise BuildError(f"Possible private credential or maintainer path in {label}")


def tracked_sources(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "src/cvals"],
        cwd=root,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise BuildError("Cannot determine the tracked application source allowlist")
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def expected_package(root: Path) -> dict[str, bytes]:
    expected: dict[str, bytes] = {}
    for name in tracked_sources(root):
        if name.endswith(".py") or name == "src/cvals/tui/workbench.tcss":
            relative = name.removeprefix("src/")
            if not relative.startswith("cvals/") or "/tests/" in relative:
                raise BuildError("Unexpected tracked application path")
            expected[relative] = read_source(root, name)
    if "cvals/__init__.py" not in expected or "cvals/cli.py" not in expected:
        raise BuildError("Tracked application source is incomplete")
    static = root / "src/cvals/static"
    if static.is_symlink() or not static.is_dir():
        raise BuildError("Build the bundled browser interface before packaging")
    for filename in static.rglob("*"):
        if filename.is_symlink():
            raise BuildError("Browser assets must not contain symbolic links")
        if filename.is_dir():
            continue
        relative = filename.relative_to(root / "src").as_posix()
        if relative not in STATIC_REQUIRED and not STATIC_ASSET.fullmatch(relative):
            raise BuildError(f"Unexpected browser asset: {relative}")
        expected[relative] = read_source(root, "src/" + relative)
    if not STATIC_REQUIRED.issubset(expected):
        raise BuildError("The bundled browser interface is incomplete")
    for suffix in (".js", ".css"):
        if (
            sum(
                name.startswith("cvals/static/assets/index-") and name.endswith(suffix)
                for name in expected
            )
            != 1
        ):
            raise BuildError("Expected one current browser JavaScript and CSS bundle")
    for name, data in expected.items():
        check_private(data, name)
    return expected


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _dependency(value: str) -> tuple[str, tuple[str, ...]]:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)([^;@\[\]]*)", value)
    if not match:
        raise BuildError("Unsupported dependency syntax in application metadata")
    return (
        _normalized_distribution(match[1]),
        tuple(sorted(part.strip() for part in match[2].split(",") if part.strip())),
    )


def _check_metadata(
    data: dict[str, bytes], prefix: str, root: Path, project: dict[str, Any]
) -> None:
    metadata = BytesParser(policy=policy.default).parsebytes(data[prefix + "METADATA"])
    allowed = {
        "Metadata-Version",
        "Name",
        "Version",
        "Summary",
        "Author",
        "License-Expression",
        "License-File",
        "Requires-Python",
        "Requires-Dist",
        "Description-Content-Type",
    }
    if (
        set(metadata.keys()) - allowed
        or metadata.defects
        or metadata.get_all("Metadata-Version")
        not in [[version] for version in ("2.1", "2.2", "2.3", "2.4", "2.5")]
    ):
        raise BuildError("Unexpected wheel metadata headers")
    expected_headers = {
        "Name": project["name"],
        "Version": project["version"],
        "Requires-Python": project["requires-python"],
        "Summary": project["description"],
        "License-Expression": project["license"],
        "Author": ", ".join(author["name"] for author in project["authors"]),
        "Description-Content-Type": "text/markdown",
    }
    for name, value in expected_headers.items():
        if metadata.get_all(name) != [value]:
            raise BuildError(f"Wheel metadata does not match pyproject.toml: {name}")
    if sorted(metadata.get_all("License-File", [])) != ["LICENSE", "NOTICE"]:
        raise BuildError("Wheel must preserve both license and notice files")
    if sorted(_dependency(value) for value in metadata.get_all("Requires-Dist", [])) != sorted(
        _dependency(value) for value in project["dependencies"]
    ):
        raise BuildError("Wheel dependencies do not match pyproject.toml")
    readme = project.get("readme", "README.md")
    body = metadata.get_payload(decode=True)
    if (
        not isinstance(readme, str)
        or not isinstance(body, bytes)
        or body.decode("utf-8").rstrip("\n")
        != read_source(root, readme).decode("utf-8").rstrip("\n")
    ):
        raise BuildError("Wheel description does not match the current project README")
    wheel = BytesParser(policy=policy.default).parsebytes(data[prefix + "WHEEL"])
    if set(wheel.keys()) != {"Wheel-Version", "Generator", "Root-Is-Purelib", "Tag"} or (
        wheel.get_all("Wheel-Version") != ["1.0"]
        or wheel.get_all("Root-Is-Purelib") != ["true"]
        or wheel.get_all("Tag") != ["py3-none-any"]
        or len(wheel.get_all("Generator", [])) != 1
        or not re.fullmatch(r"hatchling [0-9]+(?:\.[0-9]+){1,2}", wheel["Generator"])
        or wheel.get_payload().strip()
    ):
        raise BuildError("Expected an unmodified pure Python wheel from Hatchling")
    entrypoints = configparser.ConfigParser(interpolation=None)
    try:
        entrypoints.read_string(data[prefix + "entry_points.txt"].decode("utf-8"))
        if (
            entrypoints.sections() != ["console_scripts"]
            or dict(entrypoints["console_scripts"]) != project["scripts"]
            or entrypoints.defaults()
        ):
            raise BuildError("Wheel entry points do not match pyproject.toml")
    except configparser.Error as error:
        raise BuildError("Invalid wheel entry points") from error
    for filename in ("LICENSE", "NOTICE"):
        if data[prefix + "licenses/" + filename] != read_source(root, filename):
            raise BuildError(f"Wheel {filename} does not match the project")


def _check_record(data: dict[str, bytes], prefix: str) -> None:
    record_name = prefix + "RECORD"
    try:
        rows = list(csv.reader(io.StringIO(data[record_name].decode("utf-8")), strict=True))
    except (csv.Error, UnicodeError) as error:
        raise BuildError("Invalid wheel RECORD") from error
    records: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or row[0] in records:
            raise BuildError("Duplicate or malformed wheel RECORD entry")
        records[row[0]] = (row[1], row[2])
    if records.keys() != data.keys():
        raise BuildError("Wheel RECORD does not list exactly every member")
    for name, payload in data.items():
        checksum, size = records[name]
        if name == record_name:
            if checksum or size:
                raise BuildError("Wheel RECORD must leave its own digest and size empty")
            continue
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        if checksum != "sha256=" + encoded or size != str(len(payload)):
            raise BuildError(f"Wheel RECORD hash or size mismatch: {name}")


def _check_zip_envelope(payload: bytes, infos: list[zipfile.ZipInfo]) -> None:
    # RECORD excludes archive metadata. Reject unused ZIP space that could carry
    # private data outside the reviewed members, as well as unsupported layouts.
    if len(payload) < 22:
        raise BuildError("Incomplete wheel ZIP envelope")
    footer = struct.unpack("<4s4H2LH", payload[-22:])
    signature, disk, directory_disk, disk_count, count, directory_size, directory_at, comment = (
        footer
    )
    if (
        signature != b"PK\x05\x06"
        or disk
        or directory_disk
        or comment
        or disk_count != count
        or count != len(infos)
        or directory_at + directory_size != len(payload) - 22
    ):
        raise BuildError("Wheel ZIP comments, trailing data, or split archives are not allowed")
    offset = 0
    expected_directory_size = 0
    for info in sorted(infos, key=lambda member: member.header_offset):
        if info.header_offset != offset or offset + 30 > directory_at:
            raise BuildError("Wheel ZIP contains unreviewed space between members")
        header = struct.unpack("<4s5H3L2H", payload[offset : offset + 30])
        encoded_name = info.filename.encode("ascii")
        name_end = offset + 30 + header[9]
        if (
            header[0] != b"PK\x03\x04"
            or header[2] != 0
            or header[10] != 0
            or header[2] != info.flag_bits
            or header[3] != info.compress_type
            or header[6:9] != (info.CRC, info.compress_size, info.file_size)
            or payload[offset + 30 : name_end] != encoded_name
        ):
            raise BuildError("Wheel ZIP local headers contain unreviewed metadata")
        offset = name_end + info.compress_size
        expected_directory_size += 46 + len(encoded_name)
    if offset != directory_at or expected_directory_size != directory_size:
        raise BuildError("Wheel ZIP contains unreviewed metadata or unused bytes")


def validate_wheel(root: Path, wheel_path: Path, project: dict[str, Any]) -> bytes:
    version = project["version"]
    expected_filename = f"everval_ai-{version}-py3-none-any.whl"
    if project["name"] != "everval-ai" or wheel_path.name != expected_filename:
        raise BuildError("Wheel filename or distribution does not match pyproject.toml")
    payload = read_regular(wheel_path, maximum=MAX_WHEEL_BYTES)
    expected = expected_package(root)
    prefix = f"everval_ai-{version}.dist-info/"
    metadata_members = {
        prefix + filename
        for filename in (
            "METADATA",
            "WHEEL",
            "entry_points.txt",
            "RECORD",
            "licenses/LICENSE",
            "licenses/NOTICE",
        )
    }
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if archive.comment:
                raise BuildError("Wheel ZIP comments are not allowed")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_MEMBERS or len(names) != len(set(names)):
                raise BuildError("Duplicate or excessive wheel members")
            if sum(info.file_size for info in infos) > MAX_WHEEL_BYTES:
                raise BuildError("Wheel uncompressed payload exceeds the limit")
            for info in infos:
                name = info.filename
                kind = stat.S_IFMT(info.external_attr >> 16)
                if (
                    name.startswith("/")
                    or "\\" in name
                    or "\0" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or kind not in (0, stat.S_IFREG)
                    or info.is_dir()
                    or info.flag_bits & 1
                    or info.file_size > MAX_FILE_BYTES
                    or info.comment
                    or info.extra
                    or info.orig_filename != info.filename
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or (info.external_attr >> 16) & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
                ):
                    raise BuildError("Unsafe wheel member path, type, or size")
            if set(names) != set(expected) | metadata_members:
                raise BuildError(
                    "Wheel member list differs from the explicit application allowlist"
                )
            _check_zip_envelope(payload, infos)
            data = {name: archive.read(name) for name in names}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise BuildError("Invalid or unsupported wheel archive") from error
    for name, content in data.items():
        check_private(content, name)
        if name in expected and content != expected[name]:
            raise BuildError(f"Wheel source is stale or modified: {name}")
    _check_record(data, prefix)
    _check_metadata(data, prefix, root, project)
    return payload


def validate_requirements(data: bytes) -> None:
    if len(data) > 4 * 1024 * 1024:
        raise BuildError("Runtime requirements exceed the limit")
    check_private(data, "runtime requirements")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise BuildError("Runtime requirements must be UTF-8") from error
    current: list[str] = []
    count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                raise BuildError("Interrupted requirement continuation")
            continue
        continued = stripped.endswith("\\")
        current.append(stripped[:-1].strip() if continued else stripped)
        if continued:
            continue
        requirement = " ".join(current)
        current = []
        pin, *hashes = requirement.split(" --hash=sha256:")
        if (
            not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.+!_-]*(?:\s*;\s*[^\\\r\n]+)?",
                pin,
            )
            or "://" in pin
            or "@" in pin
            or "--" in pin
            or "file:" in pin
        ):
            raise BuildError("Runtime requirements must contain only exact registry pins")
        if not hashes or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes):
            raise BuildError("Every runtime requirement must have SHA-256 hashes")
        if pin.lower().startswith("everval"):
            raise BuildError("The application wheel must not appear in dependency requirements")
        count += 1
    if current or not count:
        raise BuildError("Runtime requirements are empty or incomplete")


def export_requirements(root: Path) -> bytes:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    if "UV_CACHE_DIR" in os.environ:
        environment["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]
    environment["UV_PYTHON_DOWNLOADS"] = "never"
    result = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--offline",
            "--no-config",
            "--no-python-downloads",
            "--no-dev",
            "--no-emit-project",
            "--no-header",
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        raise BuildError("uv could not export the existing lock offline; verify uv.lock is current")
    validate_requirements(result.stdout)
    return result.stdout


def package_metadata(
    template: dict[str, Any], project: dict[str, Any], name: str | None, access: str | None
) -> dict[str, Any]:
    metadata = dict(template)
    selected = name if name is not None else metadata.get("name", "")
    component = r"[a-z0-9][a-z0-9._-]*"
    if (
        not isinstance(selected, str)
        or len(selected) > 214
        or not re.fullmatch(rf"(?:@{component}/)?{component}", selected)
        or selected in {"node_modules", "favicon.ico"}
    ):
        raise BuildError("Use a valid lowercase npm package name")
    if access not in (None, "public", "restricted"):
        raise BuildError("Package access must be public or restricted")
    if access == "restricted" and not selected.startswith("@"):
        raise BuildError("Restricted npm packages must use an @scope/name")
    if metadata.get("version") != project["version"] or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", project["version"]
    ):
        raise BuildError("npm and Python versions must match and use semantic versioning")
    allowed = {
        "name",
        "version",
        "private",
        "description",
        "type",
        "bin",
        "files",
        "scripts",
        "engines",
        "os",
        "license",
        "author",
        "repository",
        "homepage",
        "bugs",
        "keywords",
        "publishConfig",
    }
    if set(metadata) - allowed or set(metadata.get("scripts", {})) - {"test"}:
        raise BuildError(
            "npm template must have no dependencies, installation hooks, or extra fields"
        )
    if (
        metadata.get("bin") != {"everval": "bin/everval.mjs", "everval-ai": "bin/everval.mjs"}
        or metadata.get("type") != "module"
    ):
        raise BuildError("npm template must use the reviewed CLI entry points")
    metadata["name"] = selected
    metadata["files"] = PACKAGE_FILES
    metadata.pop("scripts", None)
    metadata.pop("publishConfig", None)
    if access is None:
        metadata["private"] = True
    else:
        metadata.pop("private", None)
        metadata["publishConfig"] = {"access": access}
    return metadata


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_distribution(
    root: Path, wheel: Path, output: Path, *, name: str | None = None, access: str | None = None
) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise BuildError("Output already exists; choose a new staging directory")
    if not output.parent.is_dir():
        raise BuildError("Create the output parent directory before staging")
    project = tomllib.loads(read_source(root, "pyproject.toml").decode("utf-8"))["project"]
    template = json.loads(read_source(root, "npm/package.json"))
    metadata = package_metadata(template, project, name, access)
    wheel_bytes = validate_wheel(root, wheel, project)
    requirements = export_requirements(root)
    files = {relative: read_source(root, "npm/" + relative) for relative in SHIPPING_SOURCES}
    files.update({filename: read_source(root, filename) for filename in ("LICENSE", "NOTICE")})
    files["README.md"] = read_source(root, "npm/README.md").replace(
        b"__NPM_PACKAGE__", metadata["name"].encode("ascii")
    )
    wheel_relative = "runtime/" + wheel.name
    files[wheel_relative] = wheel_bytes
    files["runtime/requirements.txt"] = requirements
    manifest = {
        "schema_version": 1,
        "distribution": project["name"],
        "version": project["version"],
        "python": project["requires-python"],
        "wheel": {"path": wheel_relative, "sha256": digest(wheel_bytes)},
        "requirements": {"path": "runtime/requirements.txt", "sha256": digest(requirements)},
    }
    if manifest["python"] != ">=3.12":
        raise BuildError("Update the launcher before changing the supported Python constraint")
    files["runtime.json"] = _json_bytes(manifest)
    files["package.json"] = _json_bytes(metadata)
    for filename, data in files.items():
        if filename != wheel_relative:
            check_private(data, filename)
    # All validation precedes this exclusive creation. Never replace or remove an
    # existing path, including partial artifacts left by an interrupted build.
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as error:
        raise BuildError("Output already exists; choose a new staging directory") from error
    for filename, data in files.items():
        target = output / filename
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o755 if filename == "bin/everval.mjs" else 0o644)
    return {
        "name": metadata["name"],
        "version": project["version"],
        "access": access or "unpublished",
        "private": metadata.get("private", False),
        "files": len(files),
        "wheel_sha256": digest(wheel_bytes),
        "requirements_sha256": digest(requirements),
        "output": str(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--access", choices=("public", "restricted"))
    args = parser.parse_args(argv)
    try:
        result = build_distribution(
            ROOT, args.wheel, args.output, name=args.name, access=args.access
        )
    except (BuildError, OSError, subprocess.SubprocessError, UnicodeError, ValueError) as error:
        print(f"npm staging failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
