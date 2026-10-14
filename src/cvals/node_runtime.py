"""Frozen JavaScript/TypeScript repository tests in isolated prepared images."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

RUNNERS = {"node-test", "jest", "vitest"}
_EXT = r"(?:[cm]?[jt]sx?)"
_CONFIG = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "tsconfig.json",
    "jsconfig.json",
    ".babelrc",
    ".babelrc.json",
    ".babelrc.js",
}


def is_test(path: str) -> bool:
    location = PurePosixPath(path)
    return bool(re.search(r"(?:\.test|\.spec)\." + _EXT + r"$", location.name)) or (
        any(p in {"test", "tests", "__tests__"} for p in location.parts)
        and bool(re.search(r"\." + _EXT + r"$", location.name))
    )


def hidden(path: str) -> bool:
    location = PurePosixPath(path)
    return (
        is_test(path)
        or location.name in _CONFIG
        or bool(re.match(r"(?:jest|vitest|vite|tsconfig|babel\.config)\.", location.name))
        or any(
            p in {"test", "tests", "__tests__", "__snapshots__", ".github"} for p in location.parts
        )
    )


def validate_source(files: dict[str, str]) -> None:
    from cvals.sandbox import validate_files

    validate_files(files, repository_mode=True)
    if any(
        hidden(path)
        or "node_modules" in PurePosixPath(path).parts
        or PurePosixPath(path).name == ".npmrc"
        for path in files
    ):
        raise ValueError(
            "Candidate source cannot replace frozen Node tests, dependencies or configuration"
        )


# Reporters run with test code inside the container. This offers reproducible
# functional validation, not protection from a deliberately hostile submission.
_NODE_REPORTER = r"""
export default async function* report(source) {
  const records = [];
  for await (const event of source) {
    const d = event.data;
    if (event.type !== 'test:pass' && event.type !== 'test:fail') continue;
    const error = d.details?.error;
    if (d.name === d.file || d.name === d.file?.replace('/workspace/','')) continue;
    if (d.details?.type === 'suite' || error?.failureType === 'subtestsFailed') continue;
    const cause = error?.cause || error;
    records.push({file:d.file, name:d.name, line:d.line || 0, column:d.column || 0,
      status:d.skip || d.todo ? 'skipped' : event.type === 'test:pass' ? 'passed' : 'failed',
      assertion: cause?.code === 'ERR_ASSERTION' || cause?.name === 'AssertionError'});
  }
  yield '\n__EVERVAL_NODE__' + JSON.stringify({records}) + '\n';
}
"""
_RUNNER = r"""
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const spec = JSON.parse(fs.readFileSync('/grade/spec.json','utf8'));
let args;
if (spec.runner === 'node-test') {
  args = ['--test','--test-concurrency=1','--test-reporter=/grade/reporter.mjs',...spec.selectors];
} else if (spec.runner === 'jest') {
  args = ['/node_modules/jest/bin/jest.js','--runInBand','--no-cache','--json',
    '--outputFile=/tmp/results.json','--runTestsByPath',...spec.selectors];
} else {
  args = ['/node_modules/vitest/vitest.mjs','run','--reporter=json','--outputFile=/tmp/results.json',
    '--maxWorkers=1','--no-file-parallelism','--no-cache',...spec.selectors];
  const major = Number(JSON.parse(fs.readFileSync('/node_modules/vitest/package.json','utf8')).version.split('.')[0]);
  if (major >= 5) args.splice(2,0,'--fsModuleCachePath=/tmp/vitest-cache');
}
const env = {...process.env, CI:'true', NO_COLOR:'1', HOME:'/tmp', TMPDIR:'/tmp', NODE_PATH:'/node_modules'};
const result = spawnSync(process.execPath,args,{cwd:'/workspace',env,encoding:'utf8',maxBuffer:1024*1024});
if (result.error || result.signal || ![0,1].includes(result.status)) process.exit(3);
if (spec.runner === 'node-test') {
  process.stdout.write(result.stdout || '');
} else {
  let report;
  try { report = JSON.parse(fs.readFileSync('/tmp/results.json','utf8')); }
  catch { process.exit(4); }
  const records = [];
  let collectionErrors = report.numRuntimeErrorTestSuites || 0;
  for (const suite of report.testResults || []) {
    if (!(suite.assertionResults || []).length || suite.testExecError) collectionErrors++;
    for (const test of suite.assertionResults || []) {
      const details = test.failureDetails || [];
      const messages = (test.failureMessages || []).join('\n');
      records.push({file:suite.name, name:test.fullName || test.title, line:0,column:0,
        status:test.status, assertion:details.some(d => d?.matcherResult || d?.name === 'AssertionError' || d?.code === 'ERR_ASSERTION')
          || /AssertionError|\bexpect\(/.test(messages)});
    }
  }
  console.log('\n__EVERVAL_NODE__' + JSON.stringify({records,collectionErrors}));
}
process.exit(result.status);
"""


def run_node_tests(
    files: dict[str, str],
    hidden_files: dict[str, str],
    selectors: list[str],
    settings: Any,
    timeout_seconds: int = 60,
    runner: str = "node-test",
) -> dict[str, Any]:
    from cvals.sandbox import _execute, validate_files

    validate_source(files)
    validate_files(hidden_files, repository_mode=True)
    if runner not in RUNNERS:
        raise ValueError("Node runner must be node-test, jest or vitest")
    if set(files) & set(hidden_files):
        raise ValueError("Candidate cannot overwrite private Node files")
    if (
        not isinstance(selectors, list)
        or not 1 <= len(selectors) <= 500
        or any(
            not isinstance(p, str)
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", p)
            or p not in hidden_files
            or not is_test(p)
            for p in selectors
        )
        or len(set(selectors)) != len(selectors)
    ):
        raise ValueError("Node tests require 1–500 explicit frozen test file paths")
    runtime = _execute(
        {**files, **hidden_files},
        {
            "runner.mjs": _RUNNER,
            "reporter.mjs": _NODE_REPORTER,
            "spec.json": json.dumps({"selectors": selectors, "runner": runner}),
        },
        settings,
        timeout_seconds,
        repository_mode=True,
        command=["node", "/grade/runner.mjs"],
    )
    result = {
        **runtime,
        "passed": False,
        "tests_run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "test_cases": [],
        "integrity": "same-process Node tests; not resistant to deliberate grader manipulation",
        "infrastructure_error": None,
    }

    def invalid(reason: str) -> dict[str, Any]:
        result.update(errors=1, infrastructure_error=reason)
        return result

    if any(runtime.get(k) for k in ("timed_out", "output_truncated", "oom_killed", "running")):
        return invalid("Node verifier exceeded a resource limit")
    lines = [line for line in runtime["output"].splitlines() if line.startswith("__EVERVAL_NODE__")]
    if len(lines) != 1 or runtime["exit_code"] not in {0, 1}:
        return invalid("Missing, ambiguous or incomplete Node runner evidence")
    try:
        payload = json.loads(lines[0][len("__EVERVAL_NODE__") :])
        records = payload["records"]
        if (
            payload.get("collectionErrors")
            or not isinstance(records, list)
            or not 1 <= len(records) <= 5000
        ):
            return invalid("Node runner collected zero tests or had a collection error")
        seen, covered = set(), set()
        for record in records:
            path = record["file"]
            if not isinstance(path, str):
                return invalid("Test file identity is absent")
            path = path.removeprefix("/workspace/")
            if path not in selectors:
                return invalid("Runner executed tests outside the frozen selection")
            name = record["name"]
            if not isinstance(name, str) or not name or len(name) > 2000:
                return invalid("Malformed Node test identity")
            identity = f"{path}::{record.get('line', 0)}:{record.get('column', 0)}::{name}"
            if identity in seen:
                return invalid("Duplicate Node test identity")
            seen.add(identity)
            covered.add(path)
            status = record["status"]
            if status not in {"passed", "failed"}:
                result["skipped"] += 1
                return invalid("Skipped, pending or todo tests cannot establish admission")
            if status == "failed" and record.get("assertion") is not True:
                return invalid("Non-assertion Node failure cannot establish a historical defect")
            result["failures"] += int(status == "failed")
            result["test_cases"].append({"id": identity, "status": status})
        if covered != set(selectors):
            return invalid("A frozen test file collected no tests")
        result["tests_run"] = len(records)
        if (result["failures"] == 0) != (runtime["exit_code"] == 0):
            return invalid("Docker exit status contradicts Node test evidence")
        result["passed"] = result["failures"] == 0
        return result
    except (ValueError, KeyError, TypeError):
        return invalid("Malformed Node runner evidence")
