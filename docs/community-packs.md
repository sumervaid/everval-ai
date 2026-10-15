# Portable community packs

A pack is a versioned JSON document containing public tasks, private deterministic checks, reference and negative controls, provenance, a declared license, and a SHA-256 content digest. Exchange it as a local file. Nothing is downloaded or uploaded automatically.

The bundled catalog contains three original installation examples: a coding edit, grounded document answers, and numeric data analysis. It is an index shipped with the source release, not a remote marketplace. Its revision and every pack digest are inspectable:

```sh
uv run everval packs catalog
uv run everval packs builtin documents /path/to/refund-pack.json --include-hidden
uv run everval packs inspect /path/to/refund-pack.json
```

Pack files contain evaluator-only material. The deliberate `--include-hidden` flag is required for both bundled and staged exports. Files are created with owner-only permissions and cannot overwrite an existing file or symlink. Inspect permissions, licenses, and sensitive content before sharing a file yourself.

## Review before admission

Staging does not create an evaluable collection:

```sh
uv run everval packs stage /path/to/refund-pack.json --reviewer operator
uv run everval packs show PACK_ID
uv run everval packs show PACK_ID --include-hidden
```

Read the task, inputs, private checks, reference output, negative controls, provenance, and license. Write a review JSON file describing the expected behavior independently for every scenario ID:

```json
{
  "rationale": "Reviewed the current policy, checked the archived distractor, and verified the controls.",
  "expected_behaviors": {
    "demo-documents": "Return the current 30-day enterprise refund window and an exact quotation from the current policy. Reject an archived-policy answer or fabricated quotation."
  }
}
```

Then admit the reviewed content:

```sh
uv run everval packs approve PACK_ID --review review.json --reviewer your-name
uv run everval packs list
```

Approval evaluates the reference and every negative control. The reference must pass and every negative control must fail. Invalid controls leave the pack staged. A successful approval atomically creates a collection and an immutable review record; the command returns its collection ID. Run that collection through the normal experiment workflow.

CLI reviewer names are trusted local labels. They are not an independently verified identity. Approval establishes one cycle of control discrimination; it does not certify publisher identity, semantic completeness, flakiness, representativeness, or legal permission. A digest establishes content identity, not trust.

Corrections are new content with a new digest. Re-importing the same digest returns the existing record. Pack content and approval records cannot be edited or deleted through this interface. Collection IDs are assigned locally; imported repository-release claims cannot become local validation attestations.

## Runtime contract and import boundaries

Version 1 supports only the built-in offline `equals`, `contains`, `numeric`, `json_schema`, `citation`, and `forbidden` checks. No archives are extracted. No installer, hook, custom grader, Python test, repository test, or imported source file is executed. Code text can be an inert input or expected output. Use the separately prepared repository workflow when execution is required.

Imports reject unknown schema fields, duplicate JSON keys, unsupported runtime contracts, schema references, digest mismatches, missing controls, and fabricated repository-release metadata. Packs are limited to 4 MiB, 100 scenarios, 20 checks per scenario, 20 negative controls per scenario, 40 nesting levels, and 50,000 JSON nodes. Community JSON schemas use a bounded structural subset: object properties, arrays, types, literal enums/constants, required fields, and numeric/string/length bounds. References, compositions, unique-items, contains, conditional/dependent, and unevaluated validators are rejected; each schema is limited to 256 nodes and 12 levels. Each scenario is limited to 512 KiB and each control output to 128 KiB. A pack-wide estimated validation-work budget also bounds control checks. Runtime and schema versions are explicit; unsupported versions fail closed.

Sources and permission statements are supplied by the author and must be reviewed. Supported explicit SPDX declarations are Apache-2.0, MIT, BSD-2-Clause, BSD-3-Clause, CC0-1.0, and CC-BY-4.0. The importer does not grant rights or infer license compatibility.

## Author and contribute

Use [the three runnable examples](../examples/community/README.md) as small format references. The full schema is available through `everval packs schema`. For a Python authoring tool, construct a `Collection` and call `cvals.community.make_pack`; it computes the canonical digest and applies the same import checks. Never hand-edit the digest after changing content without recomputing it.

A contribution should include:

- A narrow task contract and independently reviewed expected behavior.
- Original or permitted source material with attribution and an explicit license.
- Passing references and plausible failing controls, including a near miss.
- Limits of the checks and instructions for reproducing the evidence.
- A stable semantic version, content digest, and supported runtime contract.

Submit a pack proposal through the repository's contribution template. Public distribution remains a maintainer decision. Avoid sending private repositories, customer records, hidden customer graders, or credentials in an issue.

[Free walkthrough](demo.md) · [Scenario authoring](scenarios.md) · [Repository validation](benchmarks.md)
