# Contributing

Start with the [quickstart](docs/quickstart.md), [architecture](docs/ARCHITECTURE.md), and [scenario format](docs/scenarios.md). Keep changes focused and explain their observable behavior.

## Development setup

```sh
make setup
make check
make test
```

Python dependencies are managed by uv; the web app uses npm. Commit both lockfiles when dependencies change. The server runs on port 8765. The Vite development server proxies API requests to it.

For separate development terminals:

```sh
uv run cvals serve
```

```sh
cd web
npm run dev
```

After a production web build, run `uv run python scripts/build_web.py` to include its assets in the Python package.

## Checks that matter

Test behavior across boundaries: invalid task admission, exposed reference data, ambiguous billing, cancelled/interrupted work, snapshot drift, missing metrics, artifact limits, and browser error states. New domain checks need known-positive and known-negative examples.

Run Python unit/API/CLI tests, strict type checks, Ruff, web type checks, and component tests before submitting a change. Browser and Docker checks require their documented local runtimes.

No commercial provider credentials are needed for tests. Use controlled HTTP responses and original fixtures.

## Data and licensing

Use permitted source material and preserve attribution. Code licensing and task-dataset permissions are separate. Never commit private repositories, holdouts, credentials, raw private traces, workspace databases, or machine-specific settings.

## Pull requests

Describe the problem, resulting behavior, and validation. Include screenshots when interface behavior changes. State actual limitations instead of claiming unsupported compatibility or benchmark performance.

The project uses Apache-2.0 for original material. Reused dependencies or code must retain applicable notices.

## Isolated integration tests

With a dedicated Docker daemon and a pre-pulled Python image:

```sh
CVALS_TEST_SANDBOX=1 uv run pytest tests/test_sandbox.py tests/test_domains_mining.py -m sandbox
```

Set `CVALS_DOCKER_HOST` when using a separate daemon. The tests create original fixtures and never execute repository source on the host.
