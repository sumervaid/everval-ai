# Four decisions behind Everval's measurements

[Project](../README.md) · [Calibration methods](judge-methods.md) · [Current scope](PLAN.md)

The core design question is whether a reported change is evidence about the model, the measurement procedure, or the environment. These four boundaries make that distinction inspectable.

## 1. Admit tasks through controls

[Task admission](../src/cvals/benchmarks.py) reconstructs the starting revision and the reference revision in separate prepared environments. Hidden checks must fail on the starting revision and pass with the reference fix; regression checks must pass in both. Three fresh cycles must collect the same tests and agree on their outcomes. Zero tests, timeouts, dependency failures, inconsistent collections and flaky results quarantine a candidate.

This trades coverage for interpretable failures. A broken dependency is not evidence that a model failed the task. A passing original patch is necessary but does not prove that the issue statement is sufficiently clear, that every valid solution passes, or that hidden tests cover every incorrect solution. Human task review and rejection reasons therefore remain separate records.

Admission can also create selection bias: easier-to-reconstruct repositories enter more readily. Report the screened denominator, exclusions and repository mix with the admitted cohort. A small clean sample does not represent all enterprise engineering work.

## 2. Pair the unit that was sampled

[Experiment comparison](../src/cvals/comparison.py) checks collection, grader, harness and environment identity before pairing outcomes. Repeated attempts are averaged within the same task. Missing, interrupted and failed attempts remain in scheduled denominators.

The older [report intervals](../src/cvals/reports.py) use Wilson intervals for independent binary rates and bounded Hoeffding intervals for paired or repeated task means. Hoeffding needs bounded observations and an independence assumption, without estimating variance from a tiny sample. Its price is wide intervals. Four identical ties cannot establish equivalence; a naive bootstrap of those four zeros would misleadingly return zero width.

[Judge statistics](../src/cvals/judge_statistics.py) adds a family bootstrap for the richer calibration study, with frozen paired contrasts, multiplicity allocation, complete-cohort checks and a conservative bounded sensitivity interval. Degenerate or undersized bootstrap samples cannot certify precision. This is not a universal replacement for the older policy: inference depends on the endpoint and sampling design.

Unique task IDs do not establish independence. Shared repositories, templates and repeated benchmark exposure remain limitations. Prospective power calculations expose the effects a study can detect; they cannot manufacture information after a small study finishes.

## 3. Keep different definitions of quality separate

[Deterministic graders](../src/cvals/evaluators.py) verify declared contracts such as exact answers, numeric tolerance and executable checks. These are useful when correctness has an operational definition. A citation appearing verbatim does not establish that it supports a claim.

[Calibrated judges](../src/cvals/judge_calibration.py) measure an additional construct: preference or rubric-based quality against a declared reference. Human votes can disagree. Preserve those votes, report coverage and reliability, and measure model–human agreement without calling it ground truth accuracy.

Judges see both response orders and repeated judgments. A swap disagreement may reflect ordinary sampling noise; comparison with same-order repeats separates those diagnostics. Own-model preference is descriptive unless the design disentangles model identity from real response quality. A stronger-looking judge is not automatically a better measuring instrument.

## 4. Make blinding a data boundary

[Blind studies](../src/cvals/blind_studies.py) freeze assignments and rubrics, separate reviewer roles, randomize visible labels, and lock complete judgments before model identities are revealed. [Reference export](../src/cvals/judge_reference.py) checks those frozen records, preserves disagreement, and creates a private calibration bundle.

A login proves control of an account, not independent human participation. Human provenance therefore requires an explicit collection declaration. Scripted controls remain scripted. Historical public human labels are attributed to their original collection, not described as newly recruited Everval reviewers.

The model prompt is built from an allowlist of public task material and anonymous responses. Targets and generator identities stay with the evaluator. Workspace administrators remain trusted, answer wording may reveal identity, and hosted providers can see submitted content. These are the limits of the threat model.

## Read the implementation in order

Start with the admission validator and its rejected controls, then follow one paired comparison, one frozen judge request and one revealed reference export. The tests beside those modules exercise invalid evidence and state transitions as well as successful workflows. The [judge walkthrough](judge-calibration.md) provides a no-key control experiment; the [Inspect adapter](inspect.md) exposes the same evaluator boundary to an established evaluation tool.

These notes document the implementation's tradeoffs.
