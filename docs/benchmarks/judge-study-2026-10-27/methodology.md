# A frozen study of judge agreement and rubric effects

[Project](../../../README.md) · [Protocol metadata](protocol.json) · [Sources and selection](sources.json) · [Reproduction](../../../scripts/judge_study/README.md)

## Question

Does adding an explicit public checklist improve a model judge's agreement with historical human judgments? The three primary comparisons are rubric-guided minus standard judging within GPT-6 Luna, GPT-6.1 Terra and GPT-6.1 Sol. Model-to-model comparisons and response-order diagnostics are secondary.

The local protocol was frozen at 2026-10-27 17:31:52 UTC, before any paid inference. The [request manifest](request-manifest.jsonl) identifies every scheduled request by a content hash. This is a retained local pre-inference freeze, not an externally registered preregistration. Throughput amendments must preserve the original jobs, prompts, partitions, prices and analysis.

## Two different references

| Cohort | Question answered | Development / holdout |
| --- | --- | --- |
| Historical MT-Bench expert judgments | Does the judge choose the responses preferred by individual human reviewers? | 16 / 64 questions |
| Archived SWE-bench Verified patch pairs | Can the judge identify which recorded patch passed the published execution checks? | 10 / 40 issues |

MT-Bench comes from [LMSYS's published human-judgment dataset](https://huggingface.co/datasets/lmsys/mt_bench_human_judgments), pinned to the revision and SHA-256 in sources.json. It is CC-BY-4.0; credit belongs to the original collectors. No new human participants were recruited.

One first-turn, content-consistent response pair with at least two distinct reviewer identifiers is selected per question by a deterministic hash independent of vote outcomes. The same reviewer contributes at most one consistent vote to that pair; contradictory duplicate votes remain missing. Human disagreements are preserved. The primary endpoint averages agreement with individual votes, weights reviewers equally within a question, then weights questions equally. A unique-plurality reference is secondary; a tied plurality stays unlabeled.

The repository cohort uses pinned [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) metadata and [official archived submissions](https://github.com/SWE-bench/experiments). It selects issues with discordant recorded outcomes from GPT-5, GPT-5.1 and GPT-5.2 submissions. Both patch application and complete required-test coverage must be present in the archived report; missing reports or inconsistent outcomes are exclusions. The published test reports are reference evidence, not fresh Everval executions. The judge sees the public issue and anonymous patch text, without the tests, recorded outcome or reference solution.

Selecting discordant patches creates a discrimination test. It cannot estimate real-world repair success, the prevalence of correct patches, or a complete SWE-bench score.

## Controlled judging

Each of six judge configurations evaluates both A/B and B/A display order twice: 24 judgments per item and 3,120 scheduled judgments overall. Responses are normalized back to their canonical identities before analysis.

Both arms receive the same public task, anonymous responses and base rubric. The rubric-guided arm additionally receives a checklist about explicit constraints, visible correctness, unsupported claims and appropriate abstention. It receives no gold answer or target preference. Prompts request A, B, tie or abstain plus a rationale. Strict structured outputs constrain the response format.

Models run through OpenAI Responses with Standard service tier. Luna and Terra use no reasoning and at most 768 output tokens; Sol uses low reasoning and at most 1,536. Those settings define the tested configurations and prevent a general claim about each model's maximum capability. Returned model identities, usage, timing and failures are retained. No automatic retries are permitted.

All six configurations are selected for holdout in advance. Development measures compatibility and supports exploratory inspection; it does not select a favorable winner or redefine the endpoint. Holdout remains behind the product's explicit release gate.

## Statistical plan

The independent unit is a question or repository issue, not a repeated API call. Judgments are averaged over orders and repeats within that unit. The human primary endpoint is fractional, so its power plan uses a paired-mean normal approximation instead of binary McNemar power.

For 64 held-out questions and three planned contrasts sharing familywise alpha 0.05, assumed paired-difference standard deviations of 0.10, 0.25 and 0.50 imply approximately 4.04, 10.11 and 20.22 percentage-point minimum detectable effects at 80% power. These prospective sensitivities are not observed uncertainty or guarantees.

Primary contrasts use paired family resampling, 5,000 resamples and frozen seed 27102026. Each contrast receives alpha 0.05/3. Complete coverage, sufficient family count and nondegenerate resampling are checked before an interval is reported. A bounded Hoeffding interval remains a conservative sensitivity analysis. Methods and undefined cases are documented in [judge methods](../../judge-methods.md).

Secondary diagnostics include human–human alpha, judge agreement and kappa, confusion and abstention rates, repeated-order disagreement, swap inconsistency and directional position effect. Swapping a response can change a noisy judgment without establishing position bias, so same-order repeats provide a comparison.

The historical response generators do not match the current judge model snapshots. Exact-model self-preference is therefore unavailable in this cohort. A provider-family match would not establish self-preference. The implementation supports the diagnostic when exact identities and suitable references exist.

## Costs and limits

The complete work has a $50 ceiling. This judge runner has a separate $30 ceiling, split into $2 for Luna, $8 for Terra and $20 for Sol. The remaining $20 is reserved for a separately frozen fresh repository experiment.

Each call reserves a conservative maximum before dispatch. Accounting charges all input tokens at 2.5 times the standard uncached input rate and output at the standard rate to cover possible cache-write billing. The resulting number is an upper-bound estimate from reported usage, not a verified invoice. Unknown usage blocks new admissions and leaves its reservation intact. Failed or incomplete calls remain visible.

This is a diagnostic study on familiar public data, with historical responses and a selected repository cohort. Benchmark contamination, imperfect human labels, shared task sources and domain selection limit generalization. Human agreement does not establish correctness, safety or suitability for a particular deployment. Any fresh repair experiment is reported separately.
