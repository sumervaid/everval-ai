"""Prepare reusable Python test environments without executing repository setup."""

from __future__ import annotations

import builtins
import configparser
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tomllib
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.domains._process import run_bounded
from cvals.models import canonical_json, content_hash, utc_now
from cvals.projects import Projects
from cvals.sandbox import _docker, _environment, _image, _workspace_label
from cvals.store import Store

MAX_REQUIREMENTS = 128
MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_WHEEL_FILES = 200
DEFAULT_PYTEST = "pytest==8.4.2"
PREPARATION_REVISION = "python-wheels-v2"

_DOWNLOAD = r"""
import json, os, pathlib, subprocess, sys, tarfile, tempfile
requirements = json.loads(sys.argv[1])
with tempfile.TemporaryDirectory() as directory:
    command = [sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
               "download", "--only-binary=:all:", "--no-cache-dir", "--progress-bar=off",
               "--timeout=20", "--retries=1", "--index-url=https://pypi.org/simple",
               "--dest", directory, *requirements]
    result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, timeout=180)
    if result.returncode:
        raise SystemExit(result.returncode)
    paths = sorted(pathlib.Path(directory).iterdir())
    if not paths or len(paths) > 200 or sum(path.stat().st_size for path in paths) > 128*1024*1024:
        raise SystemExit("Wheel download exceeded the bounded package policy")
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        for path in paths:
            if not path.name.endswith(".whl") or not path.is_file() or path.is_symlink():
                raise SystemExit("Only wheel files are admitted")
            archive.add(path, arcname=path.name, recursive=False)
"""

_INSTALL = r"""
import json, shutil, subprocess, sys
requirements = json.loads(sys.argv[1])
command = [sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
           "install", "--only-binary=:all:", "--no-cache-dir", "--no-compile",
           "--no-index", "--find-links=/wheelhouse", "--progress-bar=off",
           "--root-user-action=ignore", *requirements]
result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, timeout=120)
if result.returncode:
    raise SystemExit(result.returncode)
frozen = subprocess.run([sys.executable, "-I", "-m", "pip", "--isolated",
                         "--disable-pip-version-check", "freeze", "--all"],
                        capture_output=True, text=True, timeout=30, check=True)
if len(frozen.stdout.encode()) > 128*1024:
    raise SystemExit("Installed package metadata exceeds its size limit")
probe = subprocess.run([sys.executable, "-I", "-c", "import pytest; print(pytest.__version__)"],
                       capture_output=True, text=True, timeout=30, check=True)
shutil.rmtree("/wheelhouse")
print(json.dumps({"pip_freeze": frozen.stdout.splitlines(), "python": sys.version.split()[0],
                  "pytest_version": probe.stdout.strip()}))
"""


def validate_requirements(values: builtins.list[str]) -> builtins.list[str]:
    """Admit named PEP 508 requirements, never URLs, files, or pip options."""
    if not isinstance(values, list) or len(values) > MAX_REQUIREMENTS:
        raise ValueError("At most 128 named Python requirements are supported")
    admitted = set()
    has_pytest = False
    for value in values:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 1000
            or any(char in value for char in "\n\r\0\\")
        ):
            raise ValueError("Requirements must be bounded, single-line PEP 508 package names")
        value = value.strip()
        if value.startswith("-"):
            raise ValueError(
                "Requirement options, editable installs, and include directives are not allowed"
            )
        try:
            parsed = Requirement(value)
        except InvalidRequirement as exc:
            raise ValueError(
                "Only named PEP 508 requirements are supported; local paths and setup scripts are not allowed"
            ) from exc
        if parsed.url is not None:
            raise ValueError(
                "URL and VCS dependencies are not allowed; use reviewed packages from the public wheel index"
            )
        has_pytest |= canonicalize_name(parsed.name) == "pytest"
        admitted.add(str(parsed))
    if not has_pytest:
        admitted.add(DEFAULT_PYTEST)
    return sorted(admitted)


def _requirements_text(text: str) -> builtins.list[str]:
    if len(text.encode()) > 64 * 1024:
        raise ValueError("Dependency declaration exceeds 64 KiB")
    records = []
    for line in text.splitlines():
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if line and not line.startswith("#"):
            records.append(line)
    return records


def detect_requirements(files: dict[str, str]) -> dict[str, Any]:
    """Read declarations as data; setup.py and package code are never imported."""
    values: builtins.list[str] = []
    sources: dict[str, str] = {}
    notices = []
    requirement_paths = (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "requirements-tests.txt",
        "requirements/test.txt",
        "requirements/tests.txt",
        "requirements/dev.txt",
    )
    for path in requirement_paths:
        if path in files:
            values.extend(_requirements_text(files[path]))
            sources[path] = hashlib.sha256(files[path].encode()).hexdigest()
    static_dependencies = bool(sources)
    if "pyproject.toml" in files:
        text = files["pyproject.toml"]
        if len(text.encode()) > 64 * 1024:
            raise ValueError("pyproject.toml exceeds the bounded dependency parser")
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError("Cannot parse pyproject.toml dependency declarations") from exc
        project = data.get("project", {})
        groups = data.get("dependency-groups", {})
        tool = data.get("tool", {})
        if (
            not isinstance(project, dict)
            or not isinstance(groups, dict)
            or not isinstance(tool, dict)
        ):
            raise ValueError("Malformed pyproject.toml dependency tables")
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic, list):
            raise ValueError("Malformed dynamic dependency declaration")
        if "dependencies" in dynamic:
            raise ValueError("Dynamic dependencies require an explicit reviewed package list")
        static_dependencies |= "dependencies" in project
        dependencies = project.get("dependencies", [])
        optional = project.get("optional-dependencies", {})
        if not isinstance(dependencies, list) or not isinstance(optional, dict):
            raise ValueError("Malformed pyproject.toml dependencies")
        dependencies = list(dependencies)
        for group in ("test", "tests", "testing", "dev"):
            for items in (optional.get(group, []), groups.get(group, [])):
                if not isinstance(items, list):
                    raise ValueError("Malformed pyproject.toml dependency group")
                dependencies.extend(items)
        if any(not isinstance(value, str) for value in dependencies):
            raise ValueError(
                "Unsupported dependency group; supply an explicit reviewed package list"
            )
        poetry = tool.get("poetry", {})
        if not isinstance(poetry, dict):
            raise ValueError("Malformed pyproject.toml Poetry declaration")
        if poetry.get("dependencies") and not dependencies and not sources:
            raise ValueError(
                "Poetry-specific dependencies require an explicit reviewed package list"
            )
        values.extend(dependencies)
        sources["pyproject.toml"] = hashlib.sha256(text.encode()).hexdigest()
        if data.get("build-system"):
            notices.append(
                "The build system and project installation are intentionally not executed; import from the repository snapshot."
            )
    if "setup.cfg" in files:
        text = files["setup.cfg"]
        if len(text.encode()) > 64 * 1024:
            raise ValueError("setup.cfg exceeds the bounded dependency parser")
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text)
            static_dependencies |= parser.has_option("options", "install_requires")
            values.extend(
                _requirements_text(parser.get("options", "install_requires", fallback=""))
            )
            for group in ("test", "tests", "testing", "dev"):
                values.extend(
                    _requirements_text(parser.get("options.extras_require", group, fallback=""))
                )
        except configparser.Error as exc:
            raise ValueError("Cannot parse setup.cfg dependency declarations") from exc
        sources["setup.cfg"] = hashlib.sha256(text.encode()).hexdigest()
    if "setup.py" in files and not static_dependencies:
        raise ValueError(
            "setup.py cannot be evaluated on the host; supply an explicit reviewed package list"
        )
    if any(path in files for path in ("uv.lock", "poetry.lock", "Pipfile.lock")):
        notices.append(
            "Lockfile resolution is not imported by this connector; installed wheel hashes and pip freeze are retained."
        )
    return {
        "requirements": validate_requirements(values),
        "declaration_hashes": sources,
        "notices": notices,
        "detection": "static declarations" if sources else "dependency-free Python",
    }


def _wheel_archive(payload: bytes) -> tuple[bytes, builtins.list[dict[str, Any]]]:
    """Validate downloaded archives before transferring any bytes to installation."""
    if len(payload) > MAX_WHEEL_BYTES + 1024 * 1024:
        raise ValueError("Wheel transfer exceeds 128 MiB")
    result = io.BytesIO()
    records: builtins.list[dict[str, Any]] = []
    total = expanded = entries = 0
    seen = set()
    try:
        with (
            tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive,
            tarfile.open(fileobj=result, mode="w") as output,
        ):
            directory = tarfile.TarInfo("wheelhouse")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            output.addfile(directory)
            for member in archive:
                name = member.name
                if (
                    len(records) >= MAX_WHEEL_FILES
                    or not member.isfile()
                    or not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", name)
                    or name in seen
                ):
                    raise ValueError("Wheel transfer contains an unsupported path or object")
                seen.add(name)
                total += member.size
                if member.size < 1 or total > MAX_WHEEL_BYTES:
                    raise ValueError("Wheel transfer exceeds its file or aggregate ceiling")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("Wheel transfer is incomplete")
                data = stream.read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError("Wheel transfer is truncated")
                with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                    for entry in wheel.infolist():
                        path = PurePosixPath(entry.filename)
                        entries += 1
                        expanded += entry.file_size
                        mode = entry.external_attr >> 16
                        if (
                            path.is_absolute()
                            or ".." in path.parts
                            or "\\" in entry.filename
                            or (mode & 0o170000) == 0o120000
                        ):
                            raise ValueError("Wheel contains an unsafe path or symbolic link")
                        if entries > 20000 or expanded > MAX_EXPANDED_BYTES:
                            raise ValueError(
                                "Expanded wheels exceed the bounded installation policy"
                            )
                info = tarfile.TarInfo(f"wheelhouse/{name}")
                info.mode = 0o444
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
                records.append(
                    {
                        "filename": name,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Dependency download did not produce valid wheel archives") from exc
    if not records:
        raise ValueError("No wheel packages were downloaded")
    return result.getvalue(), records


class Environments:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        self.projects = Projects(settings)
        self.audit = AuditLog(settings)
        self.root = settings.data_dir / "environments"
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("Environment storage may not be a symbolic link")
        with self.store._connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS prepared_environments (dependency_hash TEXT PRIMARY KEY,data TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS environment_preparations (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,data TEXT NOT NULL)"
            )

    def list(self, project_id: str | None = None) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            if project_id is None:
                rows = connection.execute(
                    "SELECT data FROM environment_preparations ORDER BY rowid DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT data FROM environment_preparations WHERE project_id=? ORDER BY rowid DESC",
                    (project_id,),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def plan(
        self,
        project_id: str,
        *,
        packages: builtins.list[str] | None = None,
        revision: str | None = None,
        base_image: str | None = None,
        language: str = "python",
        runner: str = "node-test",
        artifacts: builtins.list[str] | None = None,
    ) -> dict[str, Any]:
        if language != "python":
            from cvals.node_environments import plan

            return plan(
                self,
                project_id,
                revision=revision,
                base_image=base_image,
                language=language,
                runner=runner,
                artifacts=artifacts,
                packages=packages,
            )
        from cvals.private_artifacts import PrivateArtifacts

        artifact_records = PrivateArtifacts(self.settings).resolve(artifacts, kind="wheel")
        project = self.projects.get(project_id)
        revision = revision or project["head_sha"]
        snapshot = self.projects.snapshot(
            project_id, revision, include_tests=True, max_files=2048, max_bytes=32 * 1024 * 1024
        )
        if packages is None:
            required_names = {
                "requirements.txt",
                "requirements-dev.txt",
                "requirements-test.txt",
                "requirements-tests.txt",
                "pyproject.toml",
                "setup.cfg",
            }
            if any(
                item["path"] in required_names or item["path"].startswith("requirements/")
                for item in snapshot["omitted"]
            ):
                raise ValueError(
                    "Dependency declarations were omitted from the snapshot; supply an explicit reviewed package list"
                )
            detected = detect_requirements(snapshot["files"])
        else:
            detected = {
                "requirements": validate_requirements(packages),
                "declaration_hashes": {},
                "notices": [],
                "detection": "explicit package list",
            }
        base = _image(
            replace(self.settings, sandbox_image=base_image or self.settings.sandbox_image)
        )
        dependency_hash = content_hash(
            {
                "revision": PREPARATION_REVISION,
                "base": base,
                "requirements": detected["requirements"],
                **({"artifacts": artifact_records} if artifact_records else {}),
            }
        )
        manifest = {
            "commit": revision,
            "declarations": detected["declaration_hashes"],
            "explicit": packages is not None,
        }
        return {
            "project_id": project_id,
            "language": "python",
            "runner": "python-pytest",
            "artifacts": artifact_records,
            "source_commit": revision,
            "source_manifest_hash": content_hash(manifest),
            "source_manifest": manifest,
            "base_image_digest": base,
            "dependency_hash": dependency_hash,
            "requested_base_image": base_image or self.settings.sandbox_image,
            **detected,
            "network_policy": "Offline imported wheel closure; no network"
            if artifact_records
            else "Dependency download only; install and grading have no network",
            "package_index": "https://pypi.org/simple",
            "repository_execution": False,
            "preparation_revision": PREPARATION_REVISION,
        }

    def prepare(
        self,
        project_id: str,
        *,
        packages: builtins.list[str] | None = None,
        revision: str | None = None,
        base_image: str | None = None,
        refresh: bool = False,
        language: str = "python",
        runner: str = "node-test",
        artifacts: builtins.list[str] | None = None,
    ) -> dict[str, Any]:
        plan = self.plan(
            project_id,
            packages=packages,
            revision=revision,
            base_image=base_image,
            language=language,
            runner=runner,
            artifacts=artifacts,
        )
        descriptor = os.open(
            self.root / f"{plan['dependency_hash']}.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("This dependency environment is already being prepared") from exc
            with self.store._connect() as connection:
                row = connection.execute(
                    "SELECT data FROM prepared_environments WHERE dependency_hash=?",
                    (plan["dependency_hash"],),
                ).fetchone()
            cached = json.loads(row[0]) if row and not refresh else None
            if cached:
                try:
                    _image(replace(self.settings, sandbox_image=cached["image_digest"]))
                except ValueError:
                    cached = None
            environment = cached or self._build(plan)
            record = {
                **plan,
                **environment,
                "id": content_hash(
                    {
                        "project": project_id,
                        "manifest": plan["source_manifest_hash"],
                        "image": environment["image_digest"],
                    }
                )[:24],
                "created_at": utc_now(),
                "cache_hit": cached is not None,
            }
            with self.store._connect(write=True) as connection:
                connection.execute(
                    "INSERT INTO prepared_environments(dependency_hash,data) VALUES(?,?) ON CONFLICT(dependency_hash) DO UPDATE SET data=excluded.data",
                    (plan["dependency_hash"], canonical_json(environment)),
                )
                connection.execute(
                    "INSERT INTO environment_preparations(id,project_id,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (record["id"], project_id, canonical_json(record)),
                )
                self.audit.record(
                    "environment.prepared",
                    record["id"],
                    {
                        "project_id": project_id,
                        "dependency_hash": plan["dependency_hash"],
                        "image_digest": record["image_digest"],
                        "source_manifest_hash": plan["source_manifest_hash"],
                        "cache_hit": record["cache_hit"],
                    },
                    connection=connection,
                )
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _create(
        self,
        name: str,
        digest: str,
        script: str,
        requirements: builtins.list[str],
        *,
        download: bool,
    ) -> None:
        command = [
            "create",
            "--name",
            name,
            "--pull=never",
            "--label=app=custom-vals-environment",
            "--label=cvals.environment.transient=true",
            f"--label=cvals.workspace={_workspace_label(self.settings)}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=768m",
            "--memory-swap=768m",
            "--cpus=1",
            "--ulimit=nofile=256:256",
            "--ulimit=core=0:0",
            "--log-driver=none",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PIP_CONFIG_FILE=/dev/null",
            "--network=bridge" if download else "--network=none",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=384m,mode=1777",
        ]
        if download:
            command += ["--read-only", "--user=65532:65532"]
        else:
            command += ["--user=0:0"]
        command += [digest, "python", "-I", "-B", "-c", script, canonical_json(requirements)]
        result = _docker(command, self.settings)
        if result.returncode:
            raise ValueError("Cannot create isolated dependency preparation container")

    def _start(
        self, name: str, *, timeout: int, output_limit: int
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            result = run_bounded(
                ["docker", "start", "--attach", name],
                env=_environment(self.settings),
                timeout=timeout,
                limit_bytes=output_limit,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ValueError(
                "Dependency preparation exceeded its deadline or output limit"
            ) from exc
        if result.returncode:
            raise ValueError(
                "Dependency preparation failed. Verify the pinned dependency closure and compatible package artifacts; lifecycle scripts and source builds are disabled."
            )
        state = _docker(["inspect", "--format", "{{json .State}}", name], self.settings)
        try:
            details = json.loads(state.stdout)
        except ValueError as exc:
            raise ValueError("Cannot verify dependency preparation exit state") from exc
        if (
            state.returncode
            or details.get("Running")
            or details.get("OOMKilled")
            or details.get("ExitCode") != 0
        ):
            raise ValueError(
                "Dependency preparation did not complete successfully within its resource limits"
            )
        return result

    def _build(self, plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("language", "python") != "python":
            from cvals.node_environments import build

            return build(self, plan)
        suffix = uuid4().hex
        download = f"cvals-env-download-{suffix}"
        install = f"cvals-env-install-{suffix}"
        image = None
        finished = False
        try:
            if plan.get("artifacts"):
                from cvals.private_artifacts import PrivateArtifacts

                store = PrivateArtifacts(self.settings)
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as output:
                    for artifact in plan["artifacts"]:
                        payload = store.read(artifact["id"])
                        item = tarfile.TarInfo(artifact["filename"])
                        item.size = len(payload)
                        output.addfile(item, io.BytesIO(payload))
                archive, wheels = _wheel_archive(buffer.getvalue())
            else:
                self._create(
                    download,
                    plan["base_image_digest"],
                    _DOWNLOAD,
                    plan["requirements"],
                    download=True,
                )
                result = self._start(
                    download, timeout=210, output_limit=MAX_WHEEL_BYTES + 1024 * 1024
                )
                archive, wheels = _wheel_archive(result.stdout)
                _docker(["rm", "--force", "--volumes", download], self.settings)
            self._create(
                install, plan["base_image_digest"], _INSTALL, plan["requirements"], download=False
            )
            copied = _docker(
                ["cp", "-", f"{install}:/"], self.settings, payload=archive, timeout=60
            )
            if copied.returncode:
                raise ValueError("Cannot transfer wheel dependencies to the isolated installer")
            installed = self._start(install, timeout=180, output_limit=1024 * 1024)
            try:
                evidence = json.loads(installed.stdout)
                if not isinstance(evidence["pip_freeze"], list) or any(
                    not isinstance(item, str) for item in evidence["pip_freeze"]
                ):
                    raise ValueError("Invalid installed dependency metadata")
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("Installer returned invalid dependency metadata") from exc
            committed = _docker(
                [
                    "commit",
                    "--change",
                    "LABEL cvals.environment.transient=false",
                    "--change",
                    f"LABEL cvals.environment.dependency_hash={plan['dependency_hash']}",
                    "--change",
                    "USER 65532:65532",
                    "--change",
                    'CMD ["python"]',
                    install,
                ],
                self.settings,
                timeout=60,
            )
            image = committed.stdout.decode("utf-8", "replace").strip()
            if committed.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                image = None
                raise ValueError("Cannot seal the prepared dependency image")
            image_reference = f"everval-env:{plan['dependency_hash'][:24]}-{image[7:19]}"
            tagged = _docker(["image", "tag", image, image_reference], self.settings)
            if tagged.returncode:
                raise ValueError("Cannot retain the prepared environment image")
            finished = True
            return {
                "image_digest": image,
                "image_reference": image_reference,
                "pip_freeze": evidence["pip_freeze"],
                "python_version": evidence.get("python"),
                "pytest_version": evidence.get("pytest_version"),
                "wheel_manifest": wheels,
                "wheel_manifest_hash": content_hash(wheels),
                "prepared_at": utc_now(),
                "installs": "imported offline wheels; no source builds"
                if plan.get("artifacts")
                else "public wheels only; no source builds",
                "limits": {
                    "wheel_bytes": MAX_WHEEL_BYTES,
                    "expanded_bytes": MAX_EXPANDED_BYTES,
                    "memory_mib": 768,
                    "download_seconds": 210,
                    "install_seconds": 180,
                },
            }
        finally:
            for container in (download, install):
                _docker(["rm", "--force", "--volumes", container], self.settings)
            if image and not finished:
                _docker(["image", "rm", "--force", "--no-prune", image], self.settings)


def cleanup_environments(settings: Settings, *, minimum_age_seconds: int = 900) -> dict[str, Any]:
    """Remove abandoned preparation containers belonging only to this workspace."""
    if type(minimum_age_seconds) is not int or minimum_age_seconds < 600:
        raise ValueError("Preparation cleanup must preserve containers newer than ten minutes")
    selected = _docker(
        [
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=cvals.environment.transient=true",
            "--filter",
            f"label=cvals.workspace={_workspace_label(settings)}",
        ],
        settings,
    )
    if selected.returncode:
        raise ValueError("Cannot inspect dependency preparation containers")
    removed: builtins.list[str] = []
    errors: builtins.list[str] = []
    for identifier in selected.stdout.decode("ascii", "replace").splitlines():
        if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
            continue
        inspected = _docker(["inspect", "--format", "{{json .}}", identifier], settings)
        try:
            item = json.loads(inspected.stdout)
            labels = item["Config"].get("Labels") or {}
            created = datetime.fromisoformat(item["Created"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            errors.append(identifier)
            continue
        if (
            inspected.returncode
            or labels.get("cvals.environment.transient") != "true"
            or labels.get("cvals.workspace") != _workspace_label(settings)
            or not item.get("Name", "").startswith("/cvals-env-")
        ):
            continue
        if (datetime.now(UTC) - created).total_seconds() < minimum_age_seconds:
            continue
        deleted = _docker(["rm", "--force", "--volumes", identifier], settings)
        (errors if deleted.returncode else removed).append(identifier)
    return {
        "removed": removed,
        "errors": errors,
        "images_removed": [],
        "minimum_age_seconds": minimum_age_seconds,
    }
