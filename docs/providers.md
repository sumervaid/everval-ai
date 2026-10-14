# Systems and providers

A system is a configuration that produces an output for a scenario. Experiments snapshot the system configuration along with the task collection.

## Fixture controls

The reference and incomplete controls require no network or model key. They verify the evaluation machinery. Their scores are explicitly labeled and must not be presented as model benchmarks.

## HTTP endpoints

The HTTP adapter accepts chat-completions-compatible endpoints and requests a JSON submission. It is a structured-output model adapter, not an interactive browser or terminal harness. For an existing tool-using agent, use [recorded submissions](submissions.md).

Keep keys in environment variables. Store only the variable name in the system configuration. Set variables before starting the server; it does not automatically read a `.env` file.

A local endpoint can use an explicit zero price. External endpoints require HTTPS, network opt-in, positive configured pricing, and a positive experiment budget.

```sh
export CVALS_ALLOW_REMOTE_PROVIDERS=true
export MY_PROVIDER_API_KEY=YOUR_SECRET_VALUE

uv run cvals systems add-http "My model" \
  --model PROVIDER_MODEL_ID \
  --base-url https://provider.example/v1 \
  --api-key-env MY_PROVIDER_API_KEY \
  --input-rate 1.00 \
  --output-rate 4.00
```

The URL, model, and prices above are placeholders. Enter your provider's actual current configuration and rates. Rates are USD per million tokens, with a saved pricing version. Never commit a real key.

The provider adapter restricts remote destinations, refuses redirects, pins the resolved public address for each request, and preserves TLS hostname verification. Literal loopback addresses support local endpoints.

## Cost and failure behavior

Before a call, the engine reserves a conservative estimated amount based on bounded input and maximum output. Recorded provider usage determines the calculated model cost when supplied. Price estimates are not a reconciliation against a provider invoice.

Timeouts and malformed responses can still incur charges. The engine preserves the failed attempt, keeps unknown cost unknown, and stops additional calls when ambiguous billing prevents reliable budget accounting. It does not automatically retry ambiguous paid requests.

Set realistic output limits and a budget appropriate to the task cohort. Provider errors, incomplete output, cancelled work, and budget exhaustion remain visible in the trial ledger.

## Data boundary

A hosted provider receives the scenario instructions and public task inputs. Hidden checks and reference outputs are excluded. Trace and result records remain in the local workspace. Local storage and local inference are separate choices.

Run provider compatibility tests against a controlled endpoint before a large experiment. No commercial model calls are required by the project's test suite.
