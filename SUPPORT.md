# Getting help

Start with the guide for the task you are trying to complete:

| Task | Where to look |
| --- | --- |
| Install and run an initial experiment | [First session](docs/quickstart.md), [free demo](docs/demo.md) |
| Use the terminal or connect a repository | [Terminal guide](docs/terminal.md), [GitHub onboarding](docs/github.md) |
| Understand rejected tasks or runtime failures | [Benchmark validation](docs/benchmarks.md), [sandbox](docs/sandbox.md), [failure investigation](docs/diagnostics.md) |
| Configure an agent or investigate usage | [Adapters](docs/agent-adapters.md), [providers](docs/providers.md), [budget planning](docs/budget-planning.md) |
| Operate, back up, or recover a workspace | [Operations](docs/operations.md) |
| Understand supported behavior and limits | [Delivery status](docs/PLAN.md), [verification record](docs/verification.md) |

## Report a problem

Search existing issues first. If you can reproduce unexpected behavior, use the [bug report form](https://github.com/sumervaid/everval-ai/issues/new?template=bug-report.yml). Include the Everval version or commit, relevant environment details, the smallest reproduction, and expected versus observed behavior.

Use original or synthetic examples. Remove credentials, private task material, hidden graders, and raw workspace exports before sharing logs or screenshots. An experiment export can contain more than the visible result summary.

For help choosing or completing a workflow, open a [usage question](https://github.com/sumervaid/everval-ai/issues/new?template=usage-question.yml). Explain the decision you want to make, your installation version, and what you have tried. Questions and attachments are public: use original or synthetic examples and remove private material before posting. This community support route does not promise a response time.

## Suggest an improvement

Use the [feature request form](https://github.com/sumervaid/everval-ai/issues/new?template=feature-request.yml) for a workflow improvement. Explain who needs it, what they cannot do today, and how success would be recognized.

The issue chooser also has dedicated forms for [task packs](.github/ISSUE_TEMPLATE/pack-proposal.yml), [graders](.github/ISSUE_TEMPLATE/grader-proposal.yml), and [agent adapters](.github/ISSUE_TEMPLATE/adapter-proposal.yml). Implementation guidance is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Sensitive reports

Follow [SECURITY.md](SECURITY.md#reporting-a-vulnerability) for vulnerabilities or data-boundary failures. Use the private reporting route described there before sharing exploit details. Community conduct concerns follow the route in the [code of conduct](CODE_OF_CONDUCT.md).
