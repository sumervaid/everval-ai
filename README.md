# Everval AI — On-prem continuous benchmarking for AI models and agents

Turn your own work into repeatable evaluations. Compare models, prompts, and agents; inspect quality, cost, and latency; keep checking as your system changes.

Built for evaluation engineers and ML platform teams, with a terminal workbench, shared CLI/API, and optional review browser. Tasks and evidence stay in your workspace.

[Quickstart](#try-it-without-a-key) · [Product demos](docs/demos.md) · [Documentation](docs/index.md) · [On-prem deployment](docs/on-premises.md)

[![Everval: create a recurring benchmark, inspect results, and compare rankings](docs/assets/demos/recordings/everval-tour.gif)](docs/demos.md)

*One continuous session using installation controls. [Watch the full-resolution video](docs/assets/demos/recordings/everval-tour.mp4) · [Explore all workflows](docs/demos.md).*

## Try it without a key

Start from source to use the features shown in the demos. You need Linux or macOS, Git, Python 3.12+, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Run these commands in one shell:

```sh
git clone https://github.com/sumervaid/everval-ai.git
cd everval-ai
uv sync --frozen

mkdir -p "$HOME/.local/share/everval/demos"
everval_demo_dir="$(mktemp -d "$HOME/.local/share/everval/demos/walkthrough.XXXXXX")"
uv run everval demo --workspace "$everval_demo_dir"
EVERVAL_DATA_DIR="$everval_demo_dir" uv run everval tui --no-worker
```

The walkthrough compares a passing baseline, a deliberate regression, and a restored candidate across three original examples. Expect **3/3 → 0/3 → 3/3**. Open **Experiments** to inspect the failures and retained evidence; press **Ctrl+P** for commands.

The installed demo works offline without a model key or Docker. Dependency installation may use the network. These controls verify the installation; they do not measure model quality. For a persistent tmux session, install tmux and replace the last command with `EVERVAL_DATA_DIR="$everval_demo_dir" uv run everval tmux`.

For the optional browser, install a [supported Node.js release](docs/npm.md#first-session), build the assets, and open the same workspace:

```sh
npm ci --prefix web
npm run build --prefix web
uv run python scripts/build_web.py
EVERVAL_DATA_DIR="$everval_demo_dir" uv run everval serve --no-worker
```

Follow the [first-session guide](docs/quickstart.md) to create your own workspace, connect a repository, and prepare a real evaluation.

### Packaged alternative

[npm 0.3.1](docs/npm.md) includes the packaged workbench and browser. It predates chat, double-blind studies, and checkpoint monitoring; use source for those features. It requires supported Node.js, Python 3.12+, and uv.

```sh
npm install --global everval-ai@0.3.1
everval setup
everval demo
```

## What you can do

| Workflow | What Everval records |
| --- | --- |
| [Repository benchmarks](docs/guided-run.md) | Reviewed tasks, fail-before/pass-after controls, hidden checks, and frozen benchmark releases. |
| [Documents, data, and workflows](docs/scenarios.md) | Explicit task contracts, reference outputs, grading checks, and reusable collections. |
| [Rankings and economics](docs/rankings.md) | Quality, cost, latency, coverage, and the evidence behind each comparison. |
| [Recurring benchmarks](docs/chat.md) | Plain-language setup, schedules, spending limits, and local HTML/CSV/JSON/Markdown reports. |
| [Failure investigation](docs/research-loop.md) | Saved failures, regrading, candidate comparisons, and reserved holdout decisions. |
| [Double-blind review](docs/double-blind.md) | Randomized response pairs, frozen rubrics, locked judgments, and explicit identity release. |
| [Checkpoint monitoring](docs/post-training.md) | Training trajectories, paired comparisons, regression investigations, and evaluation qualification. |

For example, with the named systems and collection configured in your workspace:

> Compare Support baseline and Support candidate on Support tickets every two weeks. Save HTML and CSV reports. Budget $2 per run and $20 total.

Everval asks for missing details and retains the conversation, runs, and reports. Keep a worker running for scheduled work. [Try the chat walkthrough](docs/chat.md), or follow [one shopping-cart bug from discovery to decision](docs/worked-example.md).

## Deploy on your infrastructure

Operate one local workspace with a dedicated worker. The [on-premises guide](docs/on-premises.md) covers service templates, internal inference, authenticated browser roles, disconnected installation, backups, and upgrades.

Workspaces default to `~/.local/share/everval`; set `EVERVAL_DATA_DIR` to choose another location. Telemetry and automatic result uploads are off. Hosted inference requires explicit configuration, credentials, prices, and a budget. Repository execution requires a supported prepared Docker runtime.

SSO, tenant isolation, and distributed execution are outside this release. Double-blind review trusts workspace operators and does not conceal prompts from hosted providers. See [operating boundaries](docs/operations.md), [current scope](docs/PLAN.md), and [security](SECURITY.md).

## Inspect the evidence

- [Release acceptance](docs/evidence/2026-10-15/README.md): installed-package, interface, accounting, and bounded API checks.
- [Public repository pilot](docs/benchmarks/public-repositories-2026-10-15/results.md): completed attempts, exclusions, and unresolved accounting; no repair-quality improvement demonstrated.
- [Checkpoint study](docs/benchmarks/checkpoint-study-2026-10-22/results.md): three local training runs, twelve checkpoints, and 3,072 responses on generated diagnostics.

Each record includes methods and limitations. Installation controls and generated tasks do not establish representative customer performance. [Verification scope](docs/verification.md) · [Study reproduction](scripts/posttraining/SETUP.md).

## Contribute

Start with a [runnable task pack](examples/community/README.md), a reproducible issue, or a focused improvement. Read [Contributing](CONTRIBUTING.md), [Roadmap](ROADMAP.md), and [Support](SUPPORT.md).

Licensed under [Apache-2.0](LICENSE). Imported material retains its permissions and [dependency notices](web/public/assets/third-party-notices.txt). Developed by Sumer S. Vaid and contributors.
