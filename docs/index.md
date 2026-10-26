# Everval AI documentation

[Project overview](../README.md) · [Product tour](product-tour.md) · [First session](quickstart.md) · [Verification](verification.md)

Use your own work to decide whether an agent change is worth shipping. Everval keeps the tasks, execution evidence, grading, cost and latency together in a local workspace.

## Start here

**Want to describe a recurring benchmark in plain language?** Open [Chat](chat.md): choose models, tasks, a schedule, reports, and spending limits in one conversation.

**Prefer npm?** Use the [packaged CLI installation guide](npm.md), including disconnected setup and release verification.

**Deploying inside an enterprise?** Follow the [on-premises installation and operations guide](on-premises.md).

**Where would your team use it?** Find the operator, decision and setup path in [practical use cases](use-cases.md).

**Want the whole workflow in plain language?** Read [one shopping-cart bug, from discovery to decision](worked-example.md).

**New to Everval?** Follow the [first session](quickstart.md): run the offline demo, inspect its evidence in tmux, and choose your first real workload. No model key or Docker installation is needed for the demo.

**Ready to evaluate a repository?** The [guided run](guided-run.md) takes you from an authorized repository to a reviewed, validated benchmark and a budgeted evaluation. Progress survives closing the terminal.

**Already have an agent?** Connect it through an [agent adapter](agent-adapters.md), configure a [model provider](providers.md), or import [recorded submissions](submissions.md).

## Choose the work to evaluate

| Your starting point | Read next |
| --- | --- |
| A GitHub repository or local Git history | [Connect and discover](github.md) · [Candidate selection](mining.md) |
| Python or JavaScript/TypeScript engineering tasks | [Prepare an environment](environments.md) · [JavaScript and TypeScript](javascript-typescript.md) |
| Documents, structured data or simulated workflows | [Scenario format](scenarios.md) · [Runnable examples](../examples/community/README.md) |
| A reusable task pack | [Review and admit community packs](community-packs.md) |
| An observed production failure | [Turn production cases into reviewed tasks](production-cases.md) |

Use [benchmark validation](benchmarks.md) to understand what qualifies for a release. A discovered candidate becomes evaluation evidence only after review and successful controls.

## Compare and decide

| Question | Guide |
| --- | --- |
| How do models rank on our different benchmarks? | [Rankings matrix](rankings.md) |
| What passed, failed or remained incomplete? | [Read results](results.md) · [Investigate failures](diagnostics.md) |
| What did the run cost, and where did time go? | [Cost and latency](cost-latency.md) |
| What can we afford to run next? | [Budget planning](budget-planning.md) |
| Is the change good enough to adopt? | [Decision policies](policies.md) |
| How do people review answers independently? | [Assigned and blinded team review](team-review.md) |

## Keep checking as systems change

- [Post-training checkpoint monitoring](post-training.md) connects a training trajectory to frozen evaluations, qualified measurements, regression investigations, and held-out mix studies. Read the [completed local checkpoint study](benchmarks/checkpoint-study-2026-10-22/results.md) and [reproduce its actual training and inference](../scripts/posttraining/SETUP.md).

- [Change-linked evaluations](change-evaluation.md) bind a run to an explicit source or configuration change.
- [Autopilot](autopilot.md) schedules bounded screening and escalates qualifying changes within a cumulative budget.
- [Research lab](research-loop.md) investigates recorded failures, probes supported graders and compares explicit configuration candidates with a reserved final holdout.
- [Recurring schedules and operations](operations.md) explain workers, recovery, backups and spending controls.

## Work with a team

Start with [responsibilities and handoffs](enterprise-workflows.md). Use the [terminal reference](terminal.md) for keyboard workflows and the [team review guide](team-review.md) for authenticated browser roles.

The supported deployment is one local workspace with a dedicated worker. Read [operations](operations.md) and [sandbox boundaries](sandbox.md) before connecting real sources or providers.

## Understand or extend Everval

[Architecture](ARCHITECTURE.md) · [Interface contracts](INTERFACES.md) · [Evaluation methodology](METHODOLOGY.md) · [Current scope](PLAN.md) · [Roadmap](../ROADMAP.md) · [Contributing](../CONTRIBUTING.md)

[Verification record](verification.md) · [Release acceptance procedure](release-acceptance.md) · [Dated evidence](evidence/2026-10-15/README.md) · [Changelog](../CHANGELOG.md)

The bundled examples are original installation controls. The dated acceptance record covers single-turn API trials on original tasks; it did not evaluate autonomous public-repository repairs or establish representative customer performance.

Read the [completed public repository pilot](benchmarks/public-repositories-2026-10-15/results.md) and the [frozen protocol](benchmarks/public-repositories-2026-10-15/methodology.md) for outcomes, exclusions, task selection and controls. Use the [reproduction recipe](../scripts/benchmarks/README.md) to prepare tasks, validate controls, run within a budget and export metadata.
