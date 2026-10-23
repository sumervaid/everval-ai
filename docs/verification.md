# Version 0.3.1 verification

For the later unreleased Rankings and on-premises changes, see the separate [2026-10-16 feature verification](evidence/2026-10-16/README.md).

Release acceptance completed on 2026-10-15 in the maintained iMac workspace. The table below identifies the executed checks and their scope. The source repository was private at acceptance and is now public. Raw workspaces, hidden graders, credentials, and traces remain local.

The [release acceptance procedure](release-acceptance.md) explains how to reproduce the installed-wheel checks. The [dated evidence summary](evidence/2026-10-15/summary.json) records the final retained outcomes and their provenance. Original fixtures exercise installation and operating behavior; they do not establish representative customer performance.

| Check | Retained result and scope |
| --- | --- |
| Full Python run before final focused changes | 533 passed, including the original six paid-phase guard regressions; 21 Docker/GitHub cases skipped behind explicit opt-in flags. |
| Focused Python follow-up | 103 passed with one Docker case deliberately deselected across the affected domain, mining, community, research, demo, and three new starter-contract tests. Two separate terminal-choice cases passed in a retained 6.72-second run. These groups overlap the full suite. |
| Paid-phase admission guards | 14 tests passed in 3.35 seconds with zero HTTP calls, preserving the original six regressions and adding eight further cases. This focused group overlaps the full suite. |
| Static Python checks | Ruff lint and formatting passed for 122 files. |
| Frontend | 20 tests passed; TypeScript checking and production build passed. |
| Development dependency advisories | 71 third-party Python dependencies from the locked development environment checked with zero reported known vulnerabilities; npm audit reported zero. |
| Installed runtime advisories | 37 installed distributions recorded; 36 third-party distributions audited with zero known advisories. Only the unpublished Everval AI 0.3.1 package was excluded from advisory lookup. |
| Installed automation and recovery | Seven lifecycle, accounting, cancellation, recovery, and restore invariants verified across preserved original runs and bounded worker-readiness rechecks; the original failed phases remain retained. |
| Installed interfaces | 29 acceptance groups passed, with 40 detailed Textual checks: 12 views and six forms at 136×46 and 80×24, plus successful form submissions. Actual tmux, loopback HTTP, and Chromium journeys passed. |
| Team boundaries | Four roles exercised through 52 capability assertions, assigned blind-review payload checks, evidence-bound decisions, identity disabling/reactivation, and token revocation. |
| Final choice controls | Two checks against the rebuilt wheel confirmed visible selected values and keyboard selection at both terminal sizes. |
| Final recorded-output regrade | 13 focused acceptance groups passed. The real browser received HTTP 201 and observed retained regrades increase from two to three; the local ledger recorded zero provider admissions and cost. |
| Final repository and pack acceptance | 135 of 135 checks passed against the final installed wheel in 129.433 seconds, exercising 73 CLI commands, four original Git histories, six validation cycles, two pack workspaces, and three demo domains. |
| Explicit runtime and GitHub cases | 21 of 21 selected opt-in cases passed in 360.16 seconds, with zero failures or skips, including Jest/Vitest, private dependencies, and read-only GitHub metadata. |
| Corrected starter controls | All 19 positive controls passed and all 22 negative controls were rejected in 40.01 seconds against the final installed code and runtime. |
| Live-provider lifecycle | 15 operating-workflow checks passed using the earlier executed harness; the later local guard changes did not trigger a second paid workflow run. |
| Corrected live cohort | 114/114 attempts completed and passed with zero execution errors in 537.167 seconds: 19 original tasks, two repetitions, three model configurations. See the [cohort and spending tables](evidence/2026-10-15/README.md). |

Dependency audits are dated snapshots of known advisories, not independent security audits. The installed-runtime audit is separate from the development-lock audit: for example, the installed wheel resolved FastAPI 0.143.1 while the development lock uses 0.143.0. Both environments were audited, and the retained runtime identity records all 37 installed versions and the wheel hash.

The full interface pass preceded the final selection-control CSS fix; the affected controls were checked separately against the rebuilt wheel. Full and focused groups overlap and must not be summed as independent coverage. Original terminal captures are retained; the curated [Autopilot](assets/autopilot.png) and [configuration proposal](assets/configuration-proposal.png) images explicitly show installation controls.

The first final automation run exceeded a 25-second worker-readiness allowance in zero-rate recovery and restored-workspace startup. A fresh recheck used a bounded 90-second allowance and explicit cleanup. The retained evidence distinguishes the original failed phases from the successful rechecks; it does not measure a service-level startup guarantee.

Browser journeys produced no JavaScript exceptions or unexpected external requests. One private long mobile regrade capture contains repeated viewport tiles and was excluded from curated images; desktop regrade and earlier mobile blind-review captures were inspected. Mobile width checks are not a cross-device usability study.

## Findings incorporated into 0.3.1

Acceptance identified and fixed five issues:

1. Interrupted HTTP attempts preserve unknown usage even at zero configured token prices, so recovery cannot admit more work using a false zero-cost assumption.
2. Optimization choices require an explicit development partition before reserving the separate holdout.
3. Bounded pure-schema admission rejects `multipleOf`, avoiding a floating-point overflow path.
4. Terminal selection controls put the border on the inner control, keeping the selected text visible.
5. All nine structured starter tasks publish precise output contracts. Document, data, and workflow collections advance to 1.1.0 while old experiments retain their frozen task definitions.

## Evidence provenance

The corrected cohort's control and executed-task hashes differed only through 85 equal-value integer-to-float conversions in `Check.weight` and `Check.tolerance` defaults. Retained proof verifies that the normalized control hash matches the executed collection; the full snapshot, frozen models, and repetitions also match. Both original hashes are preserved. The future harness now freezes the validated representation; this metadata fix was tested locally without rerunning paid evaluation.

The 15 live-workflow checks use their original executed harness. Later audit-preflight and frozen plan/model-binding changes were verified by local guard tests; the paid workflow was not rerun. The dated summary discloses metadata and audit-checkpoint discrepancies while preserving the original artifacts.

The earlier 48-trial Autopilot cohort, including 18 raw grading failures under ambiguous public contracts, is retained for diagnosis, compatibility, and lifecycle evidence. The entire cohort is excluded from quality claims; it is not filtered to retain its passing answers. The corrected cohort was evaluated separately, and spending from earlier attempts remains in cumulative accounting.

## Final provider accounting

Across all acceptance phases, **179 provider calls** have a reconciled configured-rate estimate of **$0.482999**. All usage is known, with zero unresolved usage records or outstanding reservations. This total includes the earlier 65 calls and their $0.174808 estimate; the corrected 114-attempt cohort adds $0.308191. The [dated evidence page](evidence/2026-10-15/README.md) separates per-model cohort results from cumulative calls, tokens, and cost.

Reported token counts were reconciled to frozen configured rates and the cumulative ledger. These are conservative rate-based estimates, not provider invoices. External development-service billing was unavailable and is excluded, along with hosting and other tool charges.

The optional live plan used a $100 provider ceiling and a $90 cumulative admission limit. Its revised conservative projection was approximately $34, including a $19.9857515 reservation for the corrected cohort. Reservations describe admission assumptions; unused amounts were released. Ordinary tests and free installed-wheel acceptance made no paid provider calls. See [release acceptance](release-acceptance.md) for the explicit paid phases.

## Scope

These checks cover one local installation, original controlled workloads, and the stated interfaces. They do not certify adversarial isolation, multi-tenant operation, external identity providers, production availability, or representative model quality. Customer repositories, long-duration deployment behavior, independent security review, and broader provider coverage remain separate work; see [delivery status](PLAN.md).

## Historical version 0.3.0 verification

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
