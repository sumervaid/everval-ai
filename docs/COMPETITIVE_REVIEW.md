# Public product review

Research date: 2026-10-13. This review covers public pages and documentation. The authenticated Smith workflow was not inspected.

## What the public evidence establishes

| Evidence | Observation | Implication |
| --- | --- | --- |
| [Smith product and methodology](https://www.vals.ai/vals-smith) | Describes repository import, selectable models/harnesses, tasks mined from merged PRs, pre-fix failure/post-fix success, and regression-aware scoring. | These are baseline requirements. |
| [Public benchmark catalog](https://www.vals.ai/vals-smith/benchmarks) | Groups repositories by language and displays task/model counts and dates. | Discovery across repositories is already present. |
| [flask-wtf report](https://www.vals.ai/vals-smith/pallets-eco/flask-wtf?dataset=94b74edc-0e09-4d9b-9d19-c7345e243c47&tasks=1) | Shows cost, latency, token use, harness identity, example-trajectory controls, and an explicit one-task caveat. It still ranks tied scores and uses leadership language. | Add evidence-aware presentation, not merely a cost chart. |
| [Valkyrie report](https://www.vals.ai/vals-smith/vals-ai/valkyrie?dataset=665f2b07-3586-45e7-ae18-c869b676952e&tasks=46) | The inspected 46-task report has missing cost/duration and token/timing information. | Completeness and provenance of metrics matter. This is an observation about one report. |
| [General methodology](https://www.vals.ai/methodology) | Discusses harnesses, operational metrics, uncertainty across tasks, and stochastic variation. | Statistics are not a new concept absent from Vals. Smith-specific implementation remains to be verified. |
| [Partner access](https://www.vals.ai/vals-smith/design-partner) | The private workflow is gated. | Task editing, export, isolation, collaboration, and run recovery cannot be judged from public pages alone. |
| [Valkyrie repository](https://github.com/vals-ai/Valkyrie) and [license](https://raw.githubusercontent.com/vals-ai/Valkyrie/dev/LICENSE) | Vals already publishes an evaluation framework under AGPL-3.0. | Open-source orchestration alone is insufficient differentiation. |

[Pinned Valkyrie local-runtime documentation](https://github.com/vals-ai/Valkyrie/blob/68edd7ac800114c407ae832e383ed1a2f1ac09a9/docs/contributing/local-development.mdx) also describes local Docker evaluation. It documents unrestricted egress for local Docker generation stages, retries/resumes using the current checkout, and containers surviving an executor crash. These are specific targets for stronger network enforcement, pinned execution, and cleanup. They apply to that documented revision; we have not run Valkyrie or established the behavior of Smith's hosted service. Local operation alone is not a unique feature.

Public documentation changes over time. These observations are a dated sample, not a complete feature inventory. We will not claim an authenticated feature is absent merely because a marketing page omits it.

## The strongest critique

**Small samples cannot support precise buying decisions.** A ranked table needs evidence-aware ties, uncertainty, workload coverage, and a clear statement when results are inconclusive. A one-task score can demonstrate a task, but cannot establish broad repository capability.

**Historical fixes are a selected slice of engineering.** PRs with reconstructible environments and good tests differ from ambiguous requirements, migrations, performance investigations, and multi-service work. Show the candidate funnel, rejection reasons, and coverage gaps before presenting a score.

**Pass rates conceal how a system fails.** Expose flaky tasks, tool errors, setup failures, resource exhaustion, bad patches, and genuine test failures separately. Record retry costs and incomplete experiments.

**Test quality determines benchmark quality.** Historical tests can be weak, implementation-specific, leaked, or unstable. A task needs repeated validation, independent grading, leakage review, and targeted adversarial checks.

**Useful results need a decision attached.** Identify which system meets a stated budget, latency target, and reliability threshold on a declared workload. Preserve the evidence so another person can challenge that conclusion.

These critiques motivate our design; they are not allegations about Vals' private implementation.

## Build and reuse

| Project | Evidence | Planned role |
| --- | --- | --- |
| Harbor | [Apache-2.0 license](https://raw.githubusercontent.com/harbor-framework/harbor/main/LICENSE), [task format](https://docs.harborframework.com/tasks/overview), [separate verifier](https://docs.harborframework.com/tasks/separate-verifier) | Preferred execution adapter and portable task format, conditional on a local capability spike. Separate grading is opt-in and must be explicitly enabled. |
| SWE-bench | [Repository](https://github.com/SWE-bench/SWE-bench), [MIT license](https://github.com/SWE-bench/SWE-bench/blob/main/LICENSE) | Import/export compatibility and familiar result semantics. |
| SWE-smith | [Repository](https://github.com/SWE-bench/SWE-smith), [MIT license](https://raw.githubusercontent.com/SWE-bench/SWE-smith/main/LICENSE) | Optional generation research. Its documented platform target is Ubuntu; evaluate applicable components inside Linux. |
| Valkyrie | [Repository](https://github.com/vals-ai/Valkyrie) | Architectural and compatibility reference. No implementation is copied into this project. |

Dependency versions, behavior, and notices will be pinned and checked when code is introduced. No benchmark data or competitor assets were imported for this review.
