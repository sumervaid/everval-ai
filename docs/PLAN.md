# Delivery status

Everval AI 0.2 is a terminal-first local evaluation product with a guided repository workflow, optional authenticated review browser, and shared CLI/API.

## Implemented workflows

| Area | Behavior |
| --- | --- |
| Terminal workbench | Ten views, command palette, responsive forms, resumable guided runs, health, evidence, recurring and change-linked evaluations. |
| GitHub integration | Read-only metadata, private bare mirrors, bounded history discovery, persistent rejection ledger, pagination, and conservative lineage checks. |
| Task curation | Independent reviewed statements, provenance fingerprints, before/reference/regression validation, immutable development/holdout releases. |
| Repository runtimes | Python/pytest plus JavaScript/TypeScript node:test, Jest and Vitest profiles; pinned image IDs and controlled offline private wheel/npm imports. |
| Validation | Three clean before/reference cycles, nonempty real tests, selected baseline regressions, infrastructure and inconsistency rejection. |
| Existing agents | Versioned public-task adapter and SDK, frozen revision and token ceilings, reported usage and bounded traces; recorded submissions remain supported. |
| Failure investigation | Failure inbox, bounded file differences, stage timings, and separately retained local regrades without another provider call. |
| General evaluation | Documents, structured data, and workflow simulations; redacted production examples become reviewed, control-validated collections. |
| Cost and latency | Partial-cost lower bounds, stage coverage, observed quality/cost/latency frontier, workspace/project/schedule cumulative caps. |
| Ongoing evaluation | Commit/configuration-linked deduplicated admission, local CI gates, frozen recurring cohorts, benchmark health and retained schedule digests. |
| Team review | Opt-in personal identities and API roles, assigned blind comparisons, frozen rubrics, append-only judgments, and evidence-bound decisions. |
| Operations | Durable jobs and worker recovery, local audit chain, consistent backups, exports, and no automatic result uploads. |

The repository contains original installation and integration fixtures. They are not representative customer benchmarks or paid-model performance measurements. See [verification](verification.md) for the current release checks.

## Deployment boundary

Operate one workspace on a local filesystem with a dedicated worker. SQLite transactions retain queues, accounting, and evidence. Terminal disconnection does not cancel queued work.

Team mode enforces administrator, operator, reviewer, and viewer permissions in the API and browser. Personal tokens are salted, hashed, expiring, and revocable. Terminal and filesystem access remain trusted administrative surfaces. Repository issue-review labels and legacy local review labels remain self-reported; assigned browser judgments use authenticated identities.

Prepared tests execute in isolated containers. Same-process repository tests remain susceptible to deliberately hostile grader manipulation; neither Python nor Node is a formally verified adversarial sandbox. Dependency preparation disables repository hooks, lifecycle scripts, and native builds. Private packages enter through inspected offline archives, not stored registry credentials. Unsupported histories and dependencies are rejected with reasons.

External adapters own isolation and ceiling enforcement inside their service. Everval reserves configured limits and checks returned usage and revision; it cannot independently meter that service or cancel its internal work after an HTTP timeout.

## Remaining deployment work

- Validate representative authorized customer repositories and business tasks; characterize selection bias, flakiness, and usefulness with real users.
- Conduct independent security review, long-duration fault testing, restore drills, and controlled provider compatibility pilots.
- Add broader build systems and language profiles only when their isolation and reconstruction contracts are verified.
- Add independently inspected live business-application state and calibrated semantic document assessment.
- Evaluate SSO, tenant isolation, externally anchored audit records, and distributed execution when actual deployment needs require them.

These are deployment and coverage limits, not hidden successes. Missing accounting and unavailable grading remain unknown or unsuccessful evidence.
