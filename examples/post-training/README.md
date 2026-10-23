# Checkpoint monitoring examples

For actual model training and evaluation, start with the [completed local checkpoint study](../../docs/benchmarks/checkpoint-study-2026-10-22/results.md).
It trained three QLoRA seeds, evaluated twelve checkpoints, and retained all
3,072 model responses. The [frozen executed protocol](REAL_STUDY_PROTOCOL.md),
[hashed setup and reproduction guide](../../scripts/posttraining/SETUP.md), and
[published aggregate evidence](../../docs/benchmarks/checkpoint-study-2026-10-22/summary.json)
include the negative and inconclusive findings.

## No-key installation walkthrough

Start with the complete executable installation walkthrough:

```sh
uv run everval training demo
```

Its private workspace retains `artifacts/training-run.json`, a directory of
checkpoint manifests, `training-dashboard.json`, and `training-summary.json`.
Inspect these artifacts to learn the concrete request and result formats.
The source is [training_demo.py](../../src/cvals/training_demo.py).

The walkthrough exercises ingestion, frozen evaluations, repeated-look analysis,
qualification gaps, an evidence-linked investigation, and held-out mix validation.
All checkpoints and mix observations are synthetic controls. No model training,
inference, or human study is represented.

For actual use, follow the [post-training guide](../../docs/post-training.md).
Use `everval training schema run` and the other schema commands to author your
own manifests. Connect an actual immutable checkpoint deployment through an
[agent adapter](../../docs/agent-adapters.md).

[Study protocol](STUDY.md) describes a prospective empirical experiment.
It contains no claimed results and is not evidence of a completed study.
