# Daily updates

[Project](README.md) · [Documentation](docs/index.md) · [Release changelog](CHANGELOG.md)

Dated notes on shipped work, measured findings and release status. Updates are added on days with substantive changes, newest first; each entry links to its evidence. Versioned release notes remain in the changelog.

## 2026-10-27

### ~10% lower estimated judging cost

In the 64-question GPT-6.1 Terra comparison, the standard rubric cost **9.8% less** than the checklist rubric: **$0.624685 versus $0.692560** across 256 judgments per arm. Observed agreement with historical human votes was **72.30% versus 72.11%**. The quality difference was not statistically established.

This is an observed comparison of two judging configurations using conservative token rates, not an invoice reconciliation or a guaranteed production saving. [Study and methods](docs/benchmarks/judge-study-2026-10-27/results.md) · [Token and cost evidence](docs/benchmarks/judge-study-2026-10-27/operations.json).

### Shipped

- **Calibrated AI judges:** frozen development and holdout campaigns, human-reference exports, budget controls, and HTML/JSON reports. [Judge workflow](docs/judge-calibration.md).
- **Measurement tools:** agreement and reliability statistics, answer-order and repeatability checks, paired uncertainty, prospective power and self-preference diagnostics. [Methods](docs/judge-methods.md) · [Design decisions](docs/measurement-design.md).
- **Response handling fixes:** select the final answer without concatenating commentary; reject ambiguous duplicate JSON keys. The original transport had misclassified ten valid final judgments. Historical scores and the separate correction audit remain intact. [Audit](docs/benchmarks/judge-study-2026-10-27/phase-attribution.json).
- **Inspect export and the public Python interface:** evaluator references stay separate from model inputs; use `everval` and `python -m everval`, with existing `cvals` integrations preserved. [Inspect guide](docs/inspect.md) · [Upgrade notes](docs/releasing.md#upgrade-the-python-interface).
- **Verified distribution builds:** built 0.4.0 Python and npm archives, then installed and exercised the exact artifacts in isolated environments. [Acceptance and hashes](docs/evidence/2026-10-27/README.md#accepted-local-distributions).
- **Visible product demos:** moved three continuous, looping GIFs into the README hero and added this daily log. [All recordings](docs/demos.md).

[Core engineering commit](https://github.com/sumervaid/everval-ai/commit/74d1101) · [Research publication commit](https://github.com/sumervaid/everval-ai/commit/4e4774908698f30665b78e7f82d0146782c28798).

### Completed research

| Study | Coverage | Result |
| --- | --- | --- |
| Human-reference judging | 64 held-out questions; 256 judgments per rubric in the complete Terra comparison | Standard rubric cost 9.8% less; observed agreement 72.30% versus 72.11%. |
| Fresh repository repairs | 50 admitted tasks, 300 admission-control runs, 150 model attempts | Luna/Terra/Sol verified 1/3/4 repairs. Across models, 110 empty patches and 24 rejected patches left 16 submissions for execution. |
| Self-preference follow-up | 20 preselected task families; six had usable pairs, supporting 84 judgments | Insufficient coverage for a general self-bias conclusion; the original 360-slot grid retains all 276 unavailable slots. |

The coding results apply to the tested one-shot workflow with bounded source excerpts. One observed required-test ERROR remains unclassified under the frozen analysis. [Repair results and chart](docs/benchmarks/judge-study-2026-10-27/fresh-results.md) · [Self-preference coverage](docs/benchmarks/judge-study-2026-10-27/fresh-results.md#self-preference-follow-up).

### Verification and budget

- The [research publication's CI run](https://github.com/sumervaid/everval-ai/actions/runs/40582922853) passed **1,823 Python tests**, with **46 opt-in checks skipped**, plus formatting, strict typing, documentation, web and installed-package checks.
- Independent audits reproduced the fresh outcomes and all 84 self-preference judgments, costs and statistical summaries. [Fresh audit](docs/benchmarks/judge-study-2026-10-27/fresh-independent-audit.json) · [Self-preference audit](docs/benchmarks/judge-study-2026-10-27/self-preference-audit.json).
- **3,342 provider requests; $15.142929 conservatively accounted against the $50 ceiling.** This includes $0.01312325 retained for three earlier unknown-billing requests. Modal spending was zero; hardware and electricity costs are unmeasured. [Consolidated budget](docs/benchmarks/judge-study-2026-10-27/study-budget.json).

### Release status

The 0.4 source, findings and charts are available on GitHub. The accepted 0.4 package archives are built and tested; registry publication is still pending authentication. The documented published npm version remains 0.3.1. [Installation and release status](docs/npm.md).
