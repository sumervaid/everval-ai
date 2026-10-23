# Post-training engineering verification — 2026-10-22

This record covers checkpoint monitoring and measurement workflows in the source
checkout. It is an engineering acceptance record, not a real-model post-training
study or a claim of improved model performance. The published npm 0.3.1 artifact
predates these additions.

## Verification

| Check | Result and scope |
| --- | --- |
| Full Python suite | 1,227 passed; 36 skipped; 10 sandbox tests deselected. One existing Starlette/httpx deprecation warning. |
| Full browser suite | 40 tests passed across 11 files. |
| Static checks | Ruff passed across source and tests; strict mypy passed across all 88 Python source modules. TypeScript and the production browser build passed. |
| Real worker integration | A local worker executed 48 fixture attempts and retained a regression decision through its periodic checkpoint analysis, without a manual analysis call. |
| Synthetic walkthrough | Three declared synthetic checkpoints, two domains, six experiments and 144 attempts; retained comparisons, repeated grading, investigation, qualification gaps and mix validation; local audit chain verified. |
| Budget accounting | New and already queued live trials stop after uncertain accounting or charges exceeding a frozen allocation. Known partial charges remain in conservative commitments. Tests used local accounting records, not provider calls. |
| Trainer ingestion | Byte hashing, mutation/replacement detection, rejected symlinks and special files, atomic private manifests, duplicate/conflict behavior, sorted arrival, queueing and parse failures exercised. |
| Terminal | Training and Rankings integration checks passed, including keyboard selection, narrow evidence dialogs, retained selection and accounting visibility. The stored demo was opened through Ctrl+T. |
| Local browser | Stored synthetic evidence rendered at 1440×1100 and 390×844. Selection survives refresh; no page errors, failed HTTP responses, mutation requests or horizontal mobile overflow in the final journey. |
| Packaging | Wheel built offline; installed package imports and bundled browser/terminal assets verified. The installed wheel executed the complete six-experiment, 144-attempt synthetic walkthrough with a valid audit chain. |
| Documentation | Local links and Git whitespace checks passed. The operator guide and prospective study protocol received implementation review. |

The [browser image](../../assets/training.png) shows the stored synthetic
walkthrough. Its provenance label is visible. It contains no real checkpoint
performance claim.

## What the controls establish

The deliberately empty checkpoint produces a retained regression against the
reference control. Restoring the reference produces an inconclusive comparison:
the finite-sample interval does not establish equivalence. The evaluation-mix
control deliberately misses a withheld regression, and retains that miss instead
of reporting an invented success.

These examples exercise the decision rules and evidence plumbing. They do not
establish that task families from an actual application are independent,
representative, or sensitive to a scientifically important change. Qualification
records keep missing human calibration visible and require researcher review.

Independent review corrected checkpoint-hashing races, ensured an unsettled
earlier checkpoint blocks later queued live work, and included partial known
charges in conservative budget commitments. Missing evidence, stale records,
changed execution settings, and holdout reuse remain explicit.

## Limits and reproduction

Run the source walkthrough with:

```sh
uv sync --frozen
uv run everval training demo
```

Follow the [operator guide](../../post-training.md) to open the generated workspace
or connect a declared training run. Read the [measurement methods](../../posttraining-methods.md)
before interpreting comparisons and the [prospective study protocol](../../../examples/post-training/STUDY.md)
before making empirical claims.

No paid model calls or actual model training were performed for this change.
The demo's provider spend is zero; invented mix costs are algorithm controls.
Training, hosting, human adjudication and development-service costs are outside
that provider-spend statement.

Skipped tests need separately enabled external services, Docker or optional
benchmark dependencies. They are not counted as passes. The browser capture
initially stalled in the installed headless shell, including on a blank control;
the installed full Chromium channel completed the inspected capture. No browser
download was needed.

The supported deployment remains one local worker. No distributed-fleet load
study, blinded human calibration study, independently attested remote weights,
or real-model checkpoint study is claimed. The npm package was not republished.

Raw logs, databases, terminal captures, screenshots and built package artifacts
remain in the iMac QA workspace. Private evidence and credentials are excluded
from Git.
