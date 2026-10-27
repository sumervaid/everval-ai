# Judge cost and quality confirmation

**Pre-registered: 1,000 conversation families, 2,957 retained human votes, 6,000 planned judgments.** Results will be published after the fixed run finishes.

Everval will qualify or reject a researcher-prespecified cheaper judging configuration on fresh-to-this-study HelpSteer3 comparisons. Each judge sees both response orders; human votes and generator identity metadata stay with the evaluator.

| Configuration | Reasoning | Output-token ceiling |
| --- | --- | ---: |
| GPT-6.1 Terra, standard rubric | None | 768 |
| GPT-6.1 Terra, checklist | None | 768 |
| GPT-6.1 Sol, standard rubric | Low | 1,536 |

A qualifying saving must pass a **one-percentage-point quality-loss limit** and a cost-reduction test. Both bootstrap and Student t confidence bounds must pass. The rubric comparison comes first; the premium-baseline comparison is confirmatory only if the first succeeds. Incomplete grids cannot produce an adoption recommendation.

[Protocol](protocol.md) · [Frozen identities](freeze.json) · [Cohort and provenance](cohort.json) · [Power sensitivity](power.json) · [Forecast](forecast.json)

Expected new API cost is about **$24.23**, with **$15.142929** already accounted. The hard cumulative ceiling is **$50**. This planning estimate uses offline tokenization and historical output lengths. Full configured maximum output on every call would exceed the remaining budget; the runner enforces fixed financial limits.

Preflight passed **78 tests**. An [independent audit](independent-prelaunch-audit.json) checked family weighting, order mapping, inference, label isolation and accounting. [Student t values were checked against SciPy](student-t-independent-audit.json). Simulations are retained [before](analysis-validation-v1.json) and [after](analysis-validation-v2.json) the predeclared rare-disagreement guard.

NVIDIA's reference contains curated historical human preferences. Its release keeps annotations selected for agreement, and model training exposure is unknown. The study measures agreement on the specified cohort; it does not establish customer deployment performance or invoice-reconciled savings.
