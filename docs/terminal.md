# Terminal workbench

Everval AI turns repository history into reviewable evaluation tasks. Its terminal interface uses the same local workspace, validation engine, and experiment records as the CLI and optional web application.

```sh
uv run everval tui
```

Connect a GitHub repository URL or a local Git checkout through **Connect repository**. You can also prefill the connection form when launching:

```sh
uv run everval tui --source https://github.com/owner/repository
```

Connection, discovery, validation, and release are separate steps. Connecting a repository does not establish that its changes are suitable evaluation tasks.

## Build your first benchmark

1. **Connect historical work.** Press **Ctrl+N**, enter a GitHub URL or local repository path, and connect. Private repositories require the existing local authentication supported by the repository connector; never paste credentials into a URL.
2. **Discover candidates.** In **Projects**, select a repository and choose **Discover**. Start with 20 changes. Use **Load older work** in the candidate ledger to continue from the saved cursor. The ledger keeps unsupported changes and rejection reasons visible alongside eligible drafts.
3. **Review the task.** Open the candidate ledger and select a candidate. **Review task** records a problem statement, reviewer label, and rationale. Describe what an agent could have known before the repair; exclude the later patch, private checks, and future outcomes.
4. **Validate the evidence.** Use **Prepare runtime** when dependencies are needed: inspect the plan, then approve preparation pinned to its source commit and base image. Choose **Validate**. A background operation keeps the interface responsive while the shared core verifies the reference and controls in an isolated runtime. Inspect the complete candidate record afterward. Infrastructure errors, missing dependencies, and zero-test runs do not count as successful validation.
5. **Freeze a release.** Choose **Create release** and name it. The backend admits only eligible reviewed and validated tasks, assigning stable development and holdout partitions. A holdout-only release has no development collection.
6. **Compare systems.** In **Releases**, select the release and choose **New experiment**. Explicitly select the intended release partition and systems, set repetitions and a provider budget, then start the run. Reserve holdout tasks for independent evaluation.
7. **Read the evidence.** Open **Experiments**, select a run, and inspect the comparison. **Trial evidence** opens individual attempts; **Full record** shows the complete output, checks, errors, and accounting.

A narrow accepted subset is not a representative sample of all enterprise work. Use the rejection ledger to understand what the current extractor and environment could not reconstruct.

## Navigate without a mouse

| Key | Action |
| --- | --- |
| F1 | Overview |
| F2 | Projects and candidate ledgers |
| F3 | Benchmark releases |
| F4 | Experiments and trial evidence |
| F5 | Systems |
| F6 | Recurring schedules |
| F7 | Audit trail |
| F8 | Workspace diagnostics |
| F9 | Resumable guided runs |
| F10 | Change-linked evaluations |
| Ctrl+P | Search the command palette, including Collections |
| Ctrl+N | Connect a repository |
| Ctrl+R | Refresh the current page |
| Ctrl+Q | Exit the interface |
| Tab / Shift+Tab | Move between controls |
| Arrow keys | Move through tables and choices |
| Enter | Open the selected table record or activate a control |
| Space | Toggle a system in the experiment selection list |
| Escape | Close a form, evidence view, or command palette |

Context actions appear below each table. Long rows and action bars scroll horizontally; full records open in a dedicated scrollable view. At 80×24 the navigation rail and side detail pane collapse to leave room for the ledger. The same pages remain accessible through function keys and the command palette. Larger terminals show evidence beside the selected row.

## Configure execution deliberately

For repositories that need pytest fixtures or installed dependencies, use **Projects → Environment** to register a prepared, locally available pytest image. The core records its immutable identity, explicit plugin modules, and bounded test deadline. Prepare and review that image separately; this form does not execute an installation script. Same-process repository tests provide weaker protection against deliberate grader manipulation than host-compared function cases.

**Systems → Add system** stores a model identifier, provider base URL, execution harness, step limit, explicit prices, and the name of an environment variable holding the API key. It never asks for the key itself. Define the variable in the process environment before launching the worker. Remote providers also require explicit service opt-in and a positive experiment budget. See [Systems and providers](providers.md).

Fixture systems are labeled controls. **Reference fixture · control** and **Empty submission · control** verify the pipeline; their results are not measurements of a model. Imported submissions are recorded harness evidence, and replaying them does not produce a new independent execution.

The comparison includes all scheduled attempts in its pass-rate denominator. Failed, incomplete, cancelled, and infrastructure-error attempts remain in the record. Missing cost is displayed as **unknown**, never as zero. Read the reported interval method and warnings before treating differences as meaningful.

Use **Export** to write JSON, CSV, or readable HTML to the local workspace. Exports can contain private evidence. **Compare runs** inspects cohort compatibility and differences. **Check gates** lets you declare thresholds and records a decision without changing trial evidence. Its default policy checks execution readiness, not model quality; see [Decision policies](policies.md).

## Reviews and recurring runs

From a finalized trial, **Add review** records a dimension, score from zero to one, rationale, and reviewer label. These legacy local labels are self-reported. For authenticated assignments, frozen rubrics, and blinded comparisons, use the [browser review desk](team-review.md). Reviews are append-only, and automated scores remain separate. **Review history** retains earlier judgments.

**Schedule** creates a recurrence from an experiment's frozen snapshot. The form defaults to **Paused** so you can inspect it before enabling. Each occurrence reserves the original experiment's provider budget against an explicit cumulative lifetime cap. The default cap funds one occurrence. Uncertain charges retain their reservations. Imported submission replays cannot be scheduled. Use **Schedules → Pause / enable** to change future admissions.

The interface displays durable execution state. A separate worker must be running to execute queued experiments and recurring schedules. Closing a view does not erase pending records. Follow the CLI launcher and [operations guide](operations.md) for the worker and tmux lifecycle.

## Local workspace boundaries

The terminal is a trusted local operator interface. Optional browser team mode provides authenticated roles and assigned review queues. The terminal interface talks directly to the configured Python workbench adapter; it does not require the web server. Data stays in the configured local data directory unless you explicitly connect a source, invoke a provider, or copy an export. There is no telemetry or automatic result upload.

Repository text and model output are displayed as data. Terminal control characters are removed from ordinary labels; complete JSON evidence escapes control characters. Repository hooks, install scripts, and candidate tests must never execute on the personal host. Unsupported execution policies fail closed in the shared backend.

The application is built with [Textual](https://textual.textualize.io/). Long operations use background workers, and meaningful keyboard workflows are verified with Textual's headless pilot tests.

## Guided and ongoing work

Start at **F9 Guided runs** for a resumable repository-to-first-evaluation workflow. Each stage reflects actual retained evidence; a selected record alone does not mark a run complete. **F10 Change runs** links a frozen cohort to explicit commit/configuration fingerprints and deduplicates repeated triggers. See the [guided walkthrough](guided-run.md) and [change evaluation guide](change-evaluation.md).

Experiment actions include failure triage, local regrading, economics, and a decision brief. Project health shows coverage denominators and gaps. Recurring schedules expose cumulative caps and retained digests.
