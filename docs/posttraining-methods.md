# Post-training measurement methods

Everval's post-training analysis separates recorded outcomes, statistical comparisons,
evaluation qualification, and evaluation-mix selection. These methods operate on local
evidence. They do not train models, establish production-scale performance, or replace
researcher review. Synthetic controls test the implementation; they are not empirical
results about an actual post-training run.

## Paired checkpoint comparisons

A protocol freezes the baseline checkpoint, scenario versions, grader revision, task-family
mapping, repetitions, inference/execution settings, meaningful effect threshold, maximum
number of checkpoint comparisons, domain count, and error budget before candidate results
are examined. The service retains consumed comparison slots. Changing the protocol creates
a separate research question; repeatedly restarting protocols after seeing unfavorable
results invalidates the stated error guarantee across that activity.

Each observation is one scenario and repetition, with a score in `[0, 1]`; higher is better.
The current service uses the primary pass metric: an explicit pass is 1 and an explicit
failed grading outcome is 0. An unavailable, interrupted, infrastructure-error, or otherwise
ungraded attempt is missing, never an invented zero or success. Every expected pair must
have completed, valid scores at both checkpoints. Duplicate, unexpected, incomplete,
nonfinite, out-of-range, or differently clustered evidence makes the comparison invalid.

For scenario `s`, average its paired candidate-minus-baseline differences across frozen
repetitions. Average these scenario differences equally within each frozen task family.
Finally average the family differences equally. This is the reported delta. Baseline and
candidate means use the same weighting. The estimand is therefore the equal-family mean
of the equal-scenario expected score difference for the declared measurement population;
it is not an attempt-weighted leaderboard score.

Let `n` be the number of independent task families. Each family difference lies in `[-1, 1]`.
For total error budget `alpha`, `L` planned comparison looks, and `D` planned domains:

```text
comparison_alpha = alpha / (L * D)
radius = sqrt(2 * log(2 / comparison_alpha) / n)
interval = [max(-1, delta - radius), min(1, delta + radius)]
```

The interval follows Hoeffding's inequality for independent bounded variables. Allocating
error across all planned looks and domains uses the union bound; it does not require
independence across checkpoint comparisons. It does require the within-comparison family
independence assumption, a frozen analysis policy, and no unaccounted comparisons. Reusing
the baseline can correlate looks without violating the union-bound argument when its
assumptions hold. Freezing the look grid alone does **not** correct adaptive benchmark
reuse: if checkpoints or training interventions are chosen using these same family
outcomes, the independence/unbiasedness assumptions can fail. Use a separate untouched
measurement cohort or interpret the intervals as exploratory in that setting. The method
is deliberately conservative, especially with small numbers of families. [Hoeffding,
1963, *Probability Inequalities for Sums of Bounded Random Variables*](https://doi.org/10.1080/01621459.1963.10500830).

Duplicating repetitions does not increase the independent family count or shrink the
interval. Related tasks must share a family; inventing separate family labels does not
make observations independent. Neither the service nor the formula can verify this
scientific assumption. The bound does not establish that the declared task population
represents production work or that a training intervention caused a measured change.

For meaningful difference threshold `t`, the decision rules are:

| Decision | Required evidence |
| --- | --- |
| `invalid` | Frozen coverage, valid scores, identities, or comparison budget failed. |
| `inconclusive` | Too few independent families, or the interval does not support the other decisions. |
| `regression` | The entire interval is strictly below `-t`. |
| `improvement` | The entire interval is strictly above `t`. |
| `no_material_change` | The entire interval lies inside `[-t, t]`. |

A nonsignificant difference is not equivalence. A zero observed delta with a wide interval
remains inconclusive. A threshold-crossing point estimate alone never creates a statistical
regression alert. Qualification gaps, environment differences, and infrastructure problems
still require investigation even when a statistical comparison is available.

The pure implementation is [posttraining_analysis.py](../src/cvals/posttraining_analysis.py).

## Evaluation qualification

An owner supplies a measurement claim, intended population, limitations, frozen task and
grader versions, and a semantic rubric. The assessment records evidence rather than
assigning unexplained confidence scores. Its input is the strict `QualificationEvidence`
model in [posttraining_qualification.py](../src/cvals/posttraining_qualification.py).

The assessment checks:

- A positive control expected to pass, a negative control expected to fail, and a
  behavior-breaking mutation expected to fail. Every supplied control must match its
  expected outcome. Their IDs must be unique.
- Repeated grader results on distinct items. The reported repeatability statistic is
  the fraction of items for which **every** supplied repetition agrees, not a
  pairwise agreement score. Defaults require five distinct repeated items and 90% agreement.
- Blinded human-reference judgments containing both passing and failing examples.
  Defaults require at least five distinct examples in each class. Overall agreement,
  sensitivity, specificity, and false-positive/false-negative counts remain visible.
  Each class must independently meet the declared agreement threshold. An always-pass
  grader cannot qualify merely because a calibration sample is mostly positive.
- Explicit blinding, adjudication of disagreements, and documented limitations of human
  reference judgments. Adjudication does not remove disagreement counts or change the
  original reference labels. Reviewers can share biases or misunderstand the behavior.
- Declared-population scores for a floor/ceiling check. By default a mean at or above
  0.95 or at or below 0.05 flags saturation for review. This is a descriptive signal,
  not a power analysis. It can obscure heterogeneous slices, so owners should also
  inspect behavior and domain breakdowns.

Missing evidence produces visible gaps. Complete evidence yields `ready_for_review`,
never automatic trust or a production certificate. Thresholds and minimum sample sizes
are explicit owner-supplied policy; the defaults are a checklist, not a scientific
justification that ten human labels suffice. Population-level calibration requires an
appropriate sampling design and substantially more evidence where precision demands it.

The platform records supplied versions and evidence hashes, but cannot independently
verify that judgments were blinded, the reference labels were correct, the item sample
was representative, or the rubric measures the intended behavior. Structural probes and
grader repeatability alone do not establish semantic validity.

## Selecting a monitoring suite

The input to [posttraining_mix.py](../src/cvals/posttraining_mix.py) is a bounded table:

| Field | Meaning |
| --- | --- |
| `checkpoint_id` | Immutable identity for one checkpoint comparison, with a declared reference. |
| `task_family` | Related-task family used for development/validation isolation. |
| `eval_id` | Stable evaluation identity, shared across both partitions. |
| `domain` | Stable domain of that evaluation. |
| `delta` | Supplied candidate-minus-reference effect in `[-1, 1]`, or null if unavailable. |
| `cost_usd` | Positive measured cost of that cell, or null if unknown. |

The development table must contain complete effects and costs. Each evaluation must have
its full declared set of families at every development checkpoint. The selector rejects
missing cells, duplicate identities, nonfinite numbers, inconsistent domains, and unknown
costs. Selection is bounded to 64 evaluations and 256 development checkpoints, with
at most 100,000 input rows. Values are supplied observations; the module does not contact models, verify an
invoice, or infer absent cost. Zero-priced measurements currently cannot be used for the
cost-ratio selection heuristic; give such studies a different prespecified cost unit or
use the full suite, without relabeling it as measured dollars.

Selection starts with the cheapest evaluation in every protected domain. A budget that
cannot cover these mandatory evaluations is rejected. It then greedily adds the evaluation
with the largest development loss reduction per dollar, stopping when no candidate
improves the loss or fits the budget. Ties use the evaluation ID. The development loss is
mean absolute domain-delta error plus disagreement about crossing the negative practical
threshold. An unmeasured domain contributes a fixed penalty of 3. Costs are the mean total
cost per evaluation across development checkpoints.

A domain delta averages family deltas within each evaluation, then evaluation means
within that domain. This is a declared descriptive reference; its weighting is different
from the family-level inferential method above. The full suite can be biased, saturated,
misgraded, or noisy. Matching it does not establish agreement with reality.

The saved selection includes:

- The chosen evaluations, protected domains, threshold, seed, and development hash.
- A seeded random baseline and a seeded domain-round-robin baseline, each retaining the
  mandatory protected evaluations and using the chosen suite's **cost ceiling**. Discrete
  evaluation costs can make actual expenditure differ; the report shows those differences
  rather than calling unequal expenditure exactly equal cost.
- The full-suite reference, actual development cost and loss for every strategy, and a
  hash of the entire selected configuration.

Only development results influence selection. Validation checkpoints and task families
must **both** be disjoint from development. Evaluation IDs and domains must stay fixed.
The service freezes a complete validation manifest before importing validation outcomes,
checks exact row identities, and retains one immutable validation result. The pure function
also checks checkpoint/family disjointness and the selected-configuration hash, but on its
own cannot detect an entire family omitted from an undeclared validation population.

Validation reports detected negative threshold crossings, recall, false-alert counts and
rates, mean absolute delta error, complete cost or a known-cost lower bound, and every
comparison's reference and estimated deltas. Rates condition on comparable cells. Missing
reference outcomes, absent selected-domain coverage, unavailable selected outcomes,
unknown denominator counts, and missing costs remain visible. Zero reference regressions
produce null recall, never a perfect score.

These are **descriptive threshold crossings**, not the statistical regression alerts from
the paired-family procedure. This first method does not estimate confidence intervals for
held-out recall, replay sequential detection delay, or prove generalization. One seeded
random draw is not a robust baseline distribution. Repeat an independently preregistered
study across several seeds, task populations, and independent training runs before making
claims about savings or missed-regression rates. A held-out miss is a result to explain,
not evidence to discard.

## Investigation and empirical reporting

An alert should trigger inspection of affected examples, grader versions, frozen-output
regrading, configuration changes, and infrastructure evidence before a training
intervention is recommended. Infrastructure can materially affect benchmark outcomes;
this is an empirical reason to preserve environment identity alongside model identity.
[Anthropic's infrastructure-noise investigation](https://www.anthropic.com/research/infrastructure-noise).

The engineering supports a study of monitoring cost versus missed regressions, but a
study needs actual authorized checkpoint evidence. A public report should distinguish
source observations, synthetic controls, analysis decisions, exclusions, unknowns, and
limitations. No simulated checkpoint, fabricated human calibration, or installation test
should be described as a real post-training experiment.
