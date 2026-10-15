# Project decisions

Everval AI uses maintainer review. Repository owners and reviewers with merge access decide which changes enter the project. Issues record problems and proposals; pull requests record the implementation, verification, and review.

## Proposals

Small fixes and documentation improvements can start as pull requests. For larger work, describe the affected user workflow, intended behavior, alternatives, and verification in an issue or draft pull request. The dedicated proposal forms cover task packs, graders, and adapters.

An open issue or proposal is not a commitment to implement or release it. Keep discussion focused on observable behavior and evidence. The [delivery status](docs/PLAN.md) distinguishes implemented capabilities from remaining work.

## Review criteria

Changes should fit the supported local-workspace deployment and preserve reproducible evaluation. Review considers correctness, usable workflows, data boundaries, bounded execution, accounting, compatibility, and maintenance cost.

A change to task content, a protocol, or stored data must explain its versioning and compatibility effects. Existing results remain historical evidence; a new grader or task contract must not silently rewrite past outcomes.

Record material decisions and their rationale in the issue or pull request so later contributors can understand them. Use a separate follow-up for work outside the agreed scope.

## Participation

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). Sensitive security findings use the private route in [SECURITY.md](SECURITY.md#reporting-a-vulnerability).

This guidance does not change the project's [Apache-2.0 license](LICENSE), authorship, or third-party permissions.
