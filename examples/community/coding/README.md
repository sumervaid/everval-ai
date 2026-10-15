# Coding agent: quantity-aware cart totals

```sh
uv run everval demo --example coding
```

The public task supplies a small `cart.py` that adds prices without multiplying quantities. An agent should return a `files` object containing its edited source. The pack's offline grader matches the canonical edit, and the unchanged original fails.

This contract demonstrates source delivery, private grading, retained failures, and comparison. It is intentionally narrow: equivalent code can fail exact matching. It is not a substitute for behavioral tests.

For actual code execution, first prepare the configured Docker runtime, then run:

```sh
uv run everval demo --example coding --sandbox
```

That command selects a separate original Python behavior-test fixture, runs it inside the supported sandbox, and retains the same evidence flow. It requires a local runtime image; the default command does not.

For a real codebase, use [repository onboarding](../../../docs/github.md), prepare dependencies, review independent issue statements, prove fail-before/pass-after with regressions, and freeze a development/holdout release. Connect your agent through the [adapter protocol](../../../docs/agent-adapters.md). Imported pack files never execute code.
