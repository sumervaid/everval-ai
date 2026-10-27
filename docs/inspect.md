# Use Everval judge campaigns with Inspect

[Documentation](index.md) · [Judge calibration](judge-calibration.md)

Everval exports a frozen judge campaign into [Inspect's dataset format](https://inspect.aisi.org.uk/datasets.html). This connects an existing task cohort, blinding protocol and reference record to an Inspect solver or scorer. It does not convert a repository task into a complete execution sandbox.

## Install and export

From a source checkout, install the pinned optional dependency:

~~~sh
uv sync --extra inspect --frozen
uv run everval judges export-inspect CAMPAIGN_ID \
  --judge JUDGE_ID --partition dev --output /private/study/inspect-dev
~~~

The destination must be new. Holdout exports require the campaign's frozen judge selection and explicit holdout release, just as normal request exports do.

The directory contains:

| File | Who receives it |
| --- | --- |
| samples.jsonl | Solver: task, rubric, anonymous responses and non-reference metadata. |
| references.jsonl | Evaluator only: reference labels in the displayed response order. |
| manifest.json | Custodian: protocol fingerprint, counts and file hashes. |

The exporter does not include generator model identities, reviewer names, private provenance, hidden tests or reference labels in model input. Supplied response text and optional public rubric context can still contain identity clues. The custodian must review that content.

## Load a real Inspect dataset

~~~python
from pathlib import Path
from everval import load_inspect_dataset

dataset = load_inspect_dataset(Path("/private/study/inspect-dev"))
print(len(dataset), dataset[0].id)
~~~

The loader verifies file fingerprints and sample identities, then creates Inspect Sample objects. Targets are evaluator fields; they are not appended to prompts. Do not give the solver the export directory or a custom tool that can read the reference file.

Only A, B and tie references enter the default scorer dataset. Unlabeled, disputed-without-consensus and abstained references remain in the export and its denominator but are omitted by this loader. Report that coverage: a score on the labeled subset does not estimate agreement with all individual human votes. Use Everval's human_votes endpoint for the latter.

Inspect's default text scorers do not parse Everval's JSON preference contract. A custom scorer should read the preference field, validate A/B/tie/abstain, and compare it with Sample.target. Preserve invalid or missing decisions as failures; do not silently drop them. Solver completion is the structured judgment, not a code patch.

The hashes detect changes relative to the local manifest. They do not authenticate an untrusted exporter or replace protected evaluator storage. The adapter runs no setup scripts and does not install tools from the sample.

For the established repository benchmark integration, see [SWE-bench reproduction](../scripts/benchmarks/README.md). Repository execution and human preference calibration measure different properties.
