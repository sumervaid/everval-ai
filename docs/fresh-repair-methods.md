# Fresh repository repair study

This protocol measures whether a model can produce a correct patch from a public issue and a bounded excerpt of the starting repository. The required cohort is **50 control-admitted tasks**, evaluated once per model. The completed result artifact determines actual coverage; the protocol alone does not establish a result.

The [control runner](../scripts/judge_study/fresh_repositories.py) and [dispatch adapter](../scripts/judge_study/fresh_dispatch.py) separate task admission, model requests and execution grading. Human-judge calibration and archived patch discrimination are separate studies.

## Admit tasks before calling models

The source is [SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/tree/78f471bf655a3137b2e8a75af1501690ec009ec3), pinned to revision `78f471bf655a3137b2e8a75af1501690ec009ec3`. Its parquet SHA-256 is `030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`. The task base commits, issues and reference patches match the older source revision used for the archived study; evaluation scripts and required-test labels follow the enriched revision.

Each admitted task must satisfy three complete cycles in separate containers:

1. Reset to the exact starting commit. Every required failing test must fail, every required regression test must pass, and the outer test suite must exit with code 1.
2. Apply the original reference patch. Every required test must pass, and the outer suite must exit with code 0.
3. Collect the same test identities across all base and reference runs. Missing checks, required skips, ambiguous identities and setup failures exclude the task.

A prospective [Django identity policy](../scripts/judge_study/fresh_identity.py) removes only a bare test method with exactly one dotted-class counterpart when both raw statuses are FAILED. Conflicting, ambiguous and nonfailed aliases remain inadmissible; docstrings and missing required labels are never reconstructed. Affected Django admissions require three newly executed cycles after the policy freeze. Earlier exclusions and raw evidence remain in the history.

The official SymPy parser sometimes reports both a bare test name and an identical qualified failure-summary alias. The runner removes only a unique exact alias with the same status. It never invents a missing required label. Pytest's own tests can intentionally run failing child suites; their printed failure rows do not override the outer suite's exit status.

Candidate order is frozen before model generation. The selected cohort is the first 50 strict admissions in that order. A prefix proof requires a terminal control record for every earlier candidate, preventing asynchronous worker completion from choosing the cohort. Screening exclusions and preparation amendments remain in the evidence.

Some Django candidates are excluded because the configured execution PATH does not expose locale-gen or because the upstream parser does not recover exact test identities from multiline unittest descriptions. Those failures remain exclusions in the frozen cohort; required labels and setup checks are not relaxed. Reproduction improvements use separately recorded environments and fresh controls.

The study uses locally pinned native images with explicit environment substitutions. Results are conditional on those images. A different environment requires fresh admission checks. The [native preparation recipes and artifact locks](../scripts/judge_study/environments/README.md) expose the inherited base and Django preparation steps, including the remaining operating-system reproducibility boundary.

## Keep the original fix away from the solver

Every model receives the complete public issue and the same deterministic lexical excerpts from tracked Python files at the starting commit. The issue must fit the frozen 4,500-byte limit; longer issues are excluded before evaluation. Test files, reference patches, hidden checks, later discussion and Git history are absent from the request.

The model has one request, no tools, no test feedback and a 2,048-token output limit. It returns a JSON object containing a unified patch. The source context is bounded and may omit relevant code. This is a constrained one-shot repair experiment; its scores describe that configuration.

The grader applies the submitted patch in a fresh isolated container and runs the frozen checks. Tests, dependency declarations and evaluation configuration cannot be changed by the submitted patch.

## Preserve financial and execution evidence

The three model ceilings sum to **$12**:

| Model | Ceiling |
| --- | ---: |
| `gpt-6-luna` | $0.50 |
| `gpt-6.1-terra` | $4.00 |
| `gpt-6.1-sol` | $7.50 |

Reservations use conservative input bounds, 2.5 times the frozen standard input price, and the maximum output allowance. Reported usage-based costs are conservative estimates rather than an invoice reconciliation. This study authorizes no Modal spending; local hardware and electricity costs are unmeasured.

Dispatch is serial, with one frozen job per child process. A process-level lock spans the parent and child. Every model ledger is checked before the next admission. Filtering a child to one job preserves the full campaign's financial identity.

Duplicate JSON keys are rejected in both the provider envelope and patch object. Bounded, redacted raw response bytes remain private even when parsing fails. Unknown accounting or provider errors halt further admissions. A policy rejection or refusal also quarantines that source from subsequent provider work. Recorded requests are never automatically retried.

Repository execution and artifacts stay local. OpenAI receives the public issue and selected starting-source excerpts. Request elapsed time includes local evidence-recording and accounting overhead.

## Interpret the outcome

A task is resolved only when all required checks are observed and pass, with an outer suite exit of zero. Report these categories separately:

- Verified resolution.
- Fully observed unresolved execution.
- Invalid or absent patch submission.
- Incomplete coverage or infrastructure failure, where execution is unknown.

Keep all 50 planned tasks visible. Show unknown coverage and sensitivity bounds instead of turning missing execution into an observed failed repair. Repeated controls and multiple models do not increase the independent task count.

The prospective paired-power calculation assumes 50 independent task pairs only as a sensitivity analysis. It allocates familywise alpha across three contrasts and states the assumed discordance rate. Repository clustering, public-task contamination, environment substitutions and bounded retrieval limit generalization. Three control cycles cannot establish that a task will never be flaky.

The [frozen analyzer](../scripts/judge_study/fresh_analysis.py) treats a submitted empty, invalid or unapplicable patch as an end-to-end failure while reporting it separately from an executed failing repair. An accounted provider refusal or policy rejection is a separate observed end-to-end failure and stops further source exposure. Unattempted requests and unrelated provider failures remain unknown. Analysis verifies these terminal records without invoking the dispatcher or modifying its stop state. Full-cohort bounds set unknown outcomes to zero and one; these are identification bounds, not confidence intervals.

The three fixed model contrasts report right-minus-left success differences on the same 50 tasks. An [exact conditional McNemar calculation](https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html) is available only when every paired end-to-end outcome is known, with Bonferroni allocation across three contrasts. Complete individual model outcomes also receive [exact Clopper–Pearson intervals](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html), allocating alpha 0.05 separately across three model proportions. These are two separately declared sensitivity families, not a joint six-statement guarantee.

Both calculations assume independent task outcomes; they do not establish generalization across repositories. Per-repository results and leave-one-repository-out difference ranges show descriptive sensitivity to repository composition. No bootstrap interval is reported. Zero observed successes still produce a nonzero upper binomial limit when the independent-task sensitivity calculation is applicable.

## Reproduce a frozen campaign

Start with the retained source, environment and selection manifests described in the [research runner documentation](../scripts/judge_study/README.md). Keep hidden task material and raw provider responses outside the checkout.

After all ordered admission controls finish:

```bash
python -m scripts.judge_study.fresh_dispatch freeze --root "$EVERVAL_REPAIR_STUDY_ROOT"
python -m scripts.judge_study.fresh_analysis freeze --root "$EVERVAL_REPAIR_STUDY_ROOT"
```

For this research campaign, the independent review and the separate outcome-independent self-preference selection must be frozen before the first generation request. Then execute the reviewed requests and grade their patches:

```bash
python -m scripts.judge_study.fresh_dispatch dispatch --root "$EVERVAL_REPAIR_STUDY_ROOT"
python -m scripts.judge_study.fresh_repositories grade --root "$EVERVAL_REPAIR_STUDY_ROOT"
```

Runtime migrations retain original evidence, verify exact image identities and compare supplemental base/reference smoke checks. A mismatch halts resumption. The manifests record operational changes without rewriting earlier outcomes.
