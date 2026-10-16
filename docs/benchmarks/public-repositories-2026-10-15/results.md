# Public repository pilot: recorded outcomes

**All 16 scheduled attempts finished. Review resolved 6/7 test tasks and baseline 4/7, but all four comparable graded pairs tied. The two extra review resolutions were paired with ungraded baseline API interruptions; this does not demonstrate a repair-quality improvement.**

The [frozen protocol](methodology.md) compares pinned mini-SWE-agent prompts and control flow with an Everval arm given advance notice and one review checkpoint at its first submission attempt. Both use gpt-6.1-sol, medium reasoning, identical task images and allowances. Review consumes the existing budget.

The protocol remains the pre-run record; this page reports final attempt outcomes.

[Final metadata](final-summary.json) records the outcomes and [post-outcome accounting amendment](recovery.md). The [original partial export](partial-summary.json) is unchanged. [Reproduction instructions](../../../scripts/benchmarks/README.md) explain the runner.

## Test outcomes

**12 selected; 7 admitted; 5 excluded.** Exclusions remain in the fixed denominator. Ungraded interruptions are not observed incorrect repairs.

| Measure | Baseline | Review |
| --- | --- | --- |
| Resolved / selected | 4/12 | 6/12 |
| Resolved / admitted | 4/7 | 6/7 |
| Ungraded API interruptions | 3 | 1 |
| Graded unresolved / not run | 0 / 0 | 0 / 0 |

| Test task | Admission | Baseline | Review |
| --- | --- | --- | --- |
| pallets__flask-5063 | Admitted | Resolved | Resolved |
| pallets__flask-4992 | Excluded: checkout | Excluded | Excluded |
| pallets__flask-4045 | Admitted | Unknown usage | Unknown usage |
| psf__requests-3362 | Excluded: required tests | Excluded | Excluded |
| psf__requests-2674 | Excluded: required tests | Excluded | Excluded |
| psf__requests-2317 | Excluded: required tests | Excluded | Excluded |
| pytest-dev__pytest-11148 | Admitted | Unknown usage | Resolved |
| pytest-dev__pytest-11143 | Admitted | Resolved | Resolved |
| pytest-dev__pytest-9359 | Admitted | Unknown usage | Resolved |
| sphinx-doc__sphinx-11445 | Admitted | Resolved | Resolved |
| sphinx-doc__sphinx-10451 | Excluded: checkout / missing required results | Excluded | Excluded |
| sphinx-doc__sphinx-10325 | Admitted | Resolved | Resolved |

“Unknown usage” denotes a terminal incomplete attempt without a submission or grade. Exclusions failed the unchanged control contract and were not replaced. Requests failed required controls offline; other exclusions include upstream checkout failures. Sphinx 10451 also lacks three required regression results in each control. Pytest can emit deliberate nested failure output during its own tests; required test identities and the actual command exit determine grading, not those diagnostic lines.

**Terminal coverage: 7/7 pairs; zero pending. Quality coverage: four comparable pairs, all both resolved; zero baseline-only or review-only quality wins; three unevaluable pairs.** Both arms need a graded result with every required test observed. Thus comparison_final is true while quality_comparison_complete remains false.

## Separate development outcome

**3 selected; 1 admitted; 2 excluded.** Baseline resolved pytest 8906; review submitted an unresolved repair. Requests 2148 and Sphinx 8801 were excluded. No tuning followed. Development cost was $0.145025 baseline and $0.2240525 review, separate from the test results below.

## Cost and latency

[Detailed economics and paired measurements](economics.md) retain the calculation scope.

| All seven test attempts per arm, including interruptions | Baseline | Review |
| --- | --- | --- |
| Known provider spending | $0.7716725 | $1.744005 |
| Retained unknown reservation | $0.3723275 | $0.1194075 |
| Budget commitment | $1.144 | $1.8634125 |
| Cost coverage complete | No | No |
| Solver median / timed attempts | 91.64 s / 7 | 92.39 s / 7 |

The matched subset is Flask 5063, pytest 11143, and Sphinx 11445/10325: four pairs resolved by both arms, with complete usage.

| Same four comparable pairs only | Baseline | Review |
| --- | --- | --- |
| Provider spending | $0.7716725 | $1.1182325 |
| Summed solver time | 388.23 s | 446.25 s |
| Median solver time | 61.84 s | 115.29 s |

Review added $0.34656 across this subset. The median within-pair elapsed difference was +20.04 seconds. These are descriptive observations from four tasks. Matched figures omit unevaluable attempts and are not total spending. All-attempt medians include interruptions. Solver time includes setup, calls, commands and cleanup, and excludes grading.

| Cumulative accounting | USD |
| --- | ---: |
| Known pilot spending, development plus test | $2.884755 |
| Earlier acceptance spending | $0.482999 |
| Known cumulative spending | $3.367754 |
| Retained uncertain reservation | $0.491735 |
| Budget commitment | $3.859489 |
| User ceiling | $200 |

The pilot has 158 settled requests and four explicitly reviewed unknown requests, with none pending. Unknown request token fields remain null; exported known-token totals exclude unknown usage. Zero known cost is a lower bound, not a free call. Accounting remains incomplete, so complete campaign cost per success is unavailable. Configured-rate estimates exclude infrastructure and development services and are not invoices.

## Recovery, engineering changes and limits

The accounting amendment followed observed outcomes and permitted only the initial 11 unstarted attempts. Original plan, task/test bytes, solver, images, prices, limits and order were unchanged. Prior evidence and full unknown reservations were retained; each new unknown stopped admission for individual review. No attempt was retried. All 16 solver and 12 grader container removals were confirmed; no owned containers or launcher processes remain.

Validation improved exact source-tree reconstruction, including ignored tracked files, and removal of future history. Verified setup configuration stays outside solver access and is restored for grading. Ten physical image preflights passed. **423 runner tests passed; two opt-in Docker cases were deselected.** These engineering validations are separate from model performance. Admission checks required target/regression cases, not every test in an invoked suite.

This deliberately selected Python cohort, public-task training exposure, single attempts, native ARM environment and common edit policy limit generalization. The model alias and local timing can vary. Advance notice compares whole workflows, not identical initial solutions. Representative customer studies and repeatability remain unestablished.

The earlier [114 single-turn acceptance trials](../../evidence/2026-10-15/README.md) used original starters without autonomous repository solving. They are separate. Published metadata excludes task statements, patches, test names and raw trajectories.
