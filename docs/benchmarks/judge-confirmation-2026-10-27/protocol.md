# Judge cost and quality confirmation

Registered before study inference. The [freeze record](freeze.json) identifies the sample, request grid, source revisions and analysis settings. The candidate, thresholds and comparison order were fixed before new outcomes were generated.

## Decision being tested

Can Everval qualify a cheaper judging configuration, prespecified by the researcher from earlier evidence, on a new human-rated dataset?

The candidate is GPT-6.1 Terra with the standard rubric. The first baseline is the same model with the additional checklist. The second is GPT-6.1 Sol with the standard rubric. The earlier 64-question study motivated this choice; none of those questions supply confirmation here.

A saving qualifies only when the candidate costs less and its agreement with the retained human votes meets the predeclared non-inferiority rule. A successful result supports the tested configurations and task population. It does not prove identical quality, universal model superiority, or customer return on investment.

## Fresh sample and reference

Use the validation split of [NVIDIA HelpSteer3-Preference](https://huggingface.co/datasets/nvidia/HelpSteer3/tree/f6d145777bcbde96137596340fab89793acd1031/preference), pinned to revision f6d145777bcbde96137596340fab89793acd1031, under CC-BY-4.0. It contains general-assistance, coding, STEM and multilingual response comparisons. See the [dataset paper](https://arxiv.org/abs/2505.11475v2).

The design selects 1,000 of 1,939 eligible first-user-prompt families using a frozen, label-independent hash order. Normalize the family key with Unicode NFKC, case folding and whitespace collapsing; retain the original full conversation and both responses unchanged. Duplicate rows and repeated family members do not become additional independent observations. Compare family keys against the original 80 MT-Bench questions and exclude overlaps. Record selection, exclusions and hashes.

Empty responses, blank historical messages and identical responses remain observed source behavior and receive flags. Keep individual preference scores and human explanations on the evaluator side. Map negative scores to canonical response A, positive scores to B, and zero to tie. Do not substitute the dataset's rounded overall score for individual votes.

The published dataset retains up to three of the original three-to-five annotations, preferentially keeping agreement. These are curated, retained human votes. Rater identities are unavailable, so no cross-question rater identity is fabricated. The dataset is new to Everval's experiment; possible inclusion in provider training is unknown.

## Configuration and blinding

All configurations use the Responses API at Standard service tier, no tools, and the same strict preference/rationale JSON contract. Terra uses reasoning effort none with a 768-token output ceiling. Sol uses low reasoning with a 1,536-token output ceiling, including reasoning tokens. These supported settings and the unchanged rubric/checklist match the earlier study. The frozen uncached rates are $1/$5 per million input/output tokens for Terra and $2/$10 for Sol.

Every configuration sees both A/B and B/A response orders: six scheduled judgments per family, 6,000 in total. Generator identity metadata, human votes and human explanations never enter the judge request. The original answer text can still contain identity clues. The complete grid uses Everval's existing frozen calibration protocol and append-only observation import.

The configurations have different reasoning/output budgets. Comparisons concern complete configurations, not a causal claim about model size or price alone. Retain returned model identifiers and request times; hosted weight identity is not independently attested.

## Primary quality measure

For each scheduled judgment, score the fraction of that family's retained human votes matching the judge's canonical preference. Average the two response-order scores within the family, then average families equally. Votes and repeated orders do not increase the independent sample size.

Attempted invalid responses, abstentions and provider errors receive zero delivered-agreement credit, with their separate classifications retained. Any unattempted scheduled judgment makes the automatic adoption decision inconclusive. Do not declare a winner from a convenient complete-case subset.

Define each paired family difference as candidate agreement minus baseline agreement. The prospective tolerance is **one percentage point**: at most one additional disagreement with a retained human vote per 100 equally weighted families on average. This is an engineering tolerance for this experiment, not a universal definition of acceptable quality.

Require both one-sided 95% lower bounds to exceed -0.01:

1. The fifth percentile of 20,000 whole-family paired bootstrap means, using seed 27102027.
2. The paired Student t lower confidence bound.

The conjunction is fixed in advance; the analysis cannot choose the friendlier interval afterward. Require at least 20 families with nonzero paired quality differences before estimating a quality pass. A [pre-run simulation](analysis-validation-v1.json) exposed unreliable rare-event behavior below that scale; withholding the claim is fixed before observing new outcomes. The [final simulation](analysis-validation-v2.json) retains its assumptions and Monte Carlo uncertainty. Degenerate variance does not produce automatic certainty. Publish sensitivity checks and assumptions behind inference to similar tasks.

## Ordered comparisons and cost gate

Test in this order:

1. **Rubric confirmation:** Terra standard versus Terra checklist.
2. **Premium-judge replacement:** Terra standard versus Sol standard.

The second comparison supports a confirmatory adoption claim only if the first passes all quality and cost conditions. Run all arms to the fixed grid regardless of interim scores. If the first fails, report the second descriptively without promoting it into a new primary success.

Require lower estimated candidate cost across the scheduled grid and require both the paired bootstrap and Student t one-sided 95% upper bounds for candidate-minus-baseline cost to be below zero. Use the same whole-family resampling approach and fixed seed as the quality comparison. Retain reported tokens and frozen rates. Candidate unknown charges use their full reserved upper bounds; baseline unknown charges cannot be treated as known savings. Keep estimates distinct from invoice-reconciled charges.

Report costs per judgment, per two-order comparison, and for the complete study. After a passing gate, project break-even future comparisons as full new calibration cost divided by measured saving per comparison. Include every arm, attempted failure and retained unknown reservation in calibration cost. Show the earlier research cost separately. Hardware, engineering and original human annotation costs are unmeasured.

## Power, budget and stopping

Fix the question count before inspecting new model outcomes. Planning uses explicitly labeled variance assumptions and the old exploratory study only. The old two-order slices had paired standard deviations 0.0772 and 0.0944, each with only three nonzero differences among 64 questions. They support a planning scenario, not a precise variance guarantee. At N=1,000 and paired standard deviation 0.10, normal-approximation power is approximately 93.5% at true zero difference, conditional on enough discordant families. [Full sensitivity](power.json) and [historical planning](historical-planning.json). Publish power sensitivity; a large API-call count does not establish adequate independent sample size.

There is no outcome-driven stopping, prompt revision, sample expansion or retry. Early stops are restricted to budget exhaustion, unresolved financial obligations, provider policy/refusal signals, or operational integrity failures. Preserve all scheduled denominators.

The cumulative authorized ceiling remains **$50**, including **$15.14292900** already accounted for earlier work. The new fixed suballocations are $16.00 for Terra and $18.80 for Sol, totaling $34.80 and leaving $0.057071 outside them. Forecasts use estimated input counts and historical output distributions; they do not guarantee that the full grid will fit if usage changes materially. Reserve before dispatch, preserve reservations after interruption, and never silently release unknown charges.

Disable prompt caching with explicit mode and no cache breakpoints. Use the [official Standard token rates](https://developers.openai.com/api/docs/pricing), retain usage details, and stop when usage contradicts the accounting model. [Caching semantics](https://developers.openai.com/api/docs/guides/prompt-caching).

## Diagnostics and publication

Publish every arm and the predeclared decision, including inconclusive or unfavorable outcomes. Secondary diagnostics cover answer-order reversals, validity, abstentions, per-domain agreement, human panel disagreement, observed request latency and cost coverage. Domain findings are descriptive; the pooled gate does not establish non-inferiority in every domain.

Publish the frozen protocol, provenance hashes, aggregate results, executable analysis, independent audit and a plain-language decision chart. Keep raw conversations, human references, provider responses, credentials and local workspaces outside Git. Record the final result and total accounting in the daily updates.
