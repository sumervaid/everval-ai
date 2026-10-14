# Cost and latency analysis

Compare systems on the amount of useful work they complete, the recorded provider spend, and the time each attempt takes. Read these together with completion and error rates. A cheap, fast failure is still a failure.

## What the report measures

Metrics are grouped by system within a frozen experiment.

| Metric | Meaning |
| --- | --- |
| Pass rate | Passing attempts divided by all scheduled attempts. Failed, incomplete, cancelled, and errored work stays in the denominator. |
| Known cost (`known_cost_usd`) | Sum of non-null trial costs plus known partial subtotals from otherwise unknown trials. When accounting is incomplete, this is a lower bound. |
| Total cost (`cost_usd`) | Sum of trial costs, available only when every scheduled trial has a recorded cost. `cost_complete` identifies that coverage. |
| Cost per pass (`cost_per_pass`) | Complete total cost divided by passing attempts. Spending on unsuccessful attempts is included. With no passes or incomplete accounting, the value is unavailable. |
| Median duration | Middle recorded attempt duration: useful for describing a typical attempt. |
| p95 duration | The interpolated 95th percentile of recorded attempt durations: useful for inspecting the slower end of the sample. |

Repeated attempts count separately in cost per pass. This is not cost per distinct task eventually solved after retries. Cost completeness means complete accounting under the configured pricing model, not reconciliation with a provider invoice.

### Where cost comes from

For live HTTP systems, Everval uses provider-reported token counts and the rates frozen with the experiment:

```text
attempt cost in USD =
    (input tokens × input price per million
     + output tokens × output price per million) / 1,000,000
```

The tools harness accumulates usage across its model calls. A longer agent loop can spend more even when it uses the same model and produces the same final answer.

Missing usage stays unknown. If a later call in a tool loop has uncertain billing, the trial total is unknown; earlier known spending remains in `provider_metadata.known_cost_usd` and the step trace. The report includes those partial subtotals in its aggregate known-cost lower bound without counting them twice.

The two-rate model does not separately price cached-token discounts, pricing tiers, provider tool fees, local GPU usage, environment preparation, grading infrastructure, or human review. A locally hosted endpoint with zero configured prices therefore reports zero provider-token cost, not zero operating cost. Recorded submissions retain the external harness's supplied cost and provenance.

### What latency includes

| Execution | Meaning of `duration_ms` |
| --- | --- |
| Single-turn live system | Local wall time for the provider request, response handling, and grading. |
| Tool-based live system | The whole model/tool loop, including public code execution, followed by private grading. |
| Fixture control | Local fixture handling and grading; this is not a model speed measurement. |
| Recorded submission | Duration supplied by the external harness, which Everval does not independently measure. |

Live timing starts after trial admission and stops before final result persistence. It excludes queue wait and the earlier benchmark construction/validation jobs. Sandbox setup and cleanup performed during an attempt are included. A provider timeout does not cap the complete attempt's duration.

`evaluation_duration_ms` equals the full attempt duration for live systems and fixtures. For imported submissions, it instead records the local replay-and-grading time separately from the supplied source duration; it is not a separate verifier-only timer for live runs.

Median and p95 use all non-null durations, including errors and cancellations that produced timing. Missing values are omitted; zero is retained. p95 uses linear interpolation at sorted position `(n - 1) × 0.95`. Four observations of 100, 200, 300, and 400 ms therefore yield 385 ms. A small sample cannot establish a reliable production tail-latency guarantee.

## An illustrative decision

**Hypothetical numbers only; these are not measured model results.** Assume each system completes one attempt on the same 100 tasks, with no infrastructure errors and complete cost/timing records.

| System | Passed | Total cost | Cost per pass | Median attempt | p95 attempt |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 80 / 100 | $24 | $0.30 | 12 s | 25 s |
| B | 92 / 100 | $92 | $1.00 | 40 s | 110 s |
| C | 88 / 100 | $44 | $0.50 | 18 s | 45 s |

Suppose the owner declares three limits: at least 85% observed pass rate, at most $50 per system for this benchmark, and p95 at most 60 seconds. C meets those limits. A misses the quality requirement; B exceeds both spending and latency limits.

B produces four more passing attempts than C for $48 more, or $12 per additional pass in this sample. That marginal calculation can be derived from exported totals; it is not a built-in metric or a guarantee about future workloads.

The corresponding policy is:

```json
{
  "name": "Illustrative cost and latency decision",
  "version": "1",
  "min_completion_rate": 1.0,
  "max_error_rate": 0.0,
  "min_pass_rate": 0.85,
  "max_cost_usd": 50.0,
  "max_p95_duration_ms": 60000.0
}
```

These are example thresholds, not defaults. An optional `min_pass_rate_lower_bound` can require a confidence lower bound as well as the observed rate. The default policy checks execution readiness and has no model-quality threshold.

Gate outcomes are retained per system. A multi-system experiment containing A, B, and C still has a failed overall gate because some eligible systems fail; C's individual result remains visible.

## Spending control and decision gates

An **experiment budget** controls admission across the combined run. Everval reserves a conservative amount before an attempt; tools mode reserves for the bounded loop before its first request. A reservation can exceed eventual recorded spend and can prevent work from starting even when actual usage might have been cheaper.

A **cost gate** evaluates recorded spend after execution, per eligible system. It is distinct from the experiment-wide admission budget. With missing costs, the gate is inconclusive unless the known trial-cost lower bound already exceeds the limit. A **latency gate** requires finalized attempts and timing for every scheduled attempt; a displayed percentile from partial records cannot pass that gate.

Uncertain provider billing blocks further HTTP calls in that experiment. Ambiguous paid calls are not automatically retried, and cancellation does not reverse charges already incurred. Reservations are local controls based on configured prices and usage bounds; provider billing remains authoritative.

Recurring schedules reserve each occurrence's full per-run budget against a cumulative lifetime cap. The default cap funds one occurrence; set a larger explicit cap for longer operation. Uncertain charges retain the full reservation, and legacy schedules without a cap stop until one is configured. The source experiment is excluded from the schedule's lifetime total.

Workspace limits govern all live HTTP attempts across experiments. Repository limits additionally govern tasks admitted through that repository’s frozen benchmark releases; separately curated general collections remain in workspace scope. Reservations and admission happen in the same SQLite write transaction. Daily and monthly boundaries use UTC attempt admission time. Known past spending is backfilled when a limit is configured. Changing a limit does not cancel work already admitted.

```sh
everval spending set --daily 25 --monthly 200 --lifetime 1000
everval spending set --scope project:PROJECT_ID --monthly 75
everval spending list
everval recurring budget SCHEDULE_ID --cap 50
```

These are example budgets, not recommended spending levels. Setting a scope replaces its caps; omitted periods become unlimited.

## A repeatable review workflow

1. Choose the same frozen task collection, harness limits, and repetition count for the systems being compared. Keep hardware, runtime images, and execution conditions consistent.
2. Configure input/output prices, set the experiment budget, and declare quality, cost, and duration limits before interpreting results.
3. Open **Experiments → Full record** to inspect the report, then **Trial evidence** to examine failures, costs, timings, and tool traces. Export JSON for full accounting detail or CSV for the trial ledger.
4. Use **Check gates** to retain the policy and per-system outcome. Review completion, errors, and missing accounting before accepting a low cost or short duration.
5. Compare future runs against compatible frozen evidence. Cross-run total-cost differences are not normalized for different repetition counts; equalize the workload before treating a change in total spend as an efficiency improvement.

## Stage analysis and observed tradeoffs

New attempts record submission, provider, tool, grading, and total timing when those stages ran. Every stage reports its own recorded/scheduled coverage, median, p95, and aggregate time. Old attempts do not gain invented measurements. Submission includes provider and tool execution; do not add overlapping stages together. An external agent's provider stage covers its entire HTTP service request.

`everval economics EXPERIMENT_ID` reports cost per scheduled attempt, known spend on nonpassing attempts, expensive attempts, tool-call counts, and a descriptive frontier across pass rate, cost per pass, and p95. A system is dominated when another is at least as good on all three observed metrics and strictly better on one. Fixtures, replays, unfinished or invalid evidence, incomplete costs, and missing durations are excluded.

This frontier is not a confidence statement or an automatic model-routing policy. Apply declared gates and inspect uncertainty before adopting a system. The implementation does not measure time to first token or queue time, perform load testing or hardware normalization, reconcile invoices, or estimate confidence intervals for cost and latency. Reported confidence intervals concern task success.

[Reading results](results.md) · [Decision policies](policies.md) · [Provider configuration](providers.md)
