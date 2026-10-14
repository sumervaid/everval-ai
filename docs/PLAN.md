# Delivery status and roadmap

## Product outcome

Help an organization determine which system performs its actual work reliably, and retain enough evidence to understand and improve the result.

Custom Vals now has a runnable local web application, CLI, HTTP API, evaluation engine, and original starter collections. The shared scenario model covers code, documents, structured data, and workflow simulations. This document distinguishes implemented functionality from the additional work needed for broader deployment.

## Implemented

| Capability                 | Current behavior                                                                                                                                   |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guided workbench           | Overview, collection authoring/import/review, system setup, experiment creation, live evidence, comparisons, exports, and workspace settings.      |
| Typed task collections     | Versioned scenarios, public inputs, evaluator-only checks and references, provenance, explicit controls, and content fingerprints.                 |
| Source ingestion           | Bounded local source previews and JSON/YAML task imports. An upload supplies material, not automatically a valid task.                             |
| Historical task extraction | Conservative Python Git mining with supported literal function assertions and a complete candidate ledger. Extracted tasks remain drafts.          |
| Task validation            | Evaluate a reference and declared negative controls; unavailable infrastructure and zero-test results do not establish correctness.                |
| Coding isolation           | Fresh bounded Linux verification, external expected-value comparison for function cases, immutable image IDs, and ownership-scoped cleanup.        |
| System execution           | A structured-output HTTP adapter, explicit positive/negative fixture controls, and imported external submissions.                                  |
| Durable experiments        | Frozen task and system snapshots, one-worker leases, cancellation, interrupted-attempt recovery, and no automatic retries of ambiguous paid calls. |
| Accounting                 | Conservative pre-call reservations, explicit prices, preserved incomplete attempts, and null values for missing usage or cost.                     |
| Reporting                  | Scheduled-trial denominators, completion coverage, 95% intervals, paired task comparisons, and JSON/CSV/HTML evidence exports.                     |
| Human judgment             | Append-only dimension scores and rationales linked to finalized evidence, with self-reported reviewer labels.                                      |
| Continuous runs            | Recurring evaluations of frozen snapshots, pause/resume, occurrence history, and admission failure handling.                                       |
| Local operation            | SQLite, configurable local storage, loopback service, optional bearer token, workspace diagnostics, and backup tooling.                            |

The four starter collections contain 19 original fixture scenarios: ten Python repairs and three each for documents, data, and workflows. They test installation and evaluator behavior. They are not a representative enterprise workload or measured model benchmark.

Automated checks cover the engine, validation, source boundaries, provider accounting, reports, reviews, schedules, and frontend workflows. Live Linux sandbox tests require an explicitly configured runtime. Hardware-specific verification and test artifacts stay outside the portable repository.

## Current limits

- The built-in HTTP adapter requests a JSON answer; it is not an interactive terminal or browser agent. External agents integrate through hash-bound submission bundles.
- Workflow tasks currently evaluate submitted state and action descriptions rather than a live application’s independently observed state.
- Citation validation checks source membership and quotation text, not whether the source entails a claim.
- One validation cycle establishes observed reference/control behavior. It does not certify flakiness, broad regression coverage, or benchmark representativeness.
- Python mining supports a deliberate subset of dependency-free function tests. Arbitrary builds, package installation, fixture-heavy tests, and other languages require additional work.
- The service has one trusted workspace owner and one active worker. Token access does not provide roles, verified reviewer identity, or tenant isolation.
- Recurring schedules rerun a fixed snapshot. They do not refresh a task collection, rotate holdouts, or impose a cumulative lifetime budget.
- Statistical intervals do not correct contaminated tasks, selection bias, correlated repositories, or exploratory multiple comparisons.

These boundaries are part of the current product contract. Unsupported work should fail clearly rather than appear as validated evidence.

## Next delivery: workload credibility

Before treating a model comparison as a procurement or deployment recommendation:

1. Build reviewed collections from authorized work in several task categories.
2. Record task provenance, permissible use, intended population, and known selection biases.
3. Repeat reference/control validation in clean environments and quarantine inconsistent cases.
4. Add meaningful regression and adversarial checks that challenge plausible incorrect answers.
5. Run controlled provider or external-harness pilots with declared prices, budgets, and repetitions.
6. Compare automated results with independent human judgments and document disagreements.

A useful acceptance target is at least 30 reviewed real tasks across multiple sources, with repeated trials on a declared subset. That is an engineering pilot target, not a statistical sufficiency claim. No such real-workload pilot is claimed by the starter fixtures.

## Next delivery: richer working environments

| Addition                     | Acceptance requirement                                                                                                                                     |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Interactive agent adapters   | Explicit tool and filesystem boundaries, isolated reset, captured tool evidence, cancellation, and controlled credential access.                           |
| Live workflow simulations    | Deterministic reset, independently inspected final state, constraints on harmful or unauthorized transitions, and support for multiple valid action paths. |
| Broader coding environments  | Reviewed dependency/image construction, more Python test forms, then TypeScript and additional languages with regression checks.                           |
| Document and artifact review | Support for semantic claim evaluation and rendered output inspection, with clear judge limitations and human calibration.                                  |
| Harness diagnostics          | Correlate submitted traces with failures and compare controlled harness changes on a fixed calibration collection.                                         |

Keep the shared scenario/submission/evaluator boundary stable while adding adapters. Do not expose evaluator answers to a solver in order to make an integration easier.

## Next delivery: living collections and stronger decisions

Add collection diffs, review states, incremental source refresh, benchmark release history, and explicit development/holdout partitions. A new collection version must remain distinguishable from a change in system performance.

Add saved decision constraints for reliability, latency, and spending; repeated-run reliability summaries; and uncertainty methods appropriate to multi-source task dependence. Recommendations should disclose task coverage and evidence limits rather than force a winner.

Recurring evaluation already supplies fixed-snapshot runs. Future regression gates should compare compatible cohorts, document tolerances, and preserve failed/incomplete attempts.

## Later deployment work

Pursue verified identities, roles, review permissions, retention policies, complete restore drills, and audit export guarantees before describing the product as a team service.

Consider PostgreSQL, worker pools, remote execution, and managed deployment only when measured workload or contention justifies them. Preserve local operation, explicit provider opt-in, and the ability to export a complete permitted record.

Independent sandbox review, long-running recovery tests, provider compatibility matrices, and operational load testing remain necessary before a broad production rollout.

## Reference

The [architecture](ARCHITECTURE.md) describes the implemented boundaries. [Interfaces](INTERFACES.md) document the current contracts. The [competitive review](COMPETITIVE_REVIEW.md) records the public-source research that informed the initial direction; it is not a claim of feature equivalence with another product.
