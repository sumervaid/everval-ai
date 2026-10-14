# Implementation plan

Status: proposed implementation; repository foundation only.
Date: 2026-10-13.

## Product outcome

Help a team answer: **Which coding agent should we use for this repository, under our actual constraints, and what evidence would change that decision?**

The complete workflow should turn authorized repository history into reviewed tasks, run controlled experiments, explain failures, and maintain the benchmark as the code evolves. The initial code and documentation are Apache-2.0. Work occurs on the maintainer's iMac; benchmark data stays on its local or attached storage.

The [competitive review](COMPETITIVE_REVIEW.md) distinguishes documented Vals behavior from unverified features. The [architecture](ARCHITECTURE.md) defines the execution and data boundaries.

## Baseline to implement

Support local Git import and optional GitHub metadata; inspect language/test setup; identify suitable historical changes; reconstruct a task at its original starting point; separate issue text, reference patch, and evaluator tests; validate tests; compare configurable agent systems; present per-task outcomes and operational metrics.

Do not promise arbitrary repositories initially. Start with small Python projects using pytest, followed by TypeScript. Record unsupported environments with explicit reasons.

## Additions that make the product materially stronger

| Priority | Capability | Concrete behavior |
| --- | --- | --- |
| P0 | Auditable task factory | Every candidate has origin, base/reference commits, extraction version, test evidence, reviewer decision, and acceptance/rejection reasons. Show the entire mining funnel. |
| P0 | Fresh and separate grading | Solver gets a reconstructed starting tree; gold patches, hidden tests, future Git objects, and grader credentials stay outside its environment. A fresh verifier grades the submitted patch. |
| P0 | Replayable evidence | Immutable manifests identify environment/image digest, architecture, dependency locks, harness version, configuration, budgets, and artifact hashes. Export a replay bundle when content permissions allow it. |
| P0 | Durable local execution | One-worker scheduler with transactional leases, crash recovery, cancellation, cleanup, disk limits, and admission checks. All state lives in configured local directories. |
| P0 | Honest operational accounting | Preserve failures and incomplete trials; include retries, cache usage, setup time, model spend, and compute estimates. Distinguish measured, estimated, and unavailable values. |
| P1 | Controlled comparisons | Freeze task cohort and budgets; use paired tasks and repeated trials; show uncertainty and ties; distinguish pass@1, pass@k, and reliability across repeats. |
| P1 | Decision reports | Select systems subject to explicit cost, latency, and reliability constraints. Show cost per solved task and a quality/cost frontier with uncertainty. |
| P1 | Failure workbench | Inspect candidate provenance, test evidence, patches, tool calls, traces, and failure categories. Compare the same task across systems and replay selected trials. |
| P1 | Private holdouts and freshness | Separate development, calibration, selection, and final evaluation sets; maintain time splits and unreleased tasks; audit generated instructions for hindsight. |
| P1 | Continuous evaluation | Incrementally mine repository changes, review benchmark diffs, rerun affected tasks, and detect regressions after agent/prompt/tool/model changes. |
| P2 | Stronger task verification | Add property, mutation, differential, and adversarial tests where they improve evidence. Track quality signals separately from primary correctness. |
| P2 | Broader engineering work | Add migrations, refactors, performance budgets, security repairs, browser/UI tasks, multi-service changes, and long-running tasks with explicit milestones. |
| P2 | Team and enterprise operation | Add roles, review workflows, audit exports, quotas, retention controls, portable offline packages, and optional worker pools while preserving self-hosted operation. |
| P2 | Agent improvement loop | Compare harness and prompt changes on calibration tasks, identify recurring failures, and propose interventions. Final holdouts remain sealed from tuning. |

## Scientific rules

A task is admitted only when its environment builds, tests are actually collected, the intended pre-fix assertions fail, the reference patch fixes them, and selected existing behavior remains intact. Record per-test evidence using trusted parsers. Zero tests, missing dependencies, and arbitrary process crashes are invalid evidence.

Repeat validation in fresh environments; use at least three clean base/reference validation cycles for the first pilot. Quarantine inconsistent tasks. A reference patch is one accepted solution, not the definition of every correct implementation.

Preserve original issue text separately from synthesized descriptions. Label when text or tests were generated from a later change and review for solution leakage. Public historical tasks carry potential training-contamination risk; do not label them uncontaminated.

Freeze the comparison protocol before running it. Show all scheduled trials and the exact denominator. Budget exhaustion counts as unsuccessful completion. Bound infrastructure retries, preserve every attempt, and show both valid-trial accuracy and operational completion coverage. Do not silently improve a score by dropping hard tasks or failed infrastructure.

Use task-paired differences and suitable confidence intervals; cluster repeated trials by task and multi-repository comparisons by repository where appropriate. A single run per task cannot estimate within-task randomness. Do not claim two systems differ when evidence is insufficient. Cost per solved task uses total included attempt cost divided by solved tasks; zero solved tasks has no finite cost-per-solve. Unknown costs never become zero.

A replay freezes inputs and permits a new run; external model services may still change or produce different outputs. Saved-log report reproduction and live model reruns are distinct operations.

## Delivery milestones

### M0: Repository and design — this delivery

Create the GitHub repository and authoritative iMac checkout; record the public research, architecture, scope, license, data exclusions, machine inventory, and implementation backlog.

Completion: documents are linked and reviewed, Git exclusions cover machine/runtime data, the first commit is pushed, and local/remote revision identity is verified. No product functionality is claimed.

### M1: Offline benchmark factory

Implement versioned schemas, a CLI, source import, candidate extraction, local artifacts, and Python task validation. Begin with original miniature fixtures; then use one authorized pilot repository. A deterministic patch replay adapter exercises the pipeline without model spending.

Completion: at least 10 manually reviewed real tasks reproduce in fresh Linux environments; reference patches pass; no-op and deliberately incorrect patches fail; invalid/flaky candidates are quarantined. A deliberately leaked Git object and a hidden-test canary remain inaccessible to the solver. Verify storage paths and inspect every artifact type.

Before container work: confirm the attached volume and free-space thresholds; configure a dedicated Linux VM with its disk and caches on attached storage; measure an appropriate memory budget. No mass image pulls or model downloads onto the nearly full internal disk.

### M2: Controlled agent execution

Add a pinned Harbor adapter and generic command-agent contract. Run one task at a time. Introduce the scoped model gateway, limits, trusted result parsing, restart recovery, cancellation, and cleanup.

Completion: two independently configured agent systems run the same frozen task cohort and budgets; all submissions are graded in fresh verifier environments. Stop/restart the orchestrator and recover without lost records or double-counted final results. Attempt evaluator access, forbidden networking, path traversal, result forgery, and quota bypass; each must fail or be reported as an unsupported policy before execution.

Provider-backed model runs require an explicit configured spend budget. The default original-fixture workflow remains offline. Large local models are outside the initial 8 GB machine target.

### M3: Decision workbench and initial usable release

Add the browser interface after the CLI pipeline works. Ship repository/candidate review, experiment design, live run inspection, paired results, cost/latency tradeoffs, and replay/export.

Completion: a pilot covers at least 30 validated tasks across at least three authorized repositories, with repeated trials on a declared subset. These are engineering acceptance targets, not a claim of statistical sufficiency. Reports regenerate exactly from stored artifacts; uncertainty, missing usage, ties, exclusions, and incomplete runs remain visible. An end user can trace every aggregate to tasks and attempts.

### M4: Living benchmark and broader tasks

Add TypeScript, incremental refresh, holdout rotation, benchmark release diffs, selected mutation tests, CI regression gates, and budgeted harness experiments.

Completion: updating a repository creates a reviewable change set. Old suites remain replayable. Trend charts distinguish changes in task cohorts from changes in system performance. CI runs on the configured local evaluation service; public GitHub Actions do not receive private task content.

### M5: Enterprise and ecosystem

Add PostgreSQL and optional worker pools only after measured contention justifies them. Add roles/SSO, audit exports, data retention, backup/restore, air-gapped bundles, additional task domains, and interoperability.

Completion: restore drills pass; authorization isolates projects; a compatible Linux worker can reproduce a permitted task bundle; public exports exclude private datasets and credentials. Remote workers are optional future deployments and are not enabled for this iMac-only workspace.

## First implementation backlog

| ID | Deliverable | Acceptance |
| --- | --- | --- |
| CVAL-001 | Workspace doctor | Reports architecture, RAM, writable/mounted roots, free space, VM state, tools, and supported executor capabilities without exposing secrets. |
| CVAL-002 | Task and trial contracts | Versioned typed manifests, canonical hashing, migration rules, and separate agent/evaluator views. |
| CVAL-003 | Original fixture repository | Small historical bug fixes plus invalid, flaky, zero-test, and weak-test counterexamples. |
| CVAL-004 | Local Git importer | No host-side hooks or execution; reconstructs verified base snapshots without future history. |
| CVAL-005 | Candidate extractor | Handles supported merge/squash cases and quarantines ambiguous reconstruction; preserves provenance. |
| CVAL-006 | VM/executor capability spike | Proves separate grading, enforced egress, artifact restrictions, resource limits, cleanup, and ARM64 support before choosing a pinned backend version. |
| CVAL-007 | Validation engine | Three clean fail/pass/regression cycles and trusted structured results. |
| CVAL-008 | Durable job lifecycle | Idempotent trial finalization, leases, heartbeat, bounded retries, cancel and crash recovery. |
| CVAL-009 | First replay report | Traces each result to its manifest and evidence; serializes incomplete and failed attempts explicitly. |
| CVAL-010 | Agent adapters | Generic command contract plus first model-backed configurations with spend reservation and safe termination. |

## Deliberate scope limits

Initial language: Python. Initial worker count: one. Initial state: SQLite plus local files. Initial entry point: CLI. No Kubernetes, mandatory Redis, hosted database, multi-tenant SaaS, or universal language promise in the first release.

The primary technical risks are environment reconstruction yield, weak tests, contamination/leakage, and operational resource limits. The milestones put these risks before dashboard polish.
