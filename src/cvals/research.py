"""Bounded, provider-free experiments on retained grading evidence."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from collections.abc import Iterator
from typing import Any

from cvals.audit import append_audit
from cvals.config import Settings
from cvals.diagnostics import Diagnostics
from cvals.evaluators import evaluate
from cvals.models import Collection, Scenario, canonical_json, content_hash, new_id, utc_now
from cvals.pure_checks import assert_pure_work
from cvals.store import TERMINAL, Store

PURE_CHECKS = {"equals", "contains", "numeric", "json_schema", "citation", "forbidden"}
MAX_JSON_BYTES = 65536


class ResearchRecords:
    """Append-only state transitions, including interrupted synchronous work."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.store.init()
        with self.store._connect(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS research_events(
                record_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
                data TEXT NOT NULL, hash TEXT NOT NULL,
                PRIMARY KEY(record_id,sequence)
            )""")

    @staticmethod
    def append(connection: sqlite3.Connection, value: dict[str, Any]) -> dict[str, Any]:
        row = connection.execute(
            "SELECT sequence,hash FROM research_events WHERE record_id=? ORDER BY sequence DESC LIMIT 1",
            (value["id"],),
        ).fetchone()
        value = {
            **value,
            "sequence": row[0] + 1 if row else 1,
            "previous_hash": row[1] if row else None,
            "updated_at": utc_now(),
        }
        digest = content_hash(value)
        connection.execute(
            "INSERT INTO research_events VALUES(?,?,?,?,?)",
            (value["id"], value["sequence"], value["kind"], canonical_json(value), digest),
        )
        append_audit(
            connection,
            "research." + value["status"],
            value["id"],
            {"kind": value["kind"], "sequence": value["sequence"], "hash": digest},
        )
        return value

    @staticmethod
    def read(connection: sqlite3.Connection, identifier: str) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT sequence,data,hash FROM research_events WHERE record_id=? ORDER BY sequence",
            (identifier,),
        ).fetchall()
        if not rows:
            raise KeyError(identifier)
        previous = None
        value: dict[str, Any] = {}
        for sequence, row in enumerate(rows, 1):
            value = json.loads(row["data"])
            if (
                row["sequence"] != sequence
                or value["sequence"] != sequence
                or value["previous_hash"] != previous
                or content_hash(value) != row["hash"]
            ):
                raise ValueError("Research evidence integrity check failed")
            previous = row["hash"]
        return value

    def save(self, value: dict[str, Any]) -> dict[str, Any]:
        with self.store._connect(write=True) as connection:
            return self.append(connection, value)

    def get_record(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            return self.read(connection, identifier)

    def list_records(self, kind: str | None = None) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            identities = connection.execute(
                "SELECT record_id FROM research_events WHERE (? IS NULL OR kind=?) "
                "GROUP BY record_id ORDER BY MAX(rowid) DESC LIMIT 100",
                (kind, kind),
            ).fetchall()
            return [self.read(connection, row[0]) for row in identities]


def _signature(checks: list[dict[str, Any]]) -> list[list[Any]]:
    return [[c["id"], c["passed"], c["message"]] for c in checks]


def _passed(checks: list[dict[str, Any]]) -> bool:
    required = [c for c in checks if c["required"]] or checks
    return bool(required) and all(c["passed"] for c in required)


def _pure(scenario: Scenario) -> None:
    if any(c.kind not in PURE_CHECKS for c in scenario.checks):
        raise ValueError(
            "This operation supports pure JSON/text graders only; repository code is not executed"
        )
    if len(canonical_json(scenario.model_dump()).encode()) > MAX_JSON_BYTES:
        raise ValueError("Pure research scenarios are bounded to 64 KiB")


def _evaluate(
    scenario: Scenario, output: dict[str, Any], settings: Settings
) -> list[dict[str, Any]]:
    assert_pure_work(scenario, output)
    return evaluate(scenario, output, settings)


def _reductions(value: Any, depth: int = 0) -> Iterator[Any]:
    """Deterministic deletion grammar, with bounded recursive traversal."""
    if depth > 12:
        return
    if isinstance(value, dict):
        keys = list(value)
        for key in keys:
            yield {k: v for k, v in value.items() if k != key}
        for key in keys:
            for candidate in _reductions(value[key], depth + 1):
                yield {**value, key: candidate}
    elif isinstance(value, (list, str)) and value:
        size = len(value)
        chunk = max(1, size // 2)
        while True:
            for start in range(0, size, chunk):
                yield (
                    value[:start] + value[start + chunk :]
                    if isinstance(value, str)
                    else list(value[:start]) + list(value[start + chunk :])
                )
            if chunk == 1:
                break
            chunk = max(1, chunk // 2)
        if isinstance(value, list):
            for index, item in enumerate(value):
                for candidate in _reductions(item, depth + 1):
                    yield value[:index] + [candidate] + value[index + 1 :]


def _mutate_path(output: dict[str, Any], path: str, value: Any) -> dict[str, Any] | None:
    if not path:
        return value if isinstance(value, dict) else None
    result = copy.deepcopy(output)
    current: Any = result
    pieces = path.split(".")
    for piece in pieces[:-1]:
        if isinstance(current, dict) and piece in current:
            current = current[piece]
        elif isinstance(current, list) and piece.isdecimal() and int(piece) < len(current):
            current = current[int(piece)]
        else:
            return None
    last = pieces[-1]
    if isinstance(current, dict):
        current[last] = value
    elif isinstance(current, list) and last.isdecimal() and int(last) < len(current):
        current[int(last)] = value
    else:
        return None
    return result


def task_identity(scenario: dict[str, Any]) -> str:
    """Task content identity independent of display labels or reimported IDs."""
    return content_hash({key: scenario.get(key) for key in ("domain", "instruction", "inputs")})


def task_tokens(scenario: dict[str, Any]) -> set[str]:
    text = canonical_json({key: scenario.get(key) for key in ("instruction", "inputs")})
    return set(re.findall(r"\w+", text[:MAX_JSON_BYTES].lower()))


class ResearchLab(ResearchRecords):
    def _start(self, kind: str, **data: Any) -> dict[str, Any]:
        return self.save(
            {
                "id": new_id(),
                "kind": kind,
                "status": "running",
                "created_at": utc_now(),
                "uses_provider": False,
                **data,
            }
        )

    def repeat_regrade(
        self, experiment_id: str, trial_id: str, repetitions: int = 3
    ) -> dict[str, Any]:
        if type(repetitions) is not int or not 2 <= repetitions <= 10:
            raise ValueError("Repeated grading requires 2–10 repetitions")
        diagnostics = Diagnostics(self.settings)
        experiment, trial, scenario = diagnostics._trial(experiment_id, trial_id)
        if trial["status"] not in TERMINAL or trial.get("output") is None:
            raise ValueError("A finalized attempt with recorded output is required")
        pure_checks = [check for check in scenario.checks if check.kind in PURE_CHECKS]
        if pure_checks:
            assert_pure_work(scenario.model_copy(update={"checks": pure_checks}), trial["output"])
        record = self._start(
            "repeated_grading",
            experiment_id=experiment_id,
            trial_id=trial_id,
            snapshot_hash=experiment["snapshot_hash"],
            output_hash=content_hash(trial["output"]),
            requested_repetitions=repetitions,
            regrades=[],
            interpretation="Repeated grading of the same recorded output; this does not measure model nondeterminism",
        )
        try:
            for _ in range(repetitions):
                evidence = diagnostics.regrade(experiment_id, trial_id)
                record["regrades"].append(
                    {
                        "id": evidence["id"],
                        "passed": evidence["passed"],
                        "error": evidence["error"],
                        "check_signature": _signature(evidence["checks"]),
                    }
                )
                record = self.save(record)
            valid = [r for r in record["regrades"] if r["error"] is None]
            signatures = {content_hash(r["check_signature"]) for r in valid}
            record.update(
                status="completed",
                valid_repetitions=len(valid),
                grading_intermittency_observed=len(signatures) > 1,
                infrastructure_errors=sum(r["error"] is not None for r in record["regrades"]),
                conclusion=(
                    "inconclusive"
                    if len(valid) != repetitions
                    else "grading_disagreement_observed"
                    if len(signatures) > 1
                    else "no_disagreement_observed"
                ),
            )
        except Exception as exc:
            record.update(status="failed", error=str(exc)[:2000])
            self.save(record)
            raise
        return self.save(record)

    def minimize_output(
        self, experiment_id: str, trial_id: str, max_evaluations: int = 80
    ) -> dict[str, Any]:
        if type(max_evaluations) is not int or not 2 <= max_evaluations <= 200:
            raise ValueError("Minimization requires an evaluation cap of 2–200")
        experiment, trial, scenario = Diagnostics(self.settings)._trial(experiment_id, trial_id)
        if trial["status"] not in TERMINAL or not isinstance(trial.get("output"), dict):
            raise ValueError("A finalized attempt with a recorded JSON object is required")
        _pure(scenario)
        original = trial["output"]
        if len(canonical_json(original).encode()) > MAX_JSON_BYTES:
            raise ValueError("Recorded output exceeds the 64 KiB research limit")
        checks = _evaluate(scenario, original, self.settings)
        if _passed(checks):
            raise ValueError("Minimization requires a reproducible failing recorded output")
        signature = _signature(checks)
        record = self._start(
            "minimized_output",
            experiment_id=experiment_id,
            trial_id=trial_id,
            snapshot_hash=experiment["snapshot_hash"],
            output_hash=content_hash(original),
            original_bytes=len(canonical_json(original).encode()),
            max_evaluations=max_evaluations,
            evaluation_count=1,
            candidate_count=0,
            accepted_reductions=[],
            check_signature=signature,
            interpretation="Deletion-based output reduction preserving every check outcome and message; not a causal root-cause claim",
        )
        current = copy.deepcopy(original)
        exhausted = False
        try:
            seen = {content_hash(current)}
            while True:
                accepted = False
                for candidate in _reductions(current):
                    if (
                        record["evaluation_count"] >= max_evaluations
                        or record["candidate_count"] >= max_evaluations * 4
                    ):
                        exhausted = True
                        break
                    record["candidate_count"] += 1
                    digest = content_hash(candidate)
                    if digest in seen:
                        continue
                    seen.add(digest)
                    record["evaluation_count"] += 1
                    try:
                        candidate_checks = _evaluate(scenario, candidate, self.settings)
                    except ValueError:
                        continue
                    if _signature(candidate_checks) == signature:
                        current = candidate
                        record["accepted_reductions"].append(
                            {
                                "output_hash": digest,
                                "bytes": len(canonical_json(current).encode()),
                                "evaluation": record["evaluation_count"],
                            }
                        )
                        record = self.save(record)
                        accepted = True
                        break
                if exhausted or not accepted:
                    break
            record.update(
                status="completed",
                minimized_output=current,
                minimized_bytes=len(canonical_json(current).encode()),
                minimized_output_hash=content_hash(current),
                budget_exhausted=exhausted,
                locally_minimal_under_deletion_grammar=not exhausted,
                reproduction={
                    "scenario": scenario.model_dump(mode="json"),
                    "output": current,
                    "grader_revision": experiment["snapshot"].get("grader_revision"),
                    "private_material": True,
                },
            )
        except Exception as exc:
            record.update(status="failed", error=str(exc)[:2000])
            self.save(record)
            raise
        return self.save(record)

    def quality(
        self, collection_id: str, max_scenarios: int = 100, max_pairs: int = 5000
    ) -> dict[str, Any]:
        if type(max_scenarios) is not int or not 1 <= max_scenarios <= 200:
            raise ValueError("Quality inspection covers 1–200 scenarios per invocation")
        if type(max_pairs) is not int or not 1 <= max_pairs <= 20000:
            raise ValueError("Duplicate inspection requires a pair cap of 1–20000")
        collection = Collection.model_validate(self.store.get_collection(collection_id))
        record = self._start(
            "benchmark_quality",
            collection_id=collection_id,
            collection_hash=content_hash(collection.model_dump(mode="json")),
            findings=[],
            scenarios=[],
            duplicates=[],
            pairs_inspected=0,
            scope={
                "selected": min(max_scenarios, len(collection.scenarios)),
                "total": len(collection.scenarios),
                "pair_cap": max_pairs,
            },
        )
        try:
            selected = collection.scenarios[:max_scenarios]
            tokens = {s.id: task_tokens(s.model_dump()) for s in selected}
            for i, left in enumerate(selected):
                for right in selected[i + 1 :]:
                    if record["pairs_inspected"] >= max_pairs:
                        break
                    record["pairs_inspected"] += 1
                    a, b = tokens[left.id], tokens[right.id]
                    exact = task_identity(left.model_dump()) == task_identity(right.model_dump())
                    score = len(a & b) / len(a | b) if a | b else 0
                    if exact or score >= 0.9:
                        record["duplicates"].append(
                            {
                                "left": left.id,
                                "right": right.id,
                                "exact": exact,
                                "token_jaccard": score,
                                "interpretation": "exact task content"
                                if exact
                                else "lexical overlap; review required",
                            }
                        )
            for scenario in selected:
                item: dict[str, Any] = {"scenario_id": scenario.id, "probes": []}
                try:
                    _pure(scenario)
                except ValueError as exc:
                    item.update(status="unsupported", reason=str(exc))
                    record["scenarios"].append(item)
                    continue
                if scenario.reference_output is None:
                    item.update(status="missing_reference")
                    record["scenarios"].append(item)
                    continue
                try:
                    positive = _evaluate(scenario, scenario.reference_output, self.settings)
                    if not _passed(positive):
                        item.update(status="invalid_positive_control")
                        record["scenarios"].append(item)
                        continue
                    probes: list[tuple[str, dict[str, Any], str | None]] = [
                        ("empty_submission", {}, None)
                    ]
                    for check in scenario.checks:
                        values: list[Any] = [None, "", [], {}, 0, False]
                        if check.kind == "numeric":
                            values += [
                                check.expected + check.tolerance + max(1, abs(check.expected) * 0.1)
                            ]
                        elif check.kind == "forbidden":
                            forbidden = (
                                check.expected[0]
                                if isinstance(check.expected, list) and check.expected
                                else check.expected
                            )
                            values += [forbidden, [forbidden]]
                        elif check.kind == "citation":
                            values += [
                                [
                                    {
                                        "source_id": "everval-invalid-source-" + scenario.id,
                                        "quote": "",
                                    }
                                ]
                            ]
                        for value in values:
                            output = _mutate_path(scenario.reference_output, check.path, value)
                            if output is not None and output != scenario.reference_output:
                                probes.append(("mutated_check_path", output, check.id))
                    seen = set()
                    item["probe_cap"] = 32
                    item["probes_omitted_by_cap"] = max(0, len(probes) - 32)
                    for name, output, target in probes[:32]:
                        digest = content_hash(output)
                        if digest in seen:
                            continue
                        seen.add(digest)
                        checks = _evaluate(scenario, output, self.settings)
                        passed = _passed(checks)
                        item["probes"].append(
                            {
                                "name": name,
                                "target_check_id": target,
                                "output": output
                                if len(canonical_json(output).encode()) <= 4096
                                else None,
                                "output_omitted_for_size": len(canonical_json(output).encode())
                                > 4096,
                                "output_hash": digest,
                                "accepted": passed,
                                "checks": [{"id": c["id"], "passed": c["passed"]} for c in checks],
                            }
                        )
                        if name == "empty_submission" and passed:
                            record["findings"].append(
                                {
                                    "scenario_id": scenario.id,
                                    "code": "empty_submission_accepted",
                                    "severity": "review",
                                    "output_hash": digest,
                                }
                            )
                    accepted = sum(p["accepted"] for p in item["probes"])
                    item.update(
                        status="completed",
                        probes_evaluated=len(item["probes"]),
                        probes_rejected=len(item["probes"]) - accepted,
                        probes_accepted=accepted,
                    )
                except ValueError as exc:
                    item.update(status="grading_error", error=str(exc)[:2000])
                record["scenarios"].append(item)
                if len(record["scenarios"]) % 10 == 0:
                    record = self.save(record)
            record.update(
                status="completed",
                limitations=[
                    "Mutations are structural probes, not certified semantic negatives; inspect accepted probes.",
                    "Duplicate hints use exact public task content and lexical token overlap, not semantic equivalence.",
                    "Only selected pure graders are inspected; no host code or provider calls occur.",
                ],
            )
        except Exception as exc:
            record.update(status="failed", error=str(exc)[:2000])
            self.save(record)
            raise
        return self.save(record)
