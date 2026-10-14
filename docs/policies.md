# Decision policies and comparisons

A gate answers a declared decision question against a frozen experiment. It does not establish that the benchmark represents your organization or that a model is safe for a particular deployment.

The default **Execution readiness** policy requires complete grading and zero infrastructure errors. It deliberately has no model-quality threshold: a system that produces valid but incorrect answers can pass execution readiness. Add a quality threshold when that is the decision you intend to make.

## Declare thresholds

In the terminal, select an experiment and choose **Check gates**. The form separates execution coverage, pass rate, a confidence lower bound, spending, latency, and an optional regression baseline. Empty optional thresholds are omitted. Decisions are stored with their policy and evidence fingerprints.

For repeatable automation, save a JSON policy:

```json
{
  "name": "Reviewed repository release",
  "version": "1",
  "min_completion_rate": 1.0,
  "max_error_rate": 0.0,
  "min_pass_rate": 0.85,
  "min_pass_rate_lower_bound": 0.75,
  "max_cost_usd": 10.0,
  "max_p95_duration_ms": 60000.0
}
```

These are illustrative thresholds, not recommended defaults. The cost limit applies **per eligible system**, rather than to the combined experiment. Choose thresholds from the intended workload, operational budget, and consequences of failure. See [cost and latency analysis](cost-latency.md) for measurement boundaries and a worked comparison.

```sh
uv run everval policies save ./release-policy.json
uv run everval policies list
uv run everval gate EXPERIMENT_ID --policy-id POLICY_ID
uv run everval policies decisions EXPERIMENT_ID
```

Saved policy names and versions are immutable. Saving identical content is idempotent. Changing a threshold under an existing name and version fails; create a new version. You can also evaluate an inline policy file:

```sh
uv run everval gate EXPERIMENT_ID --policy ./release-policy.json
```

The complete policy is retained with the decision, even when it was not saved as a named version. A new decision does not overwrite an earlier one. The audit trail records policy versions and gate evaluations, while the decision record retains the full result and fingerprints of the observed evidence.

## Read the outcome

| Outcome | Meaning |
| --- | --- |
| `passed` | Every selected gate has sufficient evidence and meets its declared threshold. |
| `failed` | At least one gate has evidence that violates its threshold. |
| `inconclusive` | A needed metric, compatible baseline, finalized execution, or sufficient uncertainty bound is missing. |

Systems are evaluated separately. The overall outcome fails if any eligible system fails; otherwise it remains inconclusive if any eligible system is inconclusive. A decision with no eligible systems is inconclusive.

Fixture controls and replayed external submissions are excluded by default. Their exclusions remain visible. A deliberate policy can set `include_fixture_controls` or `include_replays` to `true`, but the result remains labeled as fixture or imported evidence. Imported duration and cost are supplied by the external harness rather than independently measured here.

Pass rates count all scheduled attempts. Errors, cancelled work, and incomplete attempts do not disappear from the denominator. A running experiment cannot pass completion or quality gates. A cost or latency threshold cannot pass when its required measurements are missing. If the known cost alone exceeds a cap, the cost gate can fail even when some costs remain unknown.

The `min_pass_rate` threshold compares the observed rate. The `min_pass_rate_lower_bound` threshold compares the report's 95% lower bound instead. Single-attempt reports use Wilson intervals; repeated-attempt reports use a bounded interval over task-level averages. These intervals do not remove selection bias, contamination, shared-source dependence, or exploratory multiple comparisons.

## Compare a regression baseline

To require evidence that a run has not regressed beyond a chosen tolerance, add:

```json
{
  "name": "Repository regression check",
  "version": "1",
  "min_completion_rate": 1.0,
  "max_error_rate": 0.0,
  "max_pass_rate_regression": 0.05
}
```

```sh
uv run everval gate CURRENT_EXPERIMENT_ID   --policy ./regression-policy.json   --baseline BASELINE_EXPERIMENT_ID
```

A tolerance of `0.05` means five percentage points. The comparison uses **current minus baseline**, pairing by task and shared system identity. It averages repeated attempts within each task; twenty attempts on one task do not become twenty independent tasks.

The regression gate passes only when the paired 95% interval lies above the negative tolerance. It fails when the whole interval lies below that tolerance. If the interval crosses the boundary, the decision is inconclusive. Small task collections often cannot establish a tight regression tolerance, even when their observed rates are identical.

Comparisons require identical frozen collection fingerprints and compatible grader, harness, protocol, and verifier environment identities. Changed tasks or execution contracts produce an incompatibility result instead of a performance conclusion. System configuration changes are shown explicitly. Different system identifiers are not paired merely because their display names match.

Comparing an experiment with itself, fixture controls, or replayed submissions does not supply new independent execution evidence. Baselines with missing or inconsistent trial slots cannot silently shrink the denominator. Individual comparisons and intervals remain exploratory and unadjusted for multiple comparisons.

## Limits

Saved policies express the owner's decision criteria; they are not approved safety policies or independent certifications. Reviewer and audit actor labels belong to a trusted single-user workspace. Fingerprints and append-only records detect changes within the application contract; they do not provide externally anchored tamper resistance against the workspace owner.
