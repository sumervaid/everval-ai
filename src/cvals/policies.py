"""Explicit decision gates with reproducible policy and evidence fingerprints."""

from __future__ import annotations

import builtins
import json
import math
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cvals.comparison import compare_experiments, evidence_issues
from cvals.models import canonical_json, content_hash, new_id, utc_now
from cvals.reports import build_report

if TYPE_CHECKING:
    from cvals.config import Settings


class GatePolicy(BaseModel):
    """Defaults verify execution readiness, without inventing a quality target."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="Execution readiness", min_length=1, max_length=200)
    version: str = Field(default="1", min_length=1, max_length=100)
    min_completion_rate: float = Field(default=1, ge=0, le=1)
    max_error_rate: float = Field(default=0, ge=0, le=1)
    min_pass_rate: float | None = Field(default=None, ge=0, le=1)
    min_pass_rate_lower_bound: float | None = Field(default=None, ge=0, le=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_p95_duration_ms: float | None = Field(default=None, ge=0)
    max_pass_rate_regression: float | None = Field(default=None, ge=0, le=1)
    include_fixture_controls: bool = False
    include_replays: bool = False

    @model_validator(mode="after")
    def describe_custom_thresholds(self) -> GatePolicy:
        quality = (
            self.min_pass_rate,
            self.min_pass_rate_lower_bound,
            self.max_cost_usd,
            self.max_p95_duration_ms,
            self.max_pass_rate_regression,
        )
        if "name" not in self.model_fields_set and any(value is not None for value in quality):
            self.name = "Custom decision gates"
        return self

    @field_validator("name", "version")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Policy name and version must not be blank")
        return value.strip()

    @field_validator(
        "min_completion_rate",
        "max_error_rate",
        "min_pass_rate",
        "min_pass_rate_lower_bound",
        "max_cost_usd",
        "max_p95_duration_ms",
        "max_pass_rate_regression",
        mode="before",
    )
    @classmethod
    def numeric_threshold(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Gate thresholds must be numbers, not booleans")
        return value


def _gate(
    metric: str,
    observed: float | None,
    threshold: float,
    operator: str,
    *,
    ready: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    if observed is None or not ready or not math.isfinite(observed):
        outcome = "inconclusive"
    else:
        outcome = (
            "passed"
            if (observed >= threshold if operator == ">=" else observed <= threshold)
            else "failed"
        )
    return {
        "metric": metric,
        "observed": observed,
        "operator": operator,
        "threshold": threshold,
        "status": outcome,
        "reason": reason
        or (
            "Required evidence is unavailable or incomplete"
            if outcome == "inconclusive"
            else "Observed evidence meets the declared threshold"
            if outcome == "passed"
            else "Observed evidence violates the declared threshold"
        ),
    }


def _outcome(checks: list[dict[str, Any]]) -> str:
    statuses = {item["status"] for item in checks}
    return (
        "failed"
        if "failed" in statuses
        else "inconclusive"
        if not statuses or "inconclusive" in statuses
        else "passed"
    )


def evaluate_gates(
    experiment: dict[str, Any],
    policy: GatePolicy | dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate each eligible system separately; unknowns never become successes."""
    selected = policy if isinstance(policy, GatePolicy) else GatePolicy.model_validate(policy or {})
    policy_data = selected.model_dump(mode="json")
    issues = evidence_issues(experiment)
    result: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": experiment.get("id"),
        "policy": policy_data,
        "policy_hash": content_hash(policy_data),
        "evidence_hash": content_hash(
            {"snapshot_hash": experiment.get("snapshot_hash"), "trials": experiment.get("trials")}
        ),
        "scope": "Each eligible system is evaluated separately; the cost threshold applies per system",
        "baseline_evidence_hash": content_hash(
            {"snapshot_hash": baseline.get("snapshot_hash"), "trials": baseline.get("trials")}
        )
        if baseline is not None
        else None,
        "status": "inconclusive",
        "systems": [],
        "excluded_systems": [],
        "issues": issues,
        "warnings": [
            "These are declared decision gates, not a claim that the benchmark represents all enterprise work.",
            "Default gates check execution readiness; no model-quality threshold is implied."
            if policy is None
            else "Thresholds are supplied by the policy owner; task selection and population assumptions still require review.",
        ],
    }
    if issues:
        return result
    comparison = compare_experiments(baseline, experiment) if baseline is not None else None
    if comparison is not None:
        result["baseline_comparison"] = comparison
    for system in build_report(experiment)["systems"]:
        identity = system["system_id"]
        if (system["fixture_control"] and not selected.include_fixture_controls) or (
            system["submission_replay"] and not selected.include_replays
        ):
            result["excluded_systems"].append(
                {
                    "system_id": identity,
                    "name": system["name"],
                    "reason": "Fixture control does not measure model performance"
                    if system["fixture_control"]
                    else "Imported submissions are not independently measured executions",
                }
            )
            continue
        trials = [t for t in experiment["trials"] if t["system_id"] == identity]
        finalized = all(t["status"] not in {"queued", "running"} for t in trials)
        scheduled = system["scheduled"]
        checks = [
            _gate(
                "completion_rate",
                system["completed"] / scheduled,
                selected.min_completion_rate,
                ">=",
                ready=finalized,
            ),
            _gate(
                "error_rate",
                system["errors"] / scheduled,
                selected.max_error_rate,
                "<=",
                ready=finalized,
            ),
        ]
        if not system["completed"]:
            checks.append(
                {
                    "metric": "graded_evidence",
                    "observed": 0,
                    "operator": ">=",
                    "threshold": 1,
                    "status": "inconclusive",
                    "reason": "No finalized attempt has a valid grading result",
                }
            )
        if selected.min_pass_rate is not None:
            checks.append(
                _gate(
                    "pass_rate",
                    system["pass_rate"],
                    selected.min_pass_rate,
                    ">=",
                    ready=finalized and system["completed"] > 0,
                )
            )
        if selected.min_pass_rate_lower_bound is not None:
            gate = _gate(
                "pass_rate_lower_bound",
                system["ci_low"],
                selected.min_pass_rate_lower_bound,
                ">=",
                ready=finalized and system["completed"] > 0,
            )
            gate["interval_method"] = system["interval_method"]
            checks.append(gate)
        if selected.max_cost_usd is not None:
            cost = system["cost_usd"]
            reason = ""
            if cost is None and system["known_cost_usd"] > selected.max_cost_usd:
                cost = system["known_cost_usd"]
                reason = "Known costs already exceed the limit; missing costs cannot reduce that lower bound"
            checks.append(
                _gate("cost_usd", cost, selected.max_cost_usd, "<=", ready=finalized, reason=reason)
            )
        if selected.max_p95_duration_ms is not None:
            complete = all(t.get("duration_ms") is not None for t in trials)
            checks.append(
                _gate(
                    "p95_duration_ms",
                    system["p95_duration_ms"],
                    selected.max_p95_duration_ms,
                    "<=",
                    ready=finalized and complete,
                    reason="All scheduled attempts must have recorded duration before latency can pass"
                    if not complete
                    else "",
                )
            )
        if selected.max_pass_rate_regression is not None:
            pair = next(
                (
                    p
                    for p in (comparison or {}).get("comparisons", [])
                    if p["system_id"] == identity
                ),
                None,
            )
            threshold = -selected.max_pass_rate_regression
            gate = _gate(
                "pass_rate_change_lower_bound",
                pair.get("ci_low") if pair else None,
                threshold,
                ">=",
                ready=bool(
                    comparison and comparison["compatible"] and pair and pair["inference_supported"]
                ),
            )
            if pair and pair["inference_supported"] and comparison and comparison["compatible"]:
                if pair["ci_low"] >= threshold:
                    gate["status"] = "passed"
                    gate["reason"] = (
                        "The paired interval lies within the declared tolerated regression"
                    )
                elif pair["ci_high"] < threshold:
                    gate["status"] = "failed"
                    gate["reason"] = (
                        "The paired interval lies below the declared tolerated regression"
                    )
                else:
                    gate["status"] = "inconclusive"
                    gate["reason"] = (
                        "The paired interval crosses the tolerated regression; more evidence is required"
                    )
                gate["interval_method"] = pair["interval_method"]
                gate["ci_high"] = pair["ci_high"]
            else:
                gate["reason"] = (
                    "A compatible, independent finalized baseline with the same system identity is required"
                )
            checks.append(gate)
        if system["cost_usd"] is None:
            result["warnings"].append(
                f"{system['name']}: total cost is unknown; recorded cost is only a lower bound"
            )
        if system["fixture_control"] or system["submission_replay"]:
            result["warnings"].append(
                f"{system['name']}: included by explicit policy; fixture or imported evidence is not an independent live model measurement"
            )
        result["systems"].append(
            {
                "system_id": identity,
                "name": system["name"],
                "fixture_control": system["fixture_control"],
                "submission_replay": system["submission_replay"],
                "status": _outcome(checks),
                "checks": checks,
            }
        )
    result["status"] = _outcome(result["systems"])
    if not result["systems"]:
        result["issues"].append(
            "No eligible measured systems; fixtures and imported submissions are excluded by default"
        )
    return result


class PolicyStore:
    """Immutable named policy versions and append-only gate decisions."""

    def __init__(self, settings: Settings):
        from cvals.audit import AuditLog
        from cvals.store import Store

        self.store = Store(settings)
        self.store.init()
        self.audit = AuditLog(settings)
        with self.store._connect(write=True) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS evaluation_policies (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                    created_at TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL,
                    UNIQUE(name, version)
                );
                CREATE TABLE IF NOT EXISTS gate_decisions (
                    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    policy_id TEXT REFERENCES evaluation_policies(id), created_at TEXT NOT NULL,
                    data TEXT NOT NULL, hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS gate_decisions_experiment ON gate_decisions(experiment_id);
            """)
            for table in ("evaluation_policies", "gate_decisions"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Decision policies and evidence are append-only'); END"
                    )

    @staticmethod
    def _checked(row: Any) -> dict[str, Any]:
        if row is None:
            raise KeyError("Saved policy or gate decision does not exist")
        value = json.loads(row["data"])
        if content_hash(value) != row["hash"]:
            raise ValueError("Policy or decision fingerprint does not match its stored content")
        return dict(value)

    def save(self, policy: GatePolicy | dict[str, Any]) -> dict[str, Any]:
        validated = policy if isinstance(policy, GatePolicy) else GatePolicy.model_validate(policy)
        data = validated.model_dump(mode="json")
        with self.store._connect(write=True) as connection:
            old = connection.execute(
                "SELECT data,hash FROM evaluation_policies WHERE name=? AND version=?",
                (validated.name, validated.version),
            ).fetchone()
            if old:
                existing = self._checked(old)
                if existing["policy"] != data:
                    raise ValueError(
                        "This policy version already exists with different thresholds; create a new version"
                    )
                return existing
            value: dict[str, Any] = {
                "id": new_id(),
                "created_at": utc_now(),
                "policy": data,
                "policy_hash": content_hash(data),
            }
            connection.execute(
                "INSERT INTO evaluation_policies VALUES(?,?,?,?,?,?)",
                (
                    value["id"],
                    validated.name,
                    validated.version,
                    value["created_at"],
                    canonical_json(value),
                    content_hash(value),
                ),
            )
            self.audit.record(
                "policy.version_saved",
                value["id"],
                {
                    "name": validated.name,
                    "version": validated.version,
                    "policy_hash": value["policy_hash"],
                },
                connection=connection,
            )
        return value

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._checked(row)
                for row in connection.execute(
                    "SELECT data,hash FROM evaluation_policies ORDER BY rowid DESC LIMIT 500"
                )
            ]

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            return self._checked(
                connection.execute(
                    "SELECT data,hash FROM evaluation_policies WHERE id=?", (identifier,)
                ).fetchone()
            )

    def evaluate(
        self,
        experiment: dict[str, Any],
        policy: GatePolicy | dict[str, Any] | None = None,
        baseline: dict[str, Any] | None = None,
        *,
        policy_id: str | None = None,
    ) -> dict[str, Any]:
        if policy_id is not None:
            if policy is not None:
                raise ValueError("Choose a saved policy or inline thresholds, not both")
            policy = self.get(policy_id)["policy"]
        for supplied in (experiment, baseline):
            if supplied is None:
                continue
            current = self.store.get_experiment(supplied["id"])
            if current["snapshot_hash"] != supplied.get("snapshot_hash") or content_hash(
                current["trials"]
            ) != content_hash(supplied.get("trials")):
                raise ValueError(
                    "Evidence changed or differs from this workspace; refresh before recording gate decisions"
                )
        result = evaluate_gates(experiment, policy, baseline)
        value = {"id": new_id(), "created_at": utc_now(), "policy_id": policy_id, **result}
        with self.store._connect(write=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM experiments WHERE id=?", (experiment["id"],)
            ).fetchone():
                raise ValueError(
                    "Only experiments in this workspace can have durable gate decisions"
                )
            connection.execute(
                "INSERT INTO gate_decisions VALUES(?,?,?,?,?,?)",
                (
                    value["id"],
                    experiment["id"],
                    policy_id,
                    value["created_at"],
                    canonical_json(value),
                    content_hash(value),
                ),
            )
            self.audit.record(
                "experiment.gates_evaluated",
                experiment["id"],
                {
                    "decision_id": value["id"],
                    "policy_id": policy_id,
                    "policy_hash": result["policy_hash"],
                    "evidence_hash": result["evidence_hash"],
                    "status": result["status"],
                },
                connection=connection,
            )
        return value

    def decisions(self, experiment_id: str) -> builtins.list[dict[str, Any]]:
        with self.store._connect() as connection:
            return [
                self._checked(row)
                for row in connection.execute(
                    "SELECT data,hash FROM gate_decisions WHERE experiment_id=? ORDER BY rowid DESC LIMIT 500",
                    (experiment_id,),
                )
            ]
