# Native environment preparation

These recipes expose the fresh study's environment choices. Rebuilt images require a new selection manifest and all three admission cycles. Historical image IDs identify local artifacts, not downloadable images.

[native-images.json](native-images.json) records 11 image identities and the 351-candidate order. [django-sources.json](django-sources.json) records public source revisions; [django-wheels.lock.json](django-wheels.lock.json) pins 32 wheel URLs and SHA-256 hashes. No issue text, hidden tests, reference patches, credentials or private paths are included.

The [native preparation tool](../../benchmarks/prepare.py) builds earlier repository environments from pinned upstream assets. Django inherited an amended pytest base with Python 3.9.20, Git, pip, setuptools and wheel on Linux aarch64. The public [inherited-base recipe and locks](inherited-base/README.md) describe a derived rebuild. Historical Ubuntu/apt inputs were not completely locked, so byte-identical reproduction is unavailable.

Set EVERVAL_REPAIR_PREP to an external data directory and EVERVAL_VERIFIED_DATASET to the pinned enriched parquet documented in the [study methods](../../../docs/fresh-repair-methods.md). Run from the repository root:

```bash
uv run --with pyarrow==26.0.0 python scripts/judge_study/environments/prepare_django_sources.py --work-dir "$EVERVAL_REPAIR_PREP" --dataset "$EVERVAL_VERIFIED_DATASET" --cache "$EVERVAL_REPAIR_PREP/django-bases.git"
uv run python scripts/judge_study/environments/prepare_django_wheels.py --work-dir "$EVERVAL_REPAIR_PREP"
uv run python scripts/judge_study/environments/build_django_image.py --work-dir "$EVERVAL_REPAIR_PREP" --base-image "sha256:YOUR_VERIFIED_LOCAL_IMAGE_ID"
```

Replace the image placeholder with an existing immutable Python 3.9.20/aarch64 image. Source fetching disables hooks and verifies exact base commits and parents. Wheel downloads verify frozen hashes. Imported setup executes only in an offline container capped at 0.25 CPU and 384 MiB. Optional browser/service/native-source backends are omitted; missing required checks exclude tasks.

The new image record is written under direct-builds-final/django/environment.json in the work directory. Its fields enter a new prepared-environments.json; the [research workflow](../README.md) requires a separately installed SWE-bench harness, new controls and retained exclusions. CLI/syntax checks of these parameterized helpers do not establish a clean-machine rebuild. The historical preparation succeeded; rebuilt environments must earn their own admission evidence.
