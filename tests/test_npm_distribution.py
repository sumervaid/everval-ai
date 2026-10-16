"""The npm artifact must be reproducible, bounded, and free of workspace data."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_npm.py"
SPEC = importlib.util.spec_from_file_location("build_npm", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

PREFIX = "everval_ai-0.3.1.dist-info/"
LOCK = b"example==1.2.3 \\\n    --hash=sha256:" + b"a" * 64 + b"\n    # via everval-ai\n"


def write(root: Path, name: str, value: str | bytes) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value.encode() if isinstance(value, str) else value)


def record(members: dict[str, bytes]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, data in members.items():
        checksum = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + checksum, len(data)])
    writer.writerow([PREFIX + "RECORD", "", ""])
    return output.getvalue().encode()


def archive(
    filename: Path,
    members: dict[str, bytes],
    *,
    bad_type: str | None = None,
    duplicate: str | None = None,
) -> None:
    with zipfile.ZipFile(filename, "w") as output:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (
                (stat.S_IFLNK if name == bad_type else stat.S_IFREG) | 0o644
            ) << 16
            output.writestr(info, data)
        if duplicate:
            with pytest.warns(UserWarning, match="Duplicate name"):
                output.writestr(duplicate, members[duplicate])


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    root = tmp_path / "source"
    root.mkdir()
    pyproject = """
[project]
name = "everval-ai"
version = "0.3.1"
description = "Example application."
requires-python = ">=3.12"
license = "Apache-2.0"
readme = "README.md"
authors = [{name = "Example Maintainer"}]
dependencies = ["example>=1,<2"]

[project.scripts]
cvals = "cvals.cli:app"
everval = "cvals.cli:app"
"""
    write(root, "pyproject.toml", pyproject)
    write(root, "README.md", "# Example application\n")
    write(root, "LICENSE", "Example Apache license\n")
    write(root, "NOTICE", "Example original notices\n")
    app = {
        "cvals/__init__.py": b'__version__ = "0.3.1"\n',
        "cvals/cli.py": b"app = object()\n",
        "cvals/packs/__init__.py": b"# Original provider-free installation controls.\n",
        "cvals/tui/workbench.tcss": b"Screen { color: white; }\n",
        "cvals/static/index.html": b"<html>Example</html>\n",
        "cvals/static/favicon.svg": b"<svg></svg>\n",
        "cvals/static/assets/index-example.js": b"export const example = true;\n",
        "cvals/static/assets/index-example.css": b"body { color: black; }\n",
        "cvals/static/assets/third-party-notices.txt": b"Example third-party notices\n",
    }
    for name, data in app.items():
        write(root, "src/" + name, data)
    tracked = ["src/" + name for name in app if "/static/" not in name]
    monkeypatch.setattr(builder, "tracked_sources", lambda _: tracked)
    monkeypatch.setattr(builder, "export_requirements", lambda _: LOCK)
    metadata = (
        b"Metadata-Version: 2.5\nName: everval-ai\nVersion: 0.3.1\n"
        b"Summary: Example application.\nAuthor: Example Maintainer\n"
        b"License-Expression: Apache-2.0\nLicense-File: LICENSE\nLicense-File: NOTICE\n"
        b"Requires-Python: >=3.12\nRequires-Dist: example<2,>=1\n"
        b"Description-Content-Type: text/markdown\n\n# Example application\n"
    )
    members = {
        **app,
        PREFIX + "METADATA": metadata,
        PREFIX + "WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: hatchling 1.32.4\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        PREFIX + "entry_points.txt": (
            b"[console_scripts]\ncvals = cvals.cli:app\neverval = cvals.cli:app\n"
        ),
        PREFIX + "licenses/LICENSE": b"Example Apache license\n",
        PREFIX + "licenses/NOTICE": b"Example original notices\n",
    }
    members[PREFIX + "RECORD"] = record(members)
    wheel = tmp_path / "everval_ai-0.3.1-py3-none-any.whl"
    archive(wheel, members)
    template = {
        "name": "everval-ai",
        "version": "0.3.1",
        "type": "module",
        "private": True,
        "bin": {"everval": "bin/everval.mjs", "everval-ai": "bin/everval.mjs"},
        "files": ["test/"],
        "scripts": {"test": "node --test test/*.test.mjs"},
    }
    write(root, "npm/package.json", json.dumps(template))
    write(root, "npm/README.md", "# Example\nnpm install --global __NPM_PACKAGE__\n")
    write(root, "npm/bin/everval.mjs", "#!/usr/bin/env node\n")
    write(root, "npm/lib/launcher.mjs", "export const example = true;\n")
    write(root, "npm/test/launcher.test.mjs", "Do not publish tests.\n")
    write(root, "npm/.env", "Do not publish local data.\n")
    write(root, "npm/runtime/workspace.sqlite3", b"Do not publish a workspace.")
    return SimpleNamespace(
        root=root,
        wheel=wheel,
        members=members,
        template=template,
        project=builder.tomllib.loads(pyproject)["project"],
        output=tmp_path / "staged",
    )


def refresh(source: SimpleNamespace, *, record_data: bytes | None = None, **kwargs) -> None:
    source.members.pop(PREFIX + "RECORD", None)
    source.members[PREFIX + "RECORD"] = record_data or record(source.members)
    archive(source.wheel, source.members, **kwargs)


def stage(source: SimpleNamespace, **kwargs) -> dict:
    return builder.build_distribution(source.root, source.wheel, source.output, **kwargs)


def test_staged_files_are_explicit_and_hashes_cover_the_exact_payload(source) -> None:
    result = stage(source)
    files = {
        file.relative_to(source.output).as_posix()
        for file in source.output.rglob("*")
        if file.is_file()
    }
    assert files == {
        "bin/everval.mjs",
        "lib/launcher.mjs",
        "README.md",
        "LICENSE",
        "NOTICE",
        "runtime/everval_ai-0.3.1-py3-none-any.whl",
        "runtime/requirements.txt",
        "runtime.json",
        "package.json",
    }
    manifest = json.loads((source.output / "runtime.json").read_bytes())
    assert manifest["schema_version"] == 1
    assert manifest["distribution"] == "everval-ai"
    assert manifest["python"] == ">=3.12"
    for key in ("wheel", "requirements"):
        assert manifest[key]["sha256"] == builder.digest(
            (source.output / manifest[key]["path"]).read_bytes()
        )
    assert result["files"] == 9
    assert result["private"] is True and result["access"] == "unpublished"
    package = json.loads((source.output / "package.json").read_bytes())
    assert package["private"] is True
    assert "publishConfig" not in package and "scripts" not in package
    assert package["files"] == builder.PACKAGE_FILES
    assert (source.output / "bin/everval.mjs").stat().st_mode & 0o777 == 0o755
    assert source.output.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "access,name",
    [
        ("public", "everval-ai"),
        ("public", "@example/everval-ai"),
        ("restricted", "@example/everval-ai"),
    ],
)
def test_explicit_visibility_and_selected_name(source, access, name) -> None:
    result = stage(source, access=access, name=name)
    package = json.loads((source.output / "package.json").read_bytes())
    assert "private" not in package
    assert package["publishConfig"] == {"access": access}
    assert package["name"] == result["name"] == name
    readme = (source.output / "README.md").read_text()
    assert "__NPM_PACKAGE__" not in readme
    assert "npm install --global " + name in readme


@pytest.mark.parametrize(
    "kwargs",
    [
        {"access": "restricted"},
        {"access": "unknown"},
        {"name": "../../private"},
        {"name": "MixedCase"},
        {"name": "@scope/no space"},
    ],
)
def test_invalid_visibility_or_name_creates_nothing(source, kwargs) -> None:
    with pytest.raises(builder.BuildError):
        stage(source, **kwargs)
    assert not source.output.exists()


def test_existing_output_is_never_overwritten_or_deleted(source) -> None:
    source.output.mkdir()
    marker = source.output / "operator-owned"
    marker.write_text("retain")
    with pytest.raises(builder.BuildError, match="already exists"):
        stage(source)
    assert marker.read_text() == "retain"


def test_existing_dangling_output_symlink_is_never_followed(source) -> None:
    destination = source.output.parent / "absent"
    source.output.symlink_to(destination)
    with pytest.raises(builder.BuildError, match="already exists"):
        stage(source)
    assert source.output.is_symlink() and not destination.exists()


@pytest.mark.parametrize(
    "name",
    [
        "cvals/workspace.sqlite3",
        "cvals/.env",
        "cvals/private/task.json",
        "cvals/private.py",
        "cvals/__pycache__/cli.cpython-312.pyc",
        "tests/test_private.py",
        "cvals/static/assets/index-example.js.map",
        "../secret.py",
        "/absolute.py",
        "cvals\\secret.py",
    ],
)
def test_unapproved_or_unsafe_wheel_members_are_rejected(source, name) -> None:
    source.members[name] = b"private material"
    refresh(source)
    with pytest.raises(builder.BuildError, match="allowlist|Unsafe"):
        stage(source)
    assert not source.output.exists()


def test_duplicate_archive_member_is_rejected(source) -> None:
    archive(source.wheel, source.members, duplicate="cvals/cli.py")
    with pytest.raises(builder.BuildError, match="Duplicate"):
        stage(source)


def test_archive_symbolic_link_is_rejected(source) -> None:
    archive(source.wheel, source.members, bad_type="cvals/cli.py")
    with pytest.raises(builder.BuildError, match="Unsafe"):
        stage(source)


def test_source_symlink_is_rejected(source) -> None:
    path = source.root / "npm/lib/launcher.mjs"
    path.unlink()
    path.symlink_to(source.root / "README.md")
    with pytest.raises(builder.BuildError, match="Symbolic links"):
        stage(source)
    assert not source.output.exists()


def test_browser_directory_symlink_is_rejected(source) -> None:
    (source.root / "src/cvals/static/assets/escape").symlink_to(
        source.root, target_is_directory=True
    )
    with pytest.raises(builder.BuildError, match="symbolic links"):
        stage(source)


def test_stale_source_wheel_is_rejected_even_with_valid_record(source) -> None:
    source.members["cvals/cli.py"] = b"app = 'stale or injected'\n"
    refresh(source)
    with pytest.raises(builder.BuildError, match="stale or modified"):
        stage(source)


def test_record_tampering_is_rejected(source) -> None:
    record_data = source.members[PREFIX + "RECORD"].replace(b"sha256=", b"sha512=", 1)
    refresh(source, record_data=record_data)
    with pytest.raises(builder.BuildError, match="hash or size"):
        stage(source)


def test_duplicate_record_entry_is_rejected(source) -> None:
    record_data = source.members[PREFIX + "RECORD"]
    refresh(source, record_data=record_data + record_data.splitlines(keepends=True)[0])
    with pytest.raises(builder.BuildError, match="Duplicate"):
        stage(source)


@pytest.mark.parametrize(
    "value",
    [
        b"/Users/example/private/project",
        b"/Volumes/example-disk/private/project",
        b"sk-" + b"proj-" + b"a" * 50,
        b"-----BEGIN " + b"PRIVATE KEY-----",
    ],
)
def test_private_content_is_rejected_even_in_a_tracked_source(source, value) -> None:
    write(source.root, "src/cvals/cli.py", value)
    source.members["cvals/cli.py"] = value
    refresh(source)
    with pytest.raises(builder.BuildError, match="private credential or maintainer path"):
        stage(source)
    assert not source.output.exists()


@pytest.mark.parametrize(
    "field,before,after",
    [
        ("METADATA", b"Version: 0.3.1", b"Version: 0.3.2"),
        ("METADATA", b"example<2,>=1", b"example<3,>=2"),
        ("METADATA", b"# Example application", b"# Private deployment notes"),
        ("entry_points.txt", b"cvals.cli:app", b"untrusted.module:app"),
        ("licenses/NOTICE", b"Example original", b"Changed"),
    ],
)
def test_wheel_metadata_and_legal_files_match_current_source(source, field, before, after) -> None:
    source.members[PREFIX + field] = source.members[PREFIX + field].replace(before, after)
    refresh(source)
    with pytest.raises(builder.BuildError, match="match"):
        stage(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("dependencies", {"malicious-install": "*"}),
        ("scripts", {"postinstall": "download-and-execute"}),
        ("bin", {"everval": "unknown.js"}),
    ],
)
def test_template_cannot_add_dependencies_hooks_or_unreviewed_entrypoints(
    source, field, value
) -> None:
    source.template[field] = value
    write(source.root, "npm/package.json", json.dumps(source.template))
    with pytest.raises(builder.BuildError):
        stage(source)


@pytest.mark.parametrize(
    "requirement",
    [
        b"example>=1\n",
        b"example==1\n",
        b"example @ https://private.example/package.whl --hash=sha256:" + b"a" * 64 + b"\n",
        b"--index-url https://private.example\n",
        b"example==1 --hash=md5:" + b"a" * 32 + b"\n",
        b"",
    ],
)
def test_requirements_reject_unpinned_unhashed_or_remote_instructions(requirement) -> None:
    with pytest.raises(builder.BuildError):
        builder.validate_requirements(requirement)


def test_requirements_accept_hashed_pins_with_platform_markers() -> None:
    builder.validate_requirements(LOCK)
    builder.validate_requirements(
        b"example==1.2.3 ; sys_platform == 'win32' --hash=sha256:" + b"a" * 64 + b"\n"
    )


def test_export_uses_frozen_offline_source_without_ambient_uv_overrides(
    tmp_path, monkeypatch
) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, LOCK, b"")

    monkeypatch.setattr(builder.subprocess, "run", run)
    monkeypatch.setenv("UV_NO_VERIFY_HASHES", "true")
    monkeypatch.setenv("UV_OVERRIDE", "/private/override.txt")
    monkeypatch.setenv("UV_CONFIG_FILE", "/private/uv.toml")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "approved-cache"))
    monkeypatch.setenv("PYTHONPATH", "/private/modules")
    assert builder.export_requirements(tmp_path) == LOCK
    command, kwargs = calls[0]
    assert command == [
        "uv",
        "export",
        "--locked",
        "--offline",
        "--no-config",
        "--no-python-downloads",
        "--no-dev",
        "--no-emit-project",
        "--no-header",
    ]
    assert kwargs["cwd"] == tmp_path
    environment = kwargs["env"]
    assert environment["UV_PYTHON_DOWNLOADS"] == "never"
    assert environment["UV_CACHE_DIR"] == str(tmp_path / "approved-cache")
    assert (
        not {"UV_NO_VERIFY_HASHES", "UV_OVERRIDE", "UV_CONFIG_FILE", "PYTHONPATH"}
        & environment.keys()
    )


def test_utf8_readme_is_compared_without_ascii_replacement(source) -> None:
    readme = "# Everval AI — local evaluation\nCafé: 日本語\n".encode()
    write(source.root, "README.md", readme)
    headers, _ = source.members[PREFIX + "METADATA"].split(b"\n\n", 1)
    source.members[PREFIX + "METADATA"] = headers + b"\n\n" + readme
    refresh(source)
    assert stage(source)["private"] is True


@pytest.mark.parametrize(
    "location", ["archive_comment", "member_comment", "extra", "trailer", "prefix"]
)
def test_zip_metadata_cannot_smuggle_unrecorded_private_data(source, location) -> None:
    if location in ("archive_comment", "member_comment", "extra"):
        with zipfile.ZipFile(source.wheel, "w") as output:
            for name, data in source.members.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                if name == "cvals/cli.py":
                    if location == "member_comment":
                        info.comment = b"unrecorded private notes"
                    if location == "extra":
                        info.extra = b"\xfe\xca\x04\x00data"
                output.writestr(info, data)
            if location == "archive_comment":
                output.comment = b"unrecorded private notes"
    else:
        payload = source.wheel.read_bytes()
        source.wheel.write_bytes(
            b"unrecorded private notes" + payload
            if location == "prefix"
            else payload + b"unrecorded private notes"
        )
    with pytest.raises(builder.BuildError, match="ZIP|Unsafe"):
        stage(source)
    assert not source.output.exists()


def test_unreviewed_metadata_version_field_is_rejected(source) -> None:
    source.members[PREFIX + "METADATA"] = source.members[PREFIX + "METADATA"].replace(
        b"Metadata-Version: 2.5", b"Metadata-Version: internal deployment notes"
    )
    refresh(source)
    with pytest.raises(builder.BuildError, match="metadata"):
        stage(source)
