# Document/RAG assistant: cite the current policy

```sh
uv run everval demo --example documents
```

The task supplies a current 30-day enterprise refund policy and an archived 14-day policy. The output must contain `refund_days` and `citations`; every citation carries a source ID and exact supporting quotation.

Independent checks reject the wrong refund window, an archived-only citation, or a fabricated quotation. Controls include near misses rather than only an empty answer.

Citation checks verify source membership and verbatim quotation. They do not establish general semantic entailment or evaluate retrieval recall over a live corpus. In this small example the sources are already supplied. To evaluate a retrieval application, wrap your existing application with the [agent adapter](../../../docs/agent-adapters.md) and curate permitted examples with independently reviewed answers and citations.
