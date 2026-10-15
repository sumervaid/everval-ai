# Three runnable evaluation examples

Each example is an original, invented installation fixture. Run it without keys, network requests, or Docker:

| Example | Run | What fails |
| --- | --- | --- |
| [Coding agent](coding/README.md) | `uv run everval demo --example coding` | The original edit ignores item quantities. |
| [Document/RAG assistant](documents/README.md) | `uv run everval demo --example documents` | An archived policy answer or invented citation. |
| [Data analyst](data/README.md) | `uv run everval demo --example data` | Cancelled orders or unremoved refunds inflate revenue. |

Each directory includes `pack.json` with hidden checks and reference controls. These are intentionally inspectable teaching fixtures, not secret benchmarks. `review.example.json` illustrates the required review format; write your own independent review for actual imported data.

Run the pack workflow from the repository root:

```sh
uv run everval packs inspect examples/community/documents/pack.json
uv run everval packs stage examples/community/documents/pack.json --reviewer your-name
uv run everval packs show PACK_ID --include-hidden
uv run everval packs approve PACK_ID --review examples/community/documents/review.example.json --reviewer your-name
```

Use the returned collection ID with a configured system:

```sh
uv run everval run COLLECTION_ID --system SYSTEM_ID --budget 1
```

The budget is a user-selected upper admission limit, not a price estimate. Configure agreed provider rates and token ceilings first. See [agent adapters](../../docs/agent-adapters.md). Public tasks expose only the task ID, name, domain, instruction, and inputs. Hidden checks and reference outputs stay in the evaluator.

All three examples are Apache-2.0 project material. Their results demonstrate the evaluation pipeline, not model quality.
