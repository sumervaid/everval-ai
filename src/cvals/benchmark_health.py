"""Evidence-backed health summaries for a repository's benchmark ledger."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from cvals.benchmarks import Benchmarks, _candidate_hash, grader_hash
from cvals.config import Settings
from cvals.models import content_hash, utc_now
from cvals.projects import Projects


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


class BenchmarkHealth:
    def __init__(self, settings: Settings):
        self.projects = Projects(settings)
        self.benchmarks = Benchmarks(settings)

    def project(self, project_id: str, stale_days: int = 30) -> dict[str, Any]:
        if type(stale_days) is not int or not 1 <= stale_days <= 3650:
            raise ValueError("Staleness threshold must be 1–3650 days")
        project = self.projects.get(project_id)
        candidates = self.projects.candidates(project_id)
        validations = self.benchmarks.validations(project_id)
        latest: dict[str, dict[str, Any]] = {}
        by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in validations:
            latest.setdefault(value["candidate_id"], value)
            by_candidate[value["candidate_id"]].append(value)
        current_grader = grader_hash()
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        groups: dict[str, list[str]] = defaultdict(list)
        rows = []
        reasons: Counter[str] = Counter()
        paths: Counter[str] = Counter()
        extensions: Counter[str] = Counter()
        for candidate in candidates:
            identity = candidate["id"]
            validation = latest.get(identity)
            review = self.benchmarks._latest("benchmark_reviews", project_id, identity)
            matching = bool(
                validation
                and validation["status"] == "valid"
                and candidate["status"] == "discovered"
                and review
                and (validation.get("review") or {}).get("id") == review["id"]
                and validation.get("candidate_hash") == _candidate_hash(candidate)
                and validation.get("grader_hash") == current_grader
            )
            exclusion = (
                []
                if matching
                else (
                    list(validation.get("reasons", []))
                    if validation
                    else list(candidate.get("reasons", []))
                )
            )
            if not matching and not exclusion:
                exclusion = [
                    "Current candidate, review and repeated validation are not all admitted"
                ]
            reasons.update(exclusion)
            changed = candidate.get("changed_files", [])
            for path in changed:
                if isinstance(path, dict):
                    path = path.get("path", "")
                if isinstance(path, str) and path:
                    paths[path.split("/")[0] if "/" in path else "(root)"] += 1
                    extensions[
                        "." + path.rsplit(".", 1)[-1] if "." in path else "(no extension)"
                    ] += 1
            signature = content_hash(
                {
                    "base": candidate.get("base_commit"),
                    "reference": candidate.get("reference_commit"),
                }
            )
            if candidate.get("base_commit") and candidate.get("reference_commit"):
                groups[signature].append(identity)
            observed_flaky = [
                value["id"]
                for value in by_candidate[identity]
                if any(
                    "flak" in reason.lower() or "runs disagree" in reason.lower()
                    for reason in value.get("reasons", [])
                )
            ]
            validated_at = _time((validation or {}).get("completed_at"))
            stale_reasons = []
            if validation and validated_at and validated_at < cutoff:
                stale_reasons.append(f"Latest validation is older than {stale_days} days")
            if validation and validation.get("candidate_hash") != _candidate_hash(candidate):
                stale_reasons.append("Candidate metadata changed since validation")
            if validation and validation.get("grader_hash") != current_grader:
                stale_reasons.append("Validation used a different grader implementation")
            rows.append(
                {
                    "candidate_id": identity,
                    "title": candidate.get("title"),
                    "admitted": matching,
                    "validation_id": (validation or {}).get("id"),
                    "validation_status": (validation or {}).get("status", "not_validated"),
                    "exclusions": exclusion,
                    "observed_flaky_validation_ids": observed_flaky,
                    "stale_reasons": stale_reasons,
                    "validation_cycles": len((validation or {}).get("evidence", [])),
                    "validated_at": (validation or {}).get("completed_at"),
                }
            )
        discovery = project.get("last_discovery", {})
        coverage = discovery.get("coverage", discovery)
        available = coverage.get("available_merged_pull_requests")
        admitted = sum(row["admitted"] for row in rows)
        return {
            "project_id": project_id,
            "generated_at": utc_now(),
            "candidate_count": len(rows),
            "admitted_count": admitted,
            "admission_rate": admitted / len(rows) if rows else None,
            "validated_count": len(latest),
            "excluded_count": len(rows) - admitted,
            "observed_flaky_count": sum(bool(row["observed_flaky_validation_ids"]) for row in rows),
            "stale_count": sum(bool(row["stale_reasons"]) for row in rows),
            "stale_days": stale_days,
            "exclusion_counts": dict(reasons.most_common()),
            "exact_lineage_duplicates": [ids for ids in groups.values() if len(ids) > 1],
            "coverage": {
                "discovered_candidates": len(rows),
                "available_merged_pull_requests": available,
                "discovery_fraction": len(rows) / available
                if isinstance(available, int) and available > 0
                else None,
                "more_history_available": coverage.get("has_more", discovery.get("has_more")),
                "changed_path_occurrences_by_top_level": dict(paths.most_common()),
                "changed_file_extensions": dict(extensions.most_common()),
                "languages": project.get("metadata", {}).get("languages", {}),
            },
            "candidates": rows,
            "releases": [
                {
                    "id": value["id"],
                    "name": value["name"],
                    "created_at": value["created_at"],
                    "members": len(value.get("members", [])),
                }
                for value in self.benchmarks.list(project_id)
            ],
            "limitations": [
                "Coverage describes the discovered ledger, not a representative sample of repository work.",
                "Duplicate detection compares exact base/reference commit pairs; it does not detect semantic duplicates.",
                "Flakiness counts explicit repeated-control disagreements, not model variation or proof of determinism.",
                "Age is a review prompt; old frozen releases retain their original evidence.",
            ],
        }
