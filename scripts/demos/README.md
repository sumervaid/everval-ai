# Record continuous product demos

These scripts record the actual application at 25 frames per second. Browser inputs include pointer motion, clicks, sequential typing and smooth scrolling. Terminal recordings stream a real Everval process from a PTY into a local terminal renderer.

Each clip is one uninterrupted recording. The encoder trims its beginning and end, then exports a GIF, full-resolution MP4 and still preview. It does not assemble screenshots, insert caption cards, concatenate scenes, interpolate motion or change playback speed.

## Capture

Use the [source development setup](../../docs/quickstart.md#install-from-source), its locked development dependencies, and an installed Google Chrome. Browser viewport and recording size are both 1280×800. Authentication is injected before visible navigation; credentials are never typed into recorded forms.

~~~sh
npm ci --prefix web
npm run build --prefix web
uv run --no-sync python scripts/build_web.py
uv run --no-sync python -m playwright install ffmpeg

uv run --no-sync python scripts/demos/capture_browser.py --output .local/continuous/browser
uv run --no-sync python scripts/demos/capture_studies.py --output .local/continuous/studies
uv run --no-sync python scripts/demos/capture_terminal.py --output .local/continuous/terminal
~~~

Choose new private output directories. The terminal renderer uses a pinned xterm.js package in a private cache; its first setup may download the package. It streams actual terminal output locally rather than converting terminal screenshots into frames.

The recordings use isolated workspaces, original controls and synthetic training observations. They make no model API calls. The repository demonstration stops before isolated validation. Study reviewers are scripted accounts, not a human-preference study. Capture scripts close their owned servers, browser contexts and terminal processes.

## Encode

~~~sh
uv venv --python 3.12 .local/demo-media
uv pip install --python .local/demo-media/bin/python -r scripts/demos/requirements.txt

.local/demo-media/bin/python scripts/demos/encode.py \
  CAPTURE_DIRECTORY/everval-tour/manifest.json \
  CAPTURE_DIRECTORY/chat-and-reports/manifest.json \
  CAPTURE_DIRECTORY/rankings/manifest.json
~~~

Replace the example paths with the generated manifests. The browser script prints its capture directory below the chosen output directory; its manifests live inside that directory. Studies and terminal manifests live directly below their chosen output directories. Locate them with:

~~~sh
find .local/continuous -name manifest.json
~~~

Pass every desired manifest together to produce the complete public media index. The default destination is docs/assets/demos/recordings/; --output selects another directory. The hero is captured as its own complete user journey; it is never assembled from other clips.

GIFs use 20 frames per second at 960×600 with a 64-color palette. Bounded temporal stabilization suppresses recording noise: a previous pixel is retained only when every channel differs from the current recorded pixel by at most 6/255 before palette quantization. Larger changes update immediately. The frame timeline stays intact. MP4s preserve the native 1280×800 recording at 25 frames per second. Neither format changes playback speed.

The encoder verifies source resolution and frame rate, uninterrupted segment timing, GIF frame cadence, changing frames, PNG decoding and MP4 duration. Private source-video hashes and segment offsets are retained in the public metadata for inspection; raw recordings, credentials and workspaces remain outside Git.

Review actual playback before publishing. Inspect typing and scroll motion, early and late frames, fixture labels, account names, output content and paths. Keep timing claims limited to the recording; these demonstrations are not model-latency measurements.

~~~sh
uv run --no-sync ruff check scripts/demos
uv run --no-sync ruff format --check scripts/demos
uv run --no-sync python scripts/check_docs.py
git diff --check
~~~

[Continuous gallery](../../docs/demos.md) · [Still images](../../docs/demo-stills.md)
