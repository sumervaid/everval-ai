# Version 0.3 verification

Verified on 2026-10-14 on the maintained iMac workspace. No paid provider calls were made. Fixtures and demonstrations are installation/integration controls, not representative model benchmarks.

| Check | Observed result |
| --- | --- |
| Full Python suite | 524 passed; 21 Docker/GitHub checks skipped behind explicit opt-in flags |
| Python static checks | Ruff lint and formatting passed for 112 files; strict mypy passed for 74 source modules |
| Frontend | 20 tests passed; TypeScript checking and production build passed |
| Fresh package | Built 0.3.0 wheel installed offline in a separate environment; CLI version and document demo passed |
| Offline walkthrough | Three-domain demo completed with retained comparison, diagnosis, evidence and valid audit chain; zero provider calls |
| Coding execution demo | Explicit Docker-backed coding demo validated its controls; zero provider calls |
| Terminal | Headless integration exercises Autopilot, Research lab, budgets, catalog and staged-pack forms; wide and 80×24 captures inspected |
| Repository | Relative documentation links checked; source mirror remains private |

The full suite includes reservation/deduplication/recovery, zero-rate unknown usage, cancellation and late settlement, immutable holdout exclusion, configuration pricing, role restrictions, pack admission, malicious schema rejection, provider-free demonstrations and evidence export checks. The test client emits a third-party Starlette/httpx deprecation warning.

The original Python/Node Docker release suites were verified for 0.2 and are retained below as historical evidence; they were not all rerun for 0.3. The new coding demonstration did run in the configured local sandbox. New Autopilot and optimizer lifecycle tests use deterministic local doubles rather than paid hosted services. No representative customer workload, long-duration deployment study or independent security audit is claimed.

Browser-based rendering of new terminal SVG captures timed out in this environment; terminal layout was checked with native rendering and Textual's headless interaction tests. The existing optional browser frontend passed its automated tests and build.

Run `make check` and `make test` to reproduce the ordinary checks. See [deployment limits](PLAN.md), [Autopilot](autopilot.md), [research](research-loop.md), and [community packs](community-packs.md) for the implemented boundaries.

---

## Historical version 0.2 verification

Verified on 2026-10-14 using the maintained local workspace and dedicated Docker runtime. Test fixtures are original installation/integration controls, not customer benchmarks or model performance claims. No paid provider calls were made.

| Check | Observed result |
| --- | --- |
| Default Python suite | 418 passed; 21 explicit Docker/GitHub opt-in checks skipped in this run |
| Final worker, CLI, and economic-accounting regression | 32 passed, including slow worker startup and partial charges |
| Python Docker release checks | 100 passed, zero skipped, across historical admission, sandbox isolation, environments, and mining |
| Node and agent Docker checks | 70 passed, including JavaScript/TypeScript admission, Jest/Vitest, private npm/wheels, and public tool isolation |
| Frontend | 20 tests passed; TypeScript checking and production build passed |
| Static Python checks | Ruff lint/format passed; strict mypy passed for 59 source modules |
| Fresh package | Built wheel installed into a separate environment; CLI, bundled browser, TUI styles, identity, reviews, cases, and spending routes passed |
| Browser journeys | Seven isolated end-to-end workflows passed with zero JavaScript errors; narrow mobile layout checked |
| Terminal journeys | Guided setup, runtime selection, change runs, and evidence workflows passed; wide and 80×24 views visually checked |

These test groups overlap and must not be summed as a unique test count. The default suite's opt-in exclusions are separately exercised by the two Docker groups; the authenticated GitHub network test was not rerun in this release pass. Private repository visibility and source publication were checked separately. The test client emits a third-party Starlette/httpx deprecation warning; tests pass.

Browser journeys cover individual provisioning and revocation, administrator budgets, assigned blind comparison, evidence-bound review decisions, redacted production import with independently approved expected behavior, validated publication, and local regrading. Team mode remains an explicit operator choice.

## Reproduce

```sh
make check
make test
```

Docker checks require compatible images already present in the selected daemon. Preparation may download supported public packages when explicitly enabled:

```sh
CVALS_TEST_SANDBOX=1 CVALS_TEST_ENVIRONMENTS=1 \
CVALS_TEST_BENCHMARKS=1 CVALS_TEST_PYTEST_IMAGE=sha256:YOUR_PREPARED_IMAGE \
uv run pytest tests/test_benchmarks.py tests/test_sandbox.py \
  tests/test_environments.py tests/test_domains_mining.py

CVALS_TEST_NODE=1 CVALS_TEST_SANDBOX=1 \
CVALS_TEST_PYTEST_IMAGE=sha256:YOUR_PREPARED_IMAGE \
uv run pytest tests/test_node_repositories.py tests/test_agent_runner.py
```

Configure the daemon through `EVERVAL_DOCKER_HOST` or its compatibility alias. Follow the [runtime preparation guide](javascript-typescript.md) before enabling these tests. Raw logs, temporary workspaces, package environments, and private artifacts remain outside Git.

[Deployment and coverage limits](PLAN.md) · [Implementation acceptance](EXPANSION.md)
