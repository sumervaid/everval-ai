# Release acceptance

Everval's release acceptance exercises an installed wheel through the CLI,
worker, API, browser and terminal. It retains the evidence for repository
admission, isolation, accounting, recovery and review workflows. Optional live
provider phases measure compatibility and operating behavior on original
fixtures; they are separate from the free installation checks.

The [2026-10-15 release summary](evidence/2026-10-15/summary.json) records the
retained outcomes and their scope. Read it alongside [verification](verification.md)
and the [deployment boundary](PLAN.md). A command described below is a
reproduction procedure, not a claim that every possible configuration passed.

## Install the wheel in a clean environment

Run from the source checkout on the machine that will hold the workspaces.
Install Python 3.12+, uv, Git, Node.js and a compatible dedicated Docker runtime.
Choose a new, private local directory outside the source tree; all generated
workspaces and evidence belong there.

The build and dependency preparation can download public packages. The acceptance
runner itself makes no paid provider calls. The development environment supplies
Playwright; application processes use the separate installed wheel environment.

The retained 2026-10-15 wheel installation used uv pip install. The lock-export
and sync recipe below is the stricter reproduction procedure; it does not claim
that the retained installation resolved its runtime dependencies from that export.

~~~sh
export EVERVAL_ACCEPTANCE_ROOT="/absolute/path/to/everval-release-acceptance"
umask 077
mkdir -p "$EVERVAL_ACCEPTANCE_ROOT"

make setup
uv build --wheel --out-dir "$EVERVAL_ACCEPTANCE_ROOT/dist"
uv export --frozen --no-dev --no-emit-project --format requirements.txt \
  --output-file "$EVERVAL_ACCEPTANCE_ROOT/runtime-requirements.txt"
uv venv --python 3.12 "$EVERVAL_ACCEPTANCE_ROOT/package-env"
uv pip sync --python "$EVERVAL_ACCEPTANCE_ROOT/package-env/bin/python" \
  --require-hashes "$EVERVAL_ACCEPTANCE_ROOT/runtime-requirements.txt"
uv pip install --python "$EVERVAL_ACCEPTANCE_ROOT/package-env/bin/python" \
  --no-deps "$EVERVAL_ACCEPTANCE_ROOT/dist/everval_ai-0.3.1-py3-none-any.whl"

export EVERVAL_INSTALLED_PYTHON="$EVERVAL_ACCEPTANCE_ROOT/package-env/bin/python"
"$EVERVAL_ACCEPTANCE_ROOT/package-env/bin/everval" --version
uv run playwright install chromium
~~~

Keep the original wheel, dependency lock/export and the executed acceptance
scripts with the private evidence. Preserve the environment's bin/python path;
resolving that symlink to its underlying interpreter bypasses the environment.

## Prepare the Docker images

The repository stage requires a local Python image containing pytest and a local
Node 24 image. Prepare the Python image through the
[reviewed environment workflow](environments.md), or provide an independently
reviewed compatible image. Record its immutable image ID. The
[Node runtime guide](javascript-typescript.md) describes the corresponding image
setup. These are explicit preparation steps; the runner never pulls an image
automatically or falls back to running imported tests on the host.

For a non-default daemon, point both Everval and Docker at the same socket:

~~~sh
export EVERVAL_DOCKER_HOST="unix:///absolute/path/to/docker.sock"
export DOCKER_HOST="$EVERVAL_DOCKER_HOST"

# Explicit image download, if this approved base is not already local.
docker pull node:24-slim

export EVERVAL_ACCEPTANCE_PYTHON_IMAGE="sha256:YOUR_PREPARED_PYTEST_IMAGE_ID"
export EVERVAL_ACCEPTANCE_NODE_IMAGE="$(docker image inspect node:24-slim --format '{{.Id}}')"
docker image inspect "$EVERVAL_ACCEPTANCE_PYTHON_IMAGE"
docker image inspect "$EVERVAL_ACCEPTANCE_NODE_IMAGE"
~~~

For the default daemon, omit both host variables. Python repository controls
need pytest in the image; the plain Python image used for function-case starters
is insufficient. The acceptance stage prepares the Node fixture's empty locked
dependency set with lifecycle scripts disabled and retains that preparation.

## Run the free acceptance suite

~~~sh
uv run python scripts/acceptance/run.py \
  --output-dir "$EVERVAL_ACCEPTANCE_ROOT/free" \
  --installed-python "$EVERVAL_INSTALLED_PYTHON" \
  --python-image "$EVERVAL_ACCEPTANCE_PYTHON_IMAGE" \
  --node-image "$EVERVAL_ACCEPTANCE_NODE_IMAGE"
~~~

The output directory must not already exist. Stages run sequentially to avoid
competing Docker and browser workloads. A failed stage produces a failing exit
status and retained evidence; the runner does not silently skip it. Use
**--skip-browser** only when deliberately excluding Chromium, and retain that
exclusion in any report. Loopback HTTP, browser traffic and the selected Docker
daemon are expected; “free” means zero paid provider calls.

The stages establish different properties:

| Stage | Procedure and evidence |
| --- | --- |
| Repository admission | Create original Python and Node Git histories; import, discover and independently review a candidate; reject premature release; repeat historical failure, reference success and regression controls in Docker; freeze a release and run correct and incomplete fixture submissions. |
| Isolation and rejection | Probe the public execution workspace, immutable runtime and network boundary; verify private checks/reference material stay out of public tasks; quarantine missing dependencies and empty test collections; retain failed admission evidence. |
| Portable packs and demo | Stage a licensed, hashed data-only pack; require independent behavior review and passing positive/rejected negative controls; explicitly export hidden material and reimport into a fresh workspace requiring new approval; reject tampering and ineffective controls; run the no-key demo. |
| Automation and recovery | Use an original local HTTP adapter to exercise scheduling, screening/escalation, protected-holdout selection, spend limits, cancellation and interrupted-worker accounting. Responses are controlled fixtures, not hosted-model measurements. |
| Interfaces | Drive the installed CLI, API, Textual workbench and Chromium journeys, including roles, review, evidence and failure paths. Preserve captures and browser diagnostics privately. |

To rerun only the repository stage after an independent change, choose another
new directory:

~~~sh
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/repositories.py \
  --output "$EVERVAL_ACCEPTANCE_ROOT/repositories-rerun" \
  --python-image "$EVERVAL_ACCEPTANCE_PYTHON_IMAGE" \
  --node-image "$EVERVAL_ACCEPTANCE_NODE_IMAGE"
~~~

The repository fixtures contain local Git commits. They do not establish
authenticated GitHub API or merged-PR coverage. The correct fixture adapter
deliberately has the reference submission so the installation can verify its
grading path; it is not a solver or model-quality result. Same-process repository
tests are not an adversarial security attestation. See the
[sandbox contract](sandbox.md) for the execution boundary.

## Additional runtime and GitHub checks

The source test suite has explicit opt-in cases for Jest/Vitest, TypeScript,
private wheel/npm preparation, output/timeout controls and read-only GitHub
metadata. These supplement installed-wheel acceptance; they are source-suite
tests and should be reported separately. They may fetch public dependencies in
isolated preparation containers and require an existing GitHub CLI login for the
metadata check. They make no paid model calls.

Load the reviewed base image explicitly, then run the selected live cases
against the dedicated runtime:

~~~sh
docker pull python:3.12-slim

CVALS_DOCKER_HOST="${EVERVAL_DOCKER_HOST:-}" \
CVALS_TEST_SANDBOX=1 CVALS_TEST_ENVIRONMENTS=1 \
CVALS_TEST_BENCHMARKS=1 CVALS_TEST_NODE=1 CVALS_TEST_GITHUB=1 \
CVALS_TEST_PYTEST_IMAGE="$EVERVAL_ACCEPTANCE_PYTHON_IMAGE" \
EVERVAL_SANDBOX_IMAGE="$EVERVAL_ACCEPTANCE_PYTHON_IMAGE" \
uv run pytest \
  tests/test_benchmarks.py tests/test_node_repositories.py \
  tests/test_environments.py tests/test_agent_runner.py tests/test_projects.py \
  tests/test_sandbox.py tests/test_domains_mining.py \
  -k 'test_live or test_all_coding_references_pass_and_buggy_bases_fail or test_mined_fix_passes_and_historical_base_fails' \
  --basetemp "$EVERVAL_ACCEPTANCE_ROOT/runtime-test-tmp" \
  --junitxml "$EVERVAL_ACCEPTANCE_ROOT/runtime-tests.xml"
~~~

Use a new temporary-directory path for each run. Retain the JUnit report and
console log, including any skipped cases. GitHub metadata retrieval does not
establish end-to-end admission of an external customer repository.

## Optional live OpenAI acceptance

These commands are separate, explicit paid steps. First inspect the source
fixture cohort and provider configuration in scripts/acceptance/openai_live.py
and scripts/acceptance/openai_workflows.py. Review current official model
availability, pricing, token limits and any organization-specific charges before
execution. The manifest retains the selected model definitions, rates, pricing
sources, pricing revision, task hash and reservation assumptions.

The 2026-10-15 acceptance allocation sets a **$100 provider ceiling**, uses a
**$90 cumulative admission limit**, and projects **roughly $34** for the revised
combined conservative campaign plan. That projection is a reservation estimate, not an invoice.
It includes the pilot, repeated starter evaluation, Autopilot lifecycle and
reserved-holdout study. Clarified task contracts can also change reservations.
Recalculate both free plans for every new campaign after reviewing prices;
these amounts are not a future cost guarantee, product-wide promise or default
subscription charge.

Planning does not require a key and makes no provider calls:

~~~sh
export EVERVAL_LIVE_ACCEPTANCE="$EVERVAL_ACCEPTANCE_ROOT/openai"
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/openai_live.py \
  --output-dir "$EVERVAL_LIVE_ACCEPTANCE" --phase plan
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/openai_workflows.py \
  --output-dir "$EVERVAL_LIVE_ACCEPTANCE"
~~~

Review manifest.json, workflows-plan.json and budget.json. Supply
**OPENAI_API_KEY** through your own process environment for the paid phases; do not
put credentials in the repository, command arguments or published evidence.
Only the reviewed original fixture inputs should leave the machine.

Run the small compatibility pilot first:

~~~sh
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/openai_live.py \
  --output-dir "$EVERVAL_LIVE_ACCEPTANCE" --phase pilot
~~~

Validate the starter reference and negative controls in the selected local
runtime. This phase makes no provider calls:

~~~sh
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/openai_live.py \
  --output-dir "$EVERVAL_LIVE_ACCEPTANCE" --phase controls \
  --sandbox-image "$EVERVAL_ACCEPTANCE_PYTHON_IMAGE"
~~~

The repeated live cohort requires clean pilot completion and matching validated
task, installed-code and runtime evidence:

~~~sh
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/openai_live.py \
  --output-dir "$EVERVAL_LIVE_ACCEPTANCE" --phase benchmark \
  --sandbox-image "$EVERVAL_ACCEPTANCE_PYTHON_IMAGE"
~~~

Finally, exercise live screening/escalation and explicit candidate selection
with one reserved final holdout:

~~~sh
"$EVERVAL_INSTALLED_PYTHON" scripts/acceptance/openai_workflows.py \
  --output-dir "$EVERVAL_LIVE_ACCEPTANCE" --execute
~~~

Keep **the same output directory** throughout these phases: it owns the shared
workspace and cumulative ledger. A phase lock prevents concurrent admission;
retained start markers prevent automatic retries after an interrupted run.
Unknown provider usage blocks further calls. Inspect failed evidence and the
provider account before any separately authorized recovery. Do not create a new
ledger to work around a stopped phase.

Costs are reported token counts multiplied by declared conservative rates.
Reservations, measured charges and unknown charges remain distinct. The ledger
does not include external hosting, tools or other service bills and is not
provider invoice reconciliation. A completed incorrect answer remains an
observed failure; a protocol, grading, accounting or audit failure does not
become successful acceptance. A rejected optimization candidate is retained;
the workflow does not deploy a configuration.

## Evidence and interpretation

Raw command output, fixtures, hidden graders, reference submissions, full traces,
screenshots and workspace databases stay in private local directories. Do not
add them wholesale to Git. Retain failed stages and earlier artifacts when
rerunning; record the actual wheel, source, runtime and configuration used.

Public summaries should include status, scope, omitted checks, immutable
identifiers/hashes, observed counts, cost coverage and limitations. Review them
before any explicit publication. Summary exports and badges are evidence
pointers; an installation badge does not certify model quality. No acceptance
command automatically uploads results.

Original fixtures are intentionally small and inspectable. They may be public
or familiar to models, use bounded grading contracts and omit much of real
enterprise work. Fixed request order and one machine's runtime make latency
descriptive. These runs do not support a representative provider ranking,
customer return-on-investment estimate, production SLA or independent security
claim. Use authorized, separately reviewed workloads to assess those questions.

Version 0.3.1 includes five fixes identified during release acceptance:

- Interrupted HTTP attempts retain unknown usage even when configured token
  rates are zero, so recovery cannot admit further calls on a false zero-cost
  assumption.
- The terminal optimization form requires an explicitly declared development
  collection, matching the engine's protected-holdout contract.
- Automatically reviewed pure JSON schemas reject multipleOf, closing a
  floating-point overflow path in bounded grading.
- Terminal selection controls allocate enough height for the selected value;
  the value remains visible at both wide and narrow terminal sizes.
- All nine structured starter tasks publish precise output contracts, including
  types, casing, citation coverage, ordering and state transitions. Document,
  data and workflow collections advance to 1.1.0; existing frozen experiments
  retain their earlier task definitions.

Earlier live results using the ambiguous starter contracts are retained only
for compatibility and lifecycle evidence; they are excluded from quality
claims.

The [retained release summary](evidence/2026-10-15/summary.json) is the source for
final outcomes. Earlier installation controls and live measurements remain
separately identified.
