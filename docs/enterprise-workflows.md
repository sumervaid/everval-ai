# Work with an enterprise team

[Documentation](index.md) · [Team access and review](team-review.md) · [Operations](operations.md)

Everval gives engineering, evaluation and decision owners a shared record of what was tested and why a change was accepted. One person can fill several responsibilities; the handoffs below keep the evidence understandable as more people join.

The supported deployment is one trusted local workspace. Optional team mode authenticates browser and API users as administrators, operators, reviewers or viewers. Terminal, CLI and filesystem access remain trusted administrative surfaces. See [team access](team-review.md) for exact permissions.

## Engineering owner: choose relevant work

Connect an authorized repository and inspect discovered candidates. Favor changes with independently described, stable expected behavior. Reject tasks whose setup, lineage or scope cannot be reconstructed. Retain exclusions so reviewers can see which work the benchmark does not cover.

**Handoff:** a candidate ledger and reviewed problem statements. Start with the [guided repository workflow](guided-run.md). A high score on an unusually easy subset does not establish broad engineering capability.

## Evaluation owner: make the benchmark trustworthy

Prepare and pin the environment, validate candidates repeatedly and freeze an immutable release. Use the development partition to refine prompts and harnesses. Reserve the holdout for independent decisions; shared files or activity outside Everval can still contaminate it.

**Handoff:** a frozen cohort, successful controls, exclusions and the intended decision criteria. Read [benchmark validation](benchmarks.md) and [methodology](METHODOLOGY.md).

## Platform owner: control execution and spend

Configure model endpoints, credential variable names, actual prices, harness limits and per-run budgets. Keep the dedicated worker healthy and verify backup and recovery procedures.

Schedules reuse frozen snapshots and reserve each occurrence against an explicit cumulative lifetime cap; the default funds one occurrence. Workspace and project caps can also constrain daily, monthly and lifetime spending. Unsettled reservations count toward admission, and uncertain charges prevent further automatic spending.

**Handoff:** an execution configuration and a budget with clear assumptions. Read [budget planning](budget-planning.md), [Autopilot](autopilot.md) and [operations](operations.md). Pausing future admissions does not cancel already queued work.

## Reviewer: assess the answer independently

Use assigned browser review queues with frozen rubrics and optional blinded comparisons. Reviewer accounts can access their assigned items; the review endpoint withholds private graders, reference answers, traces and system metadata. Answer content can still disclose its source, so prepare neutral review material.

Authenticated judgments are append-only. Automated results remain separate from human assessments. Repository issue-review labels and legacy local review labels remain self-reported.

**Handoff:** judgments with rationale and explicit review coverage. Follow [team review](team-review.md).

## Decision owner: adopt, reject or gather more evidence

Read failures and incomplete attempts alongside pass rates, cost and latency. Apply a versioned policy with thresholds appropriate to the workload. Missing evidence can leave a decision inconclusive.

An operator records **Adopt**, **Reject** or **Gather more evidence** against the current review brief. Viewers can read summary reports and decision briefs without generic access to raw tasks, outputs or graders.

**Handoff:** an evidence-bound decision and the next evaluation trigger. See [decision policies](policies.md), [result interpretation](results.md) and [change-linked evaluations](change-evaluation.md).

## Operating boundary

Keep private graders, source material and detailed exports within their permitted audience. The audit chain is locally controlled; it is not independently anchored. Team mode does not provide SSO, tenant isolation or restricted CLI sessions.

Everval retains evidence and reviewable proposals. It does not deploy a selected configuration automatically. Use your existing release process to act on the decision.
