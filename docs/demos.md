# See Everval in motion

[Project](../README.md) · [Install from source](quickstart.md#install-from-source) · [Still images](demo-stills.md)

Continuous recordings of the actual browser and terminal. Watch pointer movement, typed text, scrolling, live navigation and results. Each recording follows one session without internal cuts. Full-resolution MP4s accompany the GIFs.

All runs use original installation controls or explicitly synthetic data. The recordings demonstrate product workflows; they do not measure model quality or latency. Current-source features include functionality added after npm 0.3.1.

## One request, one complete session

![Continuous session from a typed benchmark request through results and rankings](assets/demos/continuous/everval-tour.gif)

[Full-resolution MP4](assets/demos/continuous/everval-tour.mp4) · [Still image](assets/demos/continuous/everval-tour.png)

## Explore the workflows

[Terminal workbench](#terminal-workbench) · [Benchmark chat](#chat-and-reports) · [Repository onboarding](#repository-onboarding) · [Tasks and comparisons](#tasks-and-comparisons) · [Rankings, cost and latency](#rankings) · [Continuous checks and budgets](#autopilot-budget) · [Failure investigation](#failure-research) · [Production cases and team review](#production-cases) · [Checkpoint monitoring](#checkpoint-monitoring) · [Double-blind review](#double-blind)

<a id="terminal-workbench"></a>

## Terminal workbench

A real terminal process responds to typed command-palette searches, navigation and selection changes.

![Terminal workbench: continuous recording](assets/demos/continuous/terminal-workbench.gif)

[Full-resolution MP4](assets/demos/continuous/terminal-workbench.mp4) · [Still image](assets/demos/continuous/terminal-workbench.png) · [Workflow guide](terminal.md)

<a id="chat-and-reports"></a>

## Benchmark chat

Type a recurring benchmark request, start the real fixture run, inspect its reports and pause future runs.

![Benchmark chat: continuous recording](assets/demos/continuous/chat-and-reports.gif)

[Full-resolution MP4](assets/demos/continuous/chat-and-reports.mp4) · [Still image](assets/demos/continuous/chat-and-reports.png) · [Workflow guide](chat.md)

<a id="repository-onboarding"></a>

## Repository onboarding

Type through connection and discovery for an original local Git repository. Review and isolated validation remain pending; this is not a completed repository benchmark.

![Repository onboarding: continuous recording](assets/demos/continuous/repository-onboarding.gif)

[Full-resolution MP4](assets/demos/continuous/repository-onboarding.mp4) · [Still image](assets/demos/continuous/repository-onboarding.png) · [Workflow guide](guided-run.md)

<a id="tasks-and-comparisons"></a>

## Tasks and comparisons

Type a task contract, keep evaluator checks separate, validate controls and inspect both comparison outcomes.

![Tasks and comparisons: continuous recording](assets/demos/continuous/tasks-and-comparisons.gif)

[Full-resolution MP4](assets/demos/continuous/tasks-and-comparisons.mp4) · [Still image](assets/demos/continuous/tasks-and-comparisons.png) · [Workflow guide](scenarios.md)

<a id="rankings"></a>

## Rankings, cost and latency

Move through the benchmark matrix, switch metrics, filter configurations and inspect the underlying evidence. Free controls do not establish real model cost or latency.

![Rankings, cost and latency: continuous recording](assets/demos/continuous/rankings.gif)

[Full-resolution MP4](assets/demos/continuous/rankings.mp4) · [Still image](assets/demos/continuous/rankings.png) · [Workflow guide](rankings.md)

<a id="autopilot-budget"></a>

## Continuous checks and budgets

Use the terminal to inspect a bounded policy, spending commitments and its retained observations. The recorded policy is left paused.

![Continuous checks and budgets: continuous recording](assets/demos/continuous/autopilot-budget.gif)

[Full-resolution MP4](assets/demos/continuous/autopilot-budget.mp4) · [Still image](assets/demos/continuous/autopilot-budget.png) · [Workflow guide](autopilot.md)

<a id="failure-research"></a>

## Failure investigation

Navigate failed attempts and saved evidence through the actual terminal, then inspect investigation and grader-quality records.

![Failure investigation: continuous recording](assets/demos/continuous/failure-research.gif)

[Full-resolution MP4](assets/demos/continuous/failure-research.mp4) · [Still image](assets/demos/continuous/failure-research.png) · [Workflow guide](research-loop.md)

<a id="production-cases"></a>

## Production cases and team review

An approver inspects an incident previously staged by another identity, types expected behavior, validates the control and publishes a collection.

![Production cases and team review: continuous recording](assets/demos/continuous/production-cases.gif)

[Full-resolution MP4](assets/demos/continuous/production-cases.mp4) · [Still image](assets/demos/continuous/production-cases.png) · [Workflow guide](production-cases.md)

<a id="checkpoint-monitoring"></a>

## Checkpoint monitoring

Move through checkpoint trajectories, domain selection, coverage, investigations and qualification records. These are explicitly synthetic controls.

![Checkpoint monitoring: continuous recording](assets/demos/continuous/checkpoint-monitoring.gif)

[Full-resolution MP4](assets/demos/continuous/checkpoint-monitoring.mp4) · [Still image](assets/demos/continuous/checkpoint-monitoring.png) · [Workflow guide](post-training.md)

<a id="double-blind"></a>

## Double-blind review

The coordinator begins with completed reviews, locks the judgments, types a release reason, reveals identities and opens the report. This is trusted-custodian review.

![Double-blind review: continuous recording](assets/demos/continuous/double-blind.gif)

[Full-resolution MP4](assets/demos/continuous/double-blind.mp4) · [Still image](assets/demos/continuous/double-blind.png) · [Workflow guide](double-blind.md)

### The reviewer's session

A separate continuous recording follows an anonymous reviewer through the responses, scores, rationale and submission. It is recorded independently from the coordinator's session; these are scripted demonstration accounts.

![Continuous anonymous review and judgment submission](assets/demos/continuous/blind-reviewer.gif)

[Full-resolution MP4](assets/demos/continuous/blind-reviewer.mp4) · [Still image](assets/demos/continuous/blind-reviewer.png)

## Capture and verification

The browser captures are recorded at 25 frames per second. Terminal captures stream a real PTY into a local terminal renderer. The published GIFs and MP4s preserve continuous recorded motion; only the beginning and end are trimmed. No model API calls are made, and raw workspaces and credentials stay private.

[Capture recipes](../scripts/demos/README.md) · [Media metadata](assets/demos/continuous/manifest.json) · [Verification](evidence/2026-10-26-motion/README.md).
