# Calibrated-judge and distribution acceptance

[Project](../../../README.md) · [Judge workflow](../../judge-calibration.md) · [Research methods](../../benchmarks/judge-study-2026-10-27/methodology.md)

The 0.4 source passes ordinary checks and installation through both Python and npm. These checks establish software behavior. They do not establish model quality, human validity, or a representative customer benchmark.

## Committed-source validation

[GitHub run 40574741153](https://github.com/sumervaid/everval-ai/actions/runs/40574741153) passed for source commit 7054efad67684199341c4c84e968ed491e5f6e8d:

- 1,635 Python tests passed; 46 opt-in checks were skipped.
- Ruff, formatting, strict typing across 107 source files, and 783 local documentation links passed.
- Web type checks, component tests, production build, and npm launcher tests passed.
- A separately installed wheel passed the original three-domain no-key demonstration.
- The exact locally built npm tarball was installed in an isolated prefix. Setup, version reporting, calibrated-judge CLI help, and the same no-key demonstration passed.

The installed demonstrations make zero provider calls. The distribution job checks bundled browser and terminal assets, licenses, and private-file exclusions. Hosted validation does not execute repository tasks or use model credentials.

## Live product transport

A separate four-call probe used the product's ordinary CLI and Chat Completions transport with GPT-6 Luna. It exercised protocol creation, development dispatch, explicit holdout release, both response orders, durable spending records, and JSON/HTML reports.

All four calls completed. The two held-out arithmetic controls matched their scripted references. Everval correctly withheld empirical confidence intervals and human-reliability claims because these are original installation controls with no human participants.

The conservative configured-rate cost was **$0.0004795**, within a separate **$0.05 cap**. Usage was reported for every call; no automatic retries occurred. This is a token-based estimate, not a verified invoice. The [aggregate acceptance record](product-judge-acceptance.json) retains source hashes, protocol identity, coverage, and accounting. Raw workspace records remain private.

## Inspect compatibility

The optional adapter was verified with the actual Inspect 0.3.285 package in a separate environment. Eight exported control samples loaded as a MemoryDataset, with targets retained as evaluator fields and public inputs kept separate. This check made no model calls. The [compatibility record](inspect-compatibility.json) retains the export hashes and protocol identity.

## Release boundary

Version 0.4 artifacts and source must be distinguished from registry publication. At this acceptance point, the documented published npm release remains 0.3.1. A green CI run, a local candidate, or a GitHub push does not establish a new npm or PyPI release.

Real human-reference judging, archived patch discrimination, and fresh repository repairs have separate study records. Their outcomes must not be inferred from these installation checks.
