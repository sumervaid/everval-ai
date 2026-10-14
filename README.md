# Custom Vals

A local evaluation workbench for code, documents, structured data, and business workflows.

Turn work you understand into explicit tasks, compare systems on the same inputs, and inspect the evidence behind each result. Use the **web app** for guided evaluation and the **CLI** for repeatable runs. Both use the same Python engine and local workspace.

## Start here

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 24.15+ LTS, 22.22.2+, or 26+.

```sh
git clone https://github.com/sumervaid/custom-vals-enterprises.git
cd custom-vals-enterprises
make setup
uv run cvals init --starters
uv run cvals serve
```

Open **http://127.0.0.1:8765**. This repository is private; cloning requires access.

In the app, open **Experiments → New experiment**, choose **Evidence and policy reading**, and select **Reference fixture · control** and **Empty submission · control**. Start the experiment, then open **Trial evidence** to inspect the individual checks.

The four starter collections contain 19 original scenarios. These fixture controls verify the pipeline; they do not measure model performance. Document, data, and workflow examples need no model account or Docker installation. Coding checks need a separate Linux runtime and a locally available Python image; follow the [sandbox guide](docs/sandbox.md).

## Choose your next step

| I want to…                                             | Start with                                                                |
| ------------------------------------------------------ | ------------------------------------------------------------------------- |
| Run my first comparison                                | [Quickstart](docs/quickstart.md)                                          |
| Define tasks from my own work                          | [Scenario authoring](docs/scenarios.md)                                   |
| Extract coding tasks from local history                | [Repository mining](docs/mining.md)                                       |
| Connect a model endpoint                               | [Systems and providers](docs/providers.md)                                |
| Evaluate an existing agent or harness                  | [Import recorded submissions](docs/submissions.md)                        |
| Understand scores and uncertainty                      | [Reading results](docs/results.md)                                        |
| Schedule runs, retain evidence, or operate the service | [Operations](docs/operations.md)                                          |
| Understand the implementation                          | [Architecture](docs/ARCHITECTURE.md) and [interfaces](docs/INTERFACES.md) |
| See current scope and future work                      | [Delivery status and roadmap](docs/PLAN.md)                               |
| Contribute a change                                    | [Contributing](CONTRIBUTING.md)                                           |

## What you can do

- **Define and inspect tasks.** Author scenarios, preview source files, import JSON/YAML collections, and review instructions, checks, provenance, and reference outputs.
- **Mine supported Python changes.** Read local Git history into draft scenarios with a complete acceptance/rejection ledger. Validate extracted tasks separately.
- **Compare systems.** Run compatible structured-output model endpoints, fixture controls, or externally produced submissions against a frozen collection.
- **Follow every attempt.** Inspect output, individual checks, errors, timing, and available usage. Cancel work, recover interrupted execution, and rerun saved configurations.
- **Review judgments.** Add a score, dimension, rationale, and reviewer label alongside the automated result. Earlier judgments remain in the history.
- **Repeat evaluations.** Schedule recurring runs from a frozen experiment and pause or enable them in Settings. Each run has its own provider budget.
- **Export evidence.** Reproduce reports and export JSON, CSV, or self-contained HTML with explicit uncertainty, incomplete attempts, and missing costs.

The app supports a single local workspace and one active worker. Reviewer names are self-reported labels, not authenticated identities. The built-in HTTP adapter makes one structured-output request per task; interactive terminal and browser agents connect through [recorded submissions](docs/submissions.md). Workflow examples grade submitted final-state simulations rather than actions in a live business application.

## Storage and network behavior

Workspace data defaults to `~/.local/share/custom-vals`. Set `CVALS_DATA_DIR` to use another local volume. Keep private tasks, credentials, and runtime data outside the source checkout.

There is no telemetry or automatic result upload. A configured hosted provider receives the scenario instructions and public inputs; evaluator checks and reference answers are excluded. Remote providers require explicit service opt-in, declared positive pricing, and a positive experiment budget. Local storage and local inference are separate choices.

## Development

```sh
make check
make test
```

`make check` runs Python linting, formatting, type checks, and frontend checks. `make test` runs the Python test suite. Live sandbox tests require separate runtime setup and an explicit test flag.

Dependencies are locked in `uv.lock` and `web/package-lock.json`. See [Contributing](CONTRIBUTING.md) for the development workflow and [web/README.md](web/README.md) for interface development.

## License

Original project material is licensed under [Apache-2.0](LICENSE). Imported source material and task datasets retain their own permissions and attribution. Bundled web dependencies retain their [third-party notices](web/public/assets/third-party-notices.txt). This is an independent implementation and is not affiliated with Vals AI.
