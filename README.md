# Everval AI

![Everval AI — know what improved, know what it cost](docs/assets/everval-banner.svg)

**Continuous evaluation for agents doing real work, on your infrastructure.**

Turn repository changes, documents, data tasks, and workflow simulations into repeatable evaluations. Compare quality, cost, and latency. Keep checking as your models, prompts, and code change—all from a local terminal workbench.

[Quickstart](docs/quickstart.md) · [On-premises deployment](docs/on-premises.md) · [Use cases](docs/use-cases.md) · [Product tour](docs/product-tour.md) · [Documentation](docs/index.md) · [Evidence](docs/evidence/2026-10-15/README.md) · [Contribute](CONTRIBUTING.md)

**See the whole workflow:** [Follow one shopping-cart bug from repository to recurring checks](docs/worked-example.md).

## Try it without a key

You need Git, Python 3.12+, and [uv](https://docs.astral.sh/uv/getting-started/installation/). The source is currently private; cloning requires repository access.

```sh
git clone https://github.com/sumervaid/everval-ai.git
cd everval-ai
uv sync --frozen
uv run everval demo
```

Watch a passing reference, a deliberate regression, and a restored candidate run across three examples. Inspect failures, compare the same frozen tasks, and keep the evidence locally. **The installed demo makes zero model calls and needs no Docker or network connection.** Dependency installation may use the network.

These are installation controls, not a model leaderboard. [Open the demo in tmux](docs/demo.md) or [connect your own repository](docs/guided-run.md).

![Everval terminal comparing original reference and incomplete controls](docs/assets/terminal.png)

*The actual terminal workbench. These results are original installation controls.*

## What you can do

| Your question | Everval's workflow |
| --- | --- |
| **Did our coding agent improve?** | Reconstruct tasks from repository history, validate fail-before/pass-after behavior, freeze a cohort, and compare systems. [Repository guide →](docs/github.md) |
| **Which model works best on each of our benchmarks?** | Scan a model-by-benchmark matrix, switch quality/cost/latency, and inspect the exact evidence. [Rankings →](docs/rankings.md) |
| **Is the cheaper model good enough?** | Compare success, cost per passing attempt, and latency on the same workload. Retain errors and unknown accounting. [Economics →](docs/cost-latency.md) |
| **What broke after a change?** | Link runs to revisions, inspect failure evidence, and regrade stored outputs without another model call. [Investigate →](docs/diagnostics.md) |
| **Can we keep checking automatically?** | Schedule bounded screening, escalate to full evaluation, and inspect recurring budget projections. [Autopilot →](docs/autopilot.md) |
| **Which configuration should we ship?** | Compare saved candidates, validate the selected candidate against a reserved holdout, and review a proposal. [Research lab →](docs/research-loop.md) |
| **Can this evaluate more than code?** | Use reviewed document, structured-data, and workflow tasks with explicit success criteria. [Task packs →](examples/community/README.md) |

The terminal is the primary interface: thirteen views, a command palette, tmux sessions, and a durable local worker. The CLI and API use the same core. An optional browser gives reviewers blinded assignments and role-based access. [See the product tour →](docs/product-tour.md)

## From real work to a decision

```mermaid
flowchart LR
    A[Connect work] --> B[Review tasks]
    B --> C[Validate controls]
    C --> D[Freeze a benchmark]
    D --> E[Compare agents]
    E --> F[Decide with evidence]
    F --> G[Watch for changes]
    G --> E
```

An engineering owner curates the work. An evaluation engineer validates the tasks. A platform owner runs comparisons within a budget. A decision owner reviews the evidence. [Choose your team's workflow →](docs/enterprise-workflows.md)

Repository tasks must fail before the original fix, pass afterward, and preserve selected baseline behavior. Agents receive the starting point and issue statement; hidden checks and reference solutions stay with the evaluator. Infrastructure failures and empty test runs cannot establish validity. [Read the validation contract →](docs/benchmarks.md)

## Start your own workspace

After the demo, start a separate workspace with the original starter collections:

```sh
uv run everval init --starters
uv run everval tmux
```

Install [tmux](https://github.com/tmux/tmux/wiki) for the session launcher, or run `uv run everval tui` directly. Press **Ctrl+L** for rankings, **Ctrl+N** to connect a repository, **F9** for a guided run, and **Ctrl+P** for commands. **Ctrl+B, D** detaches tmux while queued work continues.

Python and JavaScript/TypeScript repository execution require Docker and a prepared runtime. GitHub onboarding uses Git and the [GitHub CLI](https://cli.github.com/). Node.js is only needed to build the optional browser interface. [Setup and first experiment →](docs/quickstart.md)

## Evidence you can inspect

The [0.3.1 acceptance record](docs/evidence/2026-10-15/README.md) includes:

- **114/114 single-turn OpenAI acceptance trials passed** across 19 original starter tasks, three models, and two repetitions.
- **135 repository and pack checks**, plus **21 Docker/GitHub checks** with no skips.
- Terminal, browser, access controls, worker recovery, spending limits, and backup/restore exercises.
- **$0.482999 estimated provider cost across 179 acceptance calls**, with no unsettled usage.

These single-turn acceptance trials exercised API compatibility and grading on small original tasks; models did not use tools or attempt autonomous repository repairs. The repository and Docker checks exercised installation controls. This evidence does not rank models or establish performance on customer repositories. Costs use recorded tokens and configured rates, not invoice reconciliation. Earlier attempts, excluded trials, overlapping checks, and deployment limits remain in the record.

[Results and artifacts](docs/evidence/2026-10-15/README.md) · [Reproduce acceptance](docs/release-acceptance.md) · [Verification scope](docs/verification.md)

The separate [public repository pilot](docs/benchmarks/public-repositories-2026-10-15/results.md) has finished all 16 scheduled attempts. Of 12 selected test tasks, seven passed admission and five were excluded. Review resolved 6/12 selected tasks and baseline 4/12 (6/7 and 4/7 admitted), while all four comparable graded pairs tied. The difference came from ungraded baseline API interruptions, so no repair-quality improvement is demonstrated. Four unknown requests remain fully reserved after an explicit [accounting amendment](docs/benchmarks/public-repositories-2026-10-15/recovery.md). Known cumulative provider spending is $3.367754; the budget commitment is $3.859489. Inspect the [final metadata](docs/benchmarks/public-repositories-2026-10-15/final-summary.json), [original partial export](docs/benchmarks/public-repositories-2026-10-15/partial-summary.json), [protocol](docs/benchmarks/public-repositories-2026-10-15/methodology.md), and [reproduction recipe](scripts/benchmarks/README.md).

## Your workspace, your evidence

Workspaces live locally; the default is `~/.local/share/everval`. Set `EVERVAL_DATA_DIR` to choose another disk. There is no telemetry or automatic result upload. Hosted inference is an explicit choice requiring configured prices, credentials, and a budget.

Imported code runs in supported isolated runtimes. Hidden graders, traces, credentials, and repository mirrors stay outside Git. [Storage, network, and operating boundaries →](docs/operations.md)

The supported deployment is one on-premises workspace and worker, with optional authenticated browser roles. Follow the [enterprise installation guide](docs/on-premises.md) for service templates, internal inference, access, backups and upgrades. SSO, tenant isolation, distributed execution, and compliance certification are outside this release. [Current scope](docs/PLAN.md) · [Security](SECURITY.md)

## Make it useful for your team—and others

Start with a [runnable example](examples/community/README.md), contribute a well-tested [task pack](docs/community-packs.md), or improve the path you just used. A useful contribution includes the problem, a small reproduction, and evidence of what changed.

[Contribution guide](CONTRIBUTING.md) · [Roadmap](ROADMAP.md) · [Support](SUPPORT.md) · [Changelog](CHANGELOG.md) · [Cite](CITATION.cff)

Original project material is licensed under [Apache-2.0](LICENSE). Imported work retains its own permissions; bundled web dependencies retain their [notices](web/public/assets/third-party-notices.txt). Developed by Sumer S. Vaid and contributors.
