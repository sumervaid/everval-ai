# Public repository pilot: incomplete results

**This comparison stopped with unresolved provider usage. It does not demonstrate an improvement from the review checkpoint.** One test pair completed and tied; six admitted pairs remain pending. The separate development pair favored the baseline.

The [frozen protocol](methodology.md) compares pinned mini-SWE-agent prompts and control flow with an Everval arm that receives advance notice and one review checkpoint at its first submission attempt. Both use `gpt-6.1-sol`, medium reasoning, identical task images and allowances. Review consumes the existing budget. [Metadata](partial-summary.json) retains the frozen identities and outcomes; [reproduction instructions](../../../scripts/benchmarks/README.md) describe the runner.

## Test cohort

**12 selected; 7 admitted; 5 excluded.** Both arms have one observed success: **1/12 selected, 1/7 admitted**. These are incomplete counts. Excluded tasks remain in the fixed denominator; pending pairs count as neither wins nor losses.

| Test task | Admission | Baseline | Review |
| --- | --- | --- | --- |
| pallets__flask-5063 | Admitted | Resolved | Resolved |
| pallets__flask-4992 | Excluded: checkout | Excluded | Excluded |
| pallets__flask-4045 | Admitted | Incomplete: unknown usage | Not run |
| psf__requests-3362 | Excluded: required tests | Excluded | Excluded |
| psf__requests-2674 | Excluded: required tests | Excluded | Excluded |
| psf__requests-2317 | Excluded: required tests | Excluded | Excluded |
| pytest-dev__pytest-11148 | Admitted | Not run | Not run |
| pytest-dev__pytest-11143 | Admitted | Not run | Not run |
| pytest-dev__pytest-9359 | Admitted | Not run | Not run |
| sphinx-doc__sphinx-11445 | Admitted | Not run | Not run |
| sphinx-doc__sphinx-10451 | Excluded: checkout and missing required results | Excluded | Excluded |
| sphinx-doc__sphinx-10325 | Admitted | Not run | Not run |

Exclusions failed the unchanged control contract and were not replaced. Requests tasks did not pass the required controls in the offline environment; other exclusions include failures in the upstream checkout sequence. Sphinx 10451 also lacks three required regression results in each control. Of seven admitted pairs, one has both outcomes: both resolved; zero baseline-only, review-only, or neither-resolved pairs; six pending.

**The sole completed pair, Flask 5063:**

| Measure | Baseline | Review |
| --- | --- | --- |
| Resolved | Yes | Yes |
| Estimated provider cost | $0.1405125 | $0.32356 |
| Solver elapsed time | 61.93 s | 158.51 s |
| Provider calls | 11 | 17 |

Review reached its checkpoint. It cost more and took longer on this task, with the same resolution outcome.

The export's **all-attempt medians** are 76.78 s for baseline (two timed attempts) and 158.51 s for review (one). Baseline includes the interrupted 91.64 s attempt; these unequal samples are not the paired timing comparison above. Solver time includes setup, calls, commands and cleanup, and excludes grading.

## Separate development results

**3 selected; 1 admitted; 2 excluded.** The sole admitted pair finished: baseline resolved; review submitted an unresolved repair. No tuning followed.

| Development task | Baseline | Review |
| --- | --- | --- |
| psf__requests-2148 | Excluded: required tests | Excluded: required tests |
| pytest-dev__pytest-8906 | Resolved | Unresolved |
| sphinx-doc__sphinx-8801 | Excluded: checkout | Excluded: checkout |

Development cost was $0.145025 baseline and $0.2240525 review; elapsed time was 59.86 s and 78.27 s respectively. These outcomes are separate from the test counts.

## Accounting stop

The next baseline request returned HTTP 400 for an output limit without usage data. Flask 4045 has no submission or grade; it is incomplete, not an observed incorrect repair. The runner retained the uncertain reservation and blocked further calls.

| Accounting | USD |
| --- | ---: |
| Known new pilot spending | $0.833150 |
| Earlier acceptance spending | $0.482999 |
| Known cumulative spending | $1.316149 |
| Budget commitment, including uncertain reservation | $1.4348515 |
| User ceiling | $200 |

Evidence agrees with the ledger, but **cost coverage is incomplete**: one request remains uncertain. Its recorded zero known cost/tokens is a lower bound, not a free call. The commitment includes $0.1187025 reserved for uncertainty, not a confirmed charge. Costs use configured rates, exclude infrastructure and development services, and are not invoices.

**Authoritative usage reconciliation is required before continuation.** Frozen limits were not changed, and no started attempt was rerun.

## Verified engineering changes and limits

Solver snapshots now retain ignored tracked files, verify exact tree identity and remove future history. Verified upstream setup configuration is isolated from the solver and restored only for grading; explicit rebuilds preserve earlier evidence. **338 runner tests passed; two opt-in cases were not run in that suite.** Ten physical image preflights passed tree, history, artifact-boundary, isolation and cleanup checks. These are engineering validations, separate from agent performance. Admission checks official target and required regression tests; it does not guarantee that the whole invoked suite passes. Some pytest diagnostics include deliberate failures in nested test runs.

This deliberately selected Python cohort, native ARM environment, common edit policy and one attempt per arm cannot establish repeatability, an official leaderboard score or broad superiority. Public tasks may have appeared in training; the model alias can change. Advance notice compares whole workflows, not identical initial solutions. Timing depends on local hardware and scheduling.

The earlier [114 single-turn acceptance trials](../../evidence/2026-10-15/README.md) exercised original starter tasks without model tools or autonomous repository solving. They are separate from this pilot. Published metadata excludes task statements, patches, test names and raw trajectories.
