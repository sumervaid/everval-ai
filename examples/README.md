# Start with a small, reviewed collection

The four built-in collections demonstrate different ways to establish success:

| Collection | Scenarios | Evaluation |
| --- | ---: | --- |
| Python repair workshop | 10 | Function inputs run in a fresh Docker container; expected JSON outputs remain on the host. |
| Evidence and policy reading | 3 | Exact facts, required sources, and verbatim source quotations. |
| Structured data decisions | 3 | Numeric totals, denominator accounting, and record membership. |
| Business workflow simulations | 3 | Explicit final state, required approvals, idempotency, and unrelated records preserved. |

These are original fixtures with invented records. A reference control supplies
the saved accepted output; an incomplete control supplies an empty output.
Neither measures a model. The deliberately incorrect outputs in each scenario's
evaluator-only provenance demonstrate that its checks reject at least one
plausible error.

## Walk through a complete evaluation

1. Start the application following the root README and install the starter collections.
2. Open **Evidence and policy reading** and inspect a scenario's instructions, inputs,
   expected checks, and reference output.
3. Validate the collection. Each reference must pass; every negative control must fail.
4. Create an experiment using the reference and incomplete fixture controls.
5. Review individual check evidence before reading aggregate results.
6. Configure a provider only after deciding which material it is permitted to receive.
   A real provider experiment must declare usable pricing and a spending ceiling.

For a portable manifest, import [document_collection.json](document_collection.json).
It uses the same schema as the built-in collection and includes evaluator-only
material. Share it only with reviewers; an agent receives just the scenario's
instruction and inputs.

## Bring your own work

[policy.md](sources/policy.md) and [invoices.csv](sources/invoices.csv) can be imported
as source previews. Importing a document does not turn it into a validated benchmark.
Choose a representative task, define success independently, prepare a reference
answer, and include plausible negative controls.

Keep confidential source records and company scenarios in the configured workspace
data directory, outside Git. Use a small calibration collection while designing
checks and a separately reviewed holdout when making a final system choice.

## What the controls establish

A passing reference and failing negative control show that the current checks
distinguish those examples. They do not prove complete test coverage, statistical
significance, or consistency across repeated executions. Citation checks verify
source identity and quoted text; they do not prove a claim logically follows from
a citation. Workflow examples grade proposed state changes and do not act on a
live application.

The Python function-case grader keeps the expected answers outside the container.
The optional arbitrary-unittest mode runs tests and candidate code in one Python
process; it is unsuitable when deliberately hostile grader manipulation is in
scope. Neither mode runs imported code on the host.
