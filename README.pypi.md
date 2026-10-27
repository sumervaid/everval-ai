# Everval AI

**On-prem continuous benchmarking for AI models and agents.**

Build repeatable evaluations from repository tasks, documents, data and workflows. Compare quality, cost and latency, retain the evidence, and keep checking as models and prompts change.

[Documentation](https://github.com/sumervaid/everval-ai/blob/main/docs/index.md) · [Research finding](https://github.com/sumervaid/everval-ai/blob/main/docs/benchmarks/judge-study-2026-10-27/results.md) · [Source and release status](https://github.com/sumervaid/everval-ai)

## Installation

Released distributions install into a dedicated Python 3.12+ environment on Linux or macOS:

~~~sh
python -m pip install everval-ai
everval --version
everval demo
~~~

The installed demo needs no model key or Docker and makes zero provider calls. It exercises original installation controls. Dependency installation may use the network.

For an unreleased source version, follow the [source quickstart](https://github.com/sumervaid/everval-ai#try-it-without-a-key).

## Start a workspace

~~~sh
everval init --starters
everval tui
~~~

Use `everval tmux` for a persistent terminal session with tmux installed, `everval serve` for the bundled browser, and `everval --help` for the command reference.

- [Repository evaluations](https://github.com/sumervaid/everval-ai/blob/main/docs/guided-run.md) admit tasks through fail-before/pass-after controls and separate hidden checks from model inputs.
- [Judge calibration](https://github.com/sumervaid/everval-ai/blob/main/docs/judge-calibration.md) measures agreement with retained human votes, order sensitivity, repeatability and paired rubric effects.
- [Inspect interoperability](https://github.com/sumervaid/everval-ai/blob/main/docs/inspect.md) exports frozen samples with references kept in evaluator fields.
- [On-premises deployment](https://github.com/sumervaid/everval-ai/blob/main/docs/on-premises.md) covers workers, internal inference, access controls, backups and disconnected installation.

The public Python namespace is `everval`; existing `cvals` imports remain compatible. Repository execution requires Docker and a prepared runtime. Hosted inference requires explicit credentials, rates and spending limits. Workspace storage remains separate from inference.

Original project material is licensed under [Apache-2.0](https://github.com/sumervaid/everval-ai/blob/main/LICENSE). Included dependencies and imported data retain their own terms.
