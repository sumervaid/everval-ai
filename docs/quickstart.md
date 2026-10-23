# Your first session

[Documentation](index.md) · [Demo details](demo.md) · [Terminal reference](terminal.md)

Run a local walkthrough, inspect the evidence in tmux, then bring your own work. The walkthrough needs no model account, API key or Docker installation.

For the big picture first, read [one plain-language example of the entire workflow](worked-example.md).

Prefer a packaged CLI? Follow the [npm installation guide](npm.md); after setup, replace `uv run everval` below with `everval`.

## Install from source

You need Git, Python 3.12 or newer, and uv. Install tmux to use the split terminal workspace; the standalone terminal interface also works. The source repository is public.

```sh
git clone https://github.com/sumervaid/everval-ai.git
cd everval-ai
uv sync --frozen
```

This checkout includes the latest checkpoint-monitoring workflow, which is not in the published npm 0.3.1 package. See the [checkpoint operator guide](post-training.md) to use it, or the [measured study and reproduction recipe](benchmarks/checkpoint-study-2026-10-22/results.md) for the local training experiment.

Dependency installation may use the network. The installed default demo runs offline.

## Run the demo

Run these commands in the same shell. They choose a fresh, persistent directory for each walkthrough, so earlier evidence remains intact:

```sh
mkdir -p "$HOME/.local/share/everval/demos"
everval_demo_dir="$(mktemp -d "$HOME/.local/share/everval/demos/walkthrough.XXXXXX")"
uv run everval demo --workspace "$everval_demo_dir"
```

The default walkthrough evaluates three original examples: a code contract, a grounded document answer and a data transformation. Expect **3/3 passing baseline controls, 0/3 after the deliberate regression, and 3/3 for the restored candidate**. It then regrades the saved failures, compares the frozen cohort and prints the retained summary location.

The reported provider calls and provider spend are both zero. These fixtures verify the installation; they do not measure model quality. See [demo evidence and limitations](demo.md#retained-evidence) for what is saved.

## Open that evidence in tmux

In the same shell:

```sh
EVERVAL_DATA_DIR="$everval_demo_dir" uv run everval tmux
```

The environment prefix selects this demo workspace for the launch. It does not change your normal workspace setting. If tmux is unavailable, browse the completed records without starting a worker:

```sh
EVERVAL_DATA_DIR="$everval_demo_dir" uv run everval tui --no-worker
```

Press **F4** for Experiments. Open **Demo · changed**, then **Trial evidence** to inspect a failed output and its checks. Compare it with **Demo · candidate**. Use **Ctrl+P** to search actions; detach from tmux with **Ctrl+B**, then **D**.

If you already ran the one-command demo from the README, use its printed **Workspace** path instead of `$everval_demo_dir`. For example:

```sh
EVERVAL_DATA_DIR="/absolute/path/printed/by/the/demo" uv run everval tui --no-worker
```

## Start your own workspace

Launch without the demo override:

```sh
uv run everval init --starters
uv run everval tmux
```

The starter collections are labeled controls, separate from the earlier demo. The terminal starts a dedicated worker; closing or detaching the interface leaves queued work intact.

| What you want to evaluate | Next step |
| --- | --- |
| Work from a GitHub repository or local Git checkout | Choose **Start guide** on Overview and follow [your first real evaluation](guided-run.md). |
| Document answers, structured data or workflow outputs | Inspect the [runnable examples](../examples/community/README.md), then [review and admit a task pack](community-packs.md). |
| An agent you already run | Connect an [agent adapter](agent-adapters.md) or import its [recorded submissions](submissions.md). |

GitHub access uses your existing `gh` authentication. Repository validation needs a supported prepared Docker runtime; the guide walks through readiness and preparation. Connecting a repository alone does not validate its tasks.

## Make the first real decision

Begin with a small reviewed development cohort. Configure a system's actual prices, explicit provider opt-in, repetition count and budget before admitting paid work. Reserve holdout tasks for an independent final decision.

Inspect failed attempts as well as the score. Use [cost and latency](cost-latency.md) to understand the tradeoff and [decision policies](policies.md) to record your quality, budget and regression thresholds. The [result guide](results.md) explains incomplete evidence and uncertainty.

[Browse all guides](index.md) · [Continue with a repository](guided-run.md)
