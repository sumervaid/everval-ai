# Authoring scenarios

A scenario describes work, its starting information, and evidence of success.
A collection groups scenarios into a versioned workload.

## A minimal collection

Save this as `collection.json`:

```json
{
  "schema_version": 1,
  "id": "invoice-checks",
  "name": "Invoice reconciliation",
  "domain": "data",
  "version": "1.0.0",
  "scenarios": [
    {
      "id": "sum-invoices",
      "name": "Total approved invoices",
      "domain": "data",
      "instruction": "Return a JSON object with total equal to the sum of approved invoice amounts.",
      "inputs": {
        "invoices": [
          {"amount": 20, "approved": true},
          {"amount": 22, "approved": true},
          {"amount": 100, "approved": false}
        ]
      },
      "checks": [
        {
          "id": "approved-total",
          "name": "Correct approved total",
          "kind": "numeric",
          "path": "total",
          "expected": 42,
          "tolerance": 0
        }
      ],
      "reference_output": {"total": 42},
      "provenance": {
        "source": "original example",
        "redistribution": "permitted",
        "negative_controls": [{"total": 142}, {"total": 20}]
      }
    }
  ]
}

```

```sh
uv run cvals collections import collection.json
uv run cvals collections validate invoice-checks
```

Manifests are limited to 5 MiB; collections support up to 1,000 scenarios and
each scenario up to 100 checks. YAML uses safe parsing and rejects aliases and
anchors. See [the full document example](../examples/document_collection.json)
for source-backed tasks.

## Choosing checks

| Kind | Behavior |
| --- | --- |
| `equals` | Exact JSON values, including value types and list order |
| `numeric` | A finite number within an absolute tolerance; strings and booleans fail |
| `contains` | A substring, required list members, or a dictionary subset |
| `forbidden` | The configured content must be absent; strings are case-sensitive |
| `json_schema` | JSON Schema structure; only local `$defs` references are permitted |
| `citation` | Known source IDs, required source coverage, and verbatim quotations |
| `python_tests` | Python function cases or unittest source inside the coding sandbox |

Paths use dotted object keys and zero-based list indexes, such as
`invoice.total` or `records.0.amount`. An empty path selects the complete
output. Paths are data selectors, not executable expressions.

Checks are required by default. Set `required: false` for a dimension that may
lower the weighted check score without disqualifying the scenario. If every
check is optional, all checks must pass. Define critical correctness separately
from optional quality.

For a citation check, supply source records in `inputs.sources` with `id`
and `text`. Configure the check as:

```json
{
  "id": "policy-evidence",
  "name": "Required policy evidence",
  "kind": "citation",
  "path": "citations",
  "expected": {"source_ids": ["policy-1"], "require_quote": true}
}

```

The output at `citations` must be a list such as
`[{"source_id":"policy-1","quote":"Exact text from that source."}]`.
This proves source identity and quote presence, not semantic entailment. Human
review remains useful when answers require interpretation.

## Controls and validation

Provide a known-correct `reference_output` and a nonempty
`provenance.negative_controls` list of plausible incorrect outputs. Controls
work for every domain, including imported collections. If the list is omitted,
validation uses an empty output as the only negative control.

```sh
uv run cvals collections validate COLLECTION_ID
```

Validation requires the reference to pass all required checks and every negative
control to fail the pass criteria. If no checks are required, all checks determine
the pass criteria. An unavailable verifier, missing reference, or negative
control that passes makes validation fail.

A validation run performs one cycle. It does not establish complete coverage,
flakiness resistance, representativeness, or repeated-run stability.

For Python repair tasks, the reference implementation must pass and the
historical/base implementation must fail. The preferred check shape is:

```json
{
  "id": "function-behavior",
  "name": "Function behavior",
  "kind": "python_tests",
  "path": "files",
  "expected": {
    "files": {"solution.py": "def total(values):\n    return len(values)\n"},
    "tests": "",
    "entrypoint": "solution:total",
    "cases": [
      {"args": [[2, 4]], "expected": 6},
      {"args": [[]], "expected": 0}
    ]
  }
}

```

The scenario's public `inputs.files` contains the starting source. A submission
returns `{"files":{"solution.py":"complete replacement source"}}`.
Submitted files overlay the base mapping. Cases may specify `kwargs` or use
`raises` with an exception class name instead of `expected`.
Expected values stay outside the container. See [Sandbox setup](sandbox.md).

## Inputs and evaluator data

Only scenario ID, name, domain, instruction, and inputs are sent to a model
endpoint. Checks, expected values, reference outputs, and evaluator provenance
remain in the local workspace.

```sh
uv run cvals collections tasks COLLECTION_ID --output tasks.json
```

This exports solver inputs and the collection fingerprint. A full collection
export includes grading information and should remain private when maintaining
holdouts.

## Workload quality

Use tasks drawn from real work, with clear inclusion and exclusion criteria.
Preserve source versions, permissions, time cutoffs, and reviewer decisions.
The [historical task compiler](mining.md) records extraction limitations and
leaves task wording, licensing, and validity for review.

Separate development tasks from final evaluation holdouts. State the task mix
and inspect domain-specific summaries before drawing an overall conclusion.
A small fixture collection is not a broad model leaderboard.
