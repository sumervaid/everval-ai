# Reproduce the local checkpoint study

This optional research recipe trains actual LoRA adapters and evaluates their
outputs through Everval's HTTP adapter and durable experiment engine. It uses
original generated diagnostics. It does not establish representative customer
performance or frontier-model training behavior.

Use an Apple Silicon Mac with Python 3.12.14, a compatible macOS release, and
[uv](https://docs.astral.sh/uv/getting-started/installation/). The original host
had 8 GB unified memory; reserve several GB of disk for environments, the
approximately 988 MB upstream weights, conversion, adapters and private evidence.
This recipe makes no paid provider calls. Download and installation require
network access; training and evaluation run locally. Hardware, electricity and
operator time are not priced.

Run these commands from the checked-out repository revision containing this
study. Choose a new absolute artifact directory outside Git. Do not reuse the
original study directory or overwrite retained evidence.

## Install the exact runtime

The public runtime lock pins all 72 third-party packages in the original study
environment. The separate build lock pins the wheel-building toolchain for this
reproduction recipe. It was added after the original wheel was built, so it does
not retroactively establish that wheel's build toolchain. Each run binds the
wheel's actual SHA-256 and installed evaluator bytes before evaluation.

```sh
EVERVAL_STUDY_ROOT="/absolute/private/path/everval-checkpoint-study"
mkdir -p "$EVERVAL_STUDY_ROOT"
unset PYTHONPATH

uv venv --python 3.12.14 "$EVERVAL_STUDY_ROOT/build-venv"
uv pip sync --python "$EVERVAL_STUDY_ROOT/build-venv/bin/python" \
  --require-hashes --only-binary=:all: scripts/posttraining/build-requirements.lock
"$EVERVAL_STUDY_ROOT/build-venv/bin/python" -m hatchling build \
  -t wheel -d "$EVERVAL_STUDY_ROOT/runtime-wheels"

uv venv --python 3.12.14 "$EVERVAL_STUDY_ROOT/venv"
uv pip sync --python "$EVERVAL_STUDY_ROOT/venv/bin/python" \
  --require-hashes --only-binary=:all: scripts/posttraining/requirements.lock
uv pip install --python "$EVERVAL_STUDY_ROOT/venv/bin/python" \
  --no-deps --no-index \
  "$EVERVAL_STUDY_ROOT/runtime-wheels/everval_ai-0.3.1-py3-none-any.whl"
```

This is a source-study installation; the published 0.3.1 package predates the
checkpoint workflow. The source wheel retains the repository version number.
The study's evaluator digest distinguishes its exact source from that release.

## Prepare weights and original tasks

The downloader requests only the files in
[model-source-manifest.json](model-source-manifest.json), from the official
Qwen model at revision `7ae557604adf67be50417f59c2c2f167def9a775`.
It verifies every file's SHA-256 and byte count before publishing it locally.
It does not send a Hugging Face token, load remote code, or upload artifacts.

```sh
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.prepare_runtime \
  --study "$EVERVAL_STUDY_ROOT" download
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.prepare_runtime \
  --study "$EVERVAL_STUDY_ROOT" convert
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.build_dataset \
  "$EVERVAL_STUDY_ROOT/dataset"
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.prepare_runtime \
  --study "$EVERVAL_STUDY_ROOT" configure
```

Conversion uses MLX affine quantization with 4-bit weights, group size 64, and
bfloat16 nonquantized tensors. The helper invokes these flags on the verified
local source directory:

```text
--quantize --q-bits 4 --q-group-size 64 --q-mode affine --dtype bfloat16
```

The configuration declares independent seeds 17, 29 and 43; checkpoints at
0, 80, 160 and 320 updates; batch size 4; Adam at 0.0001; and rank-8 LoRA on
the last four layers' query/value projections. Scale is 16, dropout is zero,
and prompt tokens are masked in the loss. Each seed starts from the same
converted base and creates a real initialized step-zero adapter.

Configuration writes local model, dataset, runtime and per-seed manifests.
It verifies the installed dependency versions, refuses conflicting files, and
does not start training. Preserve `qwen-upstream/LICENSE` and the upstream
model card alongside converted artifacts; see [licenses](LICENSES.md).

## Freeze, train and evaluate in order

Freeze the prospective protocol before training. Then freeze the final
evaluation code and installed environment before the first benchmark response.
Both commands create write-once records containing the hashes and paths of
this new reproduction; do not copy the original private protocol's paths.

```sh
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.freeze_protocol \
  --study "$EVERVAL_STUDY_ROOT" protocol
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.freeze_protocol \
  --study "$EVERVAL_STUDY_ROOT" runtime

"$EVERVAL_STUDY_ROOT/venv/bin/python" -u scripts/posttraining/model_runtime.py train \
  --config "$EVERVAL_STUDY_ROOT/training-seed-17-config.json" \
  > "$EVERVAL_STUDY_ROOT/training-seed-17.log" 2>&1
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.run_study \
  --study-root "$EVERVAL_STUDY_ROOT" --phase development

"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.analyze_real_study freeze \
  --protocol "$EVERVAL_STUDY_ROOT/frozen-protocol.json" \
  --observations "$EVERVAL_STUDY_ROOT/observations.jsonl" \
  --output "$EVERVAL_STUDY_ROOT/analysis/mix-selection.json"
```

The development phase evaluates only seed 17 at steps 0, 80 and 160. The mix
selection must be frozen before evaluating step 320 or either withheld seed.
Train the remaining trajectories with the already frozen configuration; do not
change hyperparameters after inspecting development results.

```sh
for seed in 29 43; do
  "$EVERVAL_STUDY_ROOT/venv/bin/python" -u scripts/posttraining/model_runtime.py train \
    --config "$EVERVAL_STUDY_ROOT/training-seed-$seed-config.json" \
    > "$EVERVAL_STUDY_ROOT/training-seed-$seed.log" 2>&1
done

"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.run_study \
  --study-root "$EVERVAL_STUDY_ROOT" --phase validation
"$EVERVAL_STUDY_ROOT/venv/bin/python" -m scripts.posttraining.analyze_real_study report \
  --protocol "$EVERVAL_STUDY_ROOT/frozen-protocol.json" \
  --observations "$EVERVAL_STUDY_ROOT/observations.jsonl" \
  --selection "$EVERVAL_STUDY_ROOT/analysis/mix-selection.json" \
  --output "$EVERVAL_STUDY_ROOT/analysis/report.json"
```

Keep one GPU process active at a time. Training refuses an existing seed
directory. An interrupted trajectory needs an explicitly recorded recovery
decision; this recipe does not silently resume or replace it. Evaluation
retains failed and incomplete attempts and refuses to retry terminal failures
as if they had never happened.

## Read the evidence

A complete run schedules 3,072 generations: three seeds, four checkpoints and
256 cases per checkpoint. The original dataset has four domains, 32 underlying
record families per domain and two paraphrases per family. Seeds 29 and 43
share a withheld family cohort; they are not independent additional task
samples. Generated records share templates, so template dependence remains a
substantial limitation.

`observations.jsonl` retains original model text, strict parsing outcomes,
grades, measured timing, tokens and checkpoint lineage. The SQLite workspace
retains the authoritative trials and traces. Analysis rechecks task bytes,
checkpoint bytes, raw-output grading and durable trial evidence before creating
selection or report artifacts.

The primary time field is Everval client `provider_ms / 1000`: it includes
adapter/RPC handling, tokenization, prefill and generation, and excludes model
loading, training, orchestration and grading. The separately retained
`inference_seconds` includes tokenizer/prefill/generation inside the runtime,
excluding the adapter lock wait and HTTP handling. Reported mix time savings
are counterfactual subsets of an actually executed full suite. They are not
measured end-to-end deployment savings or dollar savings.

The repository ships code and a pinned recipe, not private databases, weights,
responses or machine inventory. Preserve your own evidence directory and review
the generated report's limitations before making claims.

## Render the measured figures

Keep plotting dependencies separate from the frozen training/evaluation runtime.
These commands read the verified report and write PNG and SVG figures; they do
not run the model or alter any measured outcome. Matplotlib 3.11.2 produced the
published figures. This optional plotting environment is separate from the
hash-locked inference environment, so rendering may differ across machines.

```sh
uv venv --python 3.12.14 "$EVERVAL_STUDY_ROOT/plot-venv"
uv pip install --python "$EVERVAL_STUDY_ROOT/plot-venv/bin/python" \
  'matplotlib==3.11.2'
"$EVERVAL_STUDY_ROOT/plot-venv/bin/python" -m scripts.posttraining.plot_real_study \
  --report "$EVERVAL_STUDY_ROOT/analysis/report.json" \
  --output-dir "$EVERVAL_STUDY_ROOT/figures"
```

Compare your results with the [original measured study](../../docs/benchmarks/checkpoint-study-2026-10-22/results.md)
and its complete [aggregate evidence](../../docs/benchmarks/checkpoint-study-2026-10-22/summary.json).
