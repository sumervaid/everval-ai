# Coding sandbox

Coding scenarios execute submitted source inside a separate Linux container.
Document, data, and workflow checks do not require this runtime.

## Setup

Install a Docker-compatible Linux runtime suitable for your machine. On macOS,
use a dedicated Linux VM and keep its disks and image cache on a volume with
enough space.

For a non-default daemon, configure both the application and the Docker command
used to load its image:

```sh
export EVERVAL_DOCKER_HOST=unix:///absolute/path/to/docker.sock
DOCKER_HOST="$EVERVAL_DOCKER_HOST" docker pull python:3.12-slim
export EVERVAL_SANDBOX_IMAGE=python:3.12-slim
uv run everval doctor
```

For the default daemon, omit `EVERVAL_DOCKER_HOST` and use
`docker pull python:3.12-slim`. Loading an image is an explicit setup step.
Execution only accepts an image already present in that daemon and records its
immutable digest. There is no automatic pull or host execution fallback.

Validate the coding starter after setup:

```sh
uv run everval collections validate starter-coding
```

Its ten original repair tasks contain 51 function cases. Every reference must
pass; every buggy base must be rejected.

## Execution boundary

Each verifier uses:

- UID/GID 65532, dropped Linux capabilities, and no new privileges.
- No network, host directory mounts, or Docker socket.
- A read-only root filesystem and a 16 MiB temporary filesystem.
- One CPU, 256 MiB memory, and 64 processes.
- A 20-second execution deadline by default, at most 128 KiB of captured output, and bounded input files.

Source is copied into a never-started staging container. A temporary image
supplies the final read-only verifier. No imported code executes during staging.
The verifier contains the submitted task files and its own runner; it receives
no host home directory or Git history.

Temporary containers and images are removed after each run. Recovery can remove
resources older than five minutes only when their application and workspace
ownership labels match. Shared base images and unrelated workspaces are excluded.
Cleanup failures are reported rather than silently ignored.

## Declarative function cases

The preferred evaluator invokes `module:function` with JSON arguments inside
the container. Only argument values enter the runner. Expected results and host-side
comparisons remain outside it. Each run contains one submitted implementation;
grading a candidate does not add the separate reference output.

The container returns bounded JSON values and cannot declare its own score.
Printing a forged passing summary does not make an incorrect value pass. The
grader records each case's expected value, actual value, and outcome.

This mode supports pure function behavior with JSON-compatible inputs and
outputs, plus expected exception names. It does not establish filesystem side
effects, performance guarantees, browser behavior, or package installation
compatibility.

Arbitrary unittest mode is also available for trusted compatibility tests.
Candidate code and tests share one interpreter in that mode; deliberate grader
manipulation is outside its assurance boundary. The built-in coding collection
and historical compiler use external expected-value comparisons.

## Prepared repository tests

Historical benchmark profiles run explicit pytest selections over complete UTF-8
repository snapshots. Dependencies must already exist in a pinned local image.
Private tests, pytest configuration, and reference source are excluded from the
agent's public workspace; grading receives a separate frozen harness.

The runner reconciles collection, setup/call/teardown evidence, and Docker exit
status. Collection errors, missing dependencies, skipped tests, zero tests and
resource failures cannot establish admission. Same-process pytest does not resist
deliberate grader manipulation. See [benchmark admission](benchmarks.md).

Tool-using systems execute public Python snippets in fresh containers containing
only public source and the snippet. Private grading material is introduced only
for the separate evaluator. A complete submission can set replace_files to true
to preserve deletions; otherwise files are partial replacements over the base.

## Verification

Run these checks only against a dedicated test runtime with the local Python
image loaded:

```sh
CVALS_TEST_SANDBOX=1 uv run pytest \
  tests/test_sandbox.py tests/test_domains_mining.py -m sandbox
```

The live checks cover all original reference and buggy implementations, a mined
historical repair, network and filesystem restrictions, forged pass output,
bounded output, and timeout cleanup. Ownership-scoped recovery and admission
guards are covered by the ordinary unit tests:

```sh
uv run pytest tests/test_sandbox.py tests/test_domains.py tests/test_domains_mining.py -m "not sandbox"
```

Without `CVALS_TEST_SANDBOX=1`, live Docker tests are explicitly skipped.
