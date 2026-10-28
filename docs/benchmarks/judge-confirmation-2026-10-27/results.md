# A cheaper judge passed the quality test

**Everval qualified a judging configuration with 54.1% lower estimated cost on 1,000 human-rated conversation families.** GPT-6.1 Terra with the standard rubric achieved **72.70% agreement with retained human votes**, compared with **71.15% for GPT-6.1 Sol with the standard rubric**. It passed the cost test and the predeclared quality-loss limit of one percentage point.

This is a completed, preregistered comparison of specific judging configurations. Everval validated a proposed substitution; it did not train a better model or establish a universal model ranking.

![Estimated judging cost and agreement with retained human votes for all three configurations](cost-quality.svg)

## The result

All three configurations judged the same 1,000 families in both answer orders. The full **6,000-call grid** completed, with **no unattempted calls or unresolved charges in this run**. The reference contains **2,957 retained historical human votes**.

| Configuration | Human-vote agreement | Estimated cost, 2,000 judgments | Abstentions, included as zero credit |
| --- | ---: | ---: | ---: |
| GPT-6.1 Terra — standard rubric | **72.70%** | **$4.489838** | 8 |
| GPT-6.1 Terra — checklist rubric | 72.34% | $4.719575 | 18 |
| GPT-6.1 Sol — standard rubric | 71.15% | $9.778616 | 4 |

Costs use reported tokens and the rates frozen in the protocol. They are not reconciled invoices. These are full configuration comparisons: Terra used reasoning effort none and a 768-token output ceiling; Sol used low reasoning and a 1,536-token ceiling.

**The original rubric comparison also passed.** Terra standard cost **4.87% less** than Terra checklist, with an observed agreement difference of **+0.358 percentage points**. The larger study does not reproduce the pilot's 9.8% saving as a universal number: it uses a different dataset and explicitly uncached requests. The [64-question pilot](../judge-study-2026-10-27/results.md) remains available with its original estimates and limitations.

## What the quality test established

The study fixed an allowable average agreement loss of **one percentage point** before inference. Both a whole-family paired bootstrap and a paired Student t method had to put their **one-sided 95% lower bound above −1.000 points**. Both methods also had to support lower cost. The second comparison was eligible for a confirmatory adoption decision only after the first passed.

| Ordered comparison | Observed agreement difference | Bootstrap lower bound | Student t lower bound | Estimated saving | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| A: Terra standard − Terra checklist | +0.358 points | −0.575 points | −0.583 points | 4.87% | Passed quality and cost |
| B: Terra standard − Sol standard | +1.550 points | +0.317 points | +0.336 points | 54.09% | Passed quality and cost |

These are one-sided lower bounds, not two-sided 95% intervals. The paired cost upper bounds were below zero in both comparisons. There were 106 and 135 families with nonzero quality differences, exceeding the predeclared minimum of 20.

The primary conclusion is **lower cost with quality meeting the prespecified tolerance**. Comparison B also has positive lower bounds for agreement, providing additional evidence for its observed advantage on this cohort. A separate superiority claim was not the preregistered objective. Comparison A does not establish higher quality or exact equivalence.

Each family has equal weight. Its score averages both display orders, each scored against its individual retained human votes. Six thousand calls and 2,957 votes do not turn this into 6,000 or 2,957 independent test items: the analysis unit is the **1,000 prompt families**.

The prospective power analysis explicitly varied the assumed paired standard deviation. Observed standard deviations were 0.181 and 0.233, above the central planning assumption of 0.10. The decisions use the frozen confidence rules; the study does not claim that its optimistic planning power was guaranteed or realized.

## What this means for a deployment

Consider a team using Sol to judge pairs of assistant answers. Everval can test a proposed lower-cost judge against the team's reviewed examples, freeze the quality tolerance and budget, run matched comparisons, and retain an auditable qualification decision.

This study demonstrates that workflow on a public human-preference dataset. It supports trying the qualified configuration on a representative customer holdout. It does not automatically qualify an unseen support queue, a safety-critical grading task, or repository-repair correctness.

At the measured usage and frozen prices:

| Future workload, two judgments per evaluated pair | Terra standard | Sol standard | Estimated API saving |
| --- | ---: | ---: | ---: |
| 1,000 evaluated pairs | $4.49 | $9.78 | $5.29 |
| 100,000 evaluated pairs | $448.98 | $977.86 | $528.88 |

The entire new calibration cost **$18.988029**, including all three configurations. Its projected API-only break-even against Sol is **3,591 future two-order pair evaluations**. Against the smaller rubric-only saving, break-even is **82,652 evaluations**. These are projections using the observed mix and point estimates, not measured customer ROI. They exclude engineering, hardware, electricity and original human annotation costs.

Start with the [judge-calibration workflow](../../judge-calibration.md), supply reviewed examples from the intended deployment, and use [recurring benchmarks](../../chat.md) to repeat the comparison as the system changes. A prespecified cheaper candidate and a quality limit still require a responsible operator.

## Diagnostics that matter

The cheaper configuration was less consistent under answer-order swaps than Sol:

| Configuration | Canonical preference changed after swapping answers | Valid two-order families |
| --- | ---: | ---: |
| Terra standard | 92 / 995 — 9.25% | 995 |
| Terra checklist | 98 / 989 — 9.91% | 989 |
| Sol standard | 63 / 997 — 6.32% | 997 |

These are descriptive swap inconsistencies. There are no same-order repetitions to separate position effects from stochastic variation. The main agreement score includes both orders and all scheduled calls; the swap diagnostic separately requires two valid preferences. A team that values order stability may make a different deployment decision.

Observed agreement differences by domain were:

| Domain | Families | Terra standard − checklist | Terra standard − Sol |
| --- | ---: | ---: | ---: |
| General assistance | 474 | −0.21 points | +0.70 points |
| Code | 205 | +1.95 points | +3.41 points |
| STEM | 119 | +0.28 points | +3.78 points |
| Multilingual | 202 | +0.12 points | +0.33 points |

The pooled decision does not establish non-inferiority in each domain; these subgroup estimates are descriptive.

Observed request timing also favored Terra in this run:

| Configuration | Median request time | 95th-percentile request time |
| --- | ---: | ---: |
| Terra standard | 2.00 s | 4.64 s |
| Terra checklist | 2.03 s | 3.66 s |
| Sol standard | 3.97 s | 8.70 s |

Timing includes transport and service variation under four concurrent calls. These descriptive measurements were not an inferential adoption criterion or a randomized latency comparison.

[Operational diagnostics](operations.json) retain latency and token-use summaries; they are observations from this run, not service-level guarantees.

## Evidence and reproduction

The [protocol](protocol.md), sample, candidate, thresholds, comparison order and source hashes were [published before inference](https://github.com/sumervaid/everval-ai/commit/78367ab). The run did not expand the sample, retry outcomes or revise the rubric after seeing results.

- [Frozen analysis output](results.json): all configurations, paired bounds, decisions, subgroup estimates, accounting and source hashes.
- [Derived family measurements](family-metrics.json): the independent units needed to reproduce the paired statistics, without conversations, human reference labels or provider responses.
- [Independent statistical audit](independent-statistical-audit.json): vote/order weighting, exact scores, bootstrap and Student t bounds, fixed-sequence decisions and break-even arithmetic.
- [Independent data and accounting audit](independent-data-audit.json): frozen-request coverage, provider evidence, token usage, references and ledger reconciliation.
- [Final run accounting](budget-status.json): all 6,000 requests settled; **$18.988029 new**, **$34.130958 cumulative against $50**.
- [Frozen identities](freeze.json), [cohort provenance](cohort.json), [power sensitivity](power.json), and [original forecast](forecast.json).

The cumulative opening balance retains $0.01312325 for three older unknown-billing requests; this run adds no unresolved obligations. Provider prices are frozen estimates. Hardware and electricity were not metered.

Recompute both statistical decisions from the repository root with Python 3.12. Only the standard library is needed; the command uses no model key or paid inference:

```sh
python3.12 -S -m scripts.judge_study.reproduce_confirmation \
  --data docs/benchmarks/judge-confirmation-2026-10-27/family-metrics.json \
  --expected docs/benchmarks/judge-confirmation-2026-10-27/results.json
```

The output must report `matches_published_statistics: true`, with both comparisons marked `adopt`. It recomputes the paired bounds, decisions and economics from derived measurements; raw annotation and provider-response validation require the separate retained evidence. The audits are separate computational reproductions within this project.

To regenerate the figure from the public aggregate output, without a model key:

```sh
uv run --no-project --python 3.12 --with matplotlib==3.10.7 \
  python scripts/judge_study/plot_confirmation.py \
  --results docs/benchmarks/judge-confirmation-2026-10-27/results.json \
  --output docs/benchmarks/judge-confirmation-2026-10-27
```

The [reproduction script](../../../scripts/judge_study/reproduce_confirmation.py) retains the frozen analysis settings. Full provider-response auditing uses the private workspace; raw conversations, human references, provider records and credentials are not published.

## Limits of the claim

The data comes from NVIDIA's [HelpSteer3-Preference validation split](https://huggingface.co/datasets/nvidia/HelpSteer3/tree/f6d145777bcbde96137596340fab89793acd1031/preference), pinned to the documented revision and licensed under CC-BY-4.0. It includes curated historical preferences: up to three annotations selected partly for agreement from the original three-to-five. Stable rater identities are unavailable. The cohort is fresh to Everval's study; provider training exposure is unknown.

Human-vote agreement is not objective correctness. Hosted model weights are not independently attested. Different reasoning budgets and prompts prevent interpreting the result as a pure model-size effect. Inference to similar tasks assumes the sampled families are sufficiently independent and representative. The one-point tolerance is a prospective engineering choice, not a universal quality standard.

The result supports a specific, reproducible measurement decision: **this prespecified cheaper judge passed this quality-and-cost protocol on this cohort**.
