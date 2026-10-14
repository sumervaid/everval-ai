"""Node admission and offline packages must preserve evidence and isolation."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from cvals.benchmarks import Benchmarks, assert_collection_admitted
from cvals.config import Settings
from cvals.environments import Environments
from cvals.node_environments import lock_plan
from cvals.node_runtime import hidden, is_test, run_node_tests
from cvals.private_artifacts import PrivateArtifacts, inspect_npm

DIGEST = "sha256:" + "a" * 64
LIVE = pytest.mark.skipif(
    os.environ.get("CVALS_TEST_NODE") != "1",
    reason="Set CVALS_TEST_NODE=1 with a local node:24-slim image",
)


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(path: Path, language: str = "javascript", dependency: dict | None = None) -> Path:
    path.mkdir()
    git(path, "init", "--quiet")
    git(path, "config", "user.name", "Fixture maintainer")
    git(path, "config", "user.email", "fixture@example.test")
    package = {"name": "node-fixture", "version": "1.0.0", "type": "module"}
    entries = {"": {"name": "node-fixture", "version": "1.0.0"}}
    if dependency:
        package["dependencies"] = {dependency["name"]: dependency["version"]}
        entries[""]["dependencies"] = package["dependencies"]
        entries["node_modules/" + dependency["name"]] = {
            k: v for k, v in dependency.items() if k != "name"
        }
    (path / "package.json").write_text(json.dumps(package))
    (path / "package-lock.json").write_text(
        json.dumps(
            {"name": "node-fixture", "version": "1.0.0", "lockfileVersion": 3, "packages": entries}
        )
    )
    ext = "ts" if language == "typescript" else "js"
    annotation = ": number[]" if ext == "ts" else ""
    (path / f"solution.{ext}").write_text(
        f"export const total = (values{annotation}) => values.length;\n"
    )
    imports = f"import test from 'node:test'; import assert from 'node:assert/strict'; import {{total}} from './solution.{ext}';\n"
    (path / f"baseline.test.{ext}").write_text(
        imports + "test('empty remains zero',()=>assert.equal(total([]),0));\n"
    )
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Baseline")
    (path / f"solution.{ext}").write_text(
        f"export const total = (values{annotation}) => values.reduce((a,b)=>a+b,0);\n"
    )
    (path / f"changed.test.{ext}").write_text(
        imports + "test('numeric sum',()=>assert.equal(total([2,4]),6));\n"
    )
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "Repair")
    return path


def npm_artifact(path: Path, *, member: str | None = None, link: bool = False) -> bytes:
    output = io.BytesIO()
    package = {
        "name": "@private/arithmetic",
        "version": "1.0.0",
        "main": "index.js",
        "scripts": {"postinstall": 'node -e "process.exit(97)"'},
    }
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in {
            "package/package.json": json.dumps(package).encode(),
            "package/index.js": b"exports.total = values => values.reduce((a,b)=>a+b,0);",
        }.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        if member:
            item = tarfile.TarInfo(member)
            if link:
                item.type = tarfile.SYMTYPE
                item.linkname = "/etc/passwd"
            archive.addfile(item, io.BytesIO())
    payload = output.getvalue()
    path.write_bytes(payload)
    return payload


def dependency(payload: bytes) -> dict:
    return {
        "name": "@private/arithmetic",
        "version": "1.0.0",
        "resolved": "https://packages.example.test/@private/arithmetic/-/arithmetic-1.0.0.tgz",
        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode(),
    }


@pytest.mark.parametrize(
    "path", ["math.test.js", "math.spec.ts", "__tests__/math.ts", "tests/unit.test.mjs"]
)
def test_node_test_paths_recognized(path):
    assert is_test(path) and hidden(path)


def test_profile_compiles_frozen_node_files(tmp_path, monkeypatch):
    benchmarks = Benchmarks(Settings(tmp_path / "data"))
    repo = repository(tmp_path / "repo")
    project = benchmarks.projects.connect(str(repo))
    assert project["metadata"]["environment"]["detected_node"]
    assert "node-test" in project["metadata"]["environment"]["test_runner"]
    candidate = next(
        item
        for item in benchmarks.projects.discover(project["id"])["candidates"]
        if item["status"] == "discovered"
    )
    monkeypatch.setattr("cvals.sandbox._image", lambda settings: DIGEST)
    profile = benchmarks.configure(
        project["id"], DIGEST, runtime="node-test", language="javascript"
    )
    review = benchmarks.review(
        project["id"],
        candidate["id"],
        "Add numeric values while retaining empty input behavior.",
        "maintainer",
        "Reviewed observable requirements.",
    )
    scenario = benchmarks._compile(candidate, review, profile)
    assert set(scenario.inputs["files"]) == {"solution.js"}
    assert {check.expected["phase"] for check in scenario.checks} == {"historical", "regression"}
    assert all(check.kind == "repository_tests" for check in scenario.checks)
    assert "numeric sum" not in json.dumps(scenario.agent_view())
    assert scenario.tags == ["historical", "javascript", "prepared-node"]


@pytest.mark.parametrize(
    "member,link",
    [
        ("package/../../escape", False),
        ("/package/file", False),
        ("package/link", True),
        ("package/.npmrc", False),
    ],
)
def test_unsafe_private_archives_rejected(tmp_path, member, link):
    payload = npm_artifact(tmp_path / "bad.tgz", member=member, link=link)
    with pytest.raises(ValueError):
        inspect_npm(payload)
    assert not (tmp_path / "escape").exists()


def test_private_registry_integrity_and_storage(tmp_path):
    settings = Settings(tmp_path / "data")
    store = PrivateArtifacts(settings)
    payload = npm_artifact(tmp_path / "private.tgz")
    item = store.import_file(
        tmp_path / "private.tgz", kind="npm", registry="https://packages.example.test"
    )
    assert store.read(item["id"]) == payload
    repo = repository(tmp_path / "repo", dependency=dependency(payload))
    files = {p.name: p.read_text() for p in repo.glob("*.json")}
    plan = lock_plan(files, [item], store)
    assert plan["offline"] and "file:/artifacts/" in plan["package_lock"]
    with pytest.raises(ValueError, match="requires an imported artifact"):
        lock_plan(files, [], store)
    modified = json.loads(files["package-lock.json"])
    modified["packages"]["node_modules/@private/arithmetic"]["integrity"] = (
        "sha512-" + "A" * 86 + "=="
    )
    with pytest.raises(ValueError, match="integrity"):
        lock_plan({**files, "package-lock.json": json.dumps(modified)}, [item], store)
    (settings.data_dir / "dependency-artifacts" / item["id"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        store.read(item["id"])


@pytest.mark.parametrize(
    "url",
    [
        "https://token@registry.example.test",
        "https://registry.example.test/?token=secret",
        "http://registry.example.test",
    ],
)
def test_registry_credentials_are_never_accepted(tmp_path, url):
    store = PrivateArtifacts(Settings(tmp_path / "data"))
    npm_artifact(tmp_path / "private.tgz")
    with pytest.raises(ValueError, match="without credentials"):
        store.import_file(tmp_path / "private.tgz", kind="npm", registry=url)
    assert store.list() == []


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "zero", "foreign", "exception", "contradiction", "missing_file", "skip", "spoof"],
)
def test_node_evidence_fails_closed(monkeypatch, mutation):
    record = {
        "file": "/workspace/a.test.js",
        "name": "case",
        "line": 1,
        "column": 1,
        "status": "passed",
        "assertion": False,
    }
    records = [record]
    exit_code = 0
    if mutation == "duplicate":
        records = [record, record]
    if mutation == "zero":
        records = []
    if mutation == "foreign":
        record["file"] = "/workspace/b.test.js"
    if mutation == "exception":
        record.update(status="failed", assertion=False)
        exit_code = 1
    if mutation == "contradiction":
        exit_code = 1
    if mutation == "skip":
        record["status"] = "pending"
    output = "__EVERVAL_NODE__" + json.dumps({"records": records})
    if mutation == "spoof":
        output += "\n" + output
    monkeypatch.setattr(
        "cvals.sandbox._execute",
        lambda *a, **k: {"output": output, "exit_code": exit_code, "image_digest": DIGEST},
    )
    selectors = ["a.test.js"] + (["b.test.js"] if mutation == "missing_file" else [])
    result = run_node_tests(
        {"source.js": ""}, {"a.test.js": "", "b.test.js": ""}, selectors, Settings(Path("/unused"))
    )
    assert result["infrastructure_error"] and not result["passed"]


@LIVE
@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_live_historical_node_admission(tmp_path, language):
    settings = replace(Settings.from_env(), data_dir=tmp_path / "data")
    benchmarks = Benchmarks(settings)
    repo = repository(tmp_path / "repo", language)
    project = benchmarks.projects.connect(str(repo))
    candidate = next(
        item
        for item in benchmarks.projects.discover(project["id"])["candidates"]
        if item["status"] == "discovered"
    )
    environment = Environments(settings).prepare(
        project["id"], language=language, base_image="node:24-slim"
    )
    benchmarks.configure(
        project["id"], environment["image_digest"], runtime="node-test", language=language
    )
    benchmarks.review(
        project["id"],
        candidate["id"],
        "Totals must add numeric values while retaining the behavior of empty inputs.",
        "Fixture maintainer",
        "Independent observable requirement.",
    )
    result = benchmarks.validate(project["id"], candidate["id"])
    assert result["status"] == "valid", result["reasons"]
    assert len(result["evidence"]) == 3
    release = benchmarks.release(project["id"], "node-release")
    collection = benchmarks.store.get_collection(release["collection_ids"]["all"])
    with benchmarks.store._connect() as connection:
        assert_collection_admitted(connection, collection)


@LIVE
def test_live_offline_private_npm_install_disables_scripts(tmp_path):
    settings = replace(Settings.from_env(), data_dir=tmp_path / "data")
    payload = npm_artifact(tmp_path / "private.tgz")
    store = PrivateArtifacts(settings)
    artifact = store.import_file(
        tmp_path / "private.tgz", kind="npm", registry="https://packages.example.test"
    )
    repo = repository(tmp_path / "repo", dependency=dependency(payload))
    environments = Environments(settings)
    project = environments.projects.connect(str(repo))
    prepared = environments.prepare(
        project["id"], language="javascript", artifacts=[artifact["id"]], base_image="node:24-slim"
    )
    assert prepared["offline"] is True
    result = run_node_tests(
        {"source.js": ""},
        {
            "private.test.js": "const test=require('node:test'); const assert=require('node:assert/strict'); const fs=require('node:fs'); "
            "const {total}=require('@private/arithmetic'); test('private dependency installed without its failing hook',()=>{"
            "assert.equal(total([2,4]),6); assert.equal(fs.existsSync('/artifacts'),false); "
            "assert.equal(fs.existsSync('/root/.npmrc'),false); });"
        },
        ["private.test.js"],
        replace(settings, sandbox_image=prepared["image_digest"]),
    )
    assert result["passed"], result


@LIVE
@pytest.mark.parametrize("runner,version", [("jest", "30.5.2"), ("vitest", "5.0.4")])
def test_live_common_node_runners(tmp_path, runner, version):
    from uuid import uuid4

    from cvals.sandbox import _docker

    settings = replace(Settings.from_env(), data_dir=tmp_path / "data")
    repo = repository(tmp_path / "repo")
    package = {"name": "runner-fixture", "version": "1.0.0", "devDependencies": {runner: version}}
    script = """
const fs=require('node:fs'),{spawnSync}=require('node:child_process');
fs.writeFileSync('/tmp/package.json',process.argv[1]);
const result=spawnSync('npm',['install','--package-lock-only','--ignore-scripts','--no-audit','--no-fund','--cache=/tmp/cache'],{cwd:'/tmp',encoding:'utf8',timeout:180000,maxBuffer:1024*1024});
if(result.status!==0)process.exit(2);
process.stdout.write(fs.readFileSync('/tmp/package-lock.json','utf8'));
"""
    name = "everval-test-lock-" + uuid4().hex
    try:
        fetched = _docker(
            [
                "run",
                "--rm",
                "--name",
                name,
                "--pull=never",
                "--network=bridge",
                "--read-only",
                "--user=65532:65532",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--memory=512m",
                "--pids-limit=64",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777",
                "node:24-slim",
                "node",
                "-e",
                script,
                json.dumps(package),
            ],
            settings,
            timeout=210,
        )
        assert fetched.returncode == 0, fetched.stderr.decode()
    finally:
        _docker(["rm", "--force", "--volumes", name], settings)
    (repo / "package.json").write_text(json.dumps(package))
    (repo / "package-lock.json").write_bytes(fetched.stdout)
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Pin reviewed runner")
    environments = Environments(settings)
    project = environments.projects.connect(str(repo))
    prepared = environments.prepare(
        project["id"], language="javascript", runner=runner, base_image="node:24-slim"
    )
    isolated = replace(settings, sandbox_image=prepared["image_digest"])
    if runner == "jest":
        tests = "const {total}=require('./solution.cjs'); test('numeric sum',()=>expect(total([2,4])).toBe(6));"
    else:
        tests = "import {test,expect} from 'vitest'; import {total} from './solution.cjs'; test('numeric sum',()=>expect(total([2,4])).toBe(6));"
    for expression, passing in [("values.length", False), ("values.reduce((a,b)=>a+b,0)", True)]:
        result = run_node_tests(
            {"solution.cjs": "exports.total = values => " + expression + ";"},
            {"runner.test.js": tests, "package.json": json.dumps(package)},
            ["runner.test.js"],
            isolated,
            runner=runner,
        )
        assert result["infrastructure_error"] is None, result
        assert result["tests_run"] == 1 and result["passed"] is passing, result


@LIVE
@pytest.mark.skipif(
    not os.environ.get("CVALS_TEST_PYTEST_IMAGE"),
    reason="Provide an existing prepared pytest image for offline wheel installation",
)
def test_live_offline_private_wheel(tmp_path):
    import zipfile

    from cvals.sandbox import run_repository_tests

    settings = replace(Settings.from_env(), data_dir=tmp_path / "data")
    wheel = tmp_path / "private_fixture-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, text in {
            "private_fixture/__init__.py": "def total(values): return sum(values)\n",
            "private_fixture-1.0.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: private-fixture\nVersion: 1.0.0\n",
            "private_fixture-1.0.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "private_fixture-1.0.0.dist-info/RECORD": "",
        }.items():
            archive.writestr(name, text)
    artifact = PrivateArtifacts(settings).import_file(
        wheel, kind="wheel", registry="https://packages.example.test/python"
    )
    repo = repository(tmp_path / "repo")
    environments = Environments(settings)
    project = environments.projects.connect(str(repo))
    prepared = environments.prepare(
        project["id"],
        packages=["private-fixture==1.0.0"],
        artifacts=[artifact["id"]],
        base_image=os.environ["CVALS_TEST_PYTEST_IMAGE"],
    )
    assert prepared["network_policy"] == "Offline imported wheel closure; no network"
    result = run_repository_tests(
        {"source.py": ""},
        {
            "test_private.py": "from private_fixture import total\ndef test_sum(): assert total([2,4]) == 6\n"
        },
        ["test_private.py::test_sum"],
        replace(settings, sandbox_image=prepared["image_digest"]),
    )
    assert result["passed"], result


@LIVE
@pytest.mark.parametrize(
    "body", ["", "process.exit(0);", "require('nonexistent_everval_dependency');"]
)
def test_live_node_empty_and_load_failure_are_infrastructure_errors(body):
    settings = replace(Settings.from_env(), sandbox_image="node:24-slim")
    result = run_node_tests({"source.js": ""}, {"empty.test.js": body}, ["empty.test.js"], settings)
    assert result["infrastructure_error"] and not result["passed"], result
