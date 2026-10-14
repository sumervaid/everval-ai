# Connect a repository

A project keeps a local repository connection, its discovered candidates, and
the evidence used to reconstruct each task. GitHub connections discover actual
merged pull requests. Local connections inspect local commits and remain
explicitly labeled as local history.

## Prerequisites

Use the terminal workbench on the machine that owns the evaluation workspace.
Install Git and GitHub CLI, then configure repository access there:

```sh
gh auth login
gh auth status --hostname github.com
```

GitHub CLI retains the credential. The application does not copy it into the
project database, source snapshots, mirror configuration, or solver inputs.
GitHub repository reads use the configured account's existing access.
No issues, pull requests, comments, or repository settings are created or changed.

Coding validation also needs the isolated runtime described in
[Sandbox setup](sandbox.md) and, for repositories with test dependencies,
[prepared environments](environments.md). Repository inspection itself does not run tests or
require Docker.

## Accepted sources

Connect one of these forms in the workbench:

- `OWNER/REPO`
- `https://github.com/OWNER/REPO`
- An absolute path to an existing local Git repository

GitHub URLs with embedded credentials, query parameters, other hosts, or
ambiguous path components are rejected. GitHub Enterprise hosts and SSH clone
URL syntax are not accepted by this connector.

Connections are durable and idempotent. Reconnecting the same canonical GitHub
repository or resolved local path uses the same project ID. Local source files
are not checked out or modified.

## Discovery and provenance

Discovery reads a bounded set of merged GitHub pull requests, ordered by their
latest update time. It preserves a candidate ledger with stable IDs, source
revisions, inclusion or quarantine reasons, and counts. Refreshing metadata
records a new candidate version when its evidence changes. Older candidates
remain in the ledger. Discovery defaults to 20 records per page and supports
up to 100. The result includes `coverage.next_cursor`; pass that value to
`Projects.discover(project_id, cursor=...)` to continue. Omitting it refreshes
the latest window. Local cursors are bound to HEAD and reject stale history.
GitHub pages reflect current update order, so concurrent changes can move
records between pages; ledger deduplication does not make a transactional
repository-wide snapshot.

A candidate records:

- Pull request number, title, URL, merge time, and changed paths.
- Exact base and reference commit IDs and the integration's parents.
- Linked issue text when available, with source creation/update timestamps.
- The problem statement's merge-time cutoff and hindsight-review flags.
- Repository location, environment hints, status, and exclusion reasons.

Current issue text is not proof of what the issue said before the fix. An issue
updated after the cutoff may contain later edits. Pull request descriptions can
also reveal implementation details. Every candidate therefore requires explicit
problem-statement review before admission.

Closing-issue references come from GitHub's
[documented pull request GraphQL field](https://docs.github.com/en/graphql/reference/pulls).
At most ten linked issues are read per candidate; the total count is retained.

## Correct base and reference states

The before state comes from the integration's actual Git parent, rather than
assuming the API's base-branch pointer is the historical before state.

For a two-parent merge, the first parent is the before state, the merge commit
is the reference state, and the second parent must match the recorded PR head.
A single-commit integration also has an unambiguous direct parent.

A single-parent integration containing several PR commits can be a squash or
the last commit of a rebase. Those are materially different reconstructions:
rebase integration adds individual commits to the base branch.
[GitHub's merge documentation](https://docs.github.com/en/pull-requests/reference/pull-request-merges)
describes this distinction.

For that ambiguous case, automatic discovery requires every changed path and
the complete PR patch hunks to match the direct-parent-to-reference change.
The comparison preserves hunk coordinates and whitespace, including Python indentation. Missing,
truncated, renamed, or mismatched patch evidence causes quarantine. The connector
does not treat the last rebased commit as the entire PR without this evidence.

Discovered means lineage evidence was obtained. It does not mean the task's
instructions, environment, hidden tests, or reference fix have been validated.
The benchmark admission step provides that separate evidence.

## Local storage and execution boundary

GitHub source objects live in a project-owned bare repository under
`CVALS_DATA_DIR/projects`. The mirror has no working checkout. Git runs with
hooks, filesystem monitors, external diff tools, recursive submodules, automatic
maintenance, and credential persistence disabled. HTTPS transport is limited
to the constructed GitHub repository URL.

The connector uses shallow fetches and fetches historical objects as needed.
Each mirror has a 256 MiB admission and monitored storage ceiling, a
90-second transfer deadline, bounded command output, and a private directory.
A stopped transfer may briefly exceed the storage threshold between monitor
checks; this is an application limit, not a filesystem quota.

Snapshots read exact commit blobs. They preserve UTF-8 source and configuration
files, original file modes, and a content hash. Git history, symlinks, submodules,
binary content, known credential filenames, and unsafe paths are excluded with
explicit omission records. Solver snapshots also exclude recognized test paths.

Default snapshots support 4 MiB and 128 files. Reviewed prepared environments
can request up to 32 MiB and 2,048 files; each text file is limited to 1 MiB.
Any required omission must prevent environment admission. No omission is
silently represented as a complete snapshot.

Language and test-runner hints come from filenames and bounded text inspection.
No imported module, package setup, install script, test, or build executes on the
personal host.

## Read the funnel

The workbench distinguishes candidates seen during the current refresh,
new or changed metadata, already recorded candidates, reconstructable
candidates, and quarantined candidates. GitHub's available merged-PR count and
whether additional results exist remain visible.

A recent bounded sample is not exhaustive coverage or a representative
enterprise benchmark. Include the selected window, exclusions, language mix,
and review criteria when interpreting results.

## Diagnostics

- **GitHub CLI missing:** install `gh` on the workspace machine.
- **Authentication unavailable:** run `gh auth login` there and confirm access.
- **Repository unavailable:** check its canonical owner/name and account permissions.
- **Rate limited:** wait for GitHub's limit to reset, then refresh.
- **Mirror ceiling exceeded:** select a smaller reviewed repository or narrow the workload.
- **No reconstructable candidates:** inspect quarantine reasons; do not convert them into passing tasks.
- **Docker unavailable:** metadata discovery can proceed, but coding admission requires the isolated runtime.
