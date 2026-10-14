# Product expansion

This implementation extends the local workbench into a repeatable team workflow. The acceptance criteria below are implemented in version 0.2. See [verification](verification.md) for exercised workflows and [deployment boundaries](PLAN.md) for remaining coverage limits.

| Area | Acceptance |
| --- | --- |
| Guided onboarding | Resume a real repository workflow through preflight, curation, validation, release, and first evaluation without editing JSON. |
| Existing agents | A public-task adapter protocol and SDK capture revisions, bounded outputs, available usage, and traces without revealing graders. |
| Failure investigation | Categorized failures, output/file differences, stage timing, and linked local regrading preserve original evidence. |
| Change and recurring runs | Exact revision/configuration triggers deduplicate runs; frozen digests and cumulative schedule limits support recurring decisions. |
| Benchmark health | Coverage, rejection reasons, duplicate candidates, validation instability, and stale evidence remain inspectable. |
| Real-world examples | Staged imports, redaction, reviewer-authored expected behavior, and control validation produce general task collections. |
| Cost optimization | Complete/partial accounting, stage timings, comparable quality/cost/latency choices, and cumulative budgets support explicit decisions. |
| Review workflows | Assigned queues, rubrics, blinded comparisons, and decision briefs work through authenticated identities and enforced API roles. |
| Repository coverage | JavaScript/TypeScript and reviewed private dependency preparation preserve isolation and historical validity. |

All development, execution, private source, and evidence stay on the iMac. GitHub mirrors product source and documentation. Integrations do not post results or messages automatically. Verification uses deterministic controls and local test services; no paid provider calls are required.
