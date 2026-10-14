# Delivery status

Everval AI is a terminal-first local evaluation product. The primary experience is a Textual workbench inside an optional styled tmux session; a shared Python service powers the CLI, API, and existing web interface.

## Implemented workflows

| Area | Behavior |
| --- | --- |
| Terminal workbench | Responsive eight-view navigation, command palette, repository onboarding, curation, releases, runs, evidence, schedules, health, and audit history. |
| GitHub integration | Read-only metadata, private bare mirrors, bounded history discovery, stable candidate ledger, pagination, and conservative merge/squash/rebase lineage checks. |
| Task curation | Independent reviewed issue statements, provenance fingerprints, explicit reasons for rejected or unsupported candidates. |
| Reproducible environments | Static supported dependency detection, reviewed preparation plans, public wheel download, offline installation, frozen image IDs, and wheel inventory. |
| Validation | Three clean before/reference cycles, genuinely new fail-to-pass tests, selected baseline regression controls, infrastructure and inconsistency rejection. |
| Releases | Immutable admitted task snapshots, local validation attestations, stable development/holdout partitions, release diffs, and explicit private versus solver exports. |
| Execution | Frozen experiments, one dedicated worker, durable repository jobs, lease recovery, cancellation, and reservation-based spending control. |
| Harnesses | Structured single-turn responses, bounded tools over public files with isolated Python execution, external recorded submissions. |
| General evaluation | Code, document evidence, structured data, and workflow-state simulations with typed checks. |
| Decisions | Compatible-cohort comparisons, explicit uncertainty, configurable quality gates, versioned policies, and retained decisions. |
| Operations | Recurring frozen evaluations, append-only human reviews, local audit chain, health checks, backups, and evidence exports. |

The repository contains original installation fixtures, not a representative customer benchmark. No paid-model comparison or procurement recommendation is claimed by those controls.

## Supported production boundary

Operate one trusted local workspace on a local filesystem. A dedicated worker serializes repository jobs; experiment leases protect execution across the CLI and API. SQLite transactions retain queue and evidence state. Terminal disconnection does not cancel queued work.

Prepared coding environments support Python/pytest and public wheel dependencies. Unsupported dependencies and histories are rejected with reasons. The prepared pytest profile executes project tests inside the same isolated process environment as submitted code; a deliberately hostile submission may attack that grader. The constrained literal profile provides a narrower expected-value boundary. Neither profile is a formally verified adversarial sandbox.

Issue review is a self-reported human label. The audit chain detects edits against retained digests; a local administrator can rewrite both database and chain. No SSO, RBAC, tenant isolation, external audit anchor, or regulated certification is implied.

## Work still required for broader deployment

- Validate representative authorized workloads across several repositories and task categories; characterize selection bias and real-world flakiness.
- Add isolated TypeScript and other language profiles, private dependency registries, and reviewed native-build support.
- Add live business-application simulations with independently inspected state, stronger document semantic assessment, and calibrated expert review.
- Introduce authenticated identities and roles, controlled multi-user access, external audit anchoring, retention administration, and restore drills.
- Evaluate worker pools and PostgreSQL when measured concurrency requires them.
- Conduct an independent sandbox review, long-duration fault testing, and controlled provider compatibility pilots.

Each extension must preserve the public task/private grader boundary, retain incomplete attempts, and refuse to turn unavailable evidence into a passing result.
