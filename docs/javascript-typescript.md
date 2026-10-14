# JavaScript, TypeScript, and private dependencies

Everval can admit historical JavaScript and TypeScript repairs using Node's test
runner, Jest, or Vitest. It prepares dependencies in Docker, freezes the local
image ID, and runs the same three-cycle failure/regression protocol used by
Python repository benchmarks. No imported installation, build, test, or hook
executes on the host.

## Prepare and review a Node project

Connect a GitHub repository or a local Git repository, discover candidates, and
inspect the candidate's base SHA. Use that base revision when reviewing the
dependency environment:

```sh
everval projects connect OWNER/REPO
everval projects discover PROJECT_ID --limit 20

# Explicit setup; Everval does not pull images automatically.
DOCKER_HOST="$EVERVAL_DOCKER_HOST" docker pull node:24-slim

everval environments plan PROJECT_ID --revision BASE_SHA \
  --language typescript --runner node-test --base-image node:24-slim
everval environments prepare PROJECT_ID --revision BASE_SHA \
  --language typescript --runner node-test --base-image node:24-slim
```

For the default Docker daemon, omit the `DOCKER_HOST` assignment. Select
`--language javascript` for JavaScript. Choose `--runner jest` or
`--runner vitest` when that exact test runner appears in the committed lockfile.
The CLI queues a durable preparation, waits for it, and registers the resulting
image as the project profile. Disconnecting the terminal leaves the job running;
inspect it with `everval jobs list` and `everval jobs show JOB_ID`.
The API preparation endpoint returns a queued job immediately. An existing reviewed image can also be registered explicitly:

```sh
everval projects profile PROJECT_ID sha256:FULL_IMAGE_ID \
  --language typescript --runtime node-test
```

A preparation requires root `package.json` and `package-lock.json` version 2 or 3.
Every installed package must have a SHA-512 integrity value and a supported
registry resolution. Dependencies come from that lockfile; `--package` overrides
are Python-only. Workspaces, linked packages, VCS dependencies, direct URLs in
package dependency declarations, and project build scripts are not imported.

The preparer invokes `npm ci --ignore-scripts` inside a fresh container. Only
reviewed manifests and imported package archives enter it; repository source,
`.npmrc`, host credentials, and Docker sockets do not. Public dependencies use
`registry.npmjs.org`. Private-only artifact closures prepare with networking
disabled. Dependency extraction is bounded by a 256 MiB temporary filesystem;
the separate npm cache is bounded to 384 MiB. The image retains installed
packages and their locked integrity metadata, not package registry credentials.
Native addons requiring an install/build script need a separately reviewed image.

Node 24 runs erasable TypeScript syntax through native type stripping. This is
not type checking and does not implement every TypeScript transformation or
`tsconfig` path mapping. Use a compatible pinned Jest/Vitest configuration for
projects that need transformations. Repository-specific runner configuration is
frozen with the private harness and executes only inside the grading container.

## What gets admitted

Node candidate discovery recognizes `.test` and `.spec` files with JavaScript or
TypeScript extensions and files under `test`, `tests`, or `__tests__`. The compiler
compares entire test files:

- New or changed test files supply the historical failure control.
- Separate unchanged test files supply baseline regression coverage.
- Historical controls use the reference test harness; regression controls use
  the original base harness. Source changes independently between base and fix.

A change that keeps all regressions inside one modified test file does not meet
this compiler's separate-file regression requirement. It remains quarantined.
There is no guessed coverage or synthetic passing result.

Each selected file must execute real test cases. Missing modules, empty files,
skipped/todo tests, non-assertion failures, inconsistent case identifiers,
ambiguous reports, timeouts, and resource failures cannot establish validity.
Node/Jest/Vitest reports are reconciled with the independently observed Docker
exit status. All three cycles must reproduce the failure on base, pass on fix,
and preserve the unchanged regression cases. Review the problem statement before
creating a release; see [benchmark admission](benchmarks.md).

Prepared repository limits are 2,048 UTF-8 files, 16 MiB per snapshot, 512 KiB per
file, 500 selected test files per control, and 5,000 collected cases. Binary files,
symlinks, submodules, sensitive files, and omissions produce quarantine reasons.
Grading runs without a network as UID 65532, with a read-only filesystem, one
CPU, 256 MiB memory, and a bounded 16 MiB temporary directory. Test configuration
that needs writable repository files or external services will fail closed.

These runners share a process boundary with imported test/source code. They
provide reproducible functional evidence, not resistance to deliberate grader
manipulation. Empty-file success and forged duplicate reports are rejected, but
this is not a cryptographic execution attestation.

## Import private npm packages without exposing credentials

Use your existing authenticated package client to obtain exact `.tgz` artifacts
on the same machine. `npm pack PACKAGE@VERSION --ignore-scripts` can fetch a
registry package without running its lifecycle scripts. Do not copy `.npmrc`,
tokens, or authenticated URLs into Everval. Import each required private package:

```sh
everval artifacts import /absolute/path/package-1.2.3.tgz \
  --kind npm --registry https://packages.example.com/npm
everval artifacts list

everval environments plan PROJECT_ID --revision BASE_SHA \
  --language typescript --runner vitest --artifact ARTIFACT_SHA256
everval environments prepare PROJECT_ID --revision BASE_SHA \
  --language typescript --runner vitest --artifact ARTIFACT_SHA256
```

Repeat `--artifact` for every private transitive dependency. The registry URL is
reviewed provenance, not a login configuration: it cannot contain credentials,
query parameters, or fragments. Its host/path must match the package's locked
resolution. Everval checks package name, exact version, and the lockfile's SHA-512
integrity, then substitutes a container-local tarball path. Unmatched private
resolutions are rejected. Public locked packages can still download from the
public registry; an all-artifact closure needs no network.

Imports inspect archive paths and contents without extracting or executing them
on the host. Absolute/traversal paths, symlinks, hard links, device entries,
`.npmrc`, `.env`, and oversized archives are rejected. Artifacts are stored by
SHA-256 under the local workspace with registry provenance and an audit event.
Every preparation verifies their bytes again. Archives and npm cache files are
absent from the sealed runtime image.

## Import private Python wheels

Private Python dependencies use the same artifact store. Obtain compatible
wheels with your authenticated package client, including their dependency closure
and pytest unless those packages already exist in the reviewed base image.
Everval does not build source distributions.

```sh
everval artifacts import /absolute/path/private_pkg-1.2.3-py3-none-any.whl \
  --kind wheel --registry https://packages.example.com/python

everval environments plan PROJECT_ID --revision BASE_SHA \
  --package private-pkg==1.2.3 --artifact WHEEL_SHA256
everval environments prepare PROJECT_ID --revision BASE_SHA \
  --package private-pkg==1.2.3 --artifact WHEEL_SHA256
```

Repeat `--artifact` for the remaining wheels. With any wheel artifacts selected,
Python preparation uses only that offline wheelhouse and packages already in the
base image. It never falls back to a private index or a public download. Missing
closure members fail preparation. Wheel hashes, `pip freeze`, source manifests,
and the image ID remain available in preparation evidence.

## Public execution tools and verification

The tools harness exposes `run_node` for prepared Node tasks and `run_python`
for Python tasks. A Node snippet can use `require('./source.cjs')`, resolved from
`/workspace`, or `await import('/workspace/source.ts')`. It receives public source
and the prepared dependencies. Private tests and reference code enter only the
separate evaluator. Attempts to overwrite test/configuration/dependency paths
are rejected.

Ordinary tests require no Docker or paid model calls:

```sh
uv run pytest tests/test_node_repositories.py tests/test_agent_runner.py
```

Live checks require the local Node image and explicitly fetch the pinned public
Jest/Vitest test dependencies inside Docker. The optional Python wheel check
uses an already prepared pytest image:

```sh
CVALS_TEST_NODE=1 \
CVALS_TEST_PYTEST_IMAGE=sha256:PREPARED_PYTEST_IMAGE_ID \
uv run pytest tests/test_node_repositories.py tests/test_agent_runner.py
```

The implementation uses the documented [Node test reporter events](https://nodejs.org/api/test.html),
[npm clean installation](https://docs.npmjs.com/cli/v11/commands/npm-ci/),
[Jest JSON reports](https://jestjs.io/docs/cli), and
[Vitest JSON reports](https://vitest.dev/guide/reporters).
