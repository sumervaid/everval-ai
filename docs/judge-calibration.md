# Calibrate a judge against a declared reference

[Documentation](index.md) · [Double-blind review](double-blind.md) · [Measurement methods](posttraining-methods.md)

Measure whether an automated judge agrees with independent reference judgments, whether swapping responses changes its decision, and whether repeated judgments are stable. Keep this evidence separate from executable task correctness.

The source checkout includes a frozen calibration protocol, provider-ready request export, append-only observation import, a budgeted local runner, and JSON/HTML analysis. The published npm 0.3.1 package predates this capability.

## Try the measurement controls

~~~sh
uv run python examples/judge-calibration/offline.py \
  --workspace /absolute/path/to/a/new/private/judge-demo
~~~

The [original protocol](../examples/judge-calibration/control.json) compares a scripted content-following control with a scripted position-following control. Both response orders and repeated observations are retained. The example makes no model calls and has no human participants. Its results verify the measurement pipeline, not model quality or human agreement.

The command prints the retained campaign and report location. Re-running it does not call providers or overwrite existing observations or reports.

## Bring in a completed blind review

After a double-blind study is locked and explicitly revealed, its coordinator can export the checked reference records:

~~~sh
uv run everval judges export-study STUDY_ID \
  --output /private/study/references.json \
  --provenance /private/study/collection-provenance.json
~~~

A real human collection declaration contains source=human_collected, a description of the participants and collection, the collection_protocol, and independent_reviewers=true. This is a custodian declaration, not identity verification. Omit the declaration and the export remains unverified. Scripted installation reviews must declare source=scripted.

The bundle preserves individual votes, unanimous consensus only where it exists, source hashes and private lineage. Reviewer identifiers are pseudonymous. Existing holdouts remain holdouts; other items default to development. Do not relabel previously exposed data as a new holdout.

Use the exported items and rubric in a new protocol, with explicit judge configurations and the desired measurement claim:

~~~python
import json
from pathlib import Path
from everval import CalibrationProtocol, verify_reference_bundle

bundle = json.loads(Path("/private/study/references.json").read_text())
verify_reference_bundle(bundle)
draft = json.loads(Path("/private/study/draft-protocol.json").read_text())
draft["items"] = bundle["items"]
draft["rubric"] = bundle["rubric"]
protocol = CalibrationProtocol.model_validate(draft)
~~~

Keep the full reference bundle with the evaluator. Only the public request export belongs in the judge's environment.

## Freeze the study before judging

Create a private JSON protocol containing:

- A measurement claim, versioned rubric and rubric dimensions.
- Judge IDs, exact declared model/configuration revisions and prompt variants.
- Item IDs, task IDs and task-family IDs, with explicit development or holdout partitions.
- Canonical responses A and B, plus public task instructions and inputs.
- Reference provenance, retained independent reviewer labels where available, and any adjudicated reference label.
- Repetitions, primary endpoint and contrasts, multiplicity allocation, uncertainty settings, and budget.

Task and family identities cannot cross partitions. Identical public tasks cannot be disguised by renaming IDs. Each item is scheduled in both A/B and B/A order for every judge and repetition. Frozen protocols and recorded observations cannot be edited in place.

Reference provenance is one of human_collected, execution, scripted or unverified. Authentication does not establish human participation. Imported or legacy labels default to unverified unless their collection provenance is explicitly supplied. Original disagreements remain visible; an absent consensus label need not be invented.

Use primary_endpoint=human_votes to compare against individual retained human votes, or reference for a declared adjudicated/execution reference. Predeclare primary_contrasts when the scientific question compares judge configurations. A sample-size number alone does not establish adequate power.

~~~sh
uv run everval judges create /private/study/protocol.json
uv run everval judges show CAMPAIGN_ID
uv run everval judges requests CAMPAIGN_ID --partition dev > /private/study/dev-requests.json
~~~

The creation result includes the immutable protocol fingerprint. Full protocol inspection is a trusted local action through show --include-protocol; it exposes private reference material.

## Keep target labels out of the judge prompt

Exports include only the public task, rubric, anonymous responses and the frozen judge request. Generator identities, human votes, target preference labels and hidden executable checks are not automatically serialized into prompts.

A reference_context judge additionally receives only the separately declared reference_context field. Use that field for permitted public documentation or rubric evidence. Do not populate it with target human votes, secret tests or evaluator-only reference solutions. The standard arm omits it.

Response text remains untrusted and may itself reveal a model's identity. Prompt instructions do not guarantee resistance to prompt injection. Hosted judges receive the supplied public material; this workflow is not confidential execution.

## Collect and import judgments

Each exported job has a stable job_id, presented order, zero-based repetition and provider-ready payload. An external runner can execute those requests while retaining its own metering. Import a JSON array:

~~~json
[
  {
    "job_id": "EXACT_ID_FROM_REQUEST_EXPORT",
    "response": {
      "preference": "A",
      "rationale": "The first response satisfies the stated contract."
    },
    "cost_usd": 0.002,
    "input_tokens": 350,
    "output_tokens": 45,
    "duration_ms": 1200
  }
]
~~~

The allowed preferences are A, B, tie and abstain, referring to the displayed order. Everval converts them back to canonical response identities. Optional scores must cover both responses and every frozen rubric dimension with numbers between zero and one. Malformed JSON, invalid contracts, errors, abstentions and absent outputs remain distinct records.

~~~sh
uv run everval judges import CAMPAIGN_ID /private/study/dev-observations.json
uv run everval judges analyze CAMPAIGN_ID --partition dev
uv run everval judges release-holdout CAMPAIGN_ID \
  --judge SELECTED_JUDGE_ID --reason "Selection rule fixed from development evidence."
uv run everval judges requests CAMPAIGN_ID --partition holdout > /private/study/holdout-requests.json
uv run everval judges import CAMPAIGN_ID /private/study/holdout-observations.json
uv run everval judges analyze CAMPAIGN_ID --output /private/study/report.html
~~~

Repeat --judge for every predeclared judge retained in the held-out comparison. Selection becomes immutable before held-out requests are exported. With no development items, explicitly retain the predeclared judges before opening holdout. An exact duplicate import is idempotent; a conflicting observation is rejected. Reports use new files with owner-only permissions and never overwrite an earlier report.

## Run with configured providers

Include a frozen single-turn HTTP System snapshot in each judge to use the built-in runner. Its model must match the declared judge model. Existing provider opt-in, exact internal-origin rules, credentials in environment variables and explicit pricing requirements apply.

~~~sh
uv run everval judges run CAMPAIGN_ID --partition dev
~~~

The runner reserves each call transactionally against the campaign and workspace spending limits before execution. Usage, errors and reservations survive interruption. Unknown billing blocks additional calls; a started attempt is never automatically retried. Use judges cancel CAMPAIGN_ID to stop queued work while preserving any active call. After stopping an interrupted caller, use judges mark-interrupted CAMPAIGN_ID to retain unresolved calls explicitly.

Imported observations retain reported external costs and reservations but do not independently meter the external runner or enter those costs in the built-in provider ledger. Include external hosting costs in the study owner's separate total budget. Missing usage is not reported as zero.

## Interpret the report

Inspect coverage before agreement. The analysis retains scheduled and missing judgments, confusion matrices, class-specific errors, chance-corrected agreement where defined, reviewer disagreement, response-order effects, repeated-judgment stability and generator/judge identity diagnostics.

Human preferences and executable correctness are different reference constructs. Scripted and unverified labels cannot establish human validity. Constant-label agreement can have undefined kappa. Repeated judgments do not create new independent tasks, and small or incomplete cohorts can leave uncertainty unavailable or inconclusive.

Only the frozen held-out analysis supports the declared final comparison; development results are exploratory. A preference for a judge's own outputs is a descriptive association unless the study design separates it from genuine differences in output quality.

[Calibration implementation](../src/cvals/judge_calibration.py) · [Protocol models](../src/cvals/judge_calibration_models.py) · [Verification tests](../tests/test_judge_calibration.py)
