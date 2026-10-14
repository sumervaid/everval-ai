# Contributing

The repository is currently a product and architecture plan. Start with [the implementation plan](docs/PLAN.md). Work should complete the earliest unfinished milestone before expanding the scope.

A contribution should explain the user-visible problem, resulting behavior, and meaningful validation. Keep changes small enough to review. Make proposed features, experimental results, and verified behavior easy to distinguish.

For runtime work, add checks that could detect a real failure: invalid task admission, grading leakage, lost jobs, inaccurate denominators, or broken replay. Do not add implementation-mirroring tests for simple documentation changes.

Use original, permitted fixtures. Never include private repositories, hidden holdout tasks, model credentials, raw private trajectories, or machine-specific configuration in a public change. Code licensing and dataset permissions are tracked separately.

Dependency reuse must preserve applicable notices. The planned core is Apache-2.0; the architecture currently favors an independently implemented core with a Harbor adapter. Any change to that licensing or execution strategy needs an explicit architecture update.

In the maintainer's current environment, all project work executes on the iMac; see [AGENTS.md](AGENTS.md). Other contributors can use their own development machines while following the same data-isolation and reproducibility requirements.

No application tests, installation commands, or supported release versions exist yet.
