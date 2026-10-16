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

The URL, model, and prices are placeholders. Use your provider's actual supported protocol and current prices. A free loopback endpoint can use explicitly declared zero prices. Public remote endpoints require HTTPS, public-address validation, opt-in, positive prices, and a positive experiment budget. Approved internal HTTPS endpoints use the separate policy below.

The adapter refuses redirects and environment proxies, pins the resolved destination, and preserves TLS hostname verification. Compatibility varies by provider; run a controlled pilot before a large experiment.

## Internal inference and private certificate authorities

An on-prem endpoint can stay inside your network without enabling public providers. Set the network policy before starting the API and worker:

```sh
export EVERVAL_ALLOW_REMOTE_PROVIDERS=false
export EVERVAL_INTERNAL_PROVIDER_ORIGINS=https://inference.corp.example:8443
export EVERVAL_PROVIDER_CA_BUNDLE=/etc/everval/corporate-ca.pem

uv run everval systems add-http "Internal inference" \
  --model YOUR_INTERNAL_MODEL \
  --base-url https://inference.corp.example:8443/v1 \
  --input-rate 0 \
  --output-rate 0
```

The addresses and model are examples. Omit `EVERVAL_PROVIDER_CA_BUNDLE` when the endpoint already has a trusted certificate. An explicit bundle adds trusted CA certificates while retaining certificate-chain and hostname verification. A missing or invalid file fails closed. There is no option to disable TLS verification.

`EVERVAL_INTERNAL_PROVIDER_ORIGINS` accepts a comma-separated list of exact HTTPS origins: scheme, hostname or IP address, and optional port. Entries cannot contain paths (including a trailing slash), wildcards, credentials, queries, or fragments. A system's base URL can contain its API path, such as `/v1`. Approval does not extend to another hostname, subdomain, or port. The default list is empty.

Approved internal origins may resolve to RFC1918 IPv4 addresses (`10/8`, `172.16/12`, `192.168/16`) or IPv6 unique-local addresses (`fc00::/7`). Link-local and metadata destinations, multicast, unspecified or reserved addresses, and a hostname resolving to loopback are rejected. Every DNS answer must be permitted; a mixed answer containing an unsafe address is rejected. Each request connects to one checked address while retaining the original Host header and TLS server name, avoiding a second DNS lookup at connection time.

Public internet addresses still require the separate public-provider opt-in and positive declared prices. An internal origin with zero prices cannot use that permission to send tasks to a public address. Explicit loopback endpoints retain their existing behavior.

Input and output rates must always be declared, including zero for internal inference whose provider charge is zero. This records provider charges only; it does not estimate GPU, electricity, staffing, or infrastructure costs. If either rate is positive, the experiment needs a positive budget. Missing usage remains unknown even when the configured rates are zero.

The API validates the configured credential environment variable when admitting a run, and the worker uses it for execution. If a system declares `api_key_env`, make that variable available to both processes under the current deployment model. Apply the same nonsecret destination policy to both; mount the CA file wherever provider requests execute. Existing processes must be restarted after changing these settings. Worker and tmux launchers propagate the explicit policy and CA path, including clearing inherited settings when the workspace settings are empty.

The policy applies to single-turn, tools, and external-agent HTTP harnesses. It controls the inference connection; an external adapter remains responsible for its own network, execution isolation, token ceilings, and internal service access. See the [on-prem deployment guide](on-premises.md) for the supported single-workspace topology.

## Spending and failure

The engine reserves a conservative amount before an attempt. Tools mode reserves the bounded entire loop before the first request. Reservations may be substantially larger than actual usage.

Provider-reported token counts and frozen configured rates determine recorded cost. Missing usage stays missing. Timeouts and malformed responses can incur charges; ambiguous billing blocks further calls and is never automatically retried. A known lower bound, when available, remains in the trace.

Cancellation preserves usage and evidence already produced. An active request or bounded tool loop may finish before cancellation is recorded; cancelling is not a provider-side refund.

## Public/private boundary

Only scenario instructions and public inputs enter provider context. Expected outputs, grading checks, reference fixes, and private provenance remain outside it. Prepared repository solvers receive source files and the pinned public runtime identity, without future Git history.

A hosted endpoint still receives the public task material. Choose local inference when that material must remain on the machine. Full trial exports can contain private source, outputs, and traces; handle them as workspace evidence.

The automated test suite uses fixtures, mock endpoints, and local Docker execution. It makes no paid model calls.

## Reviewable configuration variants

Create a separate saved candidate before comparing a model, prompt or tool budget:

```sh
everval systems variant SOURCE_SYSTEM "Concise candidate" --instructions-file prompt.txt --max-tokens 1024
everval systems variant SOURCE_SYSTEM "Alternate model" --model YOUR_MODEL \
  --input-rate YOUR_INPUT_RATE --output-rate YOUR_OUTPUT_RATE --pricing-version YOUR_REVIEWED_RATE_VERSION
```

Changing the model requires explicit input/output rates and a pricing version; source-model prices are never assumed valid for another model. Additional instructions are bounded to 8,000 characters and apply only to built-in single-turn and tool harnesses. They do not expose private graders. External adapter changes require a separately pinned adapter revision. The terminal offers **Systems → Save variant**.

[Compare candidates with a reserved holdout](research-loop.md) · [Project the budget](budget-planning.md)
