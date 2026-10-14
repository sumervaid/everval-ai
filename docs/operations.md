# Operating a workspace

## Deployment model

The supported default is one local workspace, one API process, and one durable worker. SQLite uses WAL, explicit transactions, a worker lease, and immutable experiment snapshots. Keep the database on a local filesystem.

Start with:

```sh
uv run cvals serve
```

For separate API and worker processes:

```sh
uv run cvals serve --no-worker
uv run cvals worker
```

Do not assume multiple API workers provide additional execution throughput. The worker lease intentionally limits concurrent execution.

## Access

The server binds to loopback by default. Use an SSH tunnel for remote access:

```sh
ssh -N -L 8765:127.0.0.1:8765 YOUR_HOST
```

Then open http://127.0.0.1:8765 locally.

Binding beyond loopback requires `CVALS_TOKEN`. Explicit hostnames must be added to `CVALS_ALLOWED_HOSTS`. Direct access and SSH tunnels are supported. The server disables proxy-header trust and checks the exact request origin, including scheme. A TLS-terminating proxy requires an explicitly designed and tested origin configuration; this release does not supply one. The browser stores its access token in session storage only.

The service rejects cross-origin mutations and unexpected Host headers, caps upload bodies, and applies restrictive response headers. A workspace token grants workspace-wide access; it is not a substitute for multi-user roles or SSO.

## Storage

`CVALS_DATA_DIR` contains the database and artifact directory. Private task definitions, recorded outputs, and model traces belong here rather than in Git.

The workspace rejects an absent macOS mounted-volume path and refuses to initialize with less than 100 MiB free. Container admission imposes its own limits. Keep additional operational headroom for SQLite, logs, and the host system.

## Backups

```sh
uv run cvals backup --output /safe/local/path/workspace-backup.sqlite3
```

The command creates a consistent SQLite backup and refuses to overwrite an existing destination. Retain the artifact directory alongside it when external artifacts are in use.

To restore, stop the API and worker, preserve the current workspace, and restore the backup into a new data directory as `workspace.sqlite3`. Copy matching artifacts. Start with `CVALS_DATA_DIR` pointing to that new directory and run `cvals serve --no-worker`. Verify collection fingerprints and recorded results. Inspect and pause restored schedules, and review queued work before starting a worker: a backup cannot know which provider calls or schedule occurrences happened after it was taken.

## Recovery and spending

A worker heartbeat protects its lease. After interruption, the next worker preserves interrupted attempts and their evidence. Ambiguous paid requests are not automatically retried. A rerun is an explicit new experiment.

Never share provider keys through task inputs, traces, Git, or exported reports. Each system refers to an environment variable; restart the service after changing its environment.

## Scope

This release provides a local evaluation application. Distributed queues, organization isolation, identity-provider integration, and regulated deployment certification are not claimed. Review the threat model and test results for your workload before exposing it as a shared service.

## Recurring evaluations

Create a schedule from a recorded experiment. Every occurrence reuses that experiment's frozen collection, systems, repetition count, and **per-run** budget. There is no cumulative schedule spending cap; pause the schedule to stop future occurrences.

```sh
uv run cvals schedules create EXPERIMENT_ID --name "Daily checks" --interval-minutes 1440 --paused
uv run cvals schedules resume SCHEDULE_ID
uv run cvals schedules list
uv run cvals schedules pause SCHEDULE_ID
```

The web service or standalone worker must remain running. The scheduler coalesces missed intervals, permits one outstanding occurrence per schedule, and disables further runs when admission fails or a previous run has uncertain billing. Recorded submissions cannot be scheduled as fresh evidence. Pausing a schedule leaves already queued work intact; cancel that experiment separately when needed.

## Human review

Reviews are append-only judgments with a self-reported reviewer label. They retain prior revisions and an evidence fingerprint, and never replace automated outcomes.

```sh
uv run cvals reviews add EXPERIMENT_ID TRIAL_ID --reviewer "Reviewer" --dimension clarity --score 0.8 --rationale "Cites the relevant source but omits one qualification."
uv run cvals reviews list EXPERIMENT_ID
```

Only finalized trials accept reviews. Full JSON experiment exports include the review history.
