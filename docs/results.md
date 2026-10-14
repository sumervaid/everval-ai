# Reading results

Every experiment records the task cohort, system settings, attempts, outputs, checks, and terminal states. Start with coverage and failure counts before interpreting a headline score.

## Pass rate and check score

A scenario passes when its required checks pass. Weighted check scores describe partial satisfaction. They are related but different measures.

Scheduled work remains in the experiment denominator. The report distinguishes valid graded results from errors, cancellation, interruption, and budget exhaustion. Inspect operational completion alongside correctness.

## Uncertainty and pairing

Single-run task results use Wilson intervals. Repeated results are grouped by task so repeated attempts do not masquerade as additional independent tasks. Paired comparisons use the same task set and report inconclusive differences explicitly.

These intervals describe the recorded sample and method. They do not establish that a hand-picked collection represents every future workload. Fixture controls are installation checks, not empirical claims about a model.

## Costs and time

A missing cost is not zero. Reports distinguish complete cost accounting from partial or unavailable accounting. Cost per successful task has no finite value when no tasks succeed.

Imported evidence preserves supplied system duration separately from local grading time. Model latency, task time, and verifier time should not be treated as interchangeable performance measurements.

## Replay and rerun

- **Replay:** recompute the report from recorded evidence; no model call.
- **Rerun:** create a new experiment using the original frozen configuration.
- **Imported-result regrade:** grade the same submission again; this adds no independent performance evidence.

Live external services can change even when the request configuration is frozen. Reproducible inputs do not guarantee identical external model outputs.

## Exports

JSON preserves structured evidence. CSV is a trial ledger with spreadsheet-formula protection. HTML is self-contained and escapes user-provided content. Review permissions before sharing private tasks or outputs.

Human judgments belong alongside automated evidence with an explicit dimension, rationale, and reviewer label. They should not silently overwrite recorded automated outcomes.
