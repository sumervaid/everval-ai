"""Reviewed historical repairs, reproducible admission and immutable releases.

A manifest's provenance is a claim. Admission requires matching evidence in this
workspace, bound to the complete task, image, grader and control snapshots.
"""

from __future__ import annotations

import ast
import fcntl
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast

from cvals.models import Check, Collection, Scenario, canonical_json, content_hash, new_id, utc_now

if TYPE_CHECKING:
    from cvals.config import Settings

GRADER_REVISION = "historical-admission/1"
REPETITIONS = 3
_CONFIG = {"pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
_DERIVED = {"local_git_history", "github_pull_request", "historical_benchmark"}


def grader_hash() -> str:
    root = Path(__file__).parent
    return content_hash(
        {
            name: (root / name).read_text(encoding="utf-8")
            for name in ("benchmarks.py", "evaluators.py", "sandbox.py", "domains/mining.py")
        }
    )


def _hidden(path: str) -> bool:
    location = PurePosixPath(path)
    return (
        location.name.startswith("test_")
        or location.name.endswith("_test.py")
        or location.name == "conftest.py"
        or location.name in _CONFIG
        or any(part in {"test", "tests", ".github"} for part in location.parts)
    )


def _test_nodes(files: dict[str, str]) -> dict[str, str]:
    """Identify whole test bodies; pytest remains responsible for collection."""
    nodes: dict[str, str] = {}
    for path, source in sorted(files.items()):
        name = PurePosixPath(path).name
        if not path.endswith(".py") or not (name.startswith("test_") or name.endswith("_test.py")):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise ValueError(f"Test source is invalid Python: {path}") from exc
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith(
                "test"
            ):
                nodes[f"{path}::{item.name}"] = content_hash(
                    ast.dump(item, include_attributes=False)
                )
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(
                        method, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and method.name.startswith("test"):
                        nodes[f"{path}::{item.name}::{method.name}"] = content_hash(
                            {
                                "class_bases": [ast.dump(n) for n in item.bases],
                                "class_decorators": [ast.dump(n) for n in item.decorator_list],
                                "method": ast.dump(method, include_attributes=False),
                            }
                        )
    return nodes


def _candidate_hash(candidate: dict[str, Any]) -> str:
    return content_hash(
        {
            key: candidate.get(key)
            for key in (
                "id",
                "project_id",
                "number",
                "source_kind",
                "base_commit",
                "reference_commit",
                "changed_files",
            )
        }
    )


def _checked(row: Any) -> dict[str, Any]:
    if row is None:
        raise KeyError("Benchmark record does not exist")
    value = json.loads(row["data"])
    if content_hash(value) != row["hash"]:
        raise ValueError("Benchmark record failed its integrity check")
    return dict(value)


def assert_collection_admitted(connection: sqlite3.Connection, collection: dict[str, Any]) -> None:
    """Called inside *every* experiment insertion, including replay and reruns."""
    release_id = collection.get("benchmark_release_id")
    derived = any(
        scenario.get("provenance", {}).get("origin") in _DERIVED
        or scenario.get("id", "").startswith(("mined-", "historical-"))
        or (
            scenario.get("provenance", {}).get("base_commit")
            and scenario.get("provenance", {}).get("reference_commit")
        )
        for scenario in collection.get("scenarios", [])
    )
    if not release_id:
        if derived:
            raise ValueError(
                "Historical tasks require a locally validated, reviewed benchmark release"
            )
        return
    try:
        release = _checked(
            connection.execute(
                "SELECT data,hash FROM benchmark_releases WHERE id=?", (release_id,)
            ).fetchone()
        )
    except sqlite3.OperationalError as exc:
        raise ValueError("This workspace has no matching benchmark release evidence") from exc
    partition = collection.get("benchmark_partition")
    if release["collection_ids"].get(partition) != collection.get("id"):
        raise ValueError("The collection is not a member of its declared benchmark release")
    if release["collection_hashes"].get(partition) != content_hash(collection):
        raise ValueError("The released collection has been modified")
    members = {item["scenario_id"]: item for item in release["members"]}
    current_grader_hash = grader_hash()
    for scenario in collection["scenarios"]:
        member = members.get(scenario["id"])
        if member is None or member["scenario_hash"] != content_hash(scenario):
            raise ValueError("Scenario does not match its frozen release membership")
        attestation = _checked(
            connection.execute(
                "SELECT data,hash FROM benchmark_validations WHERE id=?", (member["validation_id"],)
            ).fetchone()
        )
        if (
            content_hash(attestation) != member["attestation_hash"]
            or attestation["status"] != "valid"
            or attestation["grader_revision"] != GRADER_REVISION
            or attestation.get("grader_hash") != current_grader_hash
            or attestation["scenario_hash"] != content_hash(scenario)
            or attestation["repetitions"] != REPETITIONS
            or not attestation.get("review")
        ):
            raise ValueError("Scenario admission evidence is missing, incompatible or modified")


class Benchmarks:
    def __init__(self, settings: Settings):
        from cvals.projects import Projects

        self.settings = settings
        self.projects = Projects(settings)
        self.store = self.projects.store
        self.lock_dir = settings.data_dir / "benchmark-locks"
        self.lock_dir.mkdir(mode=0o700, exist_ok=True)
        if self.lock_dir.is_symlink():
            raise ValueError("Benchmark lock storage may not be a symbolic link")
        with self.store._connect(write=True) as connection:
            for table in ("benchmark_profiles", "benchmark_reviews", "benchmark_validations"):
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, "
                    "project_id TEXT NOT NULL REFERENCES repository_projects(id), "
                    "candidate_id TEXT, data TEXT NOT NULL, hash TEXT NOT NULL)"
                )
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_project ON {table}(project_id,candidate_id)"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS benchmark_releases (id TEXT PRIMARY KEY, "
                "project_id TEXT NOT NULL REFERENCES repository_projects(id), "
                "name TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(project_id,name))"
            )
            for table in (
                "benchmark_profiles",
                "benchmark_reviews",
                "benchmark_validations",
                "benchmark_releases",
            ):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                        f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Benchmark evidence is append-only'); END"
                    )
            for operation in ("UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS released_collections_no_{operation.lower()} "
                    f"BEFORE {operation} ON collections "
                    "WHEN json_extract(OLD.data,'$.benchmark_release_id') IS NOT NULL "
                    "BEGIN SELECT RAISE(ABORT,'Released benchmark collections are immutable'); END"
                )
        from cvals.audit import AuditLog

        self.audit = AuditLog(settings)

    @contextmanager
    def _lock(self, project_id: str) -> Iterator[None]:
        self.projects.get(project_id)
        name = content_hash(project_id) + ".lock"
        descriptor = os.open(self.lock_dir / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(
                    "This project's benchmark validation or release is already running"
                ) from exc
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _append(self, table: str, value: dict[str, Any], action: str) -> dict[str, Any]:
        if table not in {"benchmark_profiles", "benchmark_reviews", "benchmark_validations"}:
            raise ValueError("Unknown benchmark evidence table")
        with self.store._connect(write=True) as connection:
            connection.execute(
                f"INSERT INTO {table}(id,project_id,candidate_id,data,hash) VALUES(?,?,?,?,?)",
                (
                    value["id"],
                    value["project_id"],
                    value.get("candidate_id"),
                    canonical_json(value),
                    content_hash(value),
                ),
            )
            self.audit.record(
                action,
                value["id"],
                {
                    "project_id": value["project_id"],
                    "candidate_id": value.get("candidate_id"),
                    "status": value.get("status"),
                    "hash": content_hash(value),
                },
                connection=connection,
            )
        return value

    def _latest(
        self, table: str, project_id: str, candidate_id: str | None = None
    ) -> dict[str, Any] | None:
        if table not in {"benchmark_profiles", "benchmark_reviews", "benchmark_validations"}:
            raise ValueError("Unknown benchmark evidence table")
        with self.store._connect() as connection:
            row = connection.execute(
                f"SELECT data,hash FROM {table} WHERE project_id=? AND candidate_id IS ? ORDER BY rowid DESC LIMIT 1",
                (project_id, candidate_id),
            ).fetchone()
        return _checked(row) if row else None

    def configure(
        self,
        project_id: str,
        image: str,
        timeout_seconds: int = 60,
        plugins: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a preinstalled pytest image; resolve its immutable local ID."""
        from cvals.sandbox import _image

        self.projects.get(project_id)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("The repository test deadline must be 1–120 seconds")
        plugins = plugins or []
        if len(plugins) > 16 or any(
            not isinstance(p, str) or not re.fullmatch(r"[A-Za-z_][\w.]{0,199}", p) for p in plugins
        ):
            raise ValueError("Pytest plugins must be explicit module names")
        digest = _image(replace(self.settings, sandbox_image=image))
        value = {
            "id": new_id(),
            "project_id": project_id,
            "candidate_id": None,
            "kind": "prepared-pytest",
            "image_digest": digest,
            "requested_image": image,
            "timeout_seconds": timeout_seconds,
            "plugins": plugins,
            "max_files": 2048,
            "max_bytes": 16 * 1024 * 1024,
            "created_at": utc_now(),
            "integrity_mode": "same-process pytest; not resistant to deliberate grader manipulation",
            "network": "none",
            "installs": "none",
        }
        return self._append("benchmark_profiles", value, "benchmark.profile_created")

    def profile(self, project_id: str) -> dict[str, Any] | None:
        self.projects.get(project_id)
        return self._latest("benchmark_profiles", project_id)

    def review(
        self,
        project_id: str,
        candidate_id: str,
        statement: str,
        reviewer: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Store the exact approved problem wording without copying fix metadata."""
        candidate = self.projects.candidate(project_id, candidate_id)
        for name, field_value, limit in (
            ("statement", statement, 100000),
            ("reviewer", reviewer, 200),
            ("rationale", rationale, 10000),
        ):
            if (
                not isinstance(field_value, str)
                or not field_value.strip()
                or len(field_value) > limit
            ):
                raise ValueError(f"{name} must be nonempty text of at most {limit} characters")
        value = {
            "id": new_id(),
            "project_id": project_id,
            "candidate_id": candidate_id,
            "statement": statement.strip(),
            "reviewer": reviewer.strip(),
            "rationale": rationale.strip(),
            "reviewer_identity": "self-entered label; not authenticated identity",
            "candidate_hash": _candidate_hash(candidate),
            "created_at": utc_now(),
            "assertion": "Reviewed for hindsight and solution leakage; describes pre-fix observable requirements",
        }
        with self._lock(project_id):
            return self._append("benchmark_reviews", value, "benchmark.problem_reviewed")

    def _compile(
        self,
        candidate: dict[str, Any],
        review: dict[str, Any] | None,
        profile: dict[str, Any] | None,
    ) -> Scenario:
        from cvals.domains.mining import _function_file, extract_function_cases
        from cvals.sandbox import _image, validate_files

        if candidate["status"] != "discovered":
            raise ValueError("Candidate is quarantined: " + "; ".join(candidate.get("reasons", [])))
        if review and review["candidate_hash"] != _candidate_hash(candidate):
            raise ValueError(
                "Problem review predates a changed candidate; review the exact commits again"
            )
        limit = profile["max_bytes"] if profile else 4 * 1024 * 1024
        count = profile["max_files"] if profile else 128
        before = self.projects.snapshot(
            candidate["project_id"],
            candidate["base_commit"],
            include_tests=True,
            max_bytes=limit,
            max_files=count,
        )
        after = self.projects.snapshot(
            candidate["project_id"],
            candidate["reference_commit"],
            include_tests=True,
            max_bytes=limit,
            max_files=count,
        )
        if not before["complete"] or not after["complete"]:
            raise ValueError(
                "Source snapshot is incomplete (binary, symlink, submodule, sensitive or oversized files); prepare a supported source-only project"
            )
        base_all, fixed_all = before["files"], after["files"]
        digest = profile["image_digest"] if profile else _image(self.settings)
        checks: list[Check] = []
        if profile:
            base = {path: text for path, text in base_all.items() if not _hidden(path)}
            fixed = {path: text for path, text in fixed_all.items() if not _hidden(path)}
            old_tests, new_tests = _test_nodes(base_all), _test_nodes(fixed_all)
            historical = sorted(
                node
                for node, fingerprint in new_tests.items()
                if old_tests.get(node) != fingerprint
            )
            regression = sorted(
                node
                for node, fingerprint in old_tests.items()
                if new_tests.get(node) == fingerprint
            )
            if not historical or not regression:
                raise ValueError(
                    "A benchmark needs new/changed test bodies and separate unchanged base regression tests"
                )
            if len(historical) > 500 or len(regression) > 500:
                raise ValueError("More than 500 selected tests in a control; use a smaller project")
            for phase, selectors, source in (
                ("historical", historical, fixed_all),
                ("regression", regression, base_all),
            ):
                hidden = {path: text for path, text in source.items() if _hidden(path)}
                checks.append(
                    Check(
                        id=phase,
                        name="Historical failure"
                        if phase == "historical"
                        else "Base regression preservation",
                        kind="python_tests",
                        path="files",
                        expected={
                            "image_digest": digest,
                            "phase": phase,
                            "pytest": {
                                "hidden_files": hidden,
                                "selectors": selectors,
                                "plugins": profile["plugins"],
                                "timeout_seconds": profile["timeout_seconds"],
                            },
                        },
                    )
                )
            validate_files(base, repository_mode=True)
            validate_files(fixed, repository_mode=True)
            integrity = profile["integrity_mode"]
        else:
            base = {
                path: text
                for path, text in base_all.items()
                if path.endswith(".py") and not _hidden(path)
            }
            fixed = {
                path: text
                for path, text in fixed_all.items()
                if path.endswith(".py") and not _hidden(path)
            }
            if any(
                PurePosixPath(path).name == "conftest.py" for path in set(base_all) | set(fixed_all)
            ):
                raise ValueError("Fixture-dependent repositories require a prepared pytest profile")
            groups: list[dict[str, dict[str, dict[str, Any]]]] = []
            for files in (base_all, fixed_all):
                group: dict[str, dict[str, dict[str, Any]]] = {}
                for path, source in sorted(files.items()):
                    if (
                        _hidden(path)
                        and path.endswith(".py")
                        and PurePosixPath(path).name != "__init__.py"
                    ):
                        extracted = extract_function_cases(source, path)
                        for entrypoint, cases in extracted.items():
                            for case in cases:
                                body = {key: val for key, val in case.items() if key != "id"}
                                key = content_hash(body)
                                group.setdefault(entrypoint, {})[key] = {**body, "id": key[:24]}
                groups.append(group)
            old, new = groups
            for entrypoint in sorted(new):
                _function_file(entrypoint, base)
                _function_file(entrypoint, fixed)
                historical_cases = [
                    case
                    for key, case in new[entrypoint].items()
                    if key not in old.get(entrypoint, {})
                ]
                regression_cases = [
                    case for key, case in old.get(entrypoint, {}).items() if key in new[entrypoint]
                ]
                if not historical_cases:
                    continue
                if not regression_cases:
                    raise ValueError(f"{entrypoint} has no unchanged base regression assertion")
                for phase, cases in (
                    ("historical", historical_cases),
                    ("regression", regression_cases),
                ):
                    checks.append(
                        Check(
                            id=f"{phase}-{len(checks) + 1}",
                            name=f"{phase.title()} · {entrypoint}"[:200],
                            kind="python_tests",
                            path="files",
                            expected={
                                "entrypoint": entrypoint,
                                "cases": cases,
                                "phase": phase,
                                "image_digest": digest,
                            },
                        )
                    )
            validate_files(base)
            validate_files(fixed)
            integrity = (
                "host compares expected JSON; expected answers stay outside candidate container"
            )
        if not checks:
            raise ValueError("No historical failure and regression controls could be compiled")
        if len(checks) > 20:
            raise ValueError("A candidate exceeds the 20 verifier control limit")
        if base == fixed:
            raise ValueError("The candidate does not change its public source snapshot")
        statement = review["statement"] if review else candidate.get("issue_statement", "")
        if not statement.strip():
            raise ValueError("Review a concrete pre-fix problem statement before validation")
        scenario_id = (
            "historical-"
            + content_hash({"project": candidate["project_id"], "candidate": candidate["id"]})[:24]
        )
        return Scenario(
            id=scenario_id,
            name=f"Repository repair {candidate.get('number') or candidate['id'][:8]}",
            domain="coding",
            instruction=statement
            + "\n\nPreserve existing behavior. Return a JSON object with files mapping paths to complete UTF-8 source. Set replace_files to true when submitting a complete snapshot, including deletions. Private tests are evaluated separately.",
            inputs={
                "files": base,
                "environment": {
                    "image_digest": digest,
                    "kind": "prepared-pytest" if profile else "python-function-cases",
                    "integrity_mode": integrity,
                },
            },
            reference_output={"files": fixed, "replace_files": True},
            checks=checks,
            tags=["historical", "python", "prepared-pytest" if profile else "function-cases"],
            provenance={
                "origin": "historical_benchmark",
                "project_id": candidate["project_id"],
                "candidate_id": candidate["id"],
                "source_kind": candidate["source_kind"],
                "base_commit": candidate["base_commit"],
                "reference_commit": candidate["reference_commit"],
                "pull_request_number": candidate.get("number"),
                "url": candidate.get("url"),
                "candidate_hash": _candidate_hash(candidate),
                "snapshot_hashes": [before["snapshot_hash"], after["snapshot_hash"]],
                "review_id": review["id"] if review else None,
                "profile_hash": content_hash(profile) if profile else None,
                "grader_revision": GRADER_REVISION,
                "integrity_mode": integrity,
            },
        )

    def validate(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        from cvals.evaluators import evaluate

        with self._lock(project_id):
            candidate = self.projects.candidate(project_id, candidate_id)
            review = self._latest("benchmark_reviews", project_id, candidate_id)
            profile = self._latest("benchmark_profiles", project_id)
            value: dict[str, Any] = {
                "id": new_id(),
                "project_id": project_id,
                "candidate_id": candidate_id,
                "candidate_hash": _candidate_hash(candidate),
                "status": "quarantined",
                "reasons": [],
                "created_at": utc_now(),
                "completed_at": None,
                "grader_revision": GRADER_REVISION,
                "grader_hash": grader_hash(),
                "repetitions": REPETITIONS,
                "review": review,
                "profile": profile,
                "scenario": None,
                "scenario_hash": None,
                "controls_hash": None,
                "environment_hash": None,
                "evidence": [],
            }
            try:
                scenario = self._compile(candidate, review, profile)
                serialized = scenario.model_dump(mode="json")
                value.update(
                    scenario=serialized,
                    scenario_hash=content_hash(serialized),
                    controls_hash=content_hash(
                        {
                            "base": scenario.inputs["files"],
                            "reference": scenario.reference_output,
                            "checks": serialized["checks"],
                        }
                    ),
                    environment_hash=content_hash(scenario.inputs["environment"]),
                )
                signatures = []
                for repetition in range(REPETITIONS):
                    before = evaluate(scenario, {"files": scenario.inputs["files"]}, self.settings)
                    after = evaluate(scenario, scenario.reference_output or {}, self.settings)

                    def evidence(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
                        return [
                            {
                                "id": result["id"],
                                "passed": result["passed"],
                                "actual": {
                                    key: (item[-4096:] if key == "output" else item)
                                    for key, item in result["actual"].items()
                                },
                            }
                            for result in results
                        ]

                    cycle = {
                        "repetition": repetition + 1,
                        "base": evidence(before),
                        "reference": evidence(after),
                    }
                    value["evidence"].append(cycle)
                    signature = []
                    for check, old, fixed in zip(scenario.checks, before, after, strict=True):
                        for result in (old, fixed):
                            actual = result["actual"]
                            if (
                                not actual.get("tests_run")
                                or actual.get("errors")
                                or actual.get("skipped")
                                or actual.get("infrastructure_error")
                                or actual.get("timed_out")
                                or actual.get("output_truncated")
                                or actual.get("oom_killed")
                            ):
                                raise ValueError(
                                    "Infrastructure, zero-test or incomplete test evidence cannot validate a task"
                                )
                            if any(
                                "exception" in (case.get("actual") or {})
                                for case in actual.get("test_cases", [])
                                if isinstance(case.get("actual"), dict)
                            ):
                                raise ValueError(
                                    "Function exceptions cannot establish a historical assertion failure"
                                )
                        if not fixed["passed"]:
                            raise ValueError(
                                "The reference revision fails a frozen verifier control"
                            )
                        phase = check.expected["phase"]
                        if phase == "regression" and not old["passed"]:
                            raise ValueError(
                                "Base regression tests do not pass before and after the change"
                            )
                        if phase == "historical" and old["passed"]:
                            raise ValueError(
                                "A historical control does not fail on the base revision"
                            )
                        old_cases, fixed_cases = (
                            old["actual"]["test_cases"],
                            fixed["actual"]["test_cases"],
                        )
                        if {case["id"] for case in old_cases} != {
                            case["id"] for case in fixed_cases
                        }:
                            raise ValueError("Base and reference collected different test cases")
                        signature.append(
                            {
                                "check": check.id,
                                "base": sorted((case["id"], case["status"]) for case in old_cases),
                                "reference": sorted(
                                    (case["id"], case["status"]) for case in fixed_cases
                                ),
                            }
                        )
                    signatures.append(content_hash(signature))
                if len(set(signatures)) != 1:
                    raise ValueError("Repeated fresh verifier runs disagree; candidate is flaky")
                if review is None:
                    value["status"] = "needs_review"
                    value["reasons"] = [
                        "Mechanical controls passed; review problem wording and validate again before release"
                    ]
                else:
                    value["status"] = "valid"
            except (ValueError, KeyError, SyntaxError) as exc:
                value["reasons"] = [str(exc)[:2000]]
            value["completed_at"] = utc_now()
            return self._append("benchmark_validations", value, "benchmark.validated")

    def validations(self, project_id: str, candidate_id: str | None = None) -> list[dict[str, Any]]:
        self.projects.get(project_id)
        with self.store._connect() as connection:
            if candidate_id:
                rows = connection.execute(
                    "SELECT data,hash FROM benchmark_validations WHERE project_id=? AND candidate_id=? ORDER BY rowid DESC",
                    (project_id, candidate_id),
                )
            else:
                rows = connection.execute(
                    "SELECT data,hash FROM benchmark_validations WHERE project_id=? ORDER BY rowid DESC",
                    (project_id,),
                )
            return [_checked(row) for row in rows]

    def cases(self, project_id: str) -> list[dict[str, Any]]:
        """Latest validation for each discovered candidate, including quarantine."""
        records = []
        for candidate in self.projects.candidates(project_id):
            validation = self._latest("benchmark_validations", project_id, candidate["id"])
            review = self._latest("benchmark_reviews", project_id, candidate["id"])
            records.append(
                {
                    "candidate_id": candidate["id"],
                    "number": candidate.get("number"),
                    "status": validation["status"] if validation else "not_validated",
                    "validation_id": validation["id"] if validation else None,
                    "scenario_id": (validation.get("scenario") or {}).get("id")
                    if validation
                    else None,
                    "review_id": review["id"] if review else None,
                    "reasons": validation["reasons"]
                    if validation
                    else candidate.get("reasons", []),
                }
            )
        return records

    def release(self, project_id: str, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Release name must contain 1–200 characters")
        with self._lock(project_id):
            project = self.projects.get(project_id)
            admitted = []
            excluded = []
            for candidate in self.projects.candidates(project_id):
                validation = self._latest("benchmark_validations", project_id, candidate["id"])
                review = self._latest("benchmark_reviews", project_id, candidate["id"])
                if (
                    validation
                    and validation["status"] == "valid"
                    and review
                    and validation["review"]["id"] == review["id"]
                    and validation.get("grader_hash") == grader_hash()
                    and validation["candidate_hash"] == _candidate_hash(candidate)
                    and candidate["status"] == "discovered"
                ):
                    admitted.append(validation)
                else:
                    excluded.append(
                        {
                            "candidate_id": candidate["id"],
                            "reason": "Latest candidate and review lack matching successful admission",
                        }
                    )
            if not admitted:
                raise ValueError(
                    "No candidates have current reviewed, repeated fail-before/pass-after and regression evidence"
                )
            release_id = new_id()
            members = []
            partitions: dict[str, list[Scenario]] = {"dev": [], "holdout": [], "all": []}
            for validation in admitted:
                scenario = Scenario.model_validate(validation["scenario"])
                provenance = scenario.provenance
                group = f"{project_id}:{provenance['pull_request_number'] if provenance['pull_request_number'] is not None else provenance['reference_commit']}"
                split_hash = content_hash(group)
                partition = "holdout" if int(split_hash[:8], 16) % 5 == 0 else "dev"
                partitions[partition].append(scenario)
                partitions["all"].append(scenario)
                members.append(
                    {
                        "scenario_id": scenario.id,
                        "scenario_hash": validation["scenario_hash"],
                        "candidate_id": validation["candidate_id"],
                        "validation_id": validation["id"],
                        "attestation_hash": content_hash(validation),
                        "partition": partition,
                        "group": group,
                        "split_hash": split_hash,
                    }
                )
            with self.store._connect(write=True) as connection:
                version = (
                    connection.execute(
                        "SELECT COUNT(*) FROM benchmark_releases WHERE project_id=?", (project_id,)
                    ).fetchone()[0]
                    + 1
                )
                collections: dict[str, dict[str, Any]] = {}
                for partition, scenarios in partitions.items():
                    if not scenarios:
                        continue
                    collections[partition] = Collection(
                        name=f"{project['name']} · {name} · {partition}"[:200],
                        version=f"{version}.0.0",
                        domain="coding",
                        scenarios=scenarios,
                        benchmark_release_id=release_id,
                        benchmark_partition=cast(Literal["dev", "holdout", "all"], partition),
                        description="Immutable reviewed historical benchmark. Holdout results must not be used for development.",
                    ).model_dump(mode="json")
                value = {
                    "id": release_id,
                    "project_id": project_id,
                    "name": name.strip(),
                    "version": version,
                    "created_at": utc_now(),
                    "grader_revision": GRADER_REVISION,
                    "collection_id": collections.get("dev", {}).get("id"),
                    "collection_ids": {key: val["id"] for key, val in collections.items()},
                    "collection_hashes": {
                        key: content_hash(val) for key, val in collections.items()
                    },
                    "members": sorted(members, key=lambda item: item["scenario_id"]),
                    "excluded": excluded,
                    "split_policy": "sha256(project_id:PR number or local reference SHA), integer first 8 hex modulo 5; zero=holdout",
                    "holdout_only": not partitions["dev"],
                    "notice": "Three identical fresh controls screen obvious flakiness; they do not prove determinism. Same-process pytest is not tamper resistant.",
                }
                try:
                    connection.execute(
                        "INSERT INTO benchmark_releases(id,project_id,name,data,hash) VALUES(?,?,?,?,?)",
                        (
                            release_id,
                            project_id,
                            name.strip(),
                            canonical_json(value),
                            content_hash(value),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(
                        "Release names are immutable and must be unique within a project"
                    ) from exc
                for collection in collections.values():
                    assert_collection_admitted(connection, collection)
                    connection.execute(
                        "INSERT INTO collections(id,data) VALUES(?,?)",
                        (collection["id"], canonical_json(collection)),
                    )
                self.audit.record(
                    "benchmark.released",
                    release_id,
                    {
                        "project_id": project_id,
                        "version": version,
                        "members": len(members),
                        "hash": content_hash(value),
                    },
                    connection=connection,
                )
            return value

    def list(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = (
                connection.execute(
                    "SELECT data,hash FROM benchmark_releases WHERE project_id=? ORDER BY rowid DESC",
                    (project_id,),
                )
                if project_id
                else connection.execute(
                    "SELECT data,hash FROM benchmark_releases ORDER BY rowid DESC"
                )
            )
            return [_checked(row) for row in rows]

    def get(self, release_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            return _checked(
                connection.execute(
                    "SELECT data,hash FROM benchmark_releases WHERE id=?", (release_id,)
                ).fetchone()
            )

    def diff(self, before_id: str, after_id: str) -> dict[str, Any]:
        before, after = self.get(before_id), self.get(after_id)
        old = {item["scenario_id"]: item for item in before["members"]}
        new = {item["scenario_id"]: item for item in after["members"]}
        return {
            "before": before_id,
            "after": after_id,
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(
                key
                for key in old.keys() & new.keys()
                if old[key]["scenario_hash"] != new[key]["scenario_hash"]
            ),
            "unchanged": sorted(
                key
                for key in old.keys() & new.keys()
                if old[key]["scenario_hash"] == new[key]["scenario_hash"]
            ),
        }

    def export(self, release_id: str) -> dict[str, Any]:
        release = self.get(release_id)
        with self.store._connect() as connection:
            collections = {
                partition: self.store._get(connection, "collections", identifier)
                for partition, identifier in release["collection_ids"].items()
            }
            for collection in collections.values():
                assert_collection_admitted(connection, collection)
            validations = [
                _checked(
                    connection.execute(
                        "SELECT data,hash FROM benchmark_validations WHERE id=?",
                        (member["validation_id"],),
                    ).fetchone()
                )
                for member in release["members"]
            ]
        return {
            "schema_version": 1,
            "release": release,
            "release_hash": content_hash(release),
            "collections": collections,
            "validations": validations,
            "contains_private_grader_data": True,
        }
