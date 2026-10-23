# Model and dependency provenance

Everval's original code is [Apache-2.0](../../LICENSE). This study downloads
third-party model artifacts separately; no model weights are bundled in Git.

The official [Qwen2.5-0.5B-Instruct license at the pinned revision](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/blob/7ae557604adf67be50417f59c2c2f167def9a775/LICENSE)
is Apache-2.0. The downloader preserves its LICENSE and model card in
`qwen-upstream` and verifies their recorded hashes. Conversion and LoRA training
modify the model artifacts. Keep the upstream license and notices with any
redistributed converted model or adapters and identify your modifications.

The runtime uses [MLX LM](https://github.com/ml-explore/mlx-lm/blob/v0.32.2/README.md)
for local loading, affine quantization, LoRA training and greedy generation.
No upstream implementation was copied into the study runner. Upstream packages
are installed as pinned dependencies and retain their license files in their
installed wheel metadata.

The following labels were read from the exact installed distribution metadata:

| Distribution | Version | Metadata license |
| --- | --- | --- |
| mlx / mlx-metal | 0.32.4 | MIT |
| mlx-lm | 0.32.2 | MIT |
| transformers | 5.20.2 | Apache-2.0 |
| datasets | 5.1.0 | Apache-2.0 |
| huggingface-hub | 2.2.0 | Apache-2.0 |
| safetensors | 0.8.0 | Apache Software License classifier |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| fastapi | 0.161.1 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

This table identifies principal dependencies; it is not a replacement for all
transitive package notices. [requirements.in](requirements.in) and its
[hash lock](requirements.lock) enumerate the complete third-party runtime.
Keep the original wheel LICENSE/NOTICE files when redistributing dependencies.
The separate [build lock](build-requirements.lock) applies only to building the
local Everval wheel for reproduction.
