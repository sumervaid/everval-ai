# Checkpoint monitoring examples

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
