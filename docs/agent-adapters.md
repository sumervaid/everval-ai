# Evaluate an existing agent

Use `external-agent` when your application already owns its prompt, tool loop, or model integration. Everval supplies a public task to your service, retains its revision and reported usage, and grades the returned output privately. Use [submission import](submissions.md) for outputs that were already collected.

## Wrap the application

Install Everval in the trusted service environment and wrap your existing agent with the small SDK:

```python
from cvals.agent_sdk import AgentRequest, AgentResult, AgentUsage, create_agent_app
from my_application import run_agent

REVISION = "release-2026-10-14"

async def execute(request: AgentRequest) -> AgentResult:
    result = await run_agent(
        instruction=request.task.instruction,
        inputs=request.task.inputs,
        model=request.model,
        max_input_tokens=request.limits.input_tokens,
        max_output_tokens=request.limits.output_tokens,
        timeout_seconds=request.limits.timeout_seconds,
    )
    return AgentResult(
        revision=REVISION,
        output=result.output,
        usage=AgentUsage(
            prompt_tokens=result.total_input_tokens,
            completion_tokens=result.total_output_tokens,
        ),
        trace=result.trace,
    )

app = create_agent_app(execute, revision=REVISION, token_env="MY_AGENT_TOKEN")
```

`my_application` is your existing implementation, not an included dependency. Run this trusted service with your normal application launcher. Keep credentials in its environment. The adapter must enforce the supplied aggregate ceilings and isolate any repository code it executes; the SDK is an HTTP contract, not an execution sandbox. A client timeout does not forcibly terminate work inside an external service.

## Register and run

```sh
everval systems add-agent "Internal agent" \
  --model internal-model \
  --base-url http://127.0.0.1:9000 \
  --revision release-2026-10-14 \
  --api-key-env MY_AGENT_TOKEN \
  --input-rate 1.00 --output-rate 4.00 \
  --input-limit 32000 --output-limit 8000

everval run COLLECTION_ID --system SYSTEM_ID --budget 10
```

The prices above are illustrative USD per million tokens, not current provider prices. Configure the actual agreed rates. A remote service additionally requires explicit remote-provider opt-in, HTTPS, and a public permitted destination. Local/private network proxying is not enabled implicitly.

Select **external-agent** in the terminal or browser system form to register the same configuration. A revision is required and is frozen with each experiment. Changing an adapter's implementation under an unchanged revision defeats reproducibility; use an immutable build digest in production.

## Protocol

The service exposes `POST /everval/v1/tasks`. Requests contain `protocol_version: 1`, a request ID, adapter revision, model, limits, and a task with only `id`, `name`, `domain`, `instruction`, and `inputs`. Private checks, reference outputs, and provenance are never sent. Requests are limited to 128 KiB.

Return `protocol_version: 1`, the exact frozen revision, an output object, aggregate `usage.prompt_tokens` and `usage.completion_tokens`, and optionally a bounded trace or error. Responses are limited to 2 MiB and 200 trace entries. Do not put credentials or hidden grader material in traces.

Usage is adapter-reported, not independently metered. Everval reserves the full declared token ceilings before admission. Missing usage retains uncertain billing and blocks subsequent paid calls in that experiment. Revision mismatch, malformed output, and exceeded ceilings remain unsuccessful attempts with known charges retained. There are no automatic paid retries.

The measured provider stage covers the full external service request. Internal adapter stages are not independently timed by Everval.

[Diagnose failures](diagnostics.md) · [Cost and latency](cost-latency.md)
