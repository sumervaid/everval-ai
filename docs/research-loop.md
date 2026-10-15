# Investigate, test the benchmark, and select a candidate

The research loop turns retained observations into inspectable experiments. Investigations reuse recorded outputs and never call a model. Optimization is a separate, explicitly budgeted workflow that evaluates named live systems through the normal durable worker.

## Repeat a grade

```sh
everval research repeat EXPERIMENT_ID TRIAL_ID --repetitions 3
everval research show RESEARCH_ID
```

Everval regrades the exact stored output against its frozen task checks two to ten times. Each regrade remains a separate record; the original trial is unchanged. The summary distinguishes repeated agreement, observed grading disagreement, and incomplete grading caused by infrastructure errors.

This measures **grading intermittency**, not model nondeterminism. Repeating a model requires a new, separately budgeted experiment. Executable repository checks still use the existing isolated grader and pinned runtime contract; repeating those checks can take time.

## Reduce a failing output

```sh
everval research minimize EXPERIMENT_ID TRIAL_ID --max-evaluations 80
```

For JSON and text graders, Everval removes fields, list elements and text fragments while preserving every check's outcome and message. It rejects a reduction that merely replaces the observed failure with a missing-field failure. The artifact records the smaller output, its hash, the frozen scenario, each accepted reduction, and the evaluation count.

The search accepts 2–200 grading evaluations, bounds candidate inspection separately, and supports at most 64 KiB per scenario and output. Recursive deletion stops at twelve levels. A shared structural-work guard bounds pure grading and rejects expensive JSON Schema constructs such as composition, references and uniqueness checks. Reaching the cap is explicitly reported. A completed search establishes a local minimum under this deletion grammar; it does not prove a global minimum or identify the causal root cause.

Executable repository graders are excluded from reduction. No imported code runs on the host. Reproduction artifacts include private checks and must remain in the private workspace.

## Inspect the benchmark itself

```sh
everval research quality COLLECTION_ID --max-scenarios 100 --max-pairs 5000
```

Quality inspection first checks each supported scenario's reference output. It then tests an empty submission and deterministic mutations of declared check paths. It retains accepted and rejected probes, flags an accepted empty submission, and marks missing references, invalid positive controls, unsupported graders and grading errors separately.

There are at most 32 probes per scenario; omissions are reported. Outputs above 4 KiB are represented by their fingerprint and an explicit omission flag. These are **structural probes**, not certified semantic negative examples. An accepted mutation is a prompt for reviewer inspection, not automatic proof that the benchmark is invalid.

Duplicate evidence uses exact public task content and a lexical token-overlap threshold of 0.9. Names and imported IDs do not hide exact duplicate content. Lexical overlap is a review hint, not a semantic equivalence claim. Scenario and pair caps make coverage explicit.

All research records use an append-only sequence of fingerprinted states. `everval research list` lists retained runs. A synchronous operation interrupted by process termination can retain its last running state; a separately invoked investigation receives a new identity.

## Compare explicit candidate configurations

Start with separate collections whose manifests declare `benchmark_partition: dev` and `benchmark_partition: holdout`. Repository benchmark releases already supply these partitions; authored general-purpose collections can declare them too. Each collection needs distinct task identities and public content.

Configure candidate systems first. Use different saved system IDs for model, prompt, tool or adapter revisions. For agent-owned retry policies, connect separately versioned adapter configurations. The optimizer evaluates exactly these configurations; it does not generate prompts, deploy services or modify graders. An external adapter must actually implement its declared settings and return its frozen revision.

Inspect the allocation before admission:

```sh
everval optimize project DEV_COLLECTION HOLDOUT_COLLECTION \
  --system candidate-a --system candidate-b --budget 20
```

This reports configured admission bounds based on input sizes, prices and token/tool ceilings. The expected provider bill remains unknown. A study allocates **60% to development and 40% to the final holdout**. The projection shows whether all development attempts and the largest candidate holdout fit their phase caps. Unused allocation is not transferred.

Queue the study:

```sh
everval optimize create "Lower cost under our quality gate" DEV_COLLECTION HOLDOUT_COLLECTION \
  --system candidate-a --system candidate-b --budget 20 \
  --min-pass-rate 0.95 --max-p95-ms 30000 --repetitions 2
everval optimize show STUDY_ID
```

A study accepts one to ten named live HTTP systems, with one to ten repetitions. Fixture controls and recorded submissions are excluded from candidate selection. The collections, candidate configurations, thresholds, prices, repetitions and budget allocations freeze at creation.

The worker progresses through two finite stages:

1. **Development:** evaluate all candidates on the frozen development cohort. Select the lowest observed cost per passing attempt among candidates meeting the fixed pass-rate and p95-duration constraints. Complete grading, complete accounting and duration coverage are required. Ties use pass rate, duration and stable system identity.
2. **Final holdout:** evaluate only the selected frozen configuration. The same fixed quality and latency thresholds apply. A failed holdout ends the study; Everval does not try the next candidate on that holdout.

No candidate qualifies when cost is unknown. Invalid evidence, uncertain billing, or costs above a reserved phase or lifetime cap block further admission. Workspace and project spending limits remain in force. A provider's reported charge can exceed its configured reservation; Everval retains that charge and stops additional study admission. Configured rates are accounting assumptions, not independently reconciled invoices.

## Protect the final holdout

A study rejects holdout tasks that have appeared in any prior workspace experiment, including cancelled runs, fixtures and replays. It also rejects development/holdout overlap.

Admission reserves holdout task IDs and exact public task content. Normal experiment creation, replay import, reruns, schedules and other optimizer studies cannot evaluate those tasks through a renamed collection. The one authorized final experiment consumes the reservation. Cancelled and failed studies retain their reservations. Study views derive accounting from retained trials, including charges that settle after cancellation; immutable decision records keep their original evidence.

These controls apply to supported experiment admission paths. They cannot detect paraphrased duplicates, undisclosed training exposure or copies in external services. Operators with filesystem access can inspect private evidence; the workspace is not an adversarial secrecy boundary.

## Review the proposal

```sh
everval optimize proposal STUDY_ID
everval optimize cancel STUDY_ID
```

A passing final holdout creates a reviewable JSON proposal containing the frozen system configuration, constraints, study budget, evidence fingerprints and experiment IDs. It contains no task content and performs no deployment. Save this output only where its configuration and service endpoints may be disclosed.

Passing means that the configuration met observed thresholds on this reserved cohort. It does not establish statistical superiority, production representativeness or a guaranteed future saving. Review coverage, uncertainty, failure modes and prices before accepting the configuration.

`everval optimize advance STUDY_ID` supports explicit local progression when the worker is not ticking. It queues normal durable experiments and never executes provider calls itself.
