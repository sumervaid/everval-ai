# Structured-output specialization and retention: prospective protocol

Status: methodology and training configuration frozen before study training and
benchmark outcomes. The executed protocol is the hashed machine-readable copy
retained with the study artifacts. Final evaluation/analysis source hashes are
separately frozen, linked to this protocol, before the first benchmark response.
Amendments after either freeze must be append-only and clearly labeled.

## Research question and scope

Does supervised specialization of a small instruction model change its behavior
on explicitly defined structured tasks, and how much evaluation time can a
smaller monitoring suite save while preserving the full suite's observed changes?

This is an actual open-weight model training experiment on one local machine.
The task records are generated, fictional diagnostic examples. They are not
customer data, a sample of enterprise workloads, or evidence of frontier-model
performance. Findings apply to the declared generator populations and trained
model/checkpoints, subject to the limitations below.

## Model, training and lineage

Use `Qwen/Qwen2.5-0.5B-Instruct` at upstream revision
`7ae557604adf67be50417f59c2c2f167def9a775`. The upstream safetensors SHA-256 is
`fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe`.
The upstream model is released under Apache-2.0 and has approximately 0.49 billion
parameters. Preserve its notices and model card with the downloaded artifact.
[Official model card](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct).

Convert the pinned model locally to MLX four-bit weights. Retain conversion
parameters, converted-file hashes, tokenizer/chat-template hashes, dependencies,
trainer code hash, optimizer configuration, training examples and their hashes.
Train low-rank adapters using supervised examples from routing and grounded
extraction only. Arithmetic and untrusted-instruction resilience are retention
measurements. No policy change or reward optimization is inferred from this SFT
procedure. [Official MLX fine-tuning documentation](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md).

Training seeds are **17, 29 and 43**. All start from the same converted base.
Freeze optimizer updates and checkpoints at **0, 80, 160 and 320**. Step 0 is the actual initialized adapter: random low-rank A matrices and zero B
matrices, functionally equal to the converted base. Its bytes are retained and
hashed, without artificial hash salting. Later steps load the adapter saved at
that exact update. No checkpoint is relabeled, interpolated, selected for a
favorable result, or replaced with another independently released model.

A short feasibility pilot may verify loading, memory limits, a training update,
and generation using separate pilot records. It must not inspect any study
measurement record. The fixed training configuration is batch size 4, Adam learning rate 0.0001,
sequence length 512, prompt-masked loss, and rank-8 adapters on q_proj/v_proj in
the last four layers, scale 16 and dropout 0. Generation is greedy (temperature
0), at most 32 new tokens and 512 input tokens, with the dataset system prompt.
Pin all runtime versions and converted-weight hashes before the first study outcome. The pilot may resolve hardware failure; it must
not optimize the final benchmark. Its artifacts remain distinct from study runs.

Do not change the training schedule in response to evaluation outcomes. Training
for the three seeds may finish before evaluation because it does not use the
measurement cohorts. Treat run/seed as a replication unit; two validation seeds
do not justify a population-level variance claim about arbitrary training runs.

## Measurement population and independence

Use four narrow domains:

| Domain | Intended behavior | Training exposure |
| --- | --- | --- |
| Support routing | Apply an explicit routing rule to a fictional support request. | Supervised specialization. |
| Grounded extraction | Return the requested field from provided content; use `UNKNOWN` when absent. | Supervised specialization. |
| Arithmetic totals | Compute the requested total from explicitly supplied line items. | Retention only. |
| Untrusted-instruction resilience | Extract the grounded field while ignoring embedded conflicting instructions. | Retention only. |

For each domain, create **32 underlying fictional records**, with **two prompt
paraphrases** for each record. Group both paraphrases under the same task-family
ID. A paraphrase does not create a second independent family. These 32 records are
not 32 independent prompt templates or attack types. Each checkpoint
therefore has 256 scheduled generations: 4 domains × 32 families × 2 paraphrases.

Seed 17 uses the development cohort. Seeds 29 and 43 share a distinct validation
cohort. These two seeds reuse validation families and therefore do **not** create
64 independent families per domain. Training, development and validation records
must have disjoint record IDs and complete prompt bytes; reject exact collisions.
The task vocabulary, rules, request patterns and prompt templates are intentionally
shared. Routing includes the same small set of request phrases and labels across
splits; no unseen-intent or unseen-template generalization is claimed. Generation seeds,
record generators, prompt templates, answer rules and grader code are frozen.

Family independence is an assumption for records independently sampled from the
specified generator, conditional on the frozen training/checkpoint. Shared prompt
and generator limitations can create systematic errors; the 32 records do not
represent 32 independent enterprise populations. Report per-domain, per-seed
outcomes and preserve both paraphrase results so that these limits are inspectable.

Use deterministic generation settings and one scheduled response per prompt.
Token exhaustion is retained as a model response and graded under the frozen
rules; execution errors are missing outcomes. Report JSON syntax validity, object/answer-contract validity, exact answer fidelity
conditional on a valid JSON object, and the primary strict grade separately with
their denominators. This distinguishes format changes from answer changes without
claiming human semantic calibration. No silent retry, answer repair,
model-judged scoring, or post-outcome grader adjustment is allowed. Positive and
negative grader controls precede study execution. Human semantic calibration is
not manufactured; its absence stays explicit.

## Prospective evaluation order

1. Freeze the dataset, training/runtime declarations, outcome graders and this
   analysis protocol. Retain byte hashes and timestamps outside the source tree.
2. Evaluate seed 17 at steps **0, 80 and 160** on development families.
3. Use only those outcomes and measured generation times to freeze the reduced
   suite and all comparison baselines. Retain the selection file hash.
4. Only after selection is frozen, evaluate seed 17 at step **320**, and seeds
   **29 and 43** at all four steps on validation families.
5. Run the fixed analysis. Keep every scheduled row, including errors and null
   accounting. Report any deviation before interpreting results.

The exact full grid is **3,072 scheduled generations**: three seeds × four
checkpoints × 256 prompts. Baseline generations are actually executed for each
seed/cohort and retained. Their repeated use does not make distinct independent
baseline models. Primary suite validation uses only seeds 29/43 at step 320 versus
their own step-0 baseline. Other trajectory measurements are secondary.

## Paired checkpoint comparisons

For each seed, each later checkpoint, and each domain, compare to that seed's
step-0 baseline on the same family/paraphrase identities. Average paraphrases
within each family, then families equally. Report exact grades, family count,
coverage, means, paired difference, interval, and decision.

Use Everval's paired-family Hoeffding method with total study error budget 0.05,
allocated across three seeds, three checkpoint looks and four domains:

```text
per-run alpha = 0.05 / 3
planned looks = 3
planned domains = 4
per-comparison alpha = 0.05 / 36
practical difference threshold = 0.10
minimum independent families = 5
```

With 32 families, the untruncated interval radius is approximately **0.674**.
This conservative design is not powered to certify small changes. Report
inconclusive results plainly; do not substitute a different confidence procedure
or independence unit to obtain significance. The aggregate score is descriptive,
not a separate additional confirmatory test. See the
[measurement methods](../../docs/posttraining-methods.md).

Checkpoint selection and training are frozen independently of measurement
outcomes. This protects the stated prospective design; it cannot demonstrate
independence or representativeness outside the declared generated population.
No causal attribution to a particular training example follows from a change.
As a dependence sensitivity, also cluster all records using the same fixed surface
form together. Two such clusters cannot establish a reliable population claim;
retain this inconclusive sensitivity beside the record-family interval. All
empirical interpretation remains exploratory outside the fixed generated task
population.

## Evaluation-time mix experiment

There are eight candidate evaluations: one per domain/paraphrase combination.
Each evaluation contains the full set of 32 underlying records in its domain.
Selection uses only seed-17 step-80 and step-160 deltas against step 0. The full
suite's domain delta averages its two evaluation means.

Use **measured client provider-call wall seconds** (`provider_ms / 1000`) as the
resource unit: this includes adapter/RPC overhead, tokenization, prompt processing
and generation, and excludes model loading, training, orchestration and grading.
The supplementary model-runtime timer includes tokenization/prefill/decode after
its lock is acquired, but excludes queue wait and HTTP. Do not mix the timers. The development cost of an evaluation is its
mean total generation time across steps 80 and 160. The common reduced-suite
ceiling is **70% of the full suite's development mean generation time**.
All four domains require at least one selected evaluation. Unknown or nonpositive
development time or incomplete development grades prevents selection.

The greedy strategy begins with the cheapest evaluation in each domain. Add the
remaining evaluation that gives the greatest development-loss decrease per
second while respecting the ceiling; stop if no addition reduces loss. The loss
is mean absolute domain-delta error plus disagreement about crossing a negative
0.10 threshold. Stable evaluation-ID ordering breaks exact ties. If the cheapest
coverage set exceeds the ceiling, report infeasibility rather than changing it.

Enumerate every evaluation subset satisfying the same domain coverage and time
ceiling. Generate **32 fixed-seed random baseline draws** from the feasible sets,
and a separate domain-stratified baseline restricted to one evaluation per domain.
Retain all feasible subsets, draws and seeds. Repeated draws are algorithm
replicates, not additional independent checkpoints. Actual costs may differ even
under the same ceiling; report each one instead of claiming exact cost matching.

Freeze this selection before any validation outcome. On seeds 29/43, step 320,
report domain-delta estimation error, observed negative threshold crossings,
detected/missed crossings, false alerts, denominator coverage, and runtime for
each strategy. There are only **eight primary validation cells** (two seeds ×
four domains). Recall is null if the full reference has no negative crossings.
Show counts and every cell, not just percentages. The full reference is itself
a measurement, not ground truth. Threshold crossings in this analysis are
**descriptive**, not statistically confirmed regression alerts.

Runtime savings are counterfactual estimates from replaying a subset of retained
measurements: the executed study actually pays the full suite's runtime. They
exclude model loading, training, orchestration, human review and unrelated host
work. Measure and report those overheads separately when available. Report
held-out runtime overruns relative to development forecasts; do not silently
rescale a frozen suite using validation timings.

## Budget, disclosure and stopping

Hosted-provider API fees for this experiment are zero. Local hardware, energy,
opportunity cost and development-service bills are not measured as provider fees;
unknown expenses remain unknown. Never place seconds into a field labeled USD.
No paid fallback, remote inference, or new cloud resources are part of this study.

Stop execution on memory pressure, corrupt artifacts, unavailable storage, or an
explicit user stop. Preserve completed rows and unfinished denominators. Resume
only the same frozen grid with explicit attempt provenance; do not overwrite
failed attempts or select a favorable repeat. Any sample-size, model, training,
or scoring change after outcome inspection is an amendment or new exploratory
study, not the original preregistered experiment.

Publish code, permitted generated tasks, provenance, aggregate and per-cell
measurements, exclusions, negative results, failure explanations and limitations.
Weights, private machine details and credentials stay outside Git. A successful
execution establishes a reproducible small-model measurement study on one machine;
it does not establish enterprise representativeness, frontier safety, production
fleet scale, or superiority over another evaluation product.
