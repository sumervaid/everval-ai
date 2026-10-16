# Public repository benchmark recipe

This optional research runner compares an upstream coding-agent workflow with one explicit review checkpoint on a frozen SWE-bench Lite cohort. It uses Everval's local evidence and accounting conventions with the official SWE-bench grading contract. It is separate from the curated-task TUI runtime. Evaluation operators can use it to compare whether one review checkpoint changes successful repairs and recorded cost on this cohort. Adoption decisions still need their own authorized tasks; see the [enterprise use cases](../../docs/use-cases.md).

Read the [frozen protocol](../../docs/benchmarks/public-repositories-2026-10-15/methodology.md) before interpreting a score. This is a small native ARM pilot, not an official leaderboard submission. The [results](../../docs/benchmarks/public-repositories-2026-10-15/results.md) retain all scheduled attempts, exclusions and unresolved usage. The [accounting amendment](../../docs/benchmarks/public-repositories-2026-10-15/recovery.md) explains the bounded continuation.

## What you need

- macOS or Linux with a local Docker daemon that can run Linux ARM64 containers and enough disk space for historical repository environments. On macOS, start the Docker VM first.
- Python 3.12 and `uv`.
- A private artifact directory outside this checkout.
- An OpenAI credential only when you reach the explicit paid phase.

Historical dependency builds can take substantial time. Identical Conda requirements share a build stage, while task-specific pip installs and repository setup retain their order. Preparation also captures a local wheelhouse so the evaluator can install required build tooling offline. Package manifests, wheel hashes, and image identities are retained; mutable package indexes mean a later rebuild is not guaranteed to produce identical image bytes.

For the four Sphinx tasks, preparation verifies installation changes to configuration against the pinned recipe and original source, records their hashes, and restores a clean starting point. Unexpected tracked changes fail preparation. The solver cannot access the retained setup artifacts. A fresh grader restores the verified configuration before running the unchanged official evaluation script; source, artifact, and image hashes retain its provenance.

## 1. Prepare the frozen tasks and tooling

From the repository root:

```sh
benchmark_dir="$HOME/everval-public-pilot"
uv run --locked --script scripts/benchmarks/prepare.py --output-dir "$benchmark_dir" selection
uv run --locked --script scripts/benchmarks/prepare.py --output-dir "$benchmark_dir" download
uv run --locked --script scripts/benchmarks/prepare.py --output-dir "$benchmark_dir" tooling
uv run --locked --script scripts/benchmarks/prepare.py --output-dir "$benchmark_dir" build
```

The default Docker context must resolve to a local Unix socket. Set `CVALS_DOCKER_HOST` to the local socket explicitly when needed. The runner rejects remote Docker endpoints.

Task rows, reference repairs, hidden tests, downloaded sources, dependency environments, and build logs remain in the private directory. Repository installation and tests execute inside Docker; the host runs trusted orchestration and grading tools.

Every selected task remains in the manifest, including failed preparation. Use `--retry-failed` to retry a failed build. For a reviewed preparation repair to an already prepared image, use `--rebuild` with an explicit task ID:

```sh
uv run --locked --script scripts/benchmarks/prepare.py \
  --output-dir "$benchmark_dir" build \
  --instance-id sphinx-doc__sphinx-11445 --rebuild
```

Both options preserve earlier build evidence. Run controls again for the new image before freezing. Do not replace a difficult task with another.

## 2. Validate controls and freeze the campaign

```sh
benchmark_python="$benchmark_dir/tooling-env/bin/python"
export PYTHONPATH="$PWD:$PWD/src"

"$benchmark_python" -m scripts.benchmarks.executor controls \
  --campaign-root "$benchmark_dir" --run-id initial --timeout 600

"$benchmark_python" -m scripts.benchmarks.freeze \
  --output-dir "$benchmark_dir"
```

Controls must establish every official expected failure before the fix, every expected pass afterward, and preservation of the official regression cases. The model never receives control logs, hidden tests, reference repairs, or future Git history.

The frozen plan binds task bytes, prepared images, control evidence, runner sources, model settings, and the budget. Undeclared source changes after freezing are rejected.

A fresh campaign has zero opening spend. To carry forward an earlier supported accounting reconciliation, supply both `--opening-spend-usd` and `--prior-accounting`. The original pilot carries forward $0.482999 from 179 prior calls. The helper verifies that amount against complete retained usage; it does not reset previous charges.

No model calls occur during preparation, controls, freezing, or a run without `--execute`.

## 3. Run the matched comparison

Supply `OPENAI_API_KEY` through your credential manager. Credentials are used by the host provider client and are not passed into solver or grader containers.

Inspect the development plan first:

```sh
"$benchmark_python" -m scripts.benchmarks.campaign \
  --plan "$benchmark_dir/campaign.json" --phase development
```

Then explicitly admit the paid phases:

```sh
"$benchmark_python" -m scripts.benchmarks.campaign \
  --plan "$benchmark_dir/campaign.json" --phase development --execute

"$benchmark_python" -m scripts.benchmarks.campaign \
  --plan "$benchmark_dir/campaign.json" --phase test --execute
```

Both arms use `gpt-6.1-sol`, the same prepared image and limits, and one attempt per task. The standard mini-SWE-agent workflow is the baseline. The other arm receives a disclosed review checkpoint before its final submission. The checkpoint consumes its existing allowance. The 1,800-second time threshold is checked before another model step; an in-flight request, action, grading, and cleanup can extend elapsed time beyond it.

The default cap is $6 per attempt, $190 for cumulative admission, and $200 for the user ceiling. Every provider request reserves its maximum declared token cost before sending. Missing usage, unexpected pricing, an interrupted request, or unconfirmed container cleanup blocks further admissions. Unknown charges never become zero.

A started attempt is never silently rerun. Incomplete and failed attempts stay in the evidence. Investigate a blocked campaign locally; do not delete its ledger or start markers to bypass the stop. A missing or stale ledger with retained attempt evidence requires recovery; report export never creates a replacement ledger. Reconciliation detects mismatches among the surviving records, but cannot detect a coordinated rollback of every local copy without an independent audit anchor.

For the inspected HTTP 400 output-limit response with missing usage, an explicit
[post-outcome accounting amendment](../../docs/benchmarks/public-repositories-2026-10-15/recovery.md)
can admit only the 11 originally unstarted pilot attempts after individual review.
It retains the full unknown reservation, prior evidence and all frozen solver settings;
started attempts are never retried. Every new unknown request stops again. Supply
the same `--amendment` to campaign and report commands. The recovery guide separates
implementation verification from actual application to the pilot ledger and explains
why terminal completion does not imply complete costs or comparable quality outcomes.

## 4. Export only the reviewable metadata

```sh
"$benchmark_python" -m scripts.benchmarks.report \
  --plan "$benchmark_dir/campaign.json" \
  --output "$benchmark_dir/public-summary.json"
```

The exporter includes task identities, configuration hashes, per-task outcomes, matched differences, conservative provider cost, and solver elapsed time, including solver setup and cleanup but excluding grading. Exclusions remain in the original denominator. Failed-attempt spending contributes to cost per resolved task. Incomplete accounting prevents a cost-per-success claim.

Parsed status counts are diagnostic: pytest's own tests can print deliberate failures from nested runs. Interpret required test identities alongside the actual test-command exit code; those counts do not measure the outer suite's failure total.

Review the summary before publishing it. Raw task text, patches, private grading material, and trajectories are not included in this export. Public benchmark tasks can have appeared in training data. The cohort has 12 test tasks and 3 separate development tasks, with one attempt per arm; it cannot establish repeatability or broad superiority.

## Attribution

SWE-bench supplies the dataset and grading contract; mini-SWE-agent supplies the baseline control loop and prompts. Exact revisions and source-project notices are in the [protocol](../../docs/benchmarks/public-repositories-2026-10-15/methodology.md). The original repositories are benchmark subjects, with no implied customer relationship or endorsement.
