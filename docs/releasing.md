# Maintaining and releasing Everval

Everval currently uses a private source mirror and local release acceptance.
Creating a release, publishing a package, deploying a service and making the
repository public are separate maintainer decisions. None is performed by the
validation workflow.

## Validation without automatic hosted runs

[Manual validation](../.github/workflows/validation.yml) is triggered only by
GitHub's **Run workflow** control. It has no push, pull-request, tag or scheduled
trigger. Adding the file does not run hosted checks, establish a green CI history
or change repository visibility. A maintainer must deliberately dispatch it on
a reviewed revision. Hosted runs use GitHub runner minutes and store run logs
there; review the repository's billing and retention settings before dispatching.

During the current local-only maintenance session, run checks on the iMac and
retain generated files there. Do not dispatch the hosted workflow as a substitute
for that requirement. It is prepared for a later explicit maintainer choice.

The workflow separates three jobs:

| Job | What it checks |
| --- | --- |
| Python and documentation | Locked dependencies, Ruff, formatting, strict types, local documentation links/anchors and ordinary tests. |
| Web | Locked npm install, TypeScript, component tests and a production build. |
| Distribution and installed demo | Wheel/source archives, bundled browser and terminal assets, LICENSE/NOTICE, absence of private/generated directories, clean installation using exported locked runtime dependencies, and all three no-key demo domains. |

Docker, live GitHub checks, browser acceptance and paid provider evaluations are
outside this workflow. Their explicit procedures and evidence requirements are
in [release acceptance](release-acceptance.md). The default test run retains its
opt-in skips; a green manual run does not imply those integrations ran.

Actions use full commit hashes, checkout does not retain credentials, and the
token has only repository-content read access. Runs on the same ref cancel
superseded runs; every job has a time limit. Shared dependency caches, artifact
uploads, release creation, package publishing, deployment and provider secrets
are not configured. This follows GitHub's
[least-privilege and action-pinning guidance](https://docs.github.com/en/actions/reference/security/secure-use)
and [workflow controls](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

The pinned uv 0.11.23 and Node 26.0.0 versions match the local release toolchain.
Action revisions were resolved from their official repositories on 2026-10-15:

| Action | Reviewed release |
| --- | --- |
| actions/checkout | [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) |
| actions/setup-node | [v7.1.0](https://github.com/actions/setup-node/releases/tag/v7.1.0) |
| astral-sh/setup-uv | [v10.3.0](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.0) |

Pinning preserves a reviewed revision; it does not prove that dependencies,
runner images or build tools are free of vulnerabilities. Review pins when
upstream security fixes become available.

## Prepare a candidate locally

Use a clean source revision and a new private output directory. Install locked
dependencies, then run the ordinary gates:

~~~sh
make setup
make check
make test
uv run --no-sync python scripts/check_docs.py
~~~

Build the web interface before building the Python distribution. The
[acceptance guide](release-acceptance.md) gives the separate-environment
installation procedure and runtime requirements. Its locked export/sync recipe
is stricter than the uv-pip dependency installation used for the retained
2026-10-15 acceptance; keep the actual procedure with each new evidence record.

Before describing a candidate as verified:

1. Record its commit, wheel/source hashes, toolchain and dependency versions.
2. Run ordinary gates and the integration stages relevant to its changes.
   Retain failures, skips and infrastructure errors.
3. Verify an installed wheel, including terminal styles, bundled web assets,
   the no-key walkthrough and license notices.
4. Connect the changelog and verification record to the exact candidate.
   Separate installation controls, live measurements and untested deployment
   boundaries.
5. Review intended source/documentation files for private workspace contents,
   credentials and unintended grading evidence.

Use the same built artifact that was accepted. Rebuilding after acceptance
creates another artifact that needs its own identity and appropriate checks.
A version number alone does not prove identical code or bundled assets.

## Review dependency and automation updates

Keep Python and npm changes focused. Update the corresponding lockfile with an
intentional dependency change, inspect the resolved versions and notices, and
rerun the affected gates. CI uses uv's **--locked** mode so a stale lock fails
instead of silently changing resolution; see
[uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

Update an action by inspecting its official release, resolving the release tag
to its full commit SHA in the official repository, reviewing its inputs and
permissions, and updating both the hash and release comment. Do not copy a hash
from an unrelated fork. Keep workflow changes reviewable independently from
application changes.

Automatic dependency PRs and automatic CI triggers are not enabled. A future
maintainer can choose them after deciding update cadence, runner budgets and
review ownership. Require reviews for workflow and dependency changes; do not
give a validation job publishing credentials to simplify a later release step.

## Publish only after a separate decision

No release or registry publishing command is embedded in this repository's
workflow. When publication is authorized, decide the destination, access level,
artifact identity, signing/provenance requirements, release notes and retention
policy before enabling a publishing path. Keep publication permissions separate
from validation.

The Apache-2.0 license and NOTICE cover original project material; dependency
licenses and imported task-data permissions remain separate. Preserve included
notices. Private source visibility does not authorize redistributing imported
repositories, hidden tests, traces or customer data.
