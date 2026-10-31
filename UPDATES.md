# Daily updates

[Project](README.md) · [Documentation](docs/index.md) · [Release changelog](CHANGELOG.md)

Dated notes on shipped work, measured findings and release status. Updates are added on days with substantive changes, newest first; each entry links to its evidence. Versioned release notes remain in the changelog.

## 2026-10-31

### Projected judging API savings, with explicit assumptions

Added an illustrative savings callout beside the [README finding](README.md): approximately **$5,400 per $10,000 in comparable baseline judging API spend**, or **$54,000 per year on a $100,000 annual baseline**. These are projections from the [study's frozen cost totals](docs/benchmarks/judge-confirmation-2026-10-27/results.json), **not measured or realized customer savings**.

The callout assumes switching from Sol standard to Terra standard with the same workload mix and frozen prices as the study, and excludes deployment, calibration and other operating costs. It links the business example to the completed research without claiming customer ROI.

Verification: checked the projections against the frozen cost totals; all 903 local links across 115 Markdown files and Git whitespace checks passed. No additional model calls were made.

## 2026-10-28

### 54.1% lower estimated judging cost — qualified on 1,000 families

Published the completed, preregistered judge confirmation: **6,000 of 6,000 judgments**, **1,000 conversation families**, and **2,957 retained historical human votes**. Terra's standard rubric cost **54.1% less than Sol's standard rubric**, with **72.70% versus 71.15% observed human-vote agreement**. Both prescribed confidence methods passed the cost test and the one-percentage-point quality-loss limit.

The original standard-versus-checklist comparison also passed, with **4.87% lower estimated cost** and **72.70% versus 72.34% agreement**. The pilot's 9.8% saving remains a separate historical result. The new result qualifies the tested configuration under the declared tolerance; it does not establish exact quality equivalence, universal superiority or customer ROI.

[Full results and chart](docs/benchmarks/judge-confirmation-2026-10-27/results.md) · [Unchanged preregistered protocol](docs/benchmarks/judge-confirmation-2026-10-27/protocol.md) · [Frozen analysis](docs/benchmarks/judge-confirmation-2026-10-27/results.json)

### Evidence and reproducibility

- Independent audits reconciled all 6,000 requests and reproduced vote/order weighting, paired bootstrap and Student t bounds, cost totals, comparison ordering and break-even arithmetic. [Data audit](docs/benchmarks/judge-confirmation-2026-10-27/independent-data-audit.json) · [Statistical audit](docs/benchmarks/judge-confirmation-2026-10-27/independent-statistical-audit.json).
- Added derived [family measurements](docs/benchmarks/judge-confirmation-2026-10-27/family-metrics.json) and a [statistical reproduction command](scripts/judge_study/reproduce_confirmation.py), so the decision can be checked without another paid run. Raw conversations, human references and provider responses remain private.
- Verification: **21 new reproduction tests passed**; the full public-data reproduction matched the original statistics with site packages disabled and the API key unset. Formatting and local documentation links passed, and the chart was visually reviewed.
- Updated the README finding and decision chart while retaining the continuous product recordings. Costs are estimates from reported tokens; all configurations, abstentions, order inconsistencies and descriptive domain results remain visible.
- Completed the detached run and analysis on the maintainer's remote workspace. Model calls finished October 27 at 9:22 p.m. Eastern; the saved analysis finished at 9:38 p.m. Review and publication followed on October 28.

### Budget

**$18.988029 for the new run; $34.130958 cumulatively accounted against the $50 ceiling.** All 6,000 new requests settled, with no new pending or uncertain obligations. The earlier balance still includes $0.01312325 for three older unknown-billing requests. No further model calls were needed for the audits or publication. [Final run accounting](docs/benchmarks/judge-confirmation-2026-10-27/budget-status.json).

The 0.4 source publication and registry package status remain separate. This update publishes research evidence and documentation; it does not claim a new npm or PyPI release.

## 2026-10-27

### Larger judge confirmation — registered

Prepared a fresh **1,000-family, 6,000-judgment** test using **2,957 retained human votes**. The [protocol](docs/benchmarks/judge-confirmation-2026-10-27/protocol.md) fixes a one-percentage-point quality-loss tolerance, cost tests, comparison order and failure handling before inference. It compares Terra's standard/checklist rubrics and a Sol baseline through the complete calibration workflow.

Preflight passed **78 tests** plus independent statistical, label-isolation and accounting audits. Expected new API cost is about **$24.23**; the cumulative **$50** ceiling includes all **$15.142929** previously accounted. Results and final spending will be added when the fixed run ends. [Study record](docs/benchmarks/judge-confirmation-2026-10-27/README.md).


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
