# Autopilot: bounded continuous evaluation

Autopilot watches configured systems and selected repository evidence, runs a small frozen development cohort, and escalates to a larger frozen cohort when its rule warrants it. The existing local worker executes both stages. Closing the terminal does not cancel queued work.

Policies start paused. Provider calls still require the normal remote-provider opt-in, configured prices, per-experiment budget, and workspace/project spending admission. Creating a policy does not make a provider call.

## Start with two frozen baselines

Use a completed development experiment containing at least two scenarios. Prepare a smaller screening baseline with the same systems and repetition count:

```sh
everval autopilot screen FULL_EXPERIMENT_ID --size 2
```

The command queues a new experiment; keep the worker running with `everval worker`, or open `everval tmux`. After the screening experiment completes, register the policy:

```sh
everval autopilot create "Agent quality" FULL_EXPERIMENT_ID SCREENING_EXPERIMENT_ID \
  --cap 20 --interval 60 --trigger both --escalate regression
everval autopilot budget POLICY_ID --days 30
everval autopilot configure POLICY_ID --enabled
```

The factory supports curated, unreleased development collections. For historical repository tasks, prepare a separately validated development release containing the screening subset. Released collections are never sliced to bypass admission. Every screening scenario must exactly match a member of the full cohort, including checks and reference material. Both baselines require completed grading and known accounting, identical frozen system configurations, and identical repetition counts.

The screen factory selects a deterministic subset by scenario hash. This provides reproducibility, not a claim that the sample represents the full workload. Choose screening tasks deliberately when coverage matters. Small screens can miss regressions.

## What causes a run

| Setting | Behavior |
| --- | --- |
| `--trigger periodic` | Evaluate at the configured interval; missed intervals coalesce into one occurrence. |
| `--trigger change` | Evaluate when saved system configuration, policy configuration, or observed repository evidence changes. |
| `--trigger both` | Evaluate on either condition, with one outstanding occurrence per policy. |
| `--escalate regression` | Run the full cohort when screening finds a baseline passing task/system/repetition that now fails. |
| `--escalate change` | Escalate on a changed configuration/source observation or a screening regression. |
| `--escalate always` | Run both stages whenever screening completes with usable grading. |

Use `--project-id PROJECT_ID` to observe a connected repository. Local repositories use bounded, hook-disabled Git plumbing to inspect `HEAD`. GitHub projects use their locally retained merged-change candidate ledger. Add `--discover` to queue bounded read-only candidate discovery at the policy interval. Discovery retains proposed candidates; it never reviews an issue statement, accepts expected behavior, validates a task, or changes a frozen benchmark automatically. The most recent 20 changes are inspected per discovery job; use explicit paginated discovery for backlogs.

A policy configuration revision can represent a reviewed prompt or dataset configuration change:

```sh
everval autopilot configure POLICY_ID --config-revision retrieval-v3
```

This revision is provenance. It does not deploy a service or modify an agent. Saved system configurations are loaded and frozen when the occurrence starts; both stages use those exact configurations even if an operator changes the saved system midway through the cycle. The task cohorts remain frozen from the source experiments.

External adapters must return the revision pinned in the frozen system configuration. A mismatch fails the trial. This verifies the response's declared revision; it is not an independent software deployment attestation. A watched Git commit does not prove which software an endpoint is running. GitHub ledger observation is not a live branch-head monitor.

## Budget projection and admission

Every occurrence reserves **screening budget + full budget** atomically before queueing the first stage. This avoids admitting an inexpensive screen that has no remaining allowance for its promised escalation. Known completed charges replace stage reservations; a screen that does not escalate releases the unused full allowance. Missing accounting retains the full cycle allowance and halts the policy. The source baseline runs are outside the policy's lifetime cap.

For example, a $0.25 screening cap and a $2 full cap reserve $2.25 per occurrence. Hourly execution over 30 days has a maximum scheduled projection of 720 cycles × $2.25 = $1,620 if every cycle escalates and consumes its cap. A $20 lifetime policy cap could fully reserve eight such cycles before any unused amounts settle. Actual known settlements can permit more cycles. These are declared ceilings, not measured prices or predicted invoices; change-trigger frequency is unknown.

```sh
everval autopilot budget POLICY_ID --days 30
everval autopilot configure POLICY_ID --cap 30
```

The report separates accounted costs from retained charges/reservations. Workspace and project limits additionally apply when the engine admits each paid request. No Autopilot allowance overrides those limits. Provider accounting uses configured rates and reported usage; it excludes third-party tool, hosting and development-service bills.

## Investigation and evidence

After a cycle finalizes, the controller can investigate up to three distinct failed task/system pairs using pure JSON/text graders. It repeats grading of the recorded output twice and performs at most 20 deletion-based reduction evaluations. Research records and their IDs remain linked to the occurrence; original trials are unchanged.

This work makes no model request and starts no Docker runtime. It checks grading repeatability and reduces a failing recorded output; it does not reproduce agent nondeterminism, minimize the original task input, prove a causal root cause, or produce a repair. Code graders, absent outputs, large records, expensive JSON-schema constructs, work beyond the deterministic pure-grading limits, infrastructure errors, and failures outside the bounded inspection window are skipped. Claims commit before execution. A crash leaves an interrupted investigation visible instead of silently repeating it.

Inspect the policy, occurrences, and investigation links through the shared API/workbench. The operator CLI provides:

```sh
everval autopilot list
everval autopilot show POLICY_ID
everval experiments show EXPERIMENT_ID
```

Autopilot writes no GitHub comments, statuses, messages, or uploads. Holdout and combined benchmark partitions are excluded; tasks marked holdout are also rejected. Development feedback does not redefine the held-out benchmark.

## Pause, recovery, and limits

```sh
everval autopilot configure POLICY_ID --paused
```

Pausing prevents new occurrences and new full-stage admissions. An already queued or running experiment retains its normal lifecycle; cancel it explicitly if necessary. A completed screening stage waits with its full-stage reservation until the policy resumes. Incomplete stages, admission failures, infrastructure errors, and unknown billing halt the policy with a retained reason. Inspect that evidence before resuming; unresolved accounting prevents resumption.

The worker observes at most ten due policies per heartbeat, with at least 60 seconds between observations of a policy. It supports up to 100 policies and bounds GitHub candidate-ledger observation to 5,000 retained candidates per project. Slow or busy workers delay observation; this is local periodic evaluation, not a real-time monitoring service. Existing recurring schedules and explicit change runs remain available. Avoid configuring overlapping controllers for the same purpose unless the additional evaluations are intentional.
