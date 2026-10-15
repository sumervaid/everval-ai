from pathlib import Path

import pytest

from cvals.config import Settings
from cvals.models import Check, Scenario, System
from cvals.providers import payload_for, reserve_cost
from cvals.store import Store
from cvals.variants import variant


def test_variant_keeps_source_and_prices_bounded_prompt(tmp_path: Path) -> None:
    store = Store(Settings(tmp_path))
    store.init()
    source = System(
        name="Original",
        kind="http",
        base_url="http://127.0.0.1:9",
        model="example",
        input_cost_per_million=1,
        output_cost_per_million=1,
    )
    store.save_system(source)
    candidate = variant(
        store,
        source.id,
        "Concise",
        additional_instructions="Explain with citations.",
        max_tokens=100,
    )
    assert candidate["id"] != source.id
    assert store.get_system(source.id)["additional_instructions"] == ""
    assert candidate["provenance"]["variant_of"] == source.id
    task = Scenario(
        name="Source",
        domain="documents",
        instruction="Answer",
        inputs={"document": "Public source"},
        reference_output={"answer": "hidden reference"},
        checks=[Check(name="Private", kind="equals", path="answer", expected="hidden reference")],
    )
    payload = payload_for(task, System.model_validate(candidate))
    assert "Explain with citations." in payload["messages"][0]["content"]
    assert "hidden reference" not in str(payload)
    longer = System.model_validate({**candidate, "additional_instructions": "Long prompt " * 100})
    assert reserve_cost(task, longer) > reserve_cost(task, System.model_validate(candidate))
    with pytest.raises(ValueError):
        variant(store, source.id, "Invalid", unsupported=True)
    with pytest.raises(ValueError):
        variant(store, source.id, "Too long", additional_instructions="x" * 8001)


def test_external_adapter_cannot_silently_ignore_prompt() -> None:
    with pytest.raises(ValueError, match="adapter changes"):
        System(
            name="Agent",
            kind="http",
            model="test",
            base_url="http://127.0.0.1:9",
            harness="external-agent",
            adapter_revision="v1",
            additional_instructions="Ignored",
        )


def test_changing_model_cannot_reuse_unreviewed_source_prices(tmp_path: Path) -> None:
    store = Store(Settings(tmp_path))
    store.init()
    source = System(
        name="Original",
        kind="http",
        model="original",
        base_url="http://127.0.0.1:9",
        input_cost_per_million=0,
        output_cost_per_million=0,
    )
    store.save_system(source)
    with pytest.raises(ValueError, match="explicit input/output rates"):
        variant(store, source.id, "Different", model="different")
    candidate = variant(
        store,
        source.id,
        "Different",
        model="different",
        input_cost_per_million=3,
        output_cost_per_million=12,
        pricing_version="reviewed-2026-10-14",
    )
    assert candidate["input_cost_per_million"] == 3
    assert candidate["output_cost_per_million"] == 12
    assert candidate["pricing_version"] == "reviewed-2026-10-14"
