"""Presentation helpers that preserve missing values and untrusted text."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from rich.text import Text


def plain(value: Any, default: str = "—") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=True, default=str)
    return "".join(char for char in str(value) if unicodedata.category(char) not in {"Cc", "Cf"})


def percent(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1%}"


def money(value: Any) -> str:
    return "unknown" if value is None else f"${float(value):.4f}"


def record_text(record: Any) -> str:
    return json.dumps(record, indent=2, ensure_ascii=True, default=str)


def status(value: Any) -> Text:
    label = plain(value)
    style = (
        "#83d7c7"
        if label in {"validated", "completed", "accepted", "ready", "passed", "released"}
        else "#e6bd76"
        if label in {"pending", "running", "queued", "draft", "discovered"}
        else "#e39898"
        if label in {"rejected", "failed", "error", "invalid", "interrupted"}
        else "#a9b8c7"
    )
    return Text(label.replace("_", " "), style=style)


def system_label(system: dict[str, Any]) -> str:
    name = plain(system.get("name", system.get("id")))
    if system.get("kind") in {"reference", "incomplete"}:
        return name if "control" in name.lower() else name + " · fixture control"
    if system.get("kind") == "replay":
        return name + " · imported evidence"
    return name
