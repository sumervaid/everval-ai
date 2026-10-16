# Operating a workspace

Use the [on-premises how-to](on-premises.md) for enterprise installation, service templates, internal inference and operational handover.

## Deployment model

The supported default is one local workspace, one API process, and one durable worker. SQLite uses WAL, explicit transactions, a worker lease, and immutable experiment snapshots. Keep the database on a local filesystem.

Start the terminal workspace with:

```sh
uv run everval tmux
```

This starts a dedicated worker and opens the terminal plus an activity window. Detach with Ctrl+B, D; reattach by rerunning the command. The session settings apply only to this workspace, not your global tmux configuration.

The optional web/API service can share that worker:

```sh
uv run everval serve --no-worker
```

For separate API and worker processes:

```sh
uv run everval serve --no-worker
uv run everval worker
```

Do not assume multiple API workers provide additional execution throughput. The worker lease intentionally limits concurrent execution.

## Access

The server binds to loopback by default. Use an SSH tunnel for remote access:

```sh
ssh -N -L 8765:127.0.0.1:8765 YOUR_HOST
```

Then open http://127.0.0.1:8765 locally.

Binding beyond loopback requires a workspace token or enabled team authentication. Explicit hostnames must be added to `EVERVAL_ALLOWED_HOSTS`. Direct access and SSH tunnels are supported. The server disables proxy-header trust and checks the exact request origin, including scheme. A TLS-terminating proxy requires an explicitly designed and tested origin configuration; this release does not supply one. The browser stores its access token in session storage only.

The service rejects cross-origin mutations and unexpected Host headers, caps upload bodies, and applies restrictive response headers. A legacy workspace token grants workspace-wide access. For shared browser use, bootstrap individual identities with `everval team bootstrap --name "Administrator"`; team mode then requires personal tokens and enforces roles. It does not provide SSO. See [team access](team-review.md).

## Storage

`EVERVAL_DATA_DIR` (or compatibility alias `CVALS_DATA_DIR`) contains the database and artifact directory. Private task definitions, recorded outputs, and model traces belong here rather than in Git.

The workspace rejects an absent macOS mounted-volume path and refuses to initialize with less than 100 MiB free. Container admission imposes its own limits. Keep additional operational headroom for SQLite, logs, and the host system.

## Backups

```sh
uv run everval backup --output /safe/local/path/workspace-backup.sqlite3
```

The command creates a consistent SQLite backup and refuses to overwrite an existing destination. Retain the artifact directory alongside it when external artifacts are in use.

To restore, stop the API and worker, preserve the current workspace, and restore the backup into a new data directory as `workspace.sqlite3`. Copy matching artifacts. Start with `CVALS_DATA_DIR` pointing to that new directory and run `cvals serve --no-worker`. Verify collection fingerprints and recorded results. Inspect and pause restored schedules, and review queued work before starting a worker: a backup cannot know which provider calls or schedule occurrences happened after it was taken.

## Recovery and spending

A worker heartbeat protects its lease. After interruption, the next worker preserves interrupted attempts and their evidence. Ambiguous paid requests are not automatically retried. A rerun is an explicit new experiment.

Never share provider keys through task inputs, traces, Git, or exported reports. Each system refers to an environment variable; restart the service after changing its environment.

## Scope

This release provides a local evaluation application. Distributed queues, organization isolation, identity-provider integration, and regulated deployment certification are not claimed. Review the threat model and test results for your workload before exposing it as a shared service.

## Recurring evaluations

Create a schedule from a recorded experiment. Every occurrence reuses that experiment's frozen collection, systems, repetition count, and **per-run** budget. A cumulative lifetime cap reserves every occurrence before admission; the default funds one occurrence. Configure it explicitly for longer operation, and pause the schedule to stop future occurrences.

```sh
uv run everval schedules create EXPERIMENT_ID --name "Daily checks" --interval-minutes 1440 --lifetime-budget 25 --paused
uv run everval schedules resume SCHEDULE_ID
uv run everval schedules list
uv run everval schedules pause SCHEDULE_ID
```

The web service or standalone worker must remain running. The scheduler coalesces missed intervals, permits one outstanding occurrence per schedule, and disables further runs when admission fails or a previous run has uncertain billing. Recorded submissions cannot be scheduled as fresh evidence. Pausing a schedule leaves already queued work intact; cancel that experiment separately when needed.

## Human review

Legacy local reviews are append-only judgments with a self-reported reviewer label. Use [assigned browser reviews](team-review.md) for authenticated identities, frozen rubrics, and blinded comparisons. They retain prior revisions and an evidence fingerprint, and never replace automated outcomes.

```sh
uv run everval reviews add EXPERIMENT_ID TRIAL_ID --reviewer "Reviewer" --dimension clarity --score 0.8 --rationale "Cites the relevant source but omits one qualification."
uv run everval reviews list EXPERIMENT_ID
```

Only finalized trials accept reviews. Full JSON experiment exports include the review history.

## Repository jobs

Discovery, validation, and environment preparation use a durable queue. Inspect it with `everval jobs list` and `everval jobs show JOB_ID`. A terminal closing does not cancel a worker job. An expired job lease marks the job interrupted; retry explicitly. Preparation records the reviewed commit and base image so history changes cannot silently change a queued dependency plan.

The dedicated worker writes `worker.log` under the data directory and holds a workspace file lock. A second dedicated worker refuses to start. Provider environment variables belong to that process; restart it deliberately after credential or provider-policy changes.

Repository mirrors live under the workspace `projects` directory. Back up required mirrors and artifact files with the database when preserving a complete offline workspace.

## Audit and decisions

```sh
uv run everval audit list
uv run everval audit verify
uv run everval policies decisions EXPERIMENT_ID
```

Audit records form an append-only local hash chain. Retain the reported head digest independently, then verify with `--expected-head DIGEST` against the same recorded boundary. A later legitimate append changes the head; retain dated exports when tracking successive boundaries. The local administrator controls both database and software, so the chain alone is not an external trust anchor.

Model traces, private task bundles, full JSON exports, and audit details may contain proprietary source or human labels. Apply your organization's storage and retention rules. The product does not automatically upload them.

## Local verification and CI gates

Run `make check` and `make test` on the maintained machine. No hosted GitHub workflow is enabled by this repository. The `everval gate` command is suitable for a separately configured CI runner: passed exits zero; failed or inconclusive exits nonzero and retains the decision locally.

## Cumulative cost and feedback

Use `everval spending set --daily 25 --monthly 200` to replace workspace caps, or add `--scope project:PROJECT_ID` for a repository cap. Values are illustrative. Omitted periods become unlimited. Existing known charges count toward caps; uncertain attempts retain their reservations. API administrators can manage the same limits in the browser. See [cost accounting](cost-latency.md).

Change-linked checks and weekly schedule digests remain local. GitHub polling is explicit and read-only; there are no automatic statuses or outbound notifications. [Production cases](production-cases.md) remain staged until an operator has authored and approved expected behavior and publication has validated the controls.
