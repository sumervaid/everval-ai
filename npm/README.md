# Everval AI

**On-prem continuous benchmarking for AI models and agents.**

Turn repository changes, documents, data tasks, and workflow simulations into
repeatable evaluations. Compare quality, cost, and latency, then keep checking
as your models, prompts, and code change.

This package launches the complete Everval Python application: CLI, terminal
workbench, tmux sessions, worker, API, and bundled browser interface. Calibrated judge studies preserve human disagreements, measure order effects and repeatability, and export evaluator-only references to Inspect.

## Install

Use Linux or macOS, Node.js 22.22.2+, 24.15.0+, or 26+, an existing Python
3.12+ interpreter, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Supported Node ranges are listed in package.json.

```sh
npm install --global __NPM_PACKAGE__
everval setup
everval demo
```

Installation runs no lifecycle scripts. `everval setup` explicitly creates a
private Python environment and installs the bundled application with pinned,
hash-checked runtime dependencies. It may download dependency wheels from your
configured index; it never downloads Python or uv, or builds dependencies from
source. Other commands require that setup has completed and do not install or
download runtime dependencies.

The demo makes zero model calls and needs no Docker or network connection after
setup. It runs original installation controls, not a model leaderboard.

## Start working

```sh
everval init --starters
everval tui
```

Press **Ctrl+L** for rankings, **Ctrl+N** to connect a repository, **F9** for a
guided run, and **Ctrl+P** for commands. With tmux installed, use
`everval tmux` to keep the workbench running after detaching. Use
`everval serve` for the browser interface and `everval help` for the complete
application command reference. `everval-ai` is an alias for `everval`.

GitHub onboarding requires Git and the GitHub CLI. Repository task execution
requires Docker and a prepared runtime. The demo and non-code task workflows
do not require Docker. Model calls require explicit provider configuration and rates, credentials when
required by the endpoint, and a budget for priced calls.

## On-premises installation

Keep dependencies in an approved mirror with uv's index and certificate
settings. For disconnected setup, transfer this npm package, approved uv and
Python installations, and a platform-compatible wheelhouse containing every
dependency in the bundled `runtime/requirements.txt`.

```sh
everval setup --offline --wheelhouse /srv/approved-wheels
```

Every required wheel must match a hash in the bundled lock. Offline setup
disables registry access. See the
[installation and release guide](https://github.com/sumervaid/everval-ai/blob/main/docs/npm.md)
for wheelhouse preparation and package verification, and the
[enterprise deployment guide](https://github.com/sumervaid/everval-ai/blob/main/docs/on-premises.md)
for service accounts, internal inference, access controls, backups, and upgrades.

| Setting | Purpose |
| --- | --- |
| `EVERVAL_DATA_DIR` | Application workspace; defaults to `~/.local/share/everval`. |
| `EVERVAL_NPM_RUNTIME_DIR` | Absolute path for private, versioned Python environments; defaults to the user's cache. |
| `EVERVAL_PYTHON` | Existing Python interpreter path or uv interpreter selector. |
| `EVERVAL_UV` | Approved uv executable path. |
| `UV_DEFAULT_INDEX` | Approved dependency index for online setup. |
| `SSL_CERT_FILE` | Trusted CA bundle for your internal dependency index. |

Runtime directories must be owned by the current user with mode 0700; parent
directories must not be writable by another user. Setup reports incomplete or
locked installations with recovery instructions. Upgrades use a separate
environment keyed to the new bundled artifact; rerun `everval setup` after
updating the npm package.

Workspaces, credentials, hidden tests, and results stay outside the npm package.
There is no telemetry or automatic result upload. Hosted inference is an
explicit configuration choice. Local storage alone does not make inference
local.

[Workflow walkthrough](https://github.com/sumervaid/everval-ai/blob/main/docs/worked-example.md) ·
[Documentation](https://github.com/sumervaid/everval-ai/blob/main/docs/index.md) ·
[Support](https://github.com/sumervaid/everval-ai/blob/main/SUPPORT.md)

Original project material is licensed under Apache-2.0. Included dependency
notices retain their own terms. Developed by Sumer S. Vaid and contributors.
