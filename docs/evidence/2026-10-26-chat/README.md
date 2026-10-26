# Conversational benchmark setup — verification

This record covers the current-source chat workflow. It is not part of the published npm 0.3.1 package.

## Executed checks

- **137 Python checks passed** across conversation planning, execution, CLI, terminal, schedules, API, audit atomicity, and existing workflow regressions.
- **46 browser-component checks passed**, including six conversation tests.
- Python type checking passed for all 93 source files. TypeScript checking and the production browser build passed.
- Ruff lint passed across source, tests, scripts, deployment helpers, and npm tests. Full-repository formatting passed.
- Actual terminal rendering was inspected at wide and compact sizes.
- An actual browser request admitted and executed six original control trials through the local worker. HTML and CSV reports appeared automatically, the HTML download was inspected, and pausing and reopening the saved conversation worked.
- Desktop and mobile browser checks found no horizontal overflow, JavaScript errors, or HTTP errors. A follow-up resize check verified that the latest reply remains visible.

The browser run used the built application and existing local worker, not mocked experiment results. The two systems were the built-in reference and empty-submission controls. **Model calls: 0. New provider spend: $0.** Development time, electricity, and hardware are not priced.

## Scope and retained limitations

Tests cover fixed intervals, frozen task snapshots, explicit paid-run caps, initial-run accounting, activation retries, concurrent admission, failure recovery, report integrity, unknown-resource questions, and role boundaries. Simulated due times exercise recurring admission; this record does not claim a two-week uptime soak.

Chat resolves configured models and saved task collections. It does not automatically create valid grading rules from arbitrary raw data. Reports stay local; automatic email/Slack delivery is unsupported. Parsing is local and bounded rather than an unrestricted language-model agent.

The full-repository formatting check found one existing long line in `scripts/posttraining/plot_real_study.py`. It was reformatted, with Python syntax-tree equality verified before and after. The HTTP test client emitted an existing Starlette deprecation warning; no checks were skipped.

See the [workflow guide](../../chat.md) and [machine-readable summary](summary.json). Private transcripts, reports, databases, and test workspaces remain outside Git.

## Reproduce the checks

From the source checkout:

```sh
uv run pytest tests/test_conversation.py tests/test_conversation_execution.py tests/test_conversation_api.py tests/test_chat_tui.py tests/test_chat_cli.py tests/test_schedules.py tests/test_api.py tests/test_audit_atomic.py tests/test_growth_workflows.py tests/test_tui.py tests/test_cli.py
npm test --prefix web
npm run build --prefix web
uv run mypy src/cvals
uv run ruff check src tests scripts deploy npm/test
uv run ruff format --check src/cvals tests
uv run python scripts/check_docs.py
```

Use the [no-key chat walkthrough](../../chat.md#try-it-without-a-model-key) to repeat the interactive path.
