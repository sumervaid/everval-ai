# Prospective study: evaluation cost and missed training regressions

Status: protocol template. The engineering controls are implemented; this document
does not report an executed real-model training study.

## Question and primary outcomes

How much evaluation can a monitoring suite omit before it misses practically
important changes during post-training?

Record detection recall, false-alert rate, effect-estimation error, evaluation
cost, and time to a reviewed decision. Keep quality and safety domains separate.
Choose primary outcomes, practical thresholds, and the tolerated miss/false-alert
rates before inspecting validation results. Report inconclusive and missing
measurements explicitly.

## Data and training lineage

Use a documented, licensed open-weight training trajectory with real checkpoint
files and a stable inference configuration. Record model size, training method,
data revisions, training seed, compute, and checkpoint digests. Unrelated API
model releases are not successive checkpoints of one training run.

Select several distinct behaviors with justified task populations. Prefer
executable graders where appropriate; calibrate subjective judgments against a
blinded, independently adjudicated sample. Document limitations of the reference
judgments. Keep public task statements separate from hidden grading material.

Do not place model weights, credentials, private task bundles, or customer
responses in Git. Share only artifacts you have permission to disclose.

## Prospective split

Reserve development checkpoints and task families for suite selection. Freeze
the validation grid before collecting its outcomes. Withhold later checkpoints
and different task families; validate on another training run/seed before
generalizing beyond the original trajectory.

A chronological split within one run does not eliminate dependence or adaptive
training feedback. Separate the reusable monitoring suite from a final untouched
measurement cohort. Record all inspection of held-out material and treat the
result as exploratory if the preregistration assumptions were violated.

## Strategies and controls

Compare the selected suite, random subsets, domain-stratified subsets, and the
full reference suite. Apply equal cost ceilings and protected-domain coverage to
the cheaper strategies. Predeclare several selection/baseline seeds and evaluate
them on independently reserved splits or runs. Repeatedly selecting against the
same inspected holdout does not establish stability; the built-in single seeded
baseline is only one comparison.

The current greedy selector minimizes a descriptive development loss. Its
held-out threshold crossings are not formal statistical regression detections.
Use the separately specified paired inference procedure for confirmatory
comparisons where its assumptions hold.

Calibrate score variability before choosing sample sizes. Repeated samples from
one task or repository do not create independent families. Predeclare error
allocation across comparisons and domains, and a minimum meaningful effect.
Choose sample size from that design rather than treating an available API budget
as sufficient statistical power.

## Investigation exercise

Include an explicitly labeled operational fault drill: delayed checkpoint
arrival, duplicate notifications, a worker restart, unavailable grades, or a
grader revision on frozen outputs. Measure alert/investigation behavior
separately from actual model changes. A synthetic injected regression tests the
pipeline; it is not a discovered training regression.

For an unexpected score change, preserve:

- the observation and exact evidence identities;
- competing model, scorer, environment, and coverage explanations;
- the controlled checks that distinguish them;
- the recommendation, uncertainty, and remaining checks.

A training-data change is not a causal explanation merely because it occurred
near the regression.

## Budget and disclosure

Plan inference, training compute, hosting, and human adjudication separately.
Everval's provider ledger covers admitted evaluation calls, not every expense.
Reserve a final validation budget before exploration. Stop new admission when
the cap or accounting coverage fails; retain earlier failed and unknown attempts.

Publish the analysis plan, versioned manifests, scoring code, shareable responses,
all exclusions, negative findings, uncertainty, and reproduction commands.
Describe actual model/task/checkpoint/worker scale. The local synthetic demo and
load tests must remain separate from empirical findings.
