This directory records the inherited Python 3.9.20 ARM64 dependencies used by the native repository experiment. It also provides a derived runtime recipe for a new replication.

The locks contain a resolved ARM64 Ubuntu image digest, 27 exact Conda artifacts, nine Python wheels, and the retained Miniconda installer checksum. Every artifact is checked against its SHA-256 and byte count before installation. Conda creation and Python installation then use the downloaded local files. The three runtime wheels absent from the historical offline wheel cache are pinned to their published PyPI artifact hashes; the six recorded backend wheel hashes match the retained cache.

Build on an ARM64 Docker host:

```sh
docker build --platform linux/arm64 \
  --tag everval/native-python39:rebuild \
  scripts/judge_study/environments/inherited-base
docker image inspect --format '{{.Id}}' everval/native-python39:rebuild
```

Pass the resulting immutable `sha256:…` image ID to the parameterized Django preparation script. Keep dependency downloads in the preparation phase and run task grading without network access. For an on-prem mirror, review and freeze a new preparation manifest and source-host allowlist with the mirror URLs and the same artifact digests.

This recipe has not yet been built as part of the reported experiment. It does not reproduce the historical image digest: the original Ubuntu reference was a tag, its resolved digest was not retained, and apt packages were not locked. The derived recipe defaults to the digest recorded in `ubuntu-base.lock.json`. Override it only with a reviewed digest through `--build-arg UBUNTU_IMAGE=ubuntu@sha256:…` and record that change for a new run. The recipe records the resulting OS package inventory and exposes the installed locale generator through the sandbox PATH; it does not substitute a successful no-op for that utility. Rebuilds must pass the same base-fails/reference-passes admission controls before evaluating model patches.

The base dependency evidence comes from the [pinned SWE-bench task preparation assets](https://github.com/SWE-bench/swe-bench-tasks/tree/3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d/tasks/pytest-dev__pytest-11148). `provenance.json` records the original artifact hashes and the limits of the historical record. Package metadata contains public source URLs, versions, licenses, and digests; maintainer paths and complete Conda package file inventories are excluded.
