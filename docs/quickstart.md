# Your first evaluation

## 1. Create the workspace

The commands below assume you have cloned the repository and are in its root.
To keep runtime data on another local disk, set this before initialization:

```sh
export CVALS_DATA_DIR=/absolute/path/to/custom-vals-data
```

Omit that setting to use the default `~/.local/share/custom-vals`.
On macOS, a configured path under `/Volumes` is rejected if its volume is
not mounted.

```sh
make setup
uv run cvals init --starters
uv run cvals doctor
uv run cvals serve
```

Open **http://127.0.0.1:8765**. Leave the server running; use a second terminal
for subsequent CLI commands. The web app and CLI share the configured local
workspace. The server includes one durable worker by default.

## 2. Learn the workflow with controls

Open **Collections** and select **Evidence and policy reading**
(`starter-documents`). Review its three scenarios, source documents,
checks, reference outputs, and negative controls. Validate the collection before
running the comparison.

Create an experiment using **Reference fixture · control**
(`fixture-reference`) and **Empty submission · control**
(`fixture-incomplete`), one repetition, and a zero model budget. Open
individual trials to inspect the evidence behind each check.

All three reference submissions should pass. All three empty submissions should
fail their checks. These are installation controls, not measured model results.
This collection needs neither Docker nor a model account.

The equivalent CLI workflow is:

```sh
uv run cvals systems list
uv run cvals collections list
uv run cvals collections validate starter-documents
uv run cvals run starter-documents \
  --system fixture-reference \
  --system fixture-incomplete \
  --name "Document controls"
```

The command prints the experiment ID and its recorded result. Use the listed
system IDs if your workspace contains different controls.

## 3. Bring your own tasks

Create a collection in the app or import a JSON/YAML manifest. The
[portable document example](../examples/document_collection.json) shows the full
format. It is the same collection installed by `--starters`. Re-importing its unchanged
manifest preserves that content; choose a new ID when adapting it into your own tasks.

```sh
uv run cvals collections import examples/document_collection.json
```

Read [Scenario authoring](scenarios.md) to create your own collection with a new
ID. [Example source files](../examples/sources/) can be uploaded for previews.
A source upload does not establish correct answers or create a validated
benchmark. Add explicit instructions, independent checks, and controls.

For a local Python repository with suitable history, use the restricted
[historical task compiler](mining.md). It reports extraction and rejection
reasons, and requires separate validation.

## 4. Compare a real system

Connect a compatible local model endpoint, configure an external provider
deliberately, or import results from an existing agent. See
[Systems and providers](providers.md) and [Recorded submissions](submissions.md).

Freeze the task set and budget before comparing systems. Repetitions help assess
within-task variability; they do not create more independent tasks.

## 5. Keep the evidence

Open an experiment's export controls for JSON, CSV, or a standalone HTML report.
JSON retains the full recorded evidence, CSV is a trial ledger, and HTML is a
shareable summary.

```sh
uv run cvals experiments export EXPERIMENT_ID --format html --output report.html
uv run cvals replay EXPERIMENT_ID
```

Replace `EXPERIMENT_ID` with the recorded ID. Export commands refuse to
overwrite existing files. `replay` recomputes analysis from saved evidence.
`experiments rerun` creates a new run with the original frozen configuration
and may spend money when a provider is involved.

## Troubleshooting

- **The page says the web app needs building:** run `make web` and refresh.
- **Coding checks report no runtime:** see [Sandbox setup](sandbox.md). Other domains work without Docker.
- **A model key is unavailable:** set the environment variable named in the system configuration, then restart the service.
- **External provider requests are refused:** check explicit network opt-in, HTTPS, pricing, and the experiment budget.
- **The data disk is absent:** mount it before restarting.
- **A collection edit is rejected:** increment its version when changing content. Existing experiments retain their previous snapshot.
