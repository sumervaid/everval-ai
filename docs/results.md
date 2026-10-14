# Reading results

Every experiment records the task cohort, system settings, attempts, outputs, checks, and terminal states. Start with coverage and failure counts before interpreting a headline score.

## Pass rate and check score

A scenario passes when its required checks pass. Weighted check scores describe partial satisfaction. They are related but different measures.

Scheduled work remains in the experiment denominator. The report distinguishes valid graded results from errors, cancellation, interruption, and budget exhaustion. Inspect operational completion alongside correctness.

## Uncertainty and pairing

Single-run task results use Wilson intervals. Repeated results are grouped by task so repeated attempts do not masquerade as additional independent tasks. Paired comparisons use the same task set and report inconclusive differences explicitly.

These intervals describe the recorded sample and method. They do not establish that a hand-picked collection represents every future workload. Fixture controls are installation checks, not empirical claims about a model.

## Costs and time

Read pass rate alongside total cost, cost per passing attempt, median duration, and p95 duration. Cost per pass includes spending on unsuccessful attempts; it is unavailable when accounting is incomplete or no attempts pass. Missing costs remain unknown, and reported known costs are a lower bound.

Live duration includes model calls, tool work, and grading. Imported evidence preserves the external harness's supplied duration separately from local replay-and-grading time. Recorded percentiles include all timed attempts, including errors.

See [Cost and latency analysis](cost-latency.md) for exact field definitions, accounting limits, spending controls, and an illustrative policy decision.

## Replay and rerun

- **Replay:** recompute the report from recorded evidence; no model call.
- **Rerun:** create a new experiment using the original frozen configuration.
- **Imported-result regrade:** grade the same submission again; this adds no independent performance evidence.

Live external services can change even when the request configuration is frozen. Reproducible inputs do not guarantee identical external model outputs.

## Exports

JSON preserves structured evidence. CSV is a trial ledger with spreadsheet-formula protection. HTML is self-contained and escapes user-provided content. Review permissions before sharing private tasks or outputs.

Human judgments belong alongside automated evidence with an explicit dimension, rationale, and reviewer label. They should not silently overwrite recorded automated outcomes.
