# Judge calibration research

This study asks whether a short, explicit rubric improves agreement with human
reviewers, and whether judgments remain stable when answer order or the sampled
judge response changes. It also audits patch judgments against archived test
outcomes. These are separate experiments with separate reference standards.

## Frozen questions

- **Human references:** one answer pair for each of 80 MT-Bench questions, with
  every retained historical expert vote preserved. Sixteen questions are used for
  development and 64 remain held out. The primary score gives equal weight to
  questions and equal weight to retained reviewers within each question. A tied
  or divided human panel is not silently converted into agreement.
- **Execution references:** 50 SWE-bench Verified tasks with one archived passing
  patch and one archived failing patch. Admission checks the published report
  against all required test identifiers. Ten tasks are development and 40 held
  out. This deliberately selected disagreement cohort measures judge
  discrimination; it does not estimate a coding agent's pass rate.
- **Judges:** three OpenAI models, each with a standard rubric and a rubric
  checklist, both response orders, and two repetitions: 3,120 scheduled calls.
  The checklist contains no human votes, winning-answer labels, or test outcomes.
- **Primary contrasts:** rubric-guided minus standard within each of the three
  judge models. All six configurations are selected before any held-out calls.
  Question/task families, not repeated calls, determine effective sample size.

Human agreement is fractional, so power planning uses a paired mean sensitivity
analysis at predeclared difference standard deviations of 0.10, 0.25, and 0.50.
Exact McNemar planning applies only to a separately specified paired binary
endpoint. Repeated orders and repetitions do not create more independent tasks.

The current judges did not generate the historical MT-Bench answers.
Same-snapshot self-preference cannot be inferred from that cohort.
Provider-family preference is not a valid substitute.

## Reproduce on a trusted host

The preparation program downloads public inputs but does not call models or
execute repository code. Run from a source checkout. The optional data
dependencies are separate from the installed Everval package.

~~~sh
uv venv .local/judge-data-venv
uv pip install --python .local/judge-data-venv/bin/python -r scripts/judge_study/requirements.txt
.local/judge-data-venv/bin/python -m scripts.judge_study.prepare \
  --output .local/my-judge-study --cohort all
uv run python -m scripts.judge_study.driver \
  --root .local/my-judge-study freeze
~~~

Inspect the frozen protocols, public-source digests, exact request bodies,
model prices, output ceilings, and independent sample sizes before a paid run.
Supply OPENAI_API_KEY through your private secret mechanism. Never commit the
key or the .local directory.

~~~sh
uv run python -m scripts.judge_study.driver \
  --root .local/my-judge-study run --phase dev --smoke
uv run python -m scripts.judge_study.driver \
  --root .local/my-judge-study run --phase dev
uv run python -m scripts.judge_study.driver \
  --root .local/my-judge-study release
uv run python -m scripts.judge_study.driver \
  --root .local/my-judge-study run --phase holdout
uv run python -m scripts.judge_study.driver \
  --root .local/my-judge-study status
~~~

The transport is specific to this dated study, with a $30 allocation: $2 for
Luna, $8 for Terra, and $20 for Sol. Input spending is conservatively booked at
2.5 times the dated standard input price; output uses the standard price. These
amounts are upper-bound accounting, not an OpenAI invoice. Pricing must be
reviewed before a new study. No Modal spending occurs in this experiment.

Every request receives a durable reservation before dispatch. A call is never
automatically retried. Missing usage keeps its reservation and blocks future
admissions, including after restart. Reservations include byte-based input
bounds, framing allowance, and the complete output-token ceiling.

## Bounded parallel transport

The original serial driver remains reproducible. The operational amendment permits four simultaneous requests per model
(twelve total) and separately declares the secondary endpoint described below.
It freezes its source hashes and the already completed result prefix, leaving
prompts, job identities, samples, prices, and the original primary analysis unchanged.

Create an amendment only when no requests are pending, then use the parallel
transport:

~~~sh
uv run python -m scripts.judge_study.parallel \
  --root .local/my-judge-study amend
uv run python -m scripts.judge_study.parallel \
  --root .local/my-judge-study run --phase holdout
~~~

SIGINT, SIGTERM, or a file named STOP inside the private study directory
halts new dispatch and waits for already reserved requests. Remove the stop
file only when intentionally resuming. Process-owned leases permit concurrent
reservations; a restart cannot adopt a pending lease. SQLite serializes the
monetary admission check. An unknown response blocks new calls while existing
reservations remain charged.

## Evidence and attribution

Raw answer text, individual reviewer records, source patches, hidden test
identifiers, request bodies, and provider replies stay in the private workspace.
Publish aggregate results, protocol hashes, provenance, and limitations after
review. Never present archived execution outcomes as fresh execution or
scripted references as human judgments.

- [MT-Bench human judgments](https://huggingface.co/datasets/lmsys/mt_bench_human_judgments),
  revision f7d2896d2cc5d80f8b55c2bbc722613555233c25, CC BY 4.0.
- Zheng et al., [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685).
- [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified),
  revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a.
- [SWE-bench experiment records](https://github.com/SWE-bench/experiments),
  commit 40f164d5b8f1d249bf95a6df8b74b577fd8e519d.
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing),
  reviewed for the dated protocol.

Public upstream availability does not grant a new license to underlying
repository patches. The scripts download source artifacts for local analysis
and retain provenance; they do not redistribute a bundled task corpus.

## Delivered agreement as a secondary endpoint

Development calls produced invalid or abstained judgments. The original primary
requires a complete grid of valid categorical judgments and is retained without
changing its missingness rule. Before any held-out calls, a separate secondary
endpoint was specified: average agreement over every scheduled order and
repetition, with an observed abstention, invalid response, or delivery error
counting as zero delivered agreement. Unattempted or missing calls still block
inferential intervals. This measures successful delivery of a useful judgment;
it is not agreement conditional on a valid answer.

Coverage and original-primary results remain visible. The three secondary
paired contrasts use alpha 0.05/3; the six descriptive configuration intervals
use alpha 0.05/6. This is an explicitly separate exploratory family, not a claim
of combined primary-plus-secondary familywise control. The amendment records
the development-driven reason and timestamp. Invalid JSON is never repaired or
retried for this study.

The offline analyzer verifies the frozen sources and accounting, imports
retained observations, and writes full private reports plus an aggregate
publication file. It refuses to call an incomplete request grid final unless
partial analysis is explicitly requested.

~~~sh
uv run python -m scripts.judge_study.analyze --root .local/my-judge-study
~~~

The aggregate file omits per-item scores and reviewer identifiers. Publication
still requires reviewing its claims and confirming that each figure names the
reference standard and independent family count.

## Dated policy interruption and accounting review

The live study received one HTTP 400 policy rejection without reported token
usage. The rejected request was never retried or rewritten. Its entire
$0.0036065 reservation remains charged against the original cap; no zero-cost
billing claim is inferred. Every still-unattempted judgment of that source
question is quarantined across all configurations.

The dated recovery program is bound to that exact request, response digest,
reservation, ledger event prefix, source files, and an immutable list of
untouched unrelated jobs. It is not a general automatic retry mechanism. The
original ledger row, request, response, and completed results remain unchanged.
A new unknown request blocks continuation again.

The original human cohort therefore remains incomplete. Original inference
rules are retained, and any 63-question exclusion analysis is explicitly post
hoc. The execution-reference cohort is unchanged. The aggregate report
distinguishes an observed policy error, unattempted quarantined jobs, settled
conservative charges, and the retained unknown-charge bound.

An independent raw-response audit also found that two development records
became invalid when the frozen research transport concatenated commentary and
final-answer text. Their final-answer JSON was valid. This is a transport
extraction limitation, not demonstrated model failure to produce final JSON.
The original records remain unchanged. The live continuation preserves the
frozen extraction behavior; any later parser sensitivity must be separately
labeled.
