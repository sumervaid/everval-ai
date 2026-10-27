# What the judge study found

**The checklist had 10.9% higher estimated cost without a demonstrated agreement gain in the complete Terra human-reference comparison.** Across **64 held-out questions**, with 256 judgments per arm, GPT-6.1 Terra's human-vote agreement changed from **72.30% to 72.11%**: **−0.20 percentage points**, with a multiplicity-adjusted interval of **−2.15 to +1.76**. Conservative configured-rate cost rose from **$0.624685 to $0.692560**. These costs use reported tokens and the frozen conservative rate schedule, not invoice charges. This result concerns the tested prompts, settings and cohort; it does not establish that all rubrics are ineffective.

The complete Terra archived-execution comparison was **72.5% in both arms**, with a paired-difference interval of **−7.50 to +9.38 points**. Intervals use the frozen paired task bootstrap and allocation of alpha 0.05 within each declared family of three contrasts. Luna's original primary grid contains invalid, abstained or error outcomes; Sol has quarantined missing cells. Their complete-item primary differences remain descriptive, without a planned full-cohort interval. See [original results](results.json), the [planned-effect figure](judge-original-effects.svg) and [methods](methodology.md).

## The whole human cohort

This separate **post hoc** analysis retains all 64 questions and every scheduled judgment slot. Attempted invalid, abstained and error deliveries receive zero credit. Unattempted cells range from zero to their question's maximum possible human-vote credit. These are **finite-cohort bounds, not confidence intervals**; no midpoint is imputed.

| Judge | Base rubric | Rubric + checklist |
| --- | ---: | ---: |
| GPT-6 Luna | 63.71% | 62.05% |
| GPT-6.1 Terra | 72.30% | 72.11% |
| GPT-6.1 Sol | 71.13–72.70% | 69.77–71.33% |

![Post hoc full-cohort delivered-agreement bounds, using the original frozen transport](judge-full-cohort-bounds.svg)

[Chart data and reproduction command](full-cohort-bounds.json). Original planned analyses and the pre-holdout delivered-agreement secondary analysis remain in [results.json](results.json); points from incomplete grids are explicitly conditional on complete items.

## A transport error, caught separately

All **10 parser-invalid records** contained valid final judgments preceded by commentary. The frozen transport concatenated those phases. The [offline attribution audit](phase-attribution.json) verifies that exact concatenation and identifies a harness phase-selection error; these records do not demonstrate final-answer schema noncompliance.

The separately labeled [phase-aware regrade](phase-audit.json) makes no model calls and changes no frozen scores. It raises Luna's full-cohort human delivered agreement from **63.71% to 64.36%** for the base rubric and **62.05% to 63.22%** with the checklist. Terra, Sol and archived-execution agreement are unchanged. The [phase-aware companion figure](judge-full-cohort-phase-aware-bounds.svg) and [its data](full-cohort-phase-aware-bounds.json) remain separate from the original transport results.

## What the references support

The human reference contains **164 historical votes on 64 held-out questions** from [LMSYS's published MT-Bench human judgments](https://huggingface.co/datasets/lmsys/mt_bench_human_judgments). No new participants were recruited. **23 of 64 panels were divided**; nominal Krippendorff alpha was **0.501**. A categorical predictor choosing each panel's most common vote could match at most **84.09%** of these retained votes on average. This is a property of the fixed panels, not a universal model capability ceiling.

The repository cohort contains **40 held-out pairs of archived patches with discordant published execution outcomes**. It measures a judge's discrimination between those patches, rather than fresh coding-agent repair success. Public-data familiarity, selected domains, historical labels and changing hosted model weights limit generalization. Exact-generator self-preference is unavailable in this cohort.

Terra's complete archived-execution checklist arm changed its canonical choice on **17.5%** of swapped-order pairs versus **7.5%** disagreement on same-order repeats. This is exploratory order sensitivity, not proof of directional position bias. [Order/repeat figure](judge-order-diagnostics.svg).

## Coverage, cost and reproduction

Across development and holdout, **3,104 of 3,120 scheduled judgments were attempted**. Two policy rejections quarantined **16 still-unattempted cells**; original 64-question human and 40-task execution holdout denominators remain intact. Four attempted errors remain visible: two policy rejections, one connection failure, and one output-token truncation. The first three have unknown billing.

The phase-one conservative total is **$13.34326225**: **$13.330139** from reported usage plus **$0.01312325** in retained full unknown reservations. This is not a verified invoice. [Per-arm token, cost and timing aggregates](operations.json) retain missing/unknown counts; wall-clock timings include local bookkeeping, concurrency and disk conditions and do not isolate provider speed.

A [separate audit implementation](independent-audit.json), using retained observations and standard-library arithmetic, reproduced mappings, scores, coverage, all paired contrasts, seeded intervals, order/repeat estimates and costs. Historical transport/recovery sources are preserved at [commit 94e2960](https://github.com/sumervaid/everval-ai/tree/94e2960258a7ad658d082d628fbc258ea983461b); published figures record their own publisher hashes. See [reproduction instructions](../../../scripts/judge_study/README.md), [frozen protocol](protocol.json), and [source revisions](sources.json).

The separate [50-task repository repair study](fresh-results.md) and its self-preference follow-up are now complete. The [consolidated budget](study-budget.json) accounts for all studies and the product probe; the amount above is phase one only.
