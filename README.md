# Everval AI — On-prem continuous benchmarking for AI models and agents

[![Validation](https://github.com/sumervaid/everval-ai/actions/workflows/validation.yml/badge.svg)](https://github.com/sumervaid/everval-ai/actions/workflows/validation.yml)

[Quickstart](#try-it-without-a-key) · [Daily updates](UPDATES.md) · [All demos](docs/demos.md) · [Research finding](#inspect-the-evidence) · [On-prem deployment](docs/on-premises.md)

**54.1% lower estimated judging cost, qualified on 1,000 human-rated comparisons.**

Everval qualified GPT-6.1 Terra's standard rubric against GPT-6.1 Sol: **72.70% versus 71.15% agreement with retained human votes**, at **54.1% lower estimated cost**. It passed the preregistered cost and quality checks, using a **one-percentage-point quality-loss limit**. [Read the completed study](docs/benchmarks/judge-confirmation-2026-10-27/results.md).

> **Projected API savings:** About **$5,400 per $10,000 spent on comparable AI judging**, or **$54,000 per year on a $100,000 annual judging API budget**. **These are illustrative projections, not measured or realized customer savings.** They apply the study's 54.1% estimated cost reduction and assume switching from Sol standard to Terra standard with the same workload mix and frozen prices. Deployment, calibration and other operating costs are excluded.

Turn your own work into repeatable evaluations. Compare models, prompts and agents; track quality, cost and latency; keep checking as your system changes.

[![Everval in action: type a recurring benchmark request, inspect results and compare rankings](docs/assets/demos/recordings/everval-tour.gif)](docs/demos.md)

*One continuous 46-second workflow. [Watch the full-resolution video](docs/assets/demos/recordings/everval-tour.mp4) · [Try it locally](#try-it-without-a-key).*

| Terminal workbench · 16s | Anonymous review · 27s |
| --- | --- |
| [<img src="docs/assets/demos/recordings/terminal-workbench.gif" width="400" alt="Continuous terminal recording: search commands, switch views and inspect experiment evidence">](docs/demos/terminal-workbench.md) | [<img src="docs/assets/demos/recordings/blind-reviewer.gif" width="400" alt="Continuous blind-review recording: read anonymous answers, score them and submit a judgment">](docs/demos/blind-reviewer.md) |
| Navigate the workbench and inspect saved evidence. | Review answers before model identities are revealed. |

*These looping recordings show the actual interfaces using original demo data and a scripted reviewer account. They are separate from the research study above. [Explore all workflows](docs/demos.md) · [Browse still images](docs/demo-stills.md).*

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
| [Calibrated LLM judges](docs/judge-calibration.md) | Human-vote agreement, paired rubric comparisons, order effects, repeatability and evaluator-only references. |
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

Read the [daily updates](UPDATES.md) for dated changes, findings, verification and release status.

A preregistered study completed **6,000 judgments across 1,000 conversation families**, using **2,957 retained human votes**. Terra's standard rubric cost **$4.489838** versus **$9.778616** for Sol across 2,000 judgments per configuration: **54.1% lower estimated cost**, with observed agreement of **72.70% versus 71.15%**.

The preceding same-model comparison also passed: Terra standard cost **4.87% less** than its checklist rubric. Both ordered comparisons met the predeclared cost test and one-point quality-loss limit under both paired bootstrap and Student t methods. This qualifies specific configurations on this cohort; it does not prove identical quality or universal model superiority.

[![Three judge configurations: estimated cost per 2,000 judgments and agreement with retained human votes](docs/benchmarks/judge-confirmation-2026-10-27/cost-quality.svg)](docs/benchmarks/judge-confirmation-2026-10-27/results.md)

The full new calibration cost **$18.99**, with **$34.13 cumulatively accounted against the $50 cap**. At the measured usage, its API-only cost is projected to pay back after **3,591 future two-order comparisons** against Sol. Prices are frozen token-based estimates; customer ROI, engineering and human-label collection costs are not measured.

[Results and limitations](docs/benchmarks/judge-confirmation-2026-10-27/results.md) · [Preregistered protocol](docs/benchmarks/judge-confirmation-2026-10-27/protocol.md) · [Reproduce the statistics without paid calls](docs/benchmarks/judge-confirmation-2026-10-27/results.md#evidence-and-reproduction) · [Independent audit](docs/benchmarks/judge-confirmation-2026-10-27/independent-statistical-audit.json)

The earlier [64-question pilot](docs/benchmarks/judge-study-2026-10-27/results.md) found 9.8% lower estimated rubric cost but did not establish a quality difference. Its original results remain available; the larger confirmation uses a different dataset and uncached requests.

**Fresh repository study:** on 50 admitted tasks, the constrained one-shot setup produced 110 explicit empty patches and 24 rejected patches across 150 attempts. Only 16 submissions reached test execution. [See the audited repair results and failure breakdown](docs/benchmarks/judge-study-2026-10-27/fresh-results.md).

- [Calibrated-judge and distribution acceptance](docs/evidence/2026-10-27/README.md): live product judge calls, actual Inspect compatibility, and installed Python/npm checks.
- [Release acceptance](docs/evidence/2026-10-15/README.md): installed-package, interface, accounting, and bounded API checks.
- [Public repository pilot](docs/benchmarks/public-repositories-2026-10-15/results.md): completed attempts, exclusions, and unresolved accounting; no repair-quality improvement demonstrated.
- [Checkpoint study](docs/benchmarks/checkpoint-study-2026-10-22/results.md): three local training runs, twelve checkpoints, and 3,072 responses on generated diagnostics.

Read [four measurement decisions](docs/measurement-design.md) for the admission, comparison, grading and blinding tradeoffs, or [export a judge campaign to Inspect](docs/inspect.md).

Each record includes methods and limitations. Installation controls and generated tasks do not establish representative customer performance. [Verification scope](docs/verification.md) · [Study reproduction](scripts/posttraining/SETUP.md).

## Contribute

Start with a [runnable task pack](examples/community/README.md), a reproducible issue, or a focused improvement. Read [Contributing](CONTRIBUTING.md), [Roadmap](ROADMAP.md), and [Support](SUPPORT.md).

Licensed under [Apache-2.0](LICENSE). Imported material retains its permissions and [dependency notices](web/public/assets/third-party-notices.txt). Developed by Sumer S. Vaid and contributors.
