# custom-vals-enterprises

An open-source laboratory for choosing and improving coding agents on your own repositories.

**Status: planning.** This repository currently contains the product plan, public-source research, architecture decisions, and contribution guidelines. There is no runnable application yet.

The intended workflow is:

1. Import an authorized local repository or GitHub repository.
2. Turn suitable historical changes into reviewed, executable tasks.
3. Validate the tests against the original base and reference implementation.
4. Compare agents in isolated environments under the same resource budgets.
5. Inspect replayable evidence, uncertainty, cost, and failure causes.
6. Maintain a fresh evaluation suite as the repository changes.

The product will prioritize auditable task construction, separate grading, repeatable experiments, durable local execution, and useful model-selection decisions. Initial support targets Python/pytest, a single local worker, and a CLI. A browser workbench and additional languages follow a verified end-to-end pipeline.

| Document | Purpose |
| --- | --- |
| [Implementation plan](docs/PLAN.md) | Differentiators, phases, acceptance gates, and first backlog |
| [Competitive review](docs/COMPETITIVE_REVIEW.md) | Verified public behavior, limitations, uncertainties, and sources |
| [Architecture](docs/ARCHITECTURE.md) | Components, storage, execution boundaries, and experiment contracts |
| [Contributing](CONTRIBUTING.md) | Scope, evidence, and development expectations |
| [Agent instructions](AGENTS.md) | Instructions for automated work in this repository |

## Deployment intent

The complete default workflow will run on user-controlled hardware. Source checkouts, database files, task bundles, logs, and benchmark artifacts stay in configured local directories. GitHub hosts the reviewed project source and documentation.

A hosted model provider receives the code/context submitted to it; local artifact storage does not make such inference offline. Local-model and offline-replay modes are separate, explicit configurations. No hosted database, hosted benchmark service, or telemetry is required by the proposed architecture.

## Project provenance

This is an independent implementation informed by public product behavior and published evaluation research. It is not affiliated with Vals AI. The initial repository contains original planning documents and no Vals implementation code, website assets, or benchmark datasets.

Original project material is licensed under [Apache-2.0](LICENSE). Imported repository content and task datasets retain their own permissions, attribution, and licensing.
