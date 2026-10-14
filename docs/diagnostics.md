# Investigate an evaluation

Open an experiment's **Failure inbox** to group unsuccessful attempts by recorded execution state, failed checks, or an explicit provider/grading error label. The grouping is deterministic triage; it does not claim to infer a model's underlying reasoning.

Inspect the attempt's checks, output, provider trace, known charges, and submission/tool/grading times. Coding tasks show bounded file differences from the starting snapshot. Full records remain available separately.

```sh
everval diagnostics triage EXPERIMENT_ID
everval diagnostics show EXPERIMENT_ID TRIAL_ID
everval diagnostics regrade EXPERIMENT_ID TRIAL_ID
everval economics EXPERIMENT_ID
everval brief EXPERIMENT_ID
```

Regrading runs the recorded output against the frozen checks and requested verifier image. It makes no provider call, retains its own record and fingerprints, and leaves the original trial unchanged. It still requires the prepared local runtime and admission attestations. A failing or unavailable regrade exits nonzero.

Use the economics view to find expensive nonpassing attempts, inspect stage coverage, and compare the observed quality/cost/latency frontier. Fixtures, replays, unfinished runs, incomplete accounting, and missing duration evidence cannot enter that frontier. It is a descriptive comparison on the frozen cohort, not an automatic routing decision.

A decision brief contains system summaries and retained policy outcomes without task inputs, outputs, or hidden checks. In team mode, viewers can read briefs; detailed diagnostics and regrading require operator or administrator access. Assigned reviewers use the separate blinded review desk.

[Team review](team-review.md) · [Metric definitions](cost-latency.md)
