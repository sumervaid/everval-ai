# Judge calibration methods

[Project](../README.md) · [Double-blind review](double-blind.md) · [Implementation](../src/cvals/judge_statistics.py) · [Statistical checks](../tests/test_judge_statistics.py)

A model judge is a measurement instrument. Its agreement, stability, bias and coverage need separate evidence. This module analyzes retained judgments; it never calls a model, changes a reference label or turns missing evidence into a passing result.

## Freeze the question before judging

Freeze task and family identities, development/holdout partitions, responses, rubric, reference provenance, model configurations, repetitions, display orders, primary endpoint, primary contrasts, alpha allocation and analysis settings. Changing the rubric creates a new configuration. Do not select a different endpoint or comparison after inspecting holdout results.

Two endpoints are available:

| Endpoint | Meaning |
| --- | --- |
| **reference** | Match an explicit adjudicated or execution-grounded reference label. An ambiguous or missing reference stays missing. |
| **human_votes** | Match the retained individual human votes. Each voter has equal weight within an item, then items have equal weight within a family. A disagreement between humans remains part of the measurement. |

For example, a question with one A vote and one B vote gives an A judgment agreement of 0.5. It is not silently converted to a tie, a unanimous reference, or an excluded question. Questions with more collected votes do not automatically outweigh other questions.

Human-collected, execution, scripted and unverified references remain separate. Scripted demonstrations cannot establish human validity. A pooled mixture of reference sources receives no empirical primary interval. Source declarations, reviewer independence and blinding remain the study owner's responsibility.

## Agreement and reliability

The report retains scheduled and observed denominators, missing references, abstentions, invalid responses and errors. Confusion-matrix rows are reference labels and columns are observed labels. Each class has its own false-positive and false-negative counts, sensitivity, specificity and precision. Missing class denominators produce null values.

Raw agreement and Cohen's kappa answer different questions. Kappa adjusts observed agreement using the annotators' observed label marginals. It is undefined when expected disagreement is zero, including unanimous one-class data. Perfect observed agreement in that case is not evidence of perfect reliability. Weighted kappa is available only with an explicitly ordered category scale. [Definition and undefined cases](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.cohen_kappa_score.html).

Krippendorff's alpha uses the coincidence matrix and supports missing ratings and different numbers of raters. An item needs at least two observed ratings to contribute to that matrix; excluded item counts remain visible. Nominal alpha uses category mismatch. Ordinal alpha uses cumulative category-frequency distances in the declared order, rather than treating arbitrary integer codes as equally spaced measurements. A/B/tie preferences are nominal; their names do not define an ordinal quality scale. Alpha is undefined without pairable observations or expected disagreement. [Krippendorff's formulation](https://repository.upenn.edu/handle/20.500.14332/2089).

Human–human statistics use the original individual labels, before adjudication. Human-reference agreement does not establish that either the humans or the judge are correct. Report class prevalence, reference sampling and rubric limitations alongside the coefficients. There is no universal kappa or alpha threshold that certifies a judge.

## Independence and uncertainty

Repeated calls, response variants and additional models do not create additional independent tasks. The analyzer averages repeated judgments and display orders within each item, then averages items within their frozen family and weights families equally.

The primary comparison is a paired difference on the identical frozen cohort. Positive differences mean right configuration minus left configuration. A missing required judgment or reference prevents a complete-cohort inferential interval; available descriptive values and missing identities remain visible. Development and holdout items cannot be pooled in one analysis.

The approximate interval resamples whole family means. The default requires at least 20 observed independent families and 2,000 bootstrap resamples. These are policy guards, not scientific guarantees. At least ten expected resamples are required in each allocated tail; more simultaneous comparisons may require more resamples. The seed, allocation and resample count are recorded.

A constant family distribution or degenerate bootstrap interval does not produce a zero-width confidence claim. The report instead retains a bounded Hoeffding sensitivity interval where empirical coverage is otherwise complete. This is deliberately conservative. Neither approach corrects selected tasks, shared-source dependence, adaptive benchmark reuse or inaccurate reference labels.

Per-judge intervals and order, repeatability and self-preference diagnostics are exploratory. Confirmatory paired contrasts require their own frozen comparison allocation. For three model-specific rubric comparisons, for example, allocate total alpha 0.05 across those three contrasts before evaluating the holdout.

Family independence is a scientific assumption, not a property established by unique IDs. For repository tasks, report task-level estimates conditional on the chosen repositories and a repository-grouping sensitivity. With only a few repositories, repository-level uncertainty may remain unavailable or wide.

## Order, repeated judgments and self-preference

Every item is presented in both A/B and B/A order. The service maps preferences back to canonical response identities before analysis.

- **Swap inconsistency** measures whether canonical preferences differ across the two orders.
- **Directional position effect** scores a first-position choice as +1, second-position as −1 and tie as 0, then averages matched orders. A stable preference for the same response cancels across the two orders.
- **Repeated-order disagreement** measures variation between repetitions with the same display order.
- **Excess swap disagreement** compares swapping with that repeated-order noise. Without repeated calls, this diagnostic is unavailable; a swap change alone does not establish position bias.

The self-preference diagnostic requires an exact judge-model identity and provenance for both response generators. It compares preference for the judge's own output against the reference's preference for that same output. Equal-model pairs are ineligible. Matching a provider name or guessing an alias does not establish an exact-model match. The result is a descriptive association conditioned on fallible references, not a causal claim of favoritism.

These controls address documented [position and self-enhancement issues](https://arxiv.org/abs/2306.05685) and [self-recognition effects](https://arxiv.org/abs/2404.13076).

## Prospective power and detectable effects

The exact paired planner assumes one binary outcome per independent task or family. It uses the exact two-sided McNemar test, without a mid-p approximation. Let q be the probability that the paired systems disagree and delta the difference in success probabilities. Then the number of discordant pairs is binomial with parameters n and q; conditional on that count, directional wins are binomial with probability (q + delta) / (2q). Power sums rejection probability across both distributions. [Exact McNemar test](https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html).

For an illustrative predeclared design with 50 independent pairs, q = 0.20 and two-sided alpha = 0.05:

- A true 10-percentage-point difference has approximately 24.1% power.
- The minimum detectable difference at 80% power is approximately 17.6 percentage points.
- These are assumptions-based planning results, not observed model outcomes.

A target that is unattainable at the chosen sample size returns an unavailable MDE. The exact planner supports up to 2,000 independent pairs. Repeating a task does not increase that number.

Fractional human-vote agreement has a different estimand. Its separate paired-mean planner uses a normal approximation with an explicitly assumed standard deviation of the family differences. It reports sensitivity, not an exact guarantee. For 64 families, three planned contrasts, alpha 0.05 and assumed paired-difference SD 0.25, its 80%-power MDE is approximately 10.1 percentage points; doubling the assumed SD doubles the MDE. The SD must be declared prospectively or estimated from separate development data, never tuned to make holdout power look adequate. A zero pilot variance cannot establish certainty.

Do not label a McNemar calculation as power for a fractional or ordinal endpoint. Keep the power assumptions, primary analysis method and intended sampling population visible in the study protocol.

## A bounded first study

Use an archived, licensed human-labeled response corpus for human-reference calibration and an independently identified execution cohort for predicting test outcomes. Preserve individual votes, provenance, exclusions and task-family assignments. Archived patches evaluate judge discrimination; they do not constitute new coding-agent repair attempts.

Separate development and holdout questions before judging. Freeze a small number of meaningful within-model rubric contrasts, both display orders and enough repeated judgments to characterize noise. Reserve the complete call grid against the budget before admission. If the budget stops execution, report the missing grid instead of declaring the planned sample complete.

A useful result may be a reliable improvement, a precisely measured bias, or an adequately powered null. The protocol should make those outcomes distinguishable without promising any of them.
