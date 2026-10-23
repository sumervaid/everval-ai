# Delivery status

Everval AI 0.3.1 is a terminal-first local evaluation product with a guided repository workflow, optional authenticated review browser, and shared CLI/API.

## Implemented workflows

| Area | Behavior |
| --- | --- |
| Terminal workbench | Fourteen views, command palette, responsive forms, resumable guided runs, health, evidence, recurring and change-linked evaluations. |
| GitHub integration | Read-only metadata, private bare mirrors, bounded history discovery, persistent rejection ledger, pagination, and conservative lineage checks. |
| Task curation | Independent reviewed statements, provenance fingerprints, before/reference/regression validation, immutable development/holdout releases. |
| Repository runtimes | Python/pytest plus JavaScript/TypeScript node:test, Jest and Vitest profiles; pinned image IDs and controlled offline private wheel/npm imports. |
| Validation | Three clean before/reference cycles, nonempty real tests, selected baseline regressions, infrastructure and inconsistency rejection. |
| Existing agents | Versioned public-task adapter and SDK, frozen revision and token ceilings, reported usage and bounded traces; recorded submissions remain supported. |
| Failure investigation | Failure inbox, bounded file differences, stage timings, and separately retained local regrades without another provider call. |
| General evaluation | Documents, structured data, and workflow simulations; redacted production examples become reviewed, control-validated collections. |
| Workspace rankings | Read-only model/configuration-by-frozen-benchmark matrix in TUI, browser, CLI and API; latest evidence, exact ties, coverage and metric-specific eligibility. |
| Cost and latency | Partial-cost lower bounds, stage coverage, observed quality/cost/latency frontier, workspace/project/schedule cumulative caps. |
| Ongoing evaluation | Commit/configuration-linked deduplicated admission, local CI gates, frozen recurring cohorts, benchmark health and retained schedule digests. |
| Team review | Opt-in personal identities and API roles, assigned blind comparisons, frozen rubrics, append-only judgments, and evidence-bound decisions. |
| Post-training monitoring (current source) | Frozen run/checkpoint declarations, trainer-manifest ingestion, budgeted evaluation queues, exploratory/predeclared paired family analysis, evaluation qualification, investigations, and held-out mix diagnostics. |
| On-premises deployment | Single-host service templates and preflight, offline installation guide, explicit internal HTTPS inference origins and private CA trust; target Linux service verification remains required. |
| Operations | Durable jobs and worker recovery, local audit chain, consistent backups, exports, and no automatic result uploads. |

The repository contains original installation and integration fixtures. Reference and incomplete submissions verify the pipeline; separate opt-in live runs measure compatibility and outcomes on those bounded tasks. Neither establishes representative customer performance. The source repository is public; private workspaces and evaluation artifacts remain local.

## Release acceptance

Version 0.3.1 adds repeatable acceptance against a separately installed wheel, including real HTTP and browser role boundaries, actual tmux launch, Textual forms, repository admission, accounting, and recovery. Executed checks and omissions are documented in the [verification record](verification.md); the [dated evidence summary](evidence/2026-10-15/summary.json) retains final outcomes. Follow the [acceptance procedure](release-acceptance.md) to reproduce the stages without conflating free fixture controls and optional paid provider runs.

Acceptance also tightened interrupted-attempt accounting, development-partition admission, bounded schema validation, and visible terminal selection controls. The nine structured starter tasks now expose precise public output contracts in document, data, and workflow collection version 1.1.0. Frozen historical experiments keep their original contracts; earlier ambiguous-contract live cohorts are excluded from quality claims.

## Continuous evaluation and research

The 0.3 services add opt-in worker-driven Autopilot policies, atomic screening/full-run budget reservations, configuration/source observations, and bounded provider-free investigations for supported pure graders. Git observations describe source provenance. Adapter-reported revision checks enforce the configured adapter contract; a Git commit alone does not verify a deployed service.

Research records retain repeated grading, failure-preserving output reduction, structural negative controls and lexical duplicate signals. These are bounded diagnostic techniques, not proof of model determinism, causal attribution, semantic equivalence or grader completeness. Optimization compares explicitly saved configurations and reserves one unused final holdout; it does not invent prompts or deploy winners autonomously.

Portable community packs are bounded data-only manifests. Executable/custom graders, registry downloads and automatic pack publication are unsupported. Human behavior review and successful controls are required for pack admission. The bundled three-domain demo is an installation walkthrough, not representative model evidence.

Budget forecasts expose assumptions and distinguish observed costs from reservations. The provider ledger excludes external hosting, tools and development-service bills.

## Deployment boundary

Operate one workspace on a local filesystem with a dedicated worker. SQLite transactions retain queues, accounting, and evidence. Terminal disconnection does not cancel queued work.

Team mode enforces administrator, operator, reviewer, and viewer permissions in the API and browser. Personal tokens are salted, hashed, expiring, and revocable. Terminal and filesystem access remain trusted administrative surfaces. Repository issue-review labels and legacy local review labels remain self-reported; assigned browser judgments use authenticated identities.

Prepared tests execute in isolated containers. Same-process repository tests remain susceptible to deliberately hostile grader manipulation; neither Python nor Node is a formally verified adversarial sandbox. Dependency preparation disables repository hooks, lifecycle scripts, and native builds. Private packages enter through inspected offline archives, not stored registry credentials. Unsupported histories and dependencies are rejected with reasons.

External adapters own isolation and ceiling enforcement inside their service. Everval reserves configured limits and checks returned usage and revision; it cannot independently meter that service or cancel its internal work after an HTTP timeout.

## Post-training evidence boundary

The source checkout adds checkpoint evaluation operations and a provider-free synthetic walkthrough. The published npm 0.3.1 artifact predates this addition. The [completed local checkpoint study](benchmarks/checkpoint-study-2026-10-22/results.md) includes three actual QLoRA training runs, twelve inspected checkpoint artifacts, and 3,072 completed model generations under a frozen protocol. Its generated diagnostics demonstrate bounded structured-output improvements, an arithmetic floor, and counterfactual evaluation-time trade-offs. Shared templates, only two validation seeds, no observed regressions, and no human calibration limit the conclusions.

Weight and deployment identities in the general product remain declared; the runtime does not independently attest remotely served weights. The local study separately verifies its saved tensor contents. Statistical interpretations depend on documented independence and frozen measurement assumptions. Evaluation-mix reports are descriptive diagnostics; neither distributed-fleet validation nor autonomous training intervention is claimed. See the [operator guide](post-training.md), [methods](posttraining-methods.md), [study reproduction](../scripts/posttraining/SETUP.md), and [prospective protocol template](../examples/post-training/STUDY.md).

## Remaining deployment work

- Validate representative authorized customer repositories and business tasks; characterize selection bias, flakiness, and usefulness with real users.
- Conduct independent security review, long-duration fault testing, routine restore drills, and broader provider compatibility studies.
- Add broader build systems and language profiles only when their isolation and reconstruction contracts are verified.
- Add independently inspected live business-application state and calibrated semantic document assessment.
- Evaluate SSO, tenant isolation, externally anchored audit records, and distributed execution when actual deployment needs require them.

These are deployment and coverage limits, not hidden successes. Missing accounting and unavailable grading remain unknown or unsuccessful evidence.
