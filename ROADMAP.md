# Roadmap

[Start here](README.md) · [Implemented scope](docs/PLAN.md) · [Contribute](CONTRIBUTING.md)

Everval's direction is to make evaluation a routine part of operating an agent: use real work, preserve the evidence, and make quality and cost changes easy to understand.

These are proposed priorities, not shipped features or delivery commitments. Propose a concrete use case through a [feature request](https://github.com/sumervaid/everval-continuous-benchmarking/issues/new?template=feature-request.yml). For a small fix, go straight to the contribution guide.

## Available now

Version 0.3.1 includes a terminal/tmux workbench; reviewed repository benchmarks; document, data, and workflow tasks; live agent adapters; quality/cost/latency comparisons; bounded Autopilot; configuration studies with reserved holdouts; authenticated browser reviews; and portable task packs.

See the [product tour](docs/product-tour.md) and [dated verification](docs/evidence/2026-10-15/README.md) for behavior and evidence. One local workspace and worker remain the supported deployment boundary.

## Priorities to validate next

| Priority | User benefit | Evidence needed before calling it ready |
| --- | --- | --- |
| Representative repository pilots | Understand which real engineering tasks can be reconstructed reliably. | Permitted customer or open-source histories; published selection and rejection criteria; repeated validity controls; clearly scoped results. |
| Faster repository setup | Reach the first useful benchmark with fewer environment decisions. | Onboarding observed on fresh machines; clear recovery from missing dependencies; no execution of imported code on the host. |
| More reusable task packs | Start from a reviewed contract close to your own work. | Independent behavioral review, license/provenance checks, meaningful negative controls, and compatible runtime requirements. |
| Deeper agent integrations | Evaluate existing tools without rewriting their orchestration. | Versioned adapters, bounded output and usage, cancellation behavior, and proof that evaluator-only material stays private. |
| Recurring decision reports | See whether a model or prompt change needs attention. | Cohort compatibility, visible missing accounting, stable evidence links, and a clear distinction between observations and recommendations. |
| Broader deployment support | Operate beyond the current local workspace boundary. | A threat model and tested recovery, identity, isolation, and concurrency behavior before claiming distributed or multi-tenant support. |

## High-value ways to help

- Try the no-key demo on a fresh machine and report the first confusing step.
- Bring a small, permitted task with a plausible wrong answer that current checks should reject.
- Contribute a documented adapter for an agent you actually run.
- Improve a failure explanation or recovery instruction using a minimal reproduction.
- Reproduce a published acceptance check and report the environment and result.

An accepted contribution should improve a real workflow and include evidence proportional to the change. Popularity is not a substitute for correctness, useful examples, or maintainable interfaces.

[Contribution guide](CONTRIBUTING.md) · [Support](SUPPORT.md) · [Release process](docs/releasing.md)
