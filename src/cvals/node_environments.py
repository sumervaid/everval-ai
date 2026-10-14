"""Lockfile-based Node dependency images; repository scripts never execute."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from cvals.models import canonical_json, content_hash
from cvals.private_artifacts import PrivateArtifacts
from cvals.sandbox import _docker, _image, _workspace_label

REVISION = "npm-lockfiles-v1"
_INSTALL = r"""
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
const path = require('node:path');
fs.writeFileSync('/tmp/empty-user-npmrc','');
fs.writeFileSync('/tmp/empty-global-npmrc','');
fs.copyFileSync('/package.json','/deps/package.json');
fs.copyFileSync('/package-lock.json','/deps/package-lock.json');
const opts = {cwd:'/deps',encoding:'utf8',timeout:240000,maxBuffer:1024*1024,
 env:{PATH:process.env.PATH,HOME:'/tmp',TMPDIR:'/tmp',CI:'true',npm_config_cache:'/tmp/npm-cache'}};
const result = spawnSync('npm',['ci','--prefix=/deps','--ignore-scripts','--no-audit','--no-fund',
 '--userconfig=/tmp/empty-user-npmrc','--globalconfig=/tmp/empty-global-npmrc','--fetch-retries=1','--fetch-timeout=20000',
 '--cache=/tmp/npm-cache'],opts);
if (result.status !== 0 || result.error || result.signal) { process.stderr.write(result.stderr || String(result.error || result.signal)); process.exit(2); }
const lock = JSON.parse(fs.readFileSync('/package-lock.json','utf8'));
const packages = Object.entries(lock.packages).filter(([p])=>p).map(([p,v])=>({path:p,version:v.version,integrity:v.integrity}));
const version=spawnSync('npm',['--version'],opts);
let bytes=0,entries=0;
function inspect(directory) {
  for (const item of fs.readdirSync(directory,{withFileTypes:true})) {
    const full=path.join(directory,item.name), stat=fs.lstatSync(full);
    if (++entries>100000 || (bytes+=stat.size)>256*1024*1024) process.exit(5);
    if (stat.isSymbolicLink()) {
      const target=fs.readlinkSync(full), resolved=path.resolve(path.dirname(full),target);
      if (path.isAbsolute(target) || !resolved.startsWith('/deps/node_modules/')) process.exit(6);
    } else if (stat.isDirectory()) inspect(full);
    else if (!stat.isFile()) process.exit(7);
  }
}
if (fs.existsSync('/deps/node_modules')) {
  inspect('/deps/node_modules');
  fs.cpSync('/deps/node_modules','/node_modules',{recursive:true,dereference:false,verbatimSymlinks:true});
}
fs.rmSync('/artifacts',{recursive:true,force:true});
fs.rmSync('/package.json',{force:true});fs.rmSync('/package-lock.json',{force:true});
console.log(JSON.stringify({node:process.version,npm:version.stdout.trim(),installed_packages:packages}));
"""


def _json(text: str, label: str) -> dict[str, Any]:
    if len(text.encode()) > 2 * 1024 * 1024:
        raise ValueError(f"{label} exceeds 2 MiB")
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def lock_plan(
    files: dict[str, str], artifacts: list[dict[str, Any]], store: PrivateArtifacts
) -> dict[str, Any]:
    if "package.json" not in files or "package-lock.json" not in files:
        raise ValueError(
            "Node preparation requires committed package.json and package-lock.json (v2/v3)"
        )
    package = _json(files["package.json"], "package.json")
    lock = _json(files["package-lock.json"], "package-lock.json")
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = package.get(section, {})
        if not isinstance(values, dict) or any(
            not isinstance(name, str)
            or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", name)
            or not isinstance(spec, str)
            or len(spec) > 200
            or any(c in spec for c in ":/\\\n\r\0")
            for name, spec in values.items()
        ):
            raise ValueError(
                "Npm dependency declarations must be named registry versions, without URLs or local paths"
            )
    package = {
        key: value
        for key, value in package.items()
        if key
        in {
            "name",
            "version",
            "private",
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
            "engines",
            "workspaces",
        }
    }
    if package.get("workspaces"):
        raise ValueError(
            "Npm workspaces require separate project preparation; local links are not imported"
        )
    if lock.get("lockfileVersion") not in {2, 3} or not isinstance(lock.get("packages"), dict):
        raise ValueError("Only npm package-lock v2/v3 with a packages table is supported")
    if len(lock["packages"]) > 2000:
        raise ValueError("Npm lockfile exceeds 2000 packages")
    supplied = {(item["name"], item["version"]): item for item in artifacts}
    if len(supplied) != len(artifacts):
        raise ValueError("Ambiguous private artifact versions")
    private_ids = set()
    public = 0
    selected = []
    for path, record in lock["packages"].items():
        if path == "":
            continue
        if (
            not re.fullmatch(
                r"(?:node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+/)*(?:node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+)",
                path,
            )
            or not isinstance(record, dict)
            or record.get("link")
        ):
            raise ValueError(
                "Npm lockfile contains unsupported local, linked or unsafe package paths"
            )
        name = record.get("name") or path.rsplit("node_modules/", 1)[-1]
        version = record.get("version")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", name)
            or not isinstance(version, str)
            or len(version) > 200
        ):
            raise ValueError("Locked npm packages need bounded package names and version strings")
        resolved = record.get("resolved")
        integrity = record.get("integrity")
        if (
            not isinstance(resolved, str)
            or not isinstance(integrity, str)
            or not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", integrity)
        ):
            raise ValueError("Every locked package needs an HTTPS resolution and SHA-512 integrity")
        try:
            remote = urlsplit(resolved)
        except ValueError as exc:
            raise ValueError("Malformed npm resolution URL") from exc
        if (
            remote.scheme != "https"
            or not remote.hostname
            or remote.username
            or remote.password
            or remote.query
            or remote.fragment
            or remote.port not in {None, 443}
        ):
            raise ValueError(
                "Npm lock resolutions cannot contain credentials, query strings, local paths or non-HTTPS URLs"
            )
        artifact = supplied.get((name, version))
        if artifact:
            provenance = urlsplit(artifact["registry"])
            if remote.hostname != provenance.hostname or not remote.path.startswith(
                provenance.path.rstrip("/") + "/"
            ):
                raise ValueError(
                    "Artifact registry provenance does not match the locked package URL"
                )
            payload = store.read(artifact["id"])
            actual = "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()
            if actual != integrity:
                raise ValueError("Private npm artifact does not match lockfile integrity")
            record["resolved"] = "file:/artifacts/" + artifact["id"] + ".tgz"
            private_ids.add(artifact["id"])
        elif remote.hostname != "registry.npmjs.org":
            raise ValueError(
                "Private npm resolution requires an imported artifact with matching integrity and registry provenance"
            )
        else:
            public += 1
        selected.append({"path": path, "name": name, "version": version, "integrity": integrity})
    if private_ids != {item["id"] for item in artifacts}:
        raise ValueError("An imported npm artifact is not referenced by this exact lockfile")
    # Legacy dependency tables contain duplicated resolutions; npm v2 packages
    # are canonical. Remove them so no private URL can trigger a second fetch.
    lock.pop("dependencies", None)
    return {
        "package_json": canonical_json(package),
        "package_lock": canonical_json(lock),
        "locked_packages": selected,
        "public_package_count": public,
        "offline": public == 0,
        "declaration_hashes": {
            p: hashlib.sha256(files[p].encode()).hexdigest()
            for p in ("package.json", "package-lock.json")
        },
    }


def plan(
    environments: Any,
    project_id: str,
    *,
    revision: str | None,
    base_image: str | None,
    language: str,
    runner: str,
    artifacts: list[str] | None,
    packages: list[str] | None,
) -> dict[str, Any]:
    if language not in {"javascript", "typescript"} or runner not in {
        "node-test",
        "jest",
        "vitest",
    }:
        raise ValueError("Unsupported Node language or runner")
    if packages is not None:
        raise ValueError(
            "Node dependencies come from the committed npm lockfile, not a package list"
        )
    project = environments.projects.get(project_id)
    revision = revision or project["head_sha"]
    snapshot = environments.projects.snapshot(
        project_id, revision, include_tests=True, max_files=2048, max_bytes=32 * 1024 * 1024
    )
    store = PrivateArtifacts(environments.settings)
    records = store.resolve(artifacts, kind="npm")
    locked = lock_plan(snapshot["files"], records, store)
    names = {item["name"] for item in locked["locked_packages"]}
    if runner in {"jest", "vitest"} and runner not in names:
        raise ValueError(f"The exact npm lockfile must include the reviewed {runner} runner")
    requested = base_image or "node:24-slim"
    base = _image(replace(environments.settings, sandbox_image=requested))
    manifest = {
        "commit": revision,
        "declarations": locked["declaration_hashes"],
        "artifact_ids": sorted(artifacts or []),
    }
    return {
        "project_id": project_id,
        "source_commit": revision,
        "source_manifest": manifest,
        "source_manifest_hash": content_hash(manifest),
        "base_image_digest": base,
        "requested_base_image": requested,
        "dependency_hash": content_hash(
            {"revision": REVISION, "base": base, "lock": locked, "artifacts": records}
        ),
        "language": language,
        "runner": runner,
        "artifacts": records,
        **locked,
        "requirements": [],
        "notices": [
            "Lifecycle scripts and project builds are disabled. Node TypeScript uses native type stripping; unsupported syntax/configuration must use a reviewed compatible runner."
        ],
        "detection": "committed npm lockfile",
        "network_policy": "Public package fetch during preparation only; private-only preparation and all grading have no network",
        "package_index": "https://registry.npmjs.org",
        "repository_execution": False,
        "preparation_revision": REVISION,
    }


def build(environments: Any, plan: dict[str, Any]) -> dict[str, Any]:
    settings = environments.settings
    name = "cvals-env-npm-" + uuid4().hex
    image = None
    finished = False
    created = False
    try:
        command = [
            "create",
            "--name",
            name,
            "--pull=never",
            "--label=app=custom-vals-environment",
            "--label=cvals.environment.transient=true",
            f"--label=cvals.workspace={_workspace_label(settings)}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=768m",
            "--memory-swap=768m",
            "--cpus=1",
            "--ulimit=nofile=256:256",
            "--ulimit=core=0:0",
            "--log-driver=none",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=384m,mode=1777",
            "--tmpfs=/deps:rw,noexec,nosuid,nodev,size=256m,mode=0755",
            "--user=0:0",
            "--network=none" if plan["offline"] else "--network=bridge",
            plan["base_image_digest"],
            "node",
            "-e",
            _INSTALL,
        ]
        if _docker(command, settings).returncode:
            raise ValueError("Cannot create isolated npm preparation container")
        created = True
        output = io.BytesIO()
        store = PrivateArtifacts(settings)
        with tarfile.open(fileobj=output, mode="w") as archive:
            files = {
                "package.json": plan["package_json"].encode(),
                "package-lock.json": plan["package_lock"].encode(),
            }
            files.update(
                {"artifacts/" + r["id"] + ".tgz": store.read(r["id"]) for r in plan["artifacts"]}
            )
            for path, payload in files.items():
                item = tarfile.TarInfo(path)
                item.size = len(payload)
                item.mode = 0o444
                archive.addfile(item, io.BytesIO(payload))
        if _docker(
            ["cp", "-", name + ":/"], settings, payload=output.getvalue(), timeout=60
        ).returncode:
            raise ValueError("Cannot transfer reviewed npm declarations and artifacts")
        result = environments._start(name, timeout=270, output_limit=1024 * 1024)
        try:
            evidence = json.loads(result.stdout)
            if not isinstance(evidence["installed_packages"], list) or not isinstance(
                evidence["node"], str
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Npm installer returned invalid evidence") from exc
        commit = _docker(
            [
                "commit",
                "--change",
                "LABEL cvals.environment.transient=false",
                "--change",
                f"LABEL cvals.environment.dependency_hash={plan['dependency_hash']}",
                "--change",
                "USER 65532:65532",
                "--change",
                'CMD ["node"]',
                name,
            ],
            settings,
            timeout=60,
        )
        image = commit.stdout.decode().strip()
        if commit.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            image = None
            raise ValueError("Cannot seal npm environment")
        tag = f"everval-env:{plan['dependency_hash'][:24]}-{image[7:19]}"
        if _docker(["image", "tag", image, tag], settings).returncode:
            raise ValueError("Cannot retain npm environment")
        finished = True
        return {
            "image_digest": image,
            "image_reference": tag,
            **evidence,
            "lifecycle_scripts": "disabled",
            "artifact_manifest_hash": content_hash(plan["artifacts"]),
        }
    finally:
        errors = []
        if created and _docker(["rm", "--force", "--volumes", name], settings).returncode:
            errors.append(name)
        if (
            image
            and not finished
            and _docker(["image", "rm", "--no-prune", image], settings).returncode
        ):
            errors.append(image)
        if errors:
            raise ValueError("Npm preparation cleanup failed: " + ", ".join(errors))
