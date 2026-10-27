# Record the product walkthroughs

These scripts drive the actual terminal and browser interfaces in isolated workspaces, using original installation controls and synthetic checkpoint data. They do not call model providers or use the maintainer's evaluation workspace.

The repository clip reads an original temporary Git history and stops before isolated validation. Training observations are synthetic. Terminal audit captures replace the local OS account name with **operator**; this redaction is retained in media metadata.

## Capture

Use the [source development setup](../../docs/quickstart.md#install-from-source), locked development dependencies, and an installed Google Chrome. Browser capture uses Playwright's chrome channel. Terminal rendering uses a local font and blocks network requests.

~~~sh
npm ci --prefix web
npm run build --prefix web
uv run --no-sync python scripts/build_web.py

uv run --no-sync python scripts/demos/capture_terminal.py --output .local/demo-captures/terminal
uv run --no-sync python scripts/demos/capture_browser.py --output .local/demo-captures/browser
uv run --no-sync python scripts/demos/capture_studies.py --output .local/demo-captures/studies
~~~

Choose new output directories. Browser capture prints its new capture directory. Scripts close owned applications and servers. Keep workspaces, raw logs, credentials, SVG intermediates and capture manifests outside Git.

## Encode

Install the image encoder separately:

~~~sh
uv venv --python 3.12 .local/demo-media
uv pip install --python .local/demo-media/bin/python -r scripts/demos/requirements.txt
~~~

Replace CAPTURE_DIRECTORY with the path printed by browser capture:

~~~sh
.local/demo-media/bin/python scripts/demos/encode.py \
  .local/demo-captures/terminal/manifest.json \
  CAPTURE_DIRECTORY/chat-and-reports/manifest.json \
  CAPTURE_DIRECTORY/rankings/manifest.json \
  CAPTURE_DIRECTORY/tasks-and-comparisons/manifest.json \
  CAPTURE_DIRECTORY/production-cases/manifest.json \
  .local/demo-captures/studies/double-blind/manifest.json \
  .local/demo-captures/studies/checkpoint-monitoring/manifest.json \
  --tour
~~~

Output is GIFs, PNG stills and public metadata in docs/assets/demos/. The --output option selects another destination. The --tour option adds a thirty-second overview for ten workflows.

Captions and pauses are edited for readability, not latency measurements. A shared palette reduces size and color flicker. The encoder decodes every GIF again and compares every frame, checking dimensions, frame counts, timing, and a 5 MB per-file limit. PNG stills provide a non-animated alternative.

Review all recordings for private text, outputs, names, accounts, paths and diagnostic content. Inspect encoded frames and [gallery limitations](../../docs/demos.md), then run:

~~~sh
uv run --no-sync ruff check scripts/demos
uv run --no-sync ruff format --check scripts/demos
uv run --no-sync python scripts/check_docs.py
git diff --check
~~~

Never publish failed captures, private runtime material, or fabricated successful validation states.
