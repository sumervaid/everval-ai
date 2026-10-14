# Production examples to regression cases

Use **Production examples** to turn authorized incidents into document, data, or workflow tasks. Observed outputs record what happened. Expected behavior comes from a separate human approval.

This workflow does not infer ground truth from a model response. Coding repairs continue to use repository discovery and benchmark validation.

## Prepare a small import

Upload CSV, JSONL, or JSON. Each example has:

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | Yes | Short task name. |
| `domain` | Yes | `documents`, `data`, or `workflow`. |
| `instruction` | Yes | The task the system should perform. |
| `inputs` | Yes | Object containing public task inputs. |
| `observed_output` | No | Object describing the actual response or result. |
| `source_id` | No | An internal incident or source identifier. |

JSON accepts an array or an object containing only a `cases` array. JSONL contains one example object per nonempty line. CSV uses these field names as headers; object cells contain properly quoted JSON.

```json
[
  {
    "name": "Invoice total",
    "domain": "data",
    "instruction": "Return the sum of the invoice amounts in a total field.",
    "inputs": {"amounts": [20, 22]},
    "observed_output": {"total": 999},
    "source_id": "reviewed-incident-42"
  }
]
```

The values illustrate the format, not a customer result. An import accepts at most 500 cases and is subject to the workspace upload limit. Extra fields, including supplied reference answers or checks, are rejected. Supply evaluator material during approval.

## Redact before storage

Choose field names and exact sensitive text to replace before staging. Default field names include password, token, API key, authorization, email, customer email, and secret. Field matching is case-insensitive and recursive. Exact-text replacement is case-sensitive and covers string values and keys.

Only redacted cases, a source digest, row provenance, and redaction counts are retained. Literal redaction values and the raw upload are not stored. The uploaded source file on your machine remains your responsibility.

Review every staged case. These explicit rules are not a general privacy detector: sensitive information can remain in unlisted fields, numerical values, free text, or embedded documents. If correction is necessary, prepare and import a corrected source before approval. Redaction that would merge distinct JSON keys is rejected.

## Approve expected behavior

Select a staged case. Inspect its redacted inputs and observed output. Enter:

1. The intended correct behavior.
2. The independent source or reasoning supporting that expectation.
3. One or more approved output fields and expected values.

The guided form supports exact text, numeric values, and required text containment. It constructs a reference response from the values you explicitly enter. It never copies the observed output into the reference.

For richer tasks, use structured reference and check definitions. Existing document, data, and workflow check kinds remain available; executable Python checks are not accepted through this importer.

The default negative control is an empty response. Add known incorrect output objects when useful. **Validate and approve expected behavior** requires the approved reference to pass and every negative control to fail. Validation failures leave the case staged.

Approval records the actor, rationale, reference, checks, controls, validation evidence, and provenance. Approved cases are immutable. Stage a corrected case separately if the intended behavior changes.

## Publish and evaluate

Name the collection and choose **Publish approved cases**. All approved, unpublished cases in that import are revalidated and published together in a transaction. Staged cases remain excluded. A case cannot be published twice.

Open the resulting collection to inspect its task and evaluator definitions, then create an experiment. Observed outputs are not included in the published scenario or sent to the evaluated system. Public task instructions and inputs remain separated from the reference and private checks.

Reference and negative-control validation establishes those specific checks. It does not establish that the human expectation is correct, the examples are representative, or every incorrect response will be rejected. Review consequential expectations against independent sources.

## Access and retention

Production imports and approvals are operator or administrator workflows. Reviewers and viewers cannot retrieve staged cases through the API. Authenticated mutations are recorded in the local audit chain.

These records can contain sensitive incident data after imperfect redaction. Include their SQLite state in workspace backups and apply the same access controls as other evaluation evidence. The application does not upload imports automatically.

