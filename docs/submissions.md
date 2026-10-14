# Importing recorded submissions

Use this workflow to evaluate outputs from a coding agent, a document pipeline, or another harness. Custom Vals grades the outputs locally and retains their supplied provenance.

## Export the task inputs

```sh
uv run cvals collections tasks COLLECTION_ID --output tasks.json
```

The export contains scenario IDs, instructions, inputs, and a collection fingerprint. It excludes evaluator checks and reference outputs.

## Produce a submission bundle

Run your system on those inputs and create:

```json
{
  "name": "Agent run 2026-10-13",
  "system_name": "Internal research agent",
  "collection_id": "YOUR_COLLECTION_ID",
  "collection_hash": "COPY_THE_64_CHARACTER_FINGERPRINT_FROM_TASKS_JSON",
  "provenance": {"harness_version": "1.2.0", "run_id": "your-run-id"},
  "submissions": {
    "SCENARIO_ID": {
      "output": {"answer": "The submitted response"},
      "cost_usd": null,
      "input_tokens": null,
      "output_tokens": null,
      "duration_ms": null,
      "trace": []
    }
  }
}
```

The example fingerprint is explanatory; a real import requires the exact collection hash. Supply measured costs and timings when known. Missing values stay unknown. Do not put secrets into traces.

## Import and grade

```sh
uv run cvals import-results submissions.json
```

The app also provides **Import results**. The bundle is checked against the collection fingerprint. Missing scenario outputs remain visible as errors; extra or mismatched scenario IDs are rejected.

Imported submissions are recorded evidence. Regrading or replaying them does not create independent model attempts. Repetition counts above one are therefore rejected for replay systems. Grading time is recorded separately from the original system's supplied duration.

External traces and usage records are assertions supplied by the importing harness; they are not independently attested by this application.
