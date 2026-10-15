"""Create explicit, reviewable configuration candidates without changing a saved system."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from cvals.models import System, content_hash, new_id
from cvals.store import Store


def variant(store: Store, source_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    with store._connect() as connection:
        source = store._get(connection, "systems", source_id)
    if source["kind"] != "http":
        raise ValueError("Configuration variants require a live system")
    allowed = {"model", "temperature", "max_steps", "max_tokens", "additional_instructions"}
    allowed |= {"input_cost_per_million", "output_cost_per_million", "pricing_version"}
    if overrides.get("model", source["model"]) != source["model"]:
        price_keys = {"input_cost_per_million", "output_cost_per_million", "pricing_version"}
        if (
            not price_keys <= overrides.keys()
            or any(overrides[k] is None for k in price_keys)
            or not str(overrides["pricing_version"]).strip()
        ):
            raise ValueError(
                "A model change requires explicit input/output rates and a pricing version for that model"
            )
    if set(overrides) - allowed:
        raise ValueError("Unsupported variant setting")
    candidate = {**source, **overrides, "id": new_id(), "name": name}
    candidate["provenance"] = {
        **source.get("provenance", {}),
        "variant_of": source_id,
        "source_configuration_hash": content_hash(source),
    }
    system = System.model_validate(candidate)
    store.save_system(system)
    return system.model_dump(mode="json")


def register(systems: typer.Typer) -> None:
    from cvals.cli import fail, output, workspace

    @systems.command("variant")
    def create_variant(
        source_id: str,
        name: str,
        model: str | None = None,
        temperature: Annotated[float | None, typer.Option(min=0, max=2)] = None,
        max_steps: Annotated[int | None, typer.Option(min=1, max=30)] = None,
        max_tokens: Annotated[int | None, typer.Option(min=1, max=32768)] = None,
        input_rate: Annotated[float | None, typer.Option(min=0)] = None,
        output_rate: Annotated[float | None, typer.Option(min=0)] = None,
        pricing_version: str | None = None,
        instructions_file: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    ) -> None:
        """Save a model, prompt or tool-budget candidate; no provider call is made."""
        try:
            _, store = workspace()
            changes = {
                key: value
                for key, value in {
                    "model": model,
                    "temperature": temperature,
                    "max_steps": max_steps,
                    "max_tokens": max_tokens,
                    "input_cost_per_million": input_rate,
                    "output_cost_per_million": output_rate,
                    "pricing_version": pricing_version,
                }.items()
                if value is not None
            }
            if instructions_file is not None:
                with instructions_file.open("rb") as stream:
                    raw = stream.read(32001)
                if len(raw) > 32000:
                    raise ValueError("Instruction file exceeds the 32 KiB input limit")
                changes["additional_instructions"] = raw.decode("utf-8")
            output(variant(store, source_id, name, **changes))
        except (ValueError, KeyError, OSError) as exc:
            fail(exc)
