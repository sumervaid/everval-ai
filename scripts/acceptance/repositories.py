#!/usr/bin/env python3
"""Exercise an installed Everval release on original repository and pack fixtures.

Run on the machine holding the workspace, with a dedicated Docker runtime and
an already-local Python/pytest image. All inputs and artifacts stay below
--output. Imported test code executes only through Everval's Docker sandbox.
No model-provider calls are made. This is installation acceptance evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import cvals
from cvals.agent_runner import PublicWorkspace
from cvals.audit import AuditLog
from cvals.config import Settings
from cvals.models import Scenario, canonical_json, content_hash
from cvals.runtime import worker_running
from cvals.sandbox import _docker, _image, _workspace_label


class Acceptance:
    def __init__(self, output: Path, python_image: str, node_image: str):
        self.output = output.expanduser().absolute()
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.workspace = self.output / "workspace"
        self.python_image = python_image
        self.node_image = node_image
        self.cli_path = Path(sys.executable).parent / "everval"
        if not self.cli_path.is_file() or "site-packages" not in str(cvals.__file__):
            raise ValueError(
                "Run with an installed wheel Python and its sibling everval entrypoint"
            )
        self.environment = os.environ.copy()
        for key in list(self.environment):
            if any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
                self.environment.pop(key, None)
        self.environment.pop("PYTHONPATH", None)
        self.environment.update(
            EVERVAL_DATA_DIR=str(self.workspace),
            CVALS_DATA_DIR=str(self.workspace),
            EVERVAL_ALLOW_REMOTE_PROVIDERS="false",
            CVALS_ALLOW_REMOTE_PROVIDERS="false",
            EVERVAL_SANDBOX_IMAGE=python_image,
            CVALS_SANDBOX_IMAGE=python_image,
            NO_COLOR="1",
            TERM="dumb",
        )
        for key in (
            "EVERVAL_DATA_DIR",
            "CVALS_DATA_DIR",
            "EVERVAL_ALLOW_REMOTE_PROVIDERS",
            "CVALS_ALLOW_REMOTE_PROVIDERS",
            "EVERVAL_SANDBOX_IMAGE",
            "CVALS_SANDBOX_IMAGE",
        ):
            os.environ[key] = self.environment[key]
        self.settings = Settings.from_env()
        self.settings.prepare()
        self.commands: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.details: dict[str, Any] = {}
        self.worker: subprocess.Popen[str] | None = None
        self.worker_stream: Any = None
        self.started = time.time()
        self.phase = "initialization"

    def save(self, name: str, value: Any) -> None:
        target = self.output / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        target.chmod(0o600)

    def check(self, name: str, condition: bool, **evidence: Any) -> None:
        record = {"name": name, "passed": bool(condition), **evidence}
        self.checks.append(record)
        if not condition:
            raise AssertionError(name)

    def progress(self, phase: str) -> None:
        self.phase = phase
        print(
            json.dumps({"phase": phase, "elapsed_seconds": round(time.time() - self.started, 1)}),
            flush=True,
        )

    def cli(
        self, *args: str, workspace: Path | None = None, fail: bool = False, timeout: int = 1800
    ) -> Any:
        env = {**self.environment}
        if workspace:
            env.update(EVERVAL_DATA_DIR=str(workspace), CVALS_DATA_DIR=str(workspace))
        started = time.monotonic()
        result = subprocess.run(
            [str(self.cli_path), *args],
            env=env,
            cwd=self.output,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        entry = {
            "index": len(self.commands) + 1,
            "arguments": list(args),
            "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        self.commands.append(entry)
        self.save("private/commands.json", self.commands)
        if fail:
            if result.returncode == 0:
                raise AssertionError("Expected rejection: " + " ".join(args[:2]))
            return entry
        if result.returncode:
            raise RuntimeError(
                "CLI failed: " + " ".join(args[:2]) + "; inspect private command ledger"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CLI did not return JSON: " + " ".join(args[:2])) from exc

    def start_worker(self) -> None:
        self.worker_stream = (self.output / "private/worker-process.log").open("w")
        self.worker = subprocess.Popen(
            [sys.executable, "-m", "cvals", "worker"],
            env=self.environment,
            cwd=self.output,
            stdout=self.worker_stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if worker_running(self.settings):
                return
            if self.worker.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("Acceptance worker failed to start")

    def stop_worker(self) -> None:
        if self.worker and self.worker.poll() is None:
            self.worker.send_signal(signal.SIGTERM)
            try:
                self.worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait(timeout=5)
        if self.worker_stream:
            self.worker_stream.close()

    def git(self, path: Path, *args: str) -> str:
        env = {
            **self.environment,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_DATE": "2026-10-15T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-10-15T00:00:00+00:00",
        }
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(path), *args],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()

    def fixture(self, language: str, *, invalid: bool = False) -> dict[str, Any]:
        label = language + ("-invalid" if invalid else "")
        path = self.output / "sources" / label
        path.mkdir(parents=True)
        self.git(path, "init", "--quiet")
        self.git(path, "config", "user.name", "Acceptance fixture maintainer")
        self.git(path, "config", "user.email", "fixture@example.test")
        (path / "README.md").write_text(
            "Original Apache-2.0 installation fixture; no customer data.\n"
        )
        if language == "python":
            filename = "solution.py"
            before = "def total(values):\n    return len(values)\n"
            after = "def total(values):\n    return sum(values)\n"
            (path / filename).write_text(before)
            (path / "pyproject.toml").write_text(
                '[project]\nname="everval-acceptance-python"\nversion="1.0.0"\n'
            )
            baseline = (
                "import pytest\nfrom solution import total\n\n"
                "@pytest.fixture\ndef empty_values():\n    return []\n\n"
                "@pytest.mark.parametrize('values', [[], [1]])\n"
                "def test_preserved(values, empty_values):\n"
                "    assert total(values) == len(values)\n"
                "    assert total(empty_values) == 0\n"
            )
            (path / "test_baseline.py").write_text(baseline)
            # This source is data; neither Git import nor profiling may execute it.
            (path / "setup.py").write_text(
                "from pathlib import Path\nPath('HOST_EXECUTION_TRIPWIRE').write_text('unexpected')\nraise RuntimeError('HOST_EXECUTION_TRIPWIRE')\n"
            )
            historical = (
                "import pytest\nfrom solution import total\n\n"
                "@pytest.mark.parametrize(('values','expected'), [([2,4],6), ([10],10), ([-2,5],3)])\n"
                "def test_private_numeric_contract(values, expected):\n"
                "    assert total(values) == expected, 'PRIVATE_HISTORICAL_CANARY'\n"
            )
            if invalid:
                historical = "import never_installed_acceptance_dependency\n" + historical
            hidden_name = "test_private.py"
        else:
            filename = "solution.js"
            before = "export const total = values => values.length;\n"
            after = "export const total = values => values.reduce((a,b)=>a+b,0);\n"
            (path / filename).write_text(before)
            package = {
                "name": "everval-acceptance-node",
                "version": "1.0.0",
                "type": "module",
                "scripts": {
                    "postinstall": "node -e \"throw new Error('INSTALL_HOOK_MUST_NOT_EXECUTE')\""
                },
            }
            (path / "package.json").write_text(json.dumps(package))
            (path / "package-lock.json").write_text(
                json.dumps(
                    {
                        "name": package["name"],
                        "version": "1.0.0",
                        "lockfileVersion": 3,
                        "packages": {"": {"name": package["name"], "version": "1.0.0"}},
                    }
                )
            )
            imports = "import test from 'node:test'; import assert from 'node:assert/strict'; import {total} from './solution.js';\n"
            (path / "baseline.test.js").write_text(
                imports
                + "test('empty stays zero',()=>assert.equal(total([]),0));\ntest('single one stays one',()=>assert.equal(total([1]),1));\n"
            )
            historical = (
                imports
                + "test('PRIVATE_HISTORICAL_CANARY numeric sum',()=>{assert.equal(total([2,4]),6);assert.equal(total([-2,5]),3);});\n"
            )
            if invalid:
                historical = (
                    "// Intentionally empty test file: zero tests cannot establish validity.\n"
                )
            hidden_name = "private.test.js"
        self.git(path, "add", ".")
        self.git(path, "commit", "--quiet", "-m", "Original baseline contract")
        base = self.git(path, "rev-parse", "HEAD")
        (path / filename).write_text(after)
        (path / hidden_name).write_text(historical)
        self.git(path, "add", ".")
        self.git(path, "commit", "--quiet", "-m", "FUTURE_COMMIT_CANARY repair numeric totals")
        reference = self.git(path, "rev-parse", "HEAD")
        return {
            "path": path,
            "filename": filename,
            "base_source": before,
            "reference_source": after,
            "base_commit": base,
            "reference_commit": reference,
            "hidden_name": hidden_name,
            "invalid": invalid,
            "language": language,
        }

    def connect_review(self, fixture: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        project = self.cli("projects", "connect", str(fixture["path"]))
        discovery = self.cli("projects", "discover", project["id"])
        self.check(
            f"{fixture['path'].name}: local history identified", project["source_kind"] == "local"
        )
        candidates = [
            c
            for c in discovery["candidates"]
            if c["status"] == "discovered" and c["reference_commit"] == fixture["reference_commit"]
        ]
        self.check(f"{fixture['path'].name}: repair discovered", len(candidates) == 1)
        candidate = candidates[0]
        self.check(
            f"{fixture['path'].name}: exact base reconstructed",
            candidate["base_commit"] == fixture["base_commit"],
        )
        statement = fixture["path"].parent / (fixture["path"].name + "-review.txt")
        statement.write_text(
            "Totals must add the numeric input values, including negative values, while preserving empty and single-one input behavior."
        )
        self.cli(
            "projects",
            "review",
            project["id"],
            candidate["id"],
            str(statement),
            "Acceptance fixture reviewer",
            "Original fixture requirements independently stated without later implementation or private assertions.",
        )
        return project, candidate

    def public_isolation(self, scenario: Scenario, fixture: dict[str, Any], prefix: str) -> None:
        public = scenario.agent_view()
        serialized = canonical_json(public)
        self.save(f"private/{prefix}-public-task.json", public)
        excluded = (
            "PRIVATE_HISTORICAL_CANARY",
            "FUTURE_COMMIT_CANARY",
            canonical_json(fixture["reference_source"]),
            fixture["reference_commit"],
            fixture["hidden_name"],
            "reference_output",
        )
        self.check(
            prefix + ": no hidden or future material in public task",
            all(token not in serialized for token in excluded),
        )
        self.check(
            prefix + ": starting source is the base",
            public["inputs"]["files"][fixture["filename"]] == fixture["base_source"],
        )
        workspace = PublicWorkspace(scenario, self.settings)
        files = workspace.dispatch("list_files", {}, 30)
        self.check(
            prefix + ": public file tool agrees with snapshot",
            {r["path"] for r in files["files"]} == set(public["inputs"]["files"]),
        )
        for forbidden in (fixture["hidden_name"], ".git/HEAD"):
            try:
                workspace.dispatch("read_file", {"path": forbidden}, 30)
            except ValueError:
                rejected = True
            else:
                rejected = False
            self.check(prefix + ": private read rejected " + forbidden, rejected)
        try:
            workspace.dispatch(
                "write_file", {"path": fixture["hidden_name"], "content": "pass"}, 30
            )
        except ValueError:
            rejected = True
        else:
            rejected = False
        self.check(prefix + ": hidden overwrite rejected", rejected)
        if fixture["language"] == "python":
            probe = (
                "import json, os\nfrom pathlib import Path\n"
                "names=sorted(p.name for p in Path('/workspace').iterdir())\n"
                "try:\n Path('/workspace/write-canary').write_text('bad'); readonly=False\n"
                "except OSError:\n readonly=True\n"
                "print(json.dumps({'uid':os.getuid(),'files':names,'grade_files':sorted(p.name for p in Path('/grade').iterdir()),'git_exists':Path('/workspace/.git').exists(),'readonly':readonly,'network_routes':Path('/proc/net/route').read_text().splitlines()[1:]}))"
            )
            result = workspace.dispatch("run_python", {"code": probe}, 30)
        else:
            probe = (
                "const fs=require('node:fs'); let readonly=false; try{fs.writeFileSync('/workspace/write-canary','bad')}catch{readonly=true};"
                "console.log(JSON.stringify({uid:process.getuid(),files:fs.readdirSync('/workspace').sort(),grade_files:fs.readdirSync('/grade').sort(),git_exists:fs.existsSync('/workspace/.git'),readonly,network_routes:fs.readFileSync('/proc/net/route','utf8').trim().split('\\n').slice(1)}));"
            )
            result = workspace.dispatch("run_node", {"code": probe}, 30)
        self.save(f"private/{prefix}-public-container-probe.json", result)
        self.check(
            prefix + ": public probe executed",
            result["exit_code"] == 0 and not result.get("timed_out"),
        )
        evidence = json.loads(result["output"].strip())
        self.check(prefix + ": public container uses unprivileged UID", evidence["uid"] == 65532)
        self.check(prefix + ": public container source is read-only", evidence["readonly"])
        self.check(prefix + ": public container lacks Git history", not evidence["git_exists"])
        self.check(
            prefix + ": public container has no network route", not evidence["network_routes"]
        )
        self.check(
            prefix + ": public container has no private graders",
            set(evidence["grade_files"]) in ({"runner.py"}, {"runner.mjs"})
            and fixture["hidden_name"] not in evidence["files"],
        )

    def validate_cycles(self, value: dict[str, Any], prefix: str) -> None:
        self.check(prefix + ": admission valid", value["status"] == "valid", status=value["status"])
        self.check(prefix + ": three clean cycles retained", len(value["evidence"]) == 3)
        phases = {check["id"]: check["expected"]["phase"] for check in value["scenario"]["checks"]}
        self.check(
            prefix + ": historical and regression controls present",
            set(phases.values()) == {"historical", "regression"},
        )
        fingerprints = []
        for cycle in value["evidence"]:
            signature = []
            for base, reference in zip(cycle["base"], cycle["reference"], strict=True):
                phase = phases[base["id"]]
                self.check(
                    prefix + f": cycle {cycle['repetition']} {phase} distinguishes behavior",
                    reference["passed"] is True and base["passed"] is (phase == "regression"),
                )
                for control in (base, reference):
                    actual = control["actual"]
                    self.check(
                        prefix + f": cycle {cycle['repetition']} {phase} real tests",
                        actual["tests_run"] > 0
                        and not actual.get("errors")
                        and not actual.get("skipped")
                        and not actual.get("infrastructure_error"),
                    )
                a = sorted((c["id"], c["status"]) for c in base["actual"]["test_cases"])
                b = sorted((c["id"], c["status"]) for c in reference["actual"]["test_cases"])
                self.check(
                    prefix + f": cycle {cycle['repetition']} {phase} collection stable",
                    [r[0] for r in a] == [r[0] for r in b],
                )
                signature.append([base["id"], a, b])
            fingerprints.append(content_hash(signature))
        self.check(
            prefix + ": outcome signatures stable across cycles", len(set(fingerprints)) == 1
        )

    def await_experiment(self, identifier: str) -> dict[str, Any]:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            value = self.cli("experiments", "show", identifier)
            if value["status"] not in {"queued", "running"}:
                return value
            time.sleep(0.5)
        raise RuntimeError("Experiment did not finish within ten minutes")

    def repository(self, language: str, image: str) -> dict[str, Any]:
        self.progress(language + "-repository")
        fixture = self.fixture(language)
        project, candidate = self.connect_review(fixture)
        # Unvalidated work must not enter a release.
        denial = self.cli("benchmarks", "release", project["id"], "before-validation", fail=True)
        self.check(
            language + ": release blocked before validation", "No candidates" in denial["stderr"]
        )
        if language == "node":
            plan = self.cli(
                "environments",
                "plan",
                project["id"],
                "--revision",
                candidate["base_commit"],
                "--language",
                "javascript",
                "--runner",
                "node-test",
                "--base-image",
                image,
            )
            self.save("private/node-environment-plan.json", plan)
            prepared = self.cli(
                "environments",
                "prepare",
                project["id"],
                "--revision",
                candidate["base_commit"],
                "--language",
                "javascript",
                "--runner",
                "node-test",
                "--base-image",
                image,
            )
            self.save("private/node-environment-prepared.json", prepared)
            pinned = prepared["image_digest"]
            self.check(
                "node: preparation succeeded with failing lifecycle hook disabled",
                pinned.startswith("sha256:"),
            )
            self.check(
                "node: prepared profile pinned", prepared["profile"]["image_digest"] == pinned
            )
        else:
            profile = self.cli("projects", "profile", project["id"], image)
            pinned = profile["image_digest"]
        validated = self.cli("projects", "validate", project["id"], candidate["id"])
        self.save(f"private/{language}-validation.json", validated)
        self.validate_cycles(validated, language)
        self.progress(language + "-public-isolation-and-release")
        scenario = Scenario.model_validate(validated["scenario"])
        self.public_isolation(scenario, fixture, language)
        release = self.cli("benchmarks", "release", project["id"], "acceptance-v1")
        self.check(language + ": one validated member frozen", len(release["members"]) == 1)
        collection_id = release["collection_ids"]["all"]
        self.check(
            language + ": declared partition retained",
            release["members"][0]["partition"] in {"dev", "holdout"},
        )
        duplicate = self.cli("benchmarks", "release", project["id"], "acceptance-v1", fail=True)
        self.check(language + ": release name immutable", "immutable" in duplicate["stderr"])
        systems = self.cli("systems", "list")
        fixture_ids = [s["id"] for s in systems if s["kind"] in {"reference", "incomplete"}]
        arguments = [
            "run",
            collection_id,
            "--name",
            language + " installation controls",
            "--budget",
            "0",
            "--enqueue",
        ]
        for identifier in fixture_ids:
            arguments += ["--system", identifier]
        queued = self.cli(*arguments)
        run = self.await_experiment(queued["id"])
        self.save(f"private/{language}-experiment.json", run)
        rows = {r["kind"]: r for r in run["report"]["systems"]}
        self.check(
            language + ": engine reference passes",
            rows["reference"]["passed"] == 1 and rows["reference"]["completed"] == 1,
        )
        self.check(
            language + ": engine incomplete control fails",
            rows["incomplete"]["passed"] == 0 and rows["incomplete"]["completed"] == 1,
        )
        self.check(
            language + ": provider spend zero", all(row["cost_usd"] == 0 for row in rows.values())
        )
        public_export = self.cli("benchmarks", "export", release["id"])
        bundle = json.loads(Path(public_export["path"]).read_text())
        public_text = canonical_json(bundle)
        self.check(
            language + ": exported solver bundle excludes hidden content",
            bundle["contains_private_grader_data"] is False
            and "PRIVATE_HISTORICAL_CANARY" not in public_text
            and canonical_json(fixture["reference_source"]) not in public_text
            and fixture["hidden_name"] not in public_text,
        )
        private_export = self.cli("benchmarks", "export", release["id"], "--private-grader")
        private = json.loads(Path(private_export["path"]).read_text())
        self.check(
            language + ": private export deliberate and labeled",
            private["contains_private_grader_data"] is True
            and "PRIVATE_HISTORICAL_CANARY" in canonical_json(private),
        )
        summary_export = self.cli(
            "evidence", "export", run["id"], str(self.output / (language + "-summary"))
        )
        summary = json.loads(Path(summary_export["summary"]).read_text())
        self.check(
            language + ": share summary preserves fixture label",
            summary["fixture_controls_present"]
            and "PRIVATE_HISTORICAL_CANARY" not in canonical_json(summary),
        )
        self.progress(language + "-negative-admission")
        invalid = self.fixture(language, invalid=True)
        bad_project, bad_candidate = self.connect_review(invalid)
        self.cli(
            "projects",
            "profile",
            bad_project["id"],
            pinned,
            "--language",
            "javascript" if language == "node" else "python",
            "--runtime",
            "node-test" if language == "node" else "python-pytest",
        )
        rejected = self.cli("projects", "validate", bad_project["id"], bad_candidate["id"])
        self.save(f"private/{language}-invalid-validation.json", rejected)
        self.check(
            language + ": invalid test infrastructure quarantined",
            rejected["status"] == "quarantined" and bool(rejected["reasons"]),
        )
        self.cli("benchmarks", "release", bad_project["id"], "invalid-release", fail=True)
        tripwires = [
            self.output / "HOST_EXECUTION_TRIPWIRE",
            self.workspace / "HOST_EXECUTION_TRIPWIRE",
            fixture["path"] / "HOST_EXECUTION_TRIPWIRE",
            invalid["path"] / "HOST_EXECUTION_TRIPWIRE",
        ]
        self.check(
            language + ": no imported host execution tripwire",
            not any(path.exists() for path in tripwires),
        )
        return {
            "status": "passed",
            "source": "original local Git history fixture",
            "merged_pull_requests": False,
            "language": language,
            "validation_cycles": 3,
            "source_commits": [fixture["base_commit"], fixture["reference_commit"]],
            "image_digest": pinned,
            "scenario_hash": validated["scenario_hash"],
            "release_id": release["id"],
            "experiment_id": run["id"],
            "snapshot_hash": run["snapshot_hash"],
            "reference_passed": 1,
            "incomplete_control_failed": 1,
            "provider_calls": 0,
            "provider_cost_usd": 0,
            "negative_acceptance": "zero-test quarantine"
            if language == "node"
            else "missing-dependency quarantine",
        }

    def packs(self) -> dict[str, Any]:
        self.progress("portable-packs")
        catalog = self.cli("packs", "catalog")
        self.check(
            "packs: versioned three-example catalog",
            len(catalog["packs"]) == 3 and catalog["network"] is False,
        )
        exported = self.output / "documents-pack.json"
        denial = self.cli("packs", "builtin", "documents", str(exported), fail=True)
        self.check(
            "packs: hidden disclosure acknowledgement required",
            "include-hidden" in denial["stderr"] and not exported.exists(),
        )
        emitted = self.cli("packs", "builtin", "documents", str(exported), "--include-hidden")
        inspected = self.cli("packs", "inspect", str(exported))
        self.check(
            "packs: local content identity verified", emitted["digest"] == inspected["digest"]
        )
        staged = self.cli("packs", "stage", str(exported), "--reviewer", "Acceptance reviewer")
        self.check(
            "packs: staged content not admitted",
            staged["status"] == "staged" and staged["approval"] is None,
        )
        review = self.output / "pack-review.json"
        review.write_text(
            json.dumps(
                {
                    "rationale": "Original invented fixture reviewed against the supplied current and archived policies; incorrect numbers and fabricated quotes must fail.",
                    "expected_behaviors": {
                        "demo-documents": "The current enterprise refund window is thirty days. Require an exact quotation from the current policy; reject archived-policy and fabricated quotations."
                    },
                }
            )
        )
        approved = self.cli(
            "packs",
            "approve",
            staged["id"],
            "--review",
            str(review),
            "--reviewer",
            "Acceptance reviewer",
        )
        private = self.cli("packs", "show", staged["id"], "--include-hidden")
        self.check("packs: reviewed controls passed", private["approval"]["validation"]["valid"])
        copied = self.output / "roundtrip-pack.json"
        self.cli("packs", "export", staged["id"], str(copied), "--include-hidden")
        self.check(
            "packs: export preserves exact content digest",
            json.loads(copied.read_text())["digest"] == inspected["digest"],
        )
        second = self.output / "second-workspace"
        self.cli("init", workspace=second)
        imported = self.cli(
            "packs",
            "stage",
            str(copied),
            "--reviewer",
            "Second acceptance reviewer",
            workspace=second,
        )
        self.check(
            "packs: fresh workspace does not trust previous approval",
            imported["status"] == "staged" and imported["approval"] is None,
        )
        second_approval = self.cli(
            "packs",
            "approve",
            imported["id"],
            "--review",
            str(review),
            "--reviewer",
            "Second acceptance reviewer",
            workspace=second,
        )
        self.check(
            "packs: independent workspace admission succeeds",
            bool(second_approval["collection_id"]),
        )
        tampered = json.loads(copied.read_text())
        tampered["name"] = "Untrusted content substitution"
        bad_path = self.output / "tampered-pack.json"
        bad_path.write_text(json.dumps(tampered))
        refusal = self.cli(
            "packs",
            "stage",
            str(bad_path),
            "--reviewer",
            "Acceptance reviewer",
            workspace=second,
            fail=True,
        )
        self.check("packs: content tampering rejected", "digest" in refusal["stderr"].lower())
        invalid = json.loads(copied.read_text())
        scenario = invalid["collection"]["scenarios"][0]
        scenario["provenance"]["negative_controls"] = [scenario["reference_output"]]
        invalid["digest"] = content_hash({k: v for k, v in invalid.items() if k != "digest"})
        bad_path = self.output / "weak-control-pack.json"
        bad_path.write_text(json.dumps(invalid))
        weak = self.cli(
            "packs", "stage", str(bad_path), "--reviewer", "Acceptance reviewer", workspace=second
        )
        self.cli(
            "packs",
            "approve",
            weak["id"],
            "--review",
            str(review),
            "--reviewer",
            "Acceptance reviewer",
            workspace=second,
            fail=True,
        )
        self.check(
            "packs: nondiscriminating controls remain staged",
            self.cli("packs", "show", weak["id"], workspace=second)["status"] == "staged",
        )
        demo = self.cli(
            "demo", "--workspace", str(self.output / "demo"), "--json", workspace=second
        )
        self.check(
            "demo: no providers or Docker requested",
            demo["provider_calls"] == 0 and demo["provider_cost_usd"] == 0 and not demo["sandbox"],
        )
        self.check(
            "demo: all-domain evidence flow completed",
            [r["report"]["systems"][0]["pass_rate"] for r in demo["runs"]] == [1, 0, 1],
        )
        self.check("demo: audit valid", demo["audit"]["valid"])
        self.save("private/demo-returned-evidence.json", demo)
        return {
            "status": "passed",
            "catalog_revision": catalog["revision"],
            "pack_digest": inspected["digest"],
            "roundtrip_workspaces": 2,
            "reviewed_collection_id": approved["collection_id"],
            "demo_domains": ["coding", "documents", "data"],
            "demo_kind": "installation-control",
            "provider_calls": 0,
            "provider_cost_usd": 0,
        }

    def run(self) -> dict[str, Any]:
        source = Path(__file__).read_bytes()
        archive = self.output / "private" / "executed-harness.py"
        archive.parent.mkdir(mode=0o700, exist_ok=True)
        archive.write_bytes(source)
        archive.chmod(0o600)
        package = Path(cvals.__file__).parent
        installed_digest = hashlib.sha256()
        for path in sorted(package.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            ):
                installed_digest.update(str(path.relative_to(package)).encode() + b"\0")
                installed_digest.update(path.read_bytes())
        result: dict[str, Any] = {
            "schema_version": 1,
            "suite": "repository-and-portable-pack-acceptance",
            "purpose": "original installation controls; not customer workload or model-performance evidence",
            "package_version": cvals.__version__,
            "installed_wheel": True,
            "installed_package_sha256": installed_digest.hexdigest(),
            "package_hash_basis": "All package files by relative path and bytes; excludes bytecode caches.",
            "provider_calls": 0,
            "provider_cost_usd": 0,
            "status": "running",
            "harness_sha256": hashlib.sha256(source).hexdigest(),
            "stages": {},
        }
        self.save("summary.json", result)
        try:
            self.progress("runtime-preflight")
            self.cli("init")
            python_digest = _image(replace(self.settings, sandbox_image=self.python_image))
            node_digest = _image(replace(self.settings, sandbox_image=self.node_image))
            self.check(
                "runtime: Python and Node images already local",
                python_digest.startswith("sha256:") and node_digest.startswith("sha256:"),
            )
            self.start_worker()
            result["stages"]["python"] = self.repository("python", python_digest)
            result["stages"]["node"] = self.repository("node", node_digest)
            self.progress("docker-complete")
            result["stages"]["packs"] = self.packs()
            audit = AuditLog(self.settings).verify()
            self.check("workspace: audit chain valid", audit["valid"])
            result["audit_head"] = audit["head"]
            result["status"] = "passed"
        except Exception as exc:
            result.update(
                status="failed",
                failure={
                    "phase": self.phase,
                    "type": type(exc).__name__,
                    "message": str(exc)
                    .replace(str(self.output), "<acceptance-artifacts>")
                    .replace(str(Path.home()), "<home>")[:1500],
                },
            )
        finally:
            self.stop_worker()
            self.checks.append(
                {
                    "name": "workspace: dedicated acceptance worker stopped",
                    "passed": not worker_running(self.settings),
                }
            )
            cleanup = _docker(
                [
                    "ps",
                    "-aq",
                    "--filter",
                    "label=cvals.workspace=" + _workspace_label(self.settings),
                ],
                self.settings,
            )
            self.checks.append(
                {
                    "name": "runtime: no owned grading containers retained",
                    "passed": cleanup.returncode == 0 and not cleanup.stdout.strip(),
                }
            )
            if any(not c["passed"] for c in self.checks):
                result["status"] = "failed"
            result.update(
                elapsed_seconds=round(time.time() - self.started, 3),
                checks=self.checks,
                check_count=len(self.checks),
                command_count=len(self.commands),
                private_artifacts="private/",
                limitations=[
                    "Original small local Git histories; no real GitHub PR metadata, external customer repositories, or representative model measurements.",
                    "Prepared Python/pytest and Node node:test paths; this run does not cover every package manager, runner, language, or hostile same-process grader manipulation.",
                    "No paid model calls; fixture controls and replayed evidence cannot establish agent quality.",
                ],
            )
            self.save("summary.json", result)
            normalized = {
                **result,
                "checks": [
                    {
                        "name": item["name"],
                        "status": "passed" if item["passed"] else "failed",
                        "evidence": {
                            key: value
                            for key, value in item.items()
                            if key not in {"name", "passed"}
                        },
                    }
                    for item in self.checks
                ],
            }
            completed = [key for key in ("python", "node") if key in result["stages"]]
            normalized["scope"] = {
                "completed_repository_pipelines": len(completed),
                "fixture_repositories_created": sum(
                    (path / ".git").is_dir() for path in (self.output / "sources").glob("*")
                ),
                "completed_validation_cycles": 3 * len(completed),
                "portable_pack_workspaces": 2 if "packs" in result["stages"] else 0,
                "demo_domains": 3 if "packs" in result["stages"] else 0,
                "paid_provider_calls": 0,
            }
            for item in normalized["checks"]:
                name = item["name"]
                language = (
                    "python"
                    if name.startswith("python:")
                    else "node"
                    if name.startswith("node:")
                    else None
                )
                artifact = "private/commands.json"
                if language and (
                    "cycle" in name or "admission valid" in name or "outcome signatures" in name
                ):
                    artifact = f"private/{language}-validation.json"
                elif language and "public container" in name:
                    artifact = f"private/{language}-public-container-probe.json"
                elif language and "public task" in name:
                    artifact = f"private/{language}-public-task.json"
                elif language and "engine" in name:
                    artifact = f"private/{language}-experiment.json"
                elif language and "quarantined" in name:
                    artifact = f"private/{language}-invalid-validation.json"
                elif name.startswith("demo:"):
                    artifact = "private/demo-returned-evidence.json"
                item["evidence"]["artifact"] = artifact
            self.save("results.json", normalized)
            print(
                json.dumps(
                    {"status": result["status"], "checks": len(self.checks), "phase": self.phase}
                ),
                flush=True,
            )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private artifact directory on the workspace machine.",
    )
    parser.add_argument(
        "--python-image", required=True, help="Already-local prepared Python/pytest image ID."
    )
    parser.add_argument(
        "--node-image", default="node:24-slim", help="Already-local Node image; never pulled."
    )
    args = parser.parse_args()
    result = Acceptance(args.output, args.python_image, args.node_image).run()
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
