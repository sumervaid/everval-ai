# Contributing to Everval AI

Start with a small, reproducible improvement: fix a bug, clarify a workflow, improve a task contract, or add a tested integration. The [support guide](SUPPORT.md) helps route questions and reports; [project decisions](GOVERNANCE.md) explains how proposals become changes.

## Choose a starting point

- **Fix a bug:** describe the smallest reproduction and the expected behavior in a [bug report](https://github.com/sumervaid/everval-ai/issues/new?template=bug-report.yml).
- **Improve a workflow:** open a [feature request](https://github.com/sumervaid/everval-ai/issues/new?template=feature-request.yml) with the user's task and a concrete success criterion.
- **Add evaluation material:** read the [scenario contract](docs/scenarios.md) and [community pack guide](docs/community-packs.md). Use the dedicated task-pack, grader, or adapter proposal form.
- **Improve documentation:** a focused pull request with corrected steps or examples is enough.

For a large change, describe the intended behavior and tradeoffs in an issue or draft pull request before investing in the full implementation. Read the [architecture](docs/ARCHITECTURE.md), [delivery status](docs/PLAN.md), and [security boundaries](SECURITY.md) for the affected area.

## Set up development

Use Python 3.12+, uv, and Git. Node.js and npm are needed for web development and the complete check suite. tmux is optional; Docker is needed only for isolated coding evaluation and its opt-in tests.

Keep development experiments separate from your normal workspace:

```sh
export EVERVAL_DATA_DIR="$HOME/.local/share/everval-dev"
make setup
uv run everval demo
```

`make setup` installs locked Python and web dependencies, builds the web app, and copies its assets into the Python package. For Python-only work, `uv sync --frozen` is sufficient.

Run the interfaces from the checkout:

```sh
uv run everval tui
# Or, with tmux:
uv run everval tmux
```

For web development, run `uv run everval serve` in one terminal and `cd web && npm run dev` in another. Vite proxies API calls to the loopback service on port 8765. Run `make web` to rebuild the web interface served by the Python package.

## Verify the change

Run the smallest checks that establish the changed behavior first. For a code pull request, the complete local checks are:

```sh
make check
make test
```

`make check` runs local documentation link checks, Ruff, formatting checks, strict Python typing, web typing, and web component tests. `make test` runs the Python suite. Ordinary tests use original fixtures and controlled responses; no paid provider credentials are required.

Docker, live GitHub, browser, and paid-provider acceptance have separate prerequisites. Run the relevant opt-in checks when changing those boundaries, following the [sandbox guide](docs/sandbox.md) and [release acceptance procedure](docs/release-acceptance.md). Record anything you could not run and why. Do not treat a skipped integration check as a pass.

For documentation-only changes, run `make docs-check` and `git diff --check`, then review the changed commands and rendered layout.

## Preserve the evaluation contract

A useful regression test demonstrates observable behavior and can fail for the original defect. Pay particular attention to:

- Public task material staying separate from private checks and reference solutions.
- Before/after validation, nonempty tests, and infrastructure errors.
- Frozen snapshots and versioned task, model, harness, and pricing definitions.
- Failed or interrupted attempts, cancellation, unknown usage, and budget admission.
- Resource limits and untrusted repository or model content.

New graders need passing examples and realistic failing controls. Public task instructions must state the required output contract without revealing the answer. Changed task content needs an appropriate collection version; historical experiments must remain reproducible.

## Open a pull request

Explain the problem, resulting behavior, and verification. Link the relevant issue when one exists. For interface changes, include a screenshot or short recording made with original fixtures. Describe compatibility or migration effects when stored data, task contracts, or adapter protocols change.

Keep unrelated cleanup separate. Update the relevant guide when behavior changes. Claims about quality, cost, latency, or compatibility need reproducible evidence and its limits.

The [pull request template](.github/pull_request_template.md) covers these details. Review follows the [project decision process](GOVERNANCE.md) and [community standards](CODE_OF_CONDUCT.md).

## Data and attribution

Keep credentials, private repositories, hidden customer checks, holdouts, model traces, workspace databases, and machine-specific configuration out of Git and issue attachments. A private source repository is still a shared surface. Use synthetic or explicitly shareable reproductions; see [security reporting](SECURITY.md#reporting-a-vulnerability) for sensitive findings.

Original project material uses [Apache-2.0](LICENSE). Imported repositories and task datasets retain their own permissions. Preserve applicable attribution and third-party notices. Update the relevant lockfile when changing dependencies; do not refresh unrelated dependencies as part of an unrelated fix.
