# Prepare a Python test environment

Repository inspection reads source as data. Evaluation runs code in Docker.
Projects with third-party test dependencies need an environment image before
their historical tests can be admitted.

The environment preparer resolves dependencies inside isolated containers,
records their exact installed versions and wheel hashes, and produces a local
image digest. The workbench then configures that digest as the project's
prepared pytest profile.

## Choose the dependency declaration

Automatic detection reads these tracked files at a recorded commit:

- Root `requirements.txt`, `requirements-dev.txt`,
  `requirements-test.txt`, or `requirements-tests.txt`.
- `requirements/test.txt`, `requirements/tests.txt`, or
  `requirements/dev.txt`.
- PEP 621 dependencies in `pyproject.toml`, plus `test`, `tests`,
  `testing`, and `dev` optional dependencies and dependency groups.
- `setup.cfg` `install_requires` and the same test/development extra names.

Declarations are parsed without importing repository modules. Unsupported
include directives, dynamic dependency declarations, Poetry-specific
declarations, and executable `setup.py` metadata require an explicit reviewed
package list. Group inclusion directives are not expanded.

Named PEP 508 requirements support versions, extras, and environment markers.
Direct URLs, VCS dependencies, local paths, editable installs, pip options,
direct private-index access, and source builds are rejected. Private wheels can be
[imported as offline artifacts](javascript-typescript.md#import-private-python-wheels). A package must have a wheel
compatible with the selected Python image.

A dependency-free project receives `pytest==8.4.2`. The same default is added
when the package list does not explicitly select pytest. Test plugins must also
be named explicitly in the benchmark profile; installing a plugin alone does
not enable its automatic loading.

Use the candidate's base commit when preparing a historical task. Preparing
against the current project head records that choice; it does not establish
that dependencies match an older task. Dependency profiles are frozen in
validation evidence, and incompatible environments cannot produce admitted tasks.

## Python interface

The CLI and terminal workbench use this shared interface:

```python
from cvals.config import Settings
from cvals.environments import Environments
from cvals.benchmarks import Benchmarks

settings = Settings.from_env()
environments = Environments(settings)

plan = environments.plan(project_id, revision=base_commit)
prepared = environments.prepare(project_id, revision=base_commit)

profile = Benchmarks(settings).configure(
    project_id,
    image=prepared["image_digest"],
    timeout_seconds=60,
    plugins=[],
)
```

To supply a reviewed list instead of automatic detection:

```python
prepared = environments.prepare(
    project_id,
    revision=base_commit,
    packages=["pytest==8.4.2", "Flask==3.1.2"],
)
```

`packages=[]` intentionally selects only the default pytest dependency.
`base_image=` selects an already present, reviewed Python image. Automatic
image pulling is disabled. The requested image is resolved to its immutable
local Docker image ID before preparation.

These examples describe package selection, not a claim that those particular
versions fit every repository.

## Isolation and limits

Preparation uses two fresh containers without repository source, host mounts,
host credentials, package index credentials, or a Docker socket inside them.

1. A non-root, read-only downloader fetches wheels from the public PyPI index.
   Its temporary directory is bounded. Only this phase has network access.
2. The application checks wheel paths, archive types, byte limits, and hashes
   without extracting or executing packages on the host.
3. A separate container installs those wheels with networking disabled. It has
   a writable container filesystem and no Linux capabilities; its root user
   can write the image's Python installation but has no host filesystem mount.
4. The resulting image is sealed and recorded by digest. Grading subsequently
   uses a non-root user, no network, a read-only root filesystem, and fresh
   task-specific containers.

The downloader uses Docker bridge networking and a fixed public index. This
is an execution boundary, not a domain-filtering firewall. No repository code
or installed dependency is run during the network-enabled download phase.
The offline installer and later grading are the places where dependency code
may execute.

Downloads allow at most 200 wheels and 128 MiB of wheel bytes. Expanded archive
metadata allows at most 512 MiB and 20,000 entries. Each container has a 768 MiB
memory ceiling, one CPU, a 64-process limit, and bounded output. Download and
install operations have 210-second and 180-second deadlines. Timeouts and
failures remove the containers.

The preparer does not execute a repository Dockerfile, build system, package
installation, or setup script. Projects requiring compiled extensions without
wheels, operating-system packages, services, or custom builds
need a separately prepared and reviewed image.

The wheel-only policy uses pip's documented
[`--only-binary` option](https://pip.pypa.io/en/stable/cli/pip_install/).
It avoids source-distribution builds; it does not certify the safety of a
dependency. Select packages and base images appropriate for your repository.

## Reproducibility and cache

Every preparation retains:

- Source commit, hashes of dependency declarations, and a source-manifest hash.
- Base image digest, requested requirements, and preparation revision.
- Installed `pip freeze` output and Python version.
- Wheel filenames, byte sizes, SHA-256 hashes, and a wheel-manifest hash.
- The resulting image digest, resource policy, timestamps, and audit record.

The cache key includes the base image digest, normalized requirements, selected
offline artifact records when present, and
preparation revision. Reusing that key retains the original resolved
environment. `refresh=True` explicitly resolves again and records the new
image. Unpinned requirements can resolve differently after a refresh.

Lockfiles are identified but their resolver-specific formats are not imported.
The recorded wheel manifest and installed versions describe the environment
that was actually prepared. The source manifest is evidence of its inputs,
not a claim that every lockfile was reproduced.

Cached images live in the configured Docker runtime. They must be retained
with the local workspace when preserving a runnable benchmark release;
retaining a digest alone does not retain its image layers.

## Troubleshooting

- **No compatible wheel:** select supported versions or provide an approved image.
- **Dynamic or unsupported declaration:** review the dependencies and supply an explicit list.
- **Missing dependencies at validation:** inspect collection diagnostics, then prepare the needed packages and revalidate.
- **Plugin unavailable:** install its wheel and explicitly configure the plugin module.
- **Image missing:** load the approved base image into the configured runtime.
- **Existing preparation in progress:** wait for that operation; duplicate preparations are locked.
- **Source installation needed:** use a reviewed image or adapt the test profile. There is no host execution fallback.

For npm lockfiles, Node/Jest/Vitest profiles, and private npm artifacts, see
[JavaScript, TypeScript, and private dependencies](javascript-typescript.md).
