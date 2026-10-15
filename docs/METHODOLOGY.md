# Methodology and design

Everval AI turns real work into a maintained evaluation process: select a workload, establish valid checks, execute controlled comparisons, make a decision, and retain the evidence needed to revisit it.

The product builds on established approaches in software testing and model evaluation, including historical task reconstruction, regression tests, hidden checks, negative controls, frozen comparisons, uncertainty estimates, and expert review. Everval brings these methods into a shared local workspace and extends them with the operational workflows needed to use the results.

## Start with a valid task

A repository task begins with a reviewed engineering change and an independently written problem statement. Its private tests must fail on the starting revision, pass with the historical repair, and preserve selected baseline behavior. Repeated clean validation, pinned environments, and an inspectable rejection ledger establish what was actually admitted.

Document, data, and workflow tasks use explicit checks, reference outputs, and negative controls. Production examples enter a staged import process with redaction and independently approved expected behavior before validation and publication.

The candidate funnel and benchmark health expose exclusions, coverage gaps, duplicate candidates, instability, and stale evidence. A convenient set of accepted tasks does not establish representative coverage of an organization's work.

## Keep execution and evidence reproducible

Experiments freeze the task cohort, system configurations, harness limits, prices, and spending ceiling. Development and holdout releases remain distinct. Public task material is separated from private checks and reference solutions.

Single-turn models, bounded tool harnesses, versioned live agent adapters, and recorded submissions use the same evaluation records. Failed, interrupted, cancelled, and incomplete attempts remain visible. Local regrading retains a separate result so investigation cannot silently overwrite the original evidence.

## Turn measurements into decisions

A useful comparison includes quality, completion, failures, uncertainty, cost, and duration on a compatible workload. Known partial charges remain a lower bound; missing accounting stays unknown. Stage timing shows where execution time was recorded and where it was not.

Declared policies apply quality, reliability, cost, latency, and regression thresholds. The observed quality/cost/latency frontier helps inspect tradeoffs on the evaluated cohort. Assigned blind reviews use frozen rubrics and authenticated identities; decisions retain the evidence fingerprint they refer to.

## Maintain the evaluation over time

Everval extends a benchmark run into a connected set of team workflows:

| Evaluation need | Everval workflow |
| --- | --- |
| Establish a useful first workload | Resumable repository onboarding, reviewed task statements, repeated validation, and versioned releases. |
| Assess an existing application | Live agent adapters with frozen revisions, bounded usage, and retained traces, plus recorded-submission import. |
| Explain a failure | Categorized outcomes, file differences, stage timing, and local regrading without another model call. |
| Manage operating cost | Per-run reservations and cumulative workspace, repository, and recurring-schedule caps. |
| Evaluate a change | Commit/configuration-linked admission, deduplication, local CI gates, and retained decisions. |
| Keep results current | Frozen recurring evaluations, benchmark health, and retained schedule digests. |
| Make an accountable team decision | Assigned reviews, frozen rubrics, blind comparisons, API roles, and evidence-bound decisions. |
| Learn from production | Redacted examples, independently approved expected behavior, and control-validated publication. |

The tmux workbench, CLI, API, and focused review browser share these records. Local storage and controlled exports let the team retain its evidence; external providers receive only the configured public task material.

## Scope and evidence

Everval's contribution is the implementation and integration of these workflows. The underlying evaluation methods remain established techniques, and every capability claim should match implemented behavior and retained verification.

Installation controls demonstrate the pipeline. They are not customer benchmark results. Representative workload validation, independent security review, and deployment-specific requirements remain necessary before broader adoption. See [delivery boundaries](PLAN.md) and [release verification](verification.md).

[Repository validation](benchmarks.md) · [Cost and latency](cost-latency.md) · [Team review](team-review.md) · [Production feedback](production-cases.md)
