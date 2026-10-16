# Install Everval through npm

The npm package launches the same Python application as the source checkout,
including the CLI, TUI, worker, API, and bundled browser. You do not need to build
the frontend to use a packaged release.

## First session

Use Linux or macOS with a supported Node.js release
(`^22.22.2 || ^24.15.0 || >=26.0.0`), an existing Python 3.12+ interpreter,
and [uv](https://docs.astral.sh/uv/getting-started/installation/).

Install [Everval AI 0.3.1 from npm](https://www.npmjs.com/package/everval-ai/v/0.3.1):

```sh
npm install --global everval-ai@0.3.1
everval setup
everval demo
```

For a release candidate supplied as a local tarball, replace the first command
with `npm install --global /path/to/everval-ai-0.3.1.tgz`.

npm installation runs no lifecycle scripts. The explicit setup command installs
the bundled application and hash-locked Python dependencies into a private
environment. It does not download Python or uv, build source distributions, or
change your system Python. Online setup can download dependency wheels.

After setup, ordinary commands do not install runtime dependencies.
The demo makes zero model calls and needs no Docker or network connection.
It verifies original installation controls; its scores do not rank models.

```sh
everval init --starters
everval tui
```

Use `everval tmux` for a persistent terminal session with tmux installed,
`everval serve` for the browser interface, and `everval help` for application
commands. `everval --help` describes the npm launcher; `everval --version`
works without Python or setup. The `everval-ai` alias runs the same launcher.

In the source-based guides, replace `uv run everval` with `everval`.
GitHub onboarding still requires Git and the GitHub CLI. Repository execution
still requires Docker and a supported prepared runtime. Follow the
[workflow walkthrough](worked-example.md) or [guided run](guided-run.md) next.

## Internal mirrors and disconnected hosts

For online setup, configure `UV_DEFAULT_INDEX` with your approved Python index
and `SSL_CERT_FILE` with your internal CA bundle when needed. Keep credentials
in your approved credential mechanism; do not put them in package files.

For disconnected hosts, prepare a wheelhouse on an approved builder matching
the target OS, architecture, Python minor version, and libc. Use an existing
Python 3.12+ installation with pip. Extract the reviewed npm tarball into a new
directory, then download every dependency specified by its bundled lock:

```sh
mkdir reviewed-package wheelhouse
tar -xzf everval-ai-0.3.1.tgz -C reviewed-package
python3 -m pip download --only-binary=:all: --require-hashes --no-deps \
  --requirement reviewed-package/package/runtime/requirements.txt \
  --dest wheelhouse
```

Transfer the npm tarball, wheelhouse, and approved Node.js, Python, and uv
installations to the target. Install and initialize without registries:

```sh
npm install --global --offline --ignore-scripts ./everval-ai-0.3.1.tgz
everval setup --offline --wheelhouse /srv/approved-wheels
everval demo
```

The npm launcher has no JavaScript runtime dependencies. Offline Python setup
disables index access and checks dependency hashes. Missing, incompatible, or
altered wheels fail setup instead of falling back to a download or source build.

The [enterprise deployment guide](on-premises.md) covers service accounts,
internal inference, access controls, backups, and upgrades. Its Python-only
systemd deployment does not require Node.js on the runtime host. The npm
launcher is an additional installation path for operator workstations and
hosts approved to run Node.js.

## Storage, upgrades, and recovery

| Setting | Meaning |
| --- | --- |
| `EVERVAL_DATA_DIR` | Workspace data; defaults to `~/.local/share/everval`. |
| `EVERVAL_NPM_RUNTIME_DIR` | Absolute path for launcher-managed Python environments. |
| `EVERVAL_PYTHON` | Existing interpreter path or uv interpreter selector. |
| `EVERVAL_UV` | Approved uv executable path. |

The default runtime root is `~/Library/Caches/everval/npm` on macOS and
`~/.cache/everval/npm` on Linux; an absolute `XDG_CACHE_HOME` takes precedence.
The runtime root must be owned by the current user with mode 0700. Its parents
must be controlled by that user or root and not writable by other users.

Each payload has its own environment, keyed by content, OS, and architecture.
After updating the npm package, run `everval setup` again. Setup serializes
concurrent installations and records readiness only after dependency and version
checks pass. A failed setup leaves an incomplete directory for inspection.
If instructed to recover, inspect the printed directory and remove only that
incomplete runtime after confirming no setup process is active. Stale locks are
never removed automatically.

Package payload hashes are checked before setup and application commands.
These checks detect payload changes relative to the manifest; they are not
publisher signatures or a guarantee against a modified manifest. Retain and
verify the approved npm tarball's checksum in your release process.

Workspace data, credentials, hidden tests, and results are separate from the
runtime cache and package. There is no telemetry or automatic result upload.
Remote inference requires explicit provider configuration and budget controls.

## Build and review a distribution

Normal application launches replace Node with the verified Python interpreter, preserving terminal input, job control, and signal delivery. This uses Node’s [`process.execve`](https://nodejs.org/api/process.html#processexecvefile-args-env), which remains experimental. On Node versions before 26.1, an underlying exec failure aborts Node instead of throwing; the launcher checks interpreter existence and execute permission before handing over.

Run from the repository on an approved builder after the ordinary release gates:

```sh
npm run build --prefix web
uv run python scripts/build_web.py
uv build --wheel --out-dir dist
uv run python scripts/build_npm.py \
  --wheel dist/everval_ai-0.3.1-py3-none-any.whl \
  --output dist/npm-review
npm pack ./dist/npm-review --ignore-scripts --pack-destination dist
```

The output directory must not exist. The builder exports runtime dependencies
from the locked Python project, verifies wheel contents and RECORD hashes,
checks the wheel against current tracked source and built browser assets, and
copies only the launcher, application wheel, dependency lock, metadata, README,
and license notices. It never copies a workspace or publishes a package.

The source npm manifest and review builds use `private: true` to prevent an
accidental publish. Run `npm test --prefix npm` for launcher checks and
`uv run pytest tests/test_npm_distribution.py` for distribution checks.

For an authorized public release, use a fresh staging directory and
`--access public`:

```sh
uv run python scripts/build_npm.py \
  --wheel dist/everval_ai-0.3.1-py3-none-any.whl \
  --output dist/npm-public --access public
npm pack ./dist/npm-public --ignore-scripts --pack-destination dist
```

Review the archive, record its checksum, install that exact tarball in an
isolated prefix, and run setup plus the no-key demo before publication.
Publishing through the CLI requires an npm account with two-factor
authentication enabled. Configure it through the
[npm account settings](https://docs.npmjs.com/configuring-two-factor-authentication/)
before authenticating on the publishing host with `npm login`. Login and
publication can require separate browser approvals. Run publishing from an
interactive terminal so npm can present its authentication flow, then publish
the same accepted archive:

```sh
npm publish ./dist/everval-ai-0.3.1.tgz --access public --ignore-scripts
```

npm can accept an upload before the version becomes installable while
[publish-time scanning](https://github.blog/changelog/2026-07-28-npm-publish-time-malware-scanning-and-dual-use-metadata/)
runs. Wait for the exact version to become available, compare its downloaded
archive with the accepted checksum, and verify a fresh registry installation
before announcing the release.

For an authorized private registry package, supply a scoped name and
`--access restricted`, for example
`--name @your-company/everval-ai --access restricted`. Package visibility and
GitHub visibility are separate. See npm's
[private package requirements](https://docs.npmjs.com/creating-and-publishing-private-packages/).
Neither build path performs registry writes. Read the
[maintainer release guide](releasing.md) for the wider acceptance process.
