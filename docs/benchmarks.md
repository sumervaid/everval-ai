# Historical benchmark releases

A discovered pull request is a candidate, not an evaluation task. A release
requires a reviewed problem statement, a reproducible environment, a historical
failure that the reference repair fixes, and unchanged regression behavior.
Admission evidence stays in the local workspace.

Use **Projects** in the [terminal workbench](terminal.md) to connect a GitHub
repository or local Git checkout, discover candidates, review their wording,
configure an environment when needed, and validate. GitHub discovery retains
merge evidence and quarantines ambiguous histories. Local commits remain labeled
as local history.

## Two grading paths

| Path | Suitable work | Grading boundary |
| --- | --- | --- |
| Function cases | Small Python repairs with direct function calls and literal JSON assertions | Expected answers and comparisons stay outside the candidate container |
| Prepared pytest repository | Python repositories using fixtures, parametrization, installed dependencies, or multiple modules | Agent inputs exclude private tests and reference source; grading tests share an interpreter with candidate code |

Function compilation requires supported assertions in both snapshots. New or
changed expected behavior becomes a historical control. Unchanged assertions for
the same function become separate regression controls. Unsupported test semantics
reject the candidate instead of silently reducing its checks.

For pytest, register a prepared local image through **Projects → Environment**.
The image must contain Python, pytest, and the repository's runtime and test
dependencies. Profiles record its immutable image ID, explicit plugin modules,
and a deadline from 1 to 120 seconds. Plugin autoloading is disabled. Validation
performs no dependency installation, image pull, host build, or host execution.

The repository compiler reads both complete source snapshots. New or changed
test bodies supply historical selectors; unchanged bodies supply regression
selectors. Historical checks use the reference test harness. Regression checks
use the exact base test harness, including its fixtures and configuration.
Source is varied independently between base and reference.

The runner clears configured pytest addopts, disables the cache provider, and
selects explicit test functions or methods. Parametrized instances must produce
identical collected identifiers in base and reference runs. Pytest's documented
[exit statuses](https://docs.pytest.org/en/stable/reference/exit-codes.html)
are reconciled with collection and execution evidence and the independently
observed Docker exit status.

Limits are 2,048 text files and 16 MiB per repository snapshot, 512 KiB per file,
500 selected test bodies per control, and 5,000 collected test instances. Binary
files, symlinks, submodules, omitted sensitive files, unsupported paths, and
oversized snapshots produce explicit quarantine reasons. Tests requiring network
services, writable source trees, or native builds during execution need a
different supported execution profile; they do not run on the host.

## Admission protocol

Each candidate undergoes three fresh base/reference cycles. Every cycle must
establish all of these conditions:

1. Every historical control executes a nonempty selection and fails on the base.
2. Every reference control passes.
3. Every regression control passes on both base and reference.
4. Base and reference collect the same case identifiers.
5. Case outcomes and identifiers agree across all three cycles.

Missing dependencies, collection failures, setup or teardown failures, skipped
tests, expected failures, zero tests, timeouts, excess output, and memory failures
do not establish historical validity. The pytest path accepts assertion failures
as historical failures; other runtime exceptions are quarantined. Function
controls likewise reject exception-based failure evidence for automatic admission.

Three agreeing cycles screen obvious flakiness; they do not prove determinism
or broad workload coverage. The candidate ledger retains unsuccessful reports
and their reasons. An unsupported candidate contributes no task to a release.

## Review wording before release

Issue text retrieved today is not necessarily what existed before the repair.
The connector records creation and update timestamps and flags possible hindsight.
Pull request descriptions and commit titles may reveal the solution.

A review records the exact problem statement, reviewer label, and rationale.
Describe observable pre-fix requirements. Exclude reference code, the later patch,
private assertions, and knowledge of the eventual implementation. Reviewer labels
are self-entered, not authenticated organizational identities.

Mechanical validation without an existing review can produce a needs-review
result. A blank local-history statement must be supplied before it can compile.
A new or changed review requires fresh validation. The exact reviewed wording
is part of the scenario fingerprint.

## Freeze and compare releases

Successful validation binds hashes of the complete scenario, base/reference
controls, pinned environment profile, and the grader's actual source files.
A claimed validation status in an imported manifest is insufficient. Experiment
creation, imported submissions, reruns, and trial execution check matching
admission evidence in the local database.

Release names are immutable within a project. Each release records its version,
candidate membership, scenario fingerprints, validation IDs, attestation hashes,
and excluded candidates. Released collections cannot be edited or deleted;
create a new release after changing tasks or their admission evidence.

Each pull request stays entirely in one partition. The stable grouping key is
the project ID and PR number, or the reference SHA for local history. The first
eight hexadecimal digits of its SHA-256 hash, modulo five, assign zero to holdout
and the remaining values to development. Small releases may have an empty
partition. A holdout-only release has no default development collection; the
application never silently substitutes holdout tasks.

Use development tasks for iteration. Select holdout explicitly for an independent
evaluation. The application records partitions but cannot prevent a local owner
from reading private evidence or using holdout results during development.
Published historical tasks may have appeared in model training data.

The Benchmarks service exposes diff(before_id, after_id) for added, removed,
changed, and unchanged membership. Its export(release_id) includes frozen
collections and admission records, marked as containing private grader data.
Treat that export as evaluator material, not an agent input.

## Integrity boundaries

The function grader compares returned values on the host; a candidate cannot
declare its own passing score. Expected answers are absent from its container.

Repository pytest provides container isolation with weaker grading integrity:
tests and candidate code share one interpreter. Reconciliation detects missing,
inconsistent, or malformed evidence, but deliberately hostile candidate code
can attack the test process. This mode is not tamper-resistant attestation.

SQLite append-only triggers and hashes enforce the workflow and detect accidental
changes. A machine administrator can rewrite the database and its hashes together.
These records are local reproducibility evidence, not a cryptographic signature
from an independent authority.

Grader source changes invalidate admission for new runs. Completed reports retain
their frozen results; validating again creates evidence for a new release.

## Verify the implementation

Run ordinary failure-path and integrity tests:

    uv run pytest tests/test_benchmarks.py -m "not sandbox"

Run fresh container checks against a dedicated Docker runtime:

    CVALS_TEST_SANDBOX=1 uv run pytest \
      tests/test_benchmarks.py::test_live_function_admission_uses_repeated_host_compared_controls

    CVALS_TEST_BENCHMARKS=1 \
    CVALS_TEST_PYTEST_IMAGE=sha256:YOUR_PREPARED_IMAGE_ID \
    uv run pytest \
      tests/test_benchmarks.py::test_live_prepared_repo_repeated_admission_and_missing_dependency

These tests make no model-provider calls.
