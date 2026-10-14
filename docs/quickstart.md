# First session

Start with a local installation check, then connect real work.

## Open the workbench

```sh
uv sync --frozen
uv run everval init --starters
uv run everval tmux
```

Use `uv run everval tui` if tmux is unavailable. The terminal starts a dedicated worker. Quitting the interface leaves queued jobs and experiments intact.

## Verify the pipeline without a model account

Open **Experiments → New experiment**. Choose **Evidence and policy reading**, select **Reference fixture · control** and **Empty submission · control**, and leave the budget at zero.

After completion, inspect a trial's output and checks. The reference should satisfy the intended checks; the empty control should expose missing work. These controls test the installation. They do not establish model quality.

## Connect a repository

Open **Projects → Connect** and enter a GitHub URL, `owner/repository`, or local repository path. GitHub access uses the current `gh` authentication; the app stores no GitHub token.

Discover candidates, select one, and inspect its lineage and rejection reasons. Write a reviewed independent statement. For a prepared pytest project, inspect the environment plan and prepare its dependencies, or register an existing local image. Validate candidates and freeze a release.

Use [GitHub onboarding](github.md) for reconstruction details and [benchmark validation](benchmarks.md) for admission requirements.

## Run a system

Configure a provider under **Systems** or import an existing agent's [recorded submissions](submissions.md). Choose the release partition, systems, repetitions, and budget in **New experiment**.

A remote provider requires explicit opt-in and actual configured prices. Unknown billing halts additional requests. Begin with a small reviewed cohort and inspect failed attempts before scaling up.

## Read a decision

A completion gate alone does not establish quality. Configure explicit pass-rate, cost, latency, or regression thresholds appropriate to your use case. Read [quality policies](policies.md) and [result interpretation](results.md).

The CLI equivalents remain available through `everval --help`; all interfaces use the same workspace database.
