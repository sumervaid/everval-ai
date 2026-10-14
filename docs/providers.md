# Systems and providers

A system produces a submission for a scenario. Experiments freeze the complete system configuration, including model, harness, prices, and execution limits.

## Choose a harness

| Harness | Use |
| --- | --- |
| `single-turn` | One chat-completions-compatible request for a JSON result. Useful for document, data, workflow, and compact coding tasks. |
| `tools` | A bounded loop over public coding files: list, read, literal search, write, delete, and isolated Python execution. The final file snapshot is graded separately. |
| Recorded submissions | Evaluate results from an existing agent or external harness without invoking it again. |
| Fixture controls | Verify the evaluation machinery with declared reference or incomplete outputs. |

Tools mode does not provide a host shell, browser, network access inside execution, or hidden tests. Each public Python check uses a fresh container. Tool traces are retained locally. The loop stops at its step, context, output, or time limit, and stops when usage is unavailable.

## Configure an endpoint

Keep secrets in environment variables. Store only the variable name in a system. Set it **before starting the worker**; changing the terminal environment does not change an already-running worker.

```sh
export EVERVAL_ALLOW_REMOTE_PROVIDERS=true
export MY_PROVIDER_API_KEY=YOUR_SECRET_VALUE

uv run everval systems add-http "My coding system" \
  --model PROVIDER_MODEL_ID \
  --base-url https://provider.example/v1 \
  --api-key-env MY_PROVIDER_API_KEY \
  --input-rate 1.00 \
  --output-rate 4.00 \
  --harness tools \
  --max-steps 8 \
  --max-tokens 4096
```

The URL, model, and prices are placeholders. Use your provider's actual supported protocol and current prices. A free loopback endpoint can use explicitly declared zero prices. Remote endpoints require HTTPS, public-address validation, opt-in, positive prices, and a positive experiment budget.

The adapter refuses redirects and environment proxies, pins the resolved destination, and preserves TLS hostname verification. Compatibility varies by provider; run a controlled pilot before a large experiment.

## Spending and failure

The engine reserves a conservative amount before an attempt. Tools mode reserves the bounded entire loop before the first request. Reservations may be substantially larger than actual usage.

Provider-reported token counts and frozen configured rates determine recorded cost. Missing usage stays missing. Timeouts and malformed responses can incur charges; ambiguous billing blocks further calls and is never automatically retried. A known lower bound, when available, remains in the trace.

Cancellation preserves usage and evidence already produced. An active request or bounded tool loop may finish before cancellation is recorded; cancelling is not a provider-side refund.

## Public/private boundary

Only scenario instructions and public inputs enter provider context. Expected outputs, grading checks, reference fixes, and private provenance remain outside it. Prepared repository solvers receive source files and the pinned public runtime identity, without future Git history.

A hosted endpoint still receives the public task material. Choose local inference when that material must remain on the machine. Full trial exports can contain private source, outputs, and traces; handle them as workspace evidence.

The automated test suite uses fixtures, mock endpoints, and local Docker execution. It makes no paid model calls.
