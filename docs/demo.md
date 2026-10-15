# A useful first minute

Run the complete workflow without an account, API key, paid provider, network request, or Docker installation:

```sh
uv sync --frozen
uv run everval demo
```

Dependency installation can require a network connection. The installed demo itself is offline. It uses three small original fixtures and the same storage, experiment engine, graders, diagnostics, comparisons, and audit chain as ordinary evaluations.

The terminal shows six steps:

1. Establish a passing reference control.
2. Change the fixture configuration to return incomplete work.
3. Evaluate a candidate that restores the passing fixture.
4. Regrade the recorded failures without a provider call.
5. Compare the same frozen task cohort.
6. Retain the evidence and a local summary.

Every result is marked **installation controls only**. The demonstration does not call an AI model, repair code autonomously, measure provider latency, or establish real-world model quality. The candidate is a known reference fixture; its configuration proposal is illustrative and never deployed.

## Retained evidence

The demo creates a new isolated directory under `$EVERVAL_DATA_DIR/demos` (or the compatibility data root). It does not add demo collections or systems to the active workspace. The terminal prints the exact location.

- `workspace.sqlite3` contains frozen experiments, every attempt, diagnostic regrades, and the audit chain.
- `demo-evidence.json` contains private grader evidence. Keep it local unless deliberately reviewed for sharing.
- `summary.json` contains outcomes, experiment and snapshot identifiers, limitations, and the full evidence digest. It excludes tasks, outputs, and hidden checks.
- `installation-badge.svg` labels the result as an installation check and includes its evidence digest. It is not a model-quality badge.

Nothing is published. Files use owner-only permissions. Use a new or empty path to choose another local location; existing evidence cannot be overwritten:

```sh
uv run everval demo --workspace /path/to/new-demo
uv run everval demo --example documents --json
uv run everval demo --example data
uv run everval demo --example coding
```

The normal coding example grades a canonical text edit, so equivalent alternate implementations may fail. For an explicitly enabled semantic execution demonstration, prepare a supported local Docker image using [the sandbox guide](sandbox.md), then run:

```sh
uv run everval demo --example coding --sandbox
```

This uses an original Python fixture with isolated behavior tests. It does not execute imported code on the host and never pulls an image implicitly. The default demo never launches Docker.

## Bring an agent

Follow [the runnable examples](../examples/community/README.md) for a code edit, a document/RAG answer, and data analysis. Each includes an inspectable pack, expected review contract, near-miss controls, and a one-command installation check.

For real evidence, review a permitted workload, validate and freeze the tasks, connect an [existing agent adapter](agent-adapters.md), declare its actual rates and a budget, and run a comparison. Keep a development cohort separate from final holdout validation. Fixtures remain useful for checking integration but must not enter claims about model performance.

To browse retained demo records, set `EVERVAL_DATA_DIR` to the printed demo directory and run `everval tui`. Restore your normal environment afterward.

[Community packs](community-packs.md) · [First real experiment](quickstart.md) · [Cost and latency](cost-latency.md)

## Share a deliberate summary

For any retained experiment, write a new local export directory:

```sh
uv run everval evidence export EXPERIMENT_ID /path/to/new-summary
```

The JSON and linked SVG badge contain counts, recorded costs and durations, opaque identifiers, and evidence hashes. They omit task text, names, model configurations, outputs, hidden checks, and traces. Inspect the summary before publishing it yourself. Fixture controls, recorded submissions, incomplete runs, and unknown accounting remain explicitly labeled. A badge records evidence availability; it is not a quality certification.
