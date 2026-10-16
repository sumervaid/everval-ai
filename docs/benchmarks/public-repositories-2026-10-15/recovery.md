# Bounded accounting recovery

This is an explicit **post-outcome accounting amendment** to the [public repository pilot](methodology.md). The initial comparison stopped after an inspected HTTP 400 output-limit response omitted usage. The earlier outcomes and [partial report](results.md) remain part of the record.

**Verification status:** 423 benchmark-runner tests passed, including a complete recovery lifecycle using retained fixture artifacts and SQLite: amendment, individual review, continuation and report export. The amendment has not yet been applied to this pilot's real ledger, and no amended paid run has started.

## What the amendment permits

The original plan, model, solver prompts, review checkpoint, task and test bytes, images, prices, limits and execution order stay unchanged. A separate manifest records the reviewed accounting and reporting source changes; exports distinguish original frozen source hashes from runtime hashes.

For this stopped pilot, continuation is limited to the **11 originally unstarted attempts**. Prior start records, results, provider evidence and ledger history remain bound to the amendment. No prior attempt is retried, replaced or erased.

The interrupted request keeps its **full reservation permanently in the budget commitment**. Its usage remains unknown. Review does not settle the charge, release the reservation, create token counts or make `cost_complete` true. Known spending remains a lower bound.

This recovery supports only the inspected HTTP 400 output-limit error with no usage, a terminal unsubmitted attempt, retained request/response evidence and confirmed container cleanup. Other missing-usage cases remain blocked.

## Review and continue explicitly

Run from the repository root using `benchmark_dir` and `benchmark_python` from the [reproduction recipe](../../../scripts/benchmarks/README.md). The amendment must stay inside the existing private campaign directory.

```sh
recovery_amendment="$benchmark_dir/recovery/amendment.json"

"$benchmark_python" -m scripts.benchmarks.recovery \
  --plan "$benchmark_dir/campaign.json" --amendment "$recovery_amendment" create

"$benchmark_python" -m scripts.benchmarks.recovery \
  --plan "$benchmark_dir/campaign.json" --amendment "$recovery_amendment" inspect
```

Creation binds the original plan, evidence, ledger and allowable attempts. Inspection is read-only. Neither command makes model calls. Review the retained evidence, then replace `REQUEST_ID` below with the exact interrupted request identifier:

```sh
"$benchmark_python" -m scripts.benchmarks.recovery \
  --plan "$benchmark_dir/campaign.json" --amendment "$recovery_amendment" \
  review --request-id REQUEST_ID

"$benchmark_python" -m scripts.benchmarks.campaign \
  --plan "$benchmark_dir/campaign.json" --phase test \
  --amendment "$recovery_amendment"
```

Review appends a decision tied to the retained evidence and quarantines the interrupted attempt. The second command inspects the continuation plan without paid execution. To admit the remaining attempts:

```sh
"$benchmark_python" -m scripts.benchmarks.campaign \
  --plan "$benchmark_dir/campaign.json" --phase test \
  --amendment "$recovery_amendment" --execute
```

**Every new unknown request stops execution again.** It requires a separate explicit `review --request-id ...` decision meeting the same narrow evidence checks. Review never authorizes retrying that attempt. Keep the original amendment and ledger; no limit or cohort change is part of this procedure.

After execution stops and the campaign is idle, export to a new file:

```sh
"$benchmark_python" -m scripts.benchmarks.report \
  --plan "$benchmark_dir/campaign.json" --amendment "$recovery_amendment" \
  --output "$benchmark_dir/recovery-summary.json"
```

## Read the recovered report

Schema 2 retains the fixed selected-task denominators and original outcomes. It reports amendment provenance, reviewed and unreviewed uncertainty, quarantined attempts, known spending and the budget commitment.

`complete`, `completed_pairs` and `comparison_final` describe terminal scheduling coverage. Quality wins, losses and ties partition `quality_comparable_pairs`: both arms must have a graded result with every required test observed. Other terminal pairs are `unevaluable_pairs`.

The original ungraded Flask 4045 baseline cannot yield a review-only quality win. It also keeps `quality_comparison_complete` false even if every remaining attempt finishes. Completing the schedule does not complete cost accounting or establish an improvement. The pilot's small cohort, public-task exposure, single attempts and native ARM limitations remain unchanged.
