# Change-linked and recurring evaluation

Use a completed evaluation as a baseline for ongoing decisions. Execution stays on the local workspace worker. No GitHub status, check, comment or message is posted automatically.

## Link a change to evidence

On Experiments, select a completed run and choose **Link changes**. Choose the connected project, a configuration revision label and whether local trigger admission is enabled. **F10 · Change runs** lists these versioned baselines. **Poll and evaluate** reads the current local Git HEAD or the connected GitHub default branch's commit metadata, then queues an evaluation only when its fingerprint is new.

A trigger records:

- The exact commit, verified in the local repository or by read-only GitHub metadata.
- The reviewed configuration fingerprint and baseline version.
- The frozen baseline benchmark and original experiment fingerprint.
- The current saved configurations of the baseline's system IDs, frozen into the new experiment.

Revising a prompt or tool configuration in the saved System changes its fingerprint. The optional configuration label supplies provenance; it is not an executable command or a replacement for System configuration. Everval does not build or deploy agent code through this workflow. A repository commit alone cannot prove that a remote endpoint deployed that commit: pin and verify the adapter/deployment revision independently.

Identical commit, configuration, system and baseline fingerprints return the original run even after it finishes. Each baseline permits one outstanding evaluation. A prior run with uncertain provider billing blocks new admission. Pause or enable admission from Change runs; pausing does not cancel an already queued experiment.

## Run a local CI check

Run these commands on the iMac or the machine holding the workspace:

```sh
everval changes register "Agent quality" PROJECT_ID EXPERIMENT_ID --config-revision agent-v1
everval changes configure BASELINE_ID --enabled
everval changes trigger BASELINE_ID --commit FULL_COMMIT_SHA
everval changes history BASELINE_ID
```

To explicitly read GitHub's current default-branch commit, use `--poll` instead of a commit. Polling is a local invocation; this workflow does not install a cloud runner, webhook or background repository watcher.

```sh
everval changes check BASELINE_ID --poll --timeout 600 --min-pass-rate 0.9
```

The check starts or resumes the deduplicated evaluation, waits for its durable outcome, and records a policy decision. Exit code 0 requires the declared quality and execution gates to pass; failed or inconclusive gates return 1. Timeout returns 2 while the durable evaluation remains inspectable. Fixture controls are excluded from model-quality decisions. This command can be called by an authorized local CI runner without granting it a host build command.

## Cumulative recurring budgets

Schedule a frozen experiment from Experiments. Declare its interval and **cumulative schedule cap**. Weekly is 10,080 minutes. A new schedule without an explicit cap defaults to one original per-run budget. A zero-cost local run can recur under a zero-dollar cap.

Every occurrence atomically reserves its full maximum provider budget against that cap before its experiment is queued. A settled run releases unused reservation; its recorded committed amount remains charged. Uncertain billing retains at least the full reservation and prevents further admissions. The cap covers generated occurrences; it does not retroactively include the original baseline run or another schedule.

The budget is an admission control based on declared prices and retained billing uncertainty, not a provider invoice ceiling. Requests already sent may incur charges, and providers may bill differently. Existing schedules created before lifetime caps must be assigned a cap before another occurrence is admitted.

```sh
everval recurring budget SCHEDULE_ID
everval recurring budget SCHEDULE_ID --cap 25
everval schedules resume SCHEDULE_ID
```

Increasing a cap does not automatically enable a paused schedule. Pausing prevents future admissions; queued work remains in Experiments.

## Freeze a weekly digest

Select a schedule and choose **Weekly digest**, or run:

```sh
everval recurring digest SCHEDULE_ID --days 7
everval recurring digests SCHEDULE_ID
```

A digest freezes the observed window, baseline and occurrence fingerprints, newly failing/passing task-system pairs, incomplete outcomes, known spending and missing accounting, plus commands that open each experiment's evidence. It never changes when a pending run later completes. Create another digest to capture newer evidence.

Transitions compare graded outcomes. They do not establish that a code change caused the difference. Repetitions are grouped per task and system; a task passes only when its graded repetitions all pass, and incomplete repetitions remain incomplete. Unknown cost is not zero; known partial tool-loop spending remains a lower bound. To bound report work, a digest admits at most 500 occurrences in its window; shorten the requested window when needed.

Digests are local records created on request. No email, chat delivery or automatic publication is configured.
