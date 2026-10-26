"""Local, durable conversation planning over configured benchmark resources.

The parser recognizes a deliberately bounded vocabulary. It never asks a model
to invent evaluation criteria, executes text, or reads paths mentioned in chat.
Missing resources and unsupported delivery/scheduling requests remain blockers.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from cvals.audit import append_audit
from cvals.models import Manifest, canonical_json, content_hash, new_id, utc_now

if TYPE_CHECKING:
    from cvals.config import Settings
    from cvals.store import Store

MAX_MESSAGES = 200
FORMATS = {"html": "html", "markdown": "markdown", "md": "markdown", "csv": "csv", "json": "json"}
NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fourteen": 14,
    "thirty": 30,
}
WELCOME = (
    "Describe which configured models to compare, the task collection, how often to run, "
    "and the report format. For example: Compare MODEL-A and MODEL-B on TASKS every two "
    "weeks, with HTML and CSV reports, $5 per run and $50 total. "
    "Reports stay in this workspace. Type models or tasks to see your choices."
)


class ConversationDraft(Manifest):
    name: str = Field(default="Recurring benchmark", min_length=1, max_length=200)
    collection_id: str | None = Field(default=None, max_length=128)
    system_ids: list[str] = Field(default_factory=list, max_length=20)
    interval_minutes: int | None = Field(default=None, ge=5, le=525600)
    report_formats: list[Literal["html", "markdown", "csv", "json"]] = Field(
        default=["html"], min_length=1, max_length=4
    )
    budget_usd: float | None = Field(default=None, ge=0, le=100000)
    lifetime_budget_usd: float | None = Field(default=None, ge=0, le=1000000)
    repetitions: int = Field(default=1, ge=1, le=30)


def _contains(text: str, name: str) -> bool:
    return bool(
        name and re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.[\w-])", text, re.I)
    )


def _matches(
    text: str, entries: list[dict[str, Any]], fields: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    # Longest spans win: "Model tuned" must not also select a saved "Model".
    # At the same span an exact system ID takes precedence over display aliases.
    aliases: dict[tuple[str, int], set[str]] = {}
    for entry in entries:
        for priority, field in enumerate(fields):
            alias = str(entry.get(field) or "").casefold()
            if alias:
                aliases.setdefault((alias, priority), set()).add(entry["id"])
    occurrences = []
    for (alias, priority), ids in aliases.items():
        for match in re.finditer(
            r"(?<![\w.-])" + re.escape(alias) + r"(?![\w-]|\.[\w-])", text, re.I
        ):
            occurrences.append((match.start(), match.end(), priority, alias, ids))
    occupied: list[tuple[int, int]] = []
    selected: set[str] = set()
    ambiguous: list[str] = []
    for start, end, _, alias, ids in sorted(
        occurrences, key=lambda item: (-(item[1] - item[0]), item[2])
    ):
        if any(left <= start and end <= right for left, right in occupied):
            continue
        occupied.append((start, end))
        if len(ids) == 1:
            selected.update(ids)
        else:
            ambiguous.append(alias)
    return [entry["id"] for entry in entries if entry["id"] in selected], ambiguous


def _message(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content, "created_at": utc_now()}


class ConversationService:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        store.init()
        with store._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS benchmark_conversations (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, hash TEXT NOT NULL
                );
            """)

    def catalog(self) -> dict[str, Any]:
        return {
            "systems": [
                {key: value.get(key) for key in ("id", "name", "model", "kind", "configured")}
                for value in self.store.list_systems()
            ],
            "collections": [
                {
                    "id": value["id"],
                    "name": value["name"],
                    "domain": value["domain"],
                    "scenario_count": len(value["scenarios"]),
                }
                for value in self.store.list_collections()
            ],
            "report_formats": ["html", "markdown", "csv", "json"],
            "delivery": "local workspace",
            "schedule": "fixed intervals of 5 minutes to 365 days; worker must be running",
        }

    def _load(self, connection: Any, identifier: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT data,hash FROM benchmark_conversations WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        value: dict[str, Any] = json.loads(row["data"])
        if content_hash(value) != row["hash"]:
            raise ValueError("Conversation integrity check failed")
        return value

    def _save(self, connection: Any, value: dict[str, Any]) -> None:
        value["updated_at"] = utc_now()
        connection.execute(
            "INSERT INTO benchmark_conversations VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data,hash=excluded.hash",
            (value["id"], canonical_json(value), content_hash(value)),
        )

    def create(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": new_id(),
            "status": "draft",
            "created_at": utc_now(),
            "messages": [_message("assistant", WELCOME)],
            "draft": ConversationDraft().model_dump(mode="json"),
            "blockers": {},
            "missing": ["models", "task collection", "schedule"],
            "errors": [],
            "ready": False,
            "execution_requested": False,
            "activation_requested": False,
            "explicit_budgets": [],
            "experiment_id": None,
            "schedule_id": None,
            "reports": [],
        }
        with self.store._connect(write=True) as connection:
            if (
                connection.execute("SELECT COUNT(*) FROM benchmark_conversations").fetchone()[0]
                >= 500
            ):
                raise ValueError("This workspace supports at most 500 conversations")
            self._save(connection, value)
            append_audit(connection, "conversation.created", value["id"], {})
        return value

    def get(self, identifier: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            value = self._load(connection, identifier)
        if value.get("activation_requested") or value["status"] != "draft":
            from cvals.conversation_execution import ExecutionService

            try:
                execution = ExecutionService(self.settings, self.store).get(identifier)
            except KeyError:
                pass
            else:
                value.update(execution)
                value["id"] = identifier
        return value

    def list(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            identifiers = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM benchmark_conversations ORDER BY rowid DESC LIMIT 100"
                )
            ]
        return [self.get(identifier) for identifier in identifiers]

    def _resources(self, value: dict[str, Any], text: str, catalog: dict[str, Any]) -> None:
        draft, blockers = value["draft"], value["blockers"]
        systems, ambiguous = _matches(text, catalog["systems"], ("id", "name", "model"))
        if re.search(r"\ball (?:configured )?(?:models|systems)\b", text, re.I):
            systems = [item["id"] for item in catalog["systems"] if item["kind"] != "replay"]
        if systems:
            if re.search(r"\b(?:add|also include)\b", text, re.I):
                systems = list(dict.fromkeys([*draft["system_ids"], *systems]))
            draft["system_ids"] = systems
            blockers.pop("models", None)
            blockers.pop("selection", None)
        if ambiguous:
            blockers["models"] = (
                "Several configurations share these model names: "
                + ", ".join(ambiguous)
                + ". Use their exact system IDs."
            )
        model_clause = re.search(
            r"(?:\b(?:compare|evaluate|benchmark|test)\s+|\bmodels?\s*(?:are|:|=|to)\s*|\busing\s+)"
            r"(.+?)(?=\s+(?:on|against|every|weekly|daily|with|for|as|in|using)\b|[;\n]|$)",
            text,
            re.I,
        )
        if model_clause and (
            systems
            or re.search(r"\b(?:compare|evaluate|test|models?\s*(?:[:=]|to|are))", text, re.I)
        ):
            remainder = model_clause[1]
            aliases = sorted(
                {
                    str(item.get(field) or "")
                    for item in catalog["systems"]
                    for field in ("id", "name", "model")
                },
                key=len,
                reverse=True,
            )
            for alias in aliases:
                if alias:
                    remainder = re.sub(
                        r"(?<![\w.-])" + re.escape(alias) + r"(?![\w-]|\.[\w-])",
                        " ",
                        remainder,
                        flags=re.I,
                    )
            remainder = remainder.replace(chr(34), " ").replace(chr(39), " ")
            remainder = re.sub(
                r"\b(?:a|an|the|and|or|vs|versus|both|all|configured|models?|systems?|please|these|using)\b",
                " ",
                remainder,
                flags=re.I,
            ).strip(" ,&.:+-\t\n")
            if remainder:
                blockers["models"] = (
                    "I could not identify all requested models. Use exact configured model names or IDs."
                )
        collections, ambiguous = _matches(text, catalog["collections"], ("id", "name"))
        if len(collections) == 1 and not ambiguous:
            draft["collection_id"] = collections[0]
            blockers.pop("data", None)
            selected = next(item for item in catalog["collections"] if item["id"] == collections[0])
            draft["name"] = (selected["name"] + " · recurring")[:200]
        elif len(collections) > 1 or ambiguous:
            blockers["data"] = (
                "Choose one task collection for this benchmark; create another conversation for another collection."
            )
        data_clause = re.search(
            r"\b(?:on|(?:collection|dataset|data|tasks?)\s*(?:is|are|:|=|to))\s+(.+?)"
            r"(?=\s+(?:every|with|as|in|for|using|weekly|daily)\b|[;\n]|$)",
            text,
            re.I,
        )
        if data_clause:
            remainder = data_clause[1]
            for item in sorted(
                catalog["collections"], key=lambda item: len(item["name"]), reverse=True
            ):
                for field in ("name", "id"):
                    remainder = re.sub(
                        r"(?<![\w.-])" + re.escape(item[field]) + r"(?![\w-]|\.[\w-])",
                        " ",
                        remainder,
                        flags=re.I,
                    )
            remainder = re.sub(
                r"\b(?:the|and|collection|dataset|tasks)\b", " ", remainder, flags=re.I
            )
            remainder = remainder.strip(" ,&.:" + chr(34) + chr(39))
            if remainder:
                blockers["data"] = (
                    "I could not identify the requested task collection. Use one exact collection name or ID."
                )
        if re.search(
            r"\b(?:without|except|exclude|excluding|but not|do not use|don't use)\b", text, re.I
        ):
            blockers["selection"] = (
                "Restate the exact models and complete task collection to include; exclusion expressions are not supported."
            )
        if (
            re.search(r"^\s*(?:use|switch to|change to)\b", text, re.I)
            and not systems
            and not collections
            and not re.search(
                r"\b(?:html|markdown|md|csv|json|local|locally|reports?|format|budget|caps?|every|daily|weekly|hourly)\b",
                text,
                re.I,
            )
        ):
            blockers["selection"] = (
                "I could not resolve that selection. Use a configured model or task collection name."
            )
        if re.search(
            r"(?:https?://|(?:^|\s)[/~][^\s]+|\b[^\s]+\.(?:csv|jsonl|json|parquet|xlsx)\b)",
            text,
            re.I,
        ):
            blockers["data"] = (
                "That data source must first be imported as a task collection with explicit grading checks. "
                "Chat does not read files or invent correct answers. Then use the collection name here."
            )
        if re.search(r"\b(?:latest|new|updated|fresh)\s+(?:data|rows|tasks|files)\b", text, re.I):
            blockers["data"] = (
                "Recurring benchmarks freeze the chosen task collection. Automatic ingestion of new data is not supported."
            )
        if re.search(
            r"\b(?:sample|random|only failed|first \d+|top \d+)\s*(?:tasks?|rows?|cases?|examples?)?\b",
            text,
            re.I,
        ):
            blockers["data"] = (
                "Create a task collection containing that selection first; chat runs the complete selected collection."
            )

    def _schedule(self, value: dict[str, Any], text: str) -> None:
        blockers, draft = value["blockers"], value["draft"]
        normalized = text.casefold()
        for word, number in NUMBERS.items():
            normalized = re.sub(r"\b" + word + r"\b", str(number), normalized)
        match = re.search(r"\bevery\s+(?:(\d+)\s+)?(minutes?|hours?|days?|weeks?)\b", normalized)
        minutes = None
        if match:
            unit = match[2].rstrip("s")
            minutes = (
                int(match[1] or 1) * {"minute": 1, "hour": 60, "day": 1440, "week": 10080}[unit]
            )
        elif re.search(
            r"\b(?:every other week|fortnightly|fortnight|biweekly|bi-weekly)\b", normalized
        ):
            minutes = 20160
        elif cadence := re.search(r"\b(daily|weekly|hourly)\b", normalized):
            unit = cadence[1]
            minutes = {"daily": 1440, "weekly": 10080, "hourly": 60}[unit]
        if minutes is None and re.search(r"\bevery\b", normalized):
            blockers["schedule"] = "Use an interval in whole minutes, hours, days, or weeks."
        if minutes is not None:
            if 5 <= minutes <= 525600:
                draft["interval_minutes"] = minutes
                blockers.pop("schedule", None)
            else:
                blockers["schedule"] = "Choose an interval from 5 minutes to 365 days."
        if re.search(
            r"\b(?:months?|monthly|years?|yearly|cron|weekdays?|weekends?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|timezone)\b"
            r"|\b(?:starting|beginning)\b|\bstart\s+(?:in|on|tomorrow|next)\b|\bat\s+\d|\b(?:UTC|GMT|EST|EDT|PST|PDT)\b|\bevery\s+\d+\.\d+",
            text,
            re.I,
        ):
            blockers["schedule"] = (
                "Use an elapsed interval such as every two weeks. Calendar dates, clock times, "
                "time zones, and fractional intervals are not supported in chat."
            )

    def _formats_and_delivery(self, value: dict[str, Any], text: str) -> None:
        blockers, draft = value["blockers"], value["draft"]
        formats = list(
            dict.fromkeys(
                FORMATS[match.casefold()]
                for match in re.findall(
                    r"(?<![\w./-])(html|markdown|md|csv|json)(?![\w-]|\.[\w-])", text, re.I
                )
            )
        )
        if formats:
            draft["report_formats"] = formats
            blockers.pop("format", None)
        unsupported = re.search(
            r"\b(pdf|xlsx|excel|powerpoint|pptx|docx|word document|xml|yaml|parquet|tsv|zip)\b",
            text,
            re.I,
        )
        requested_format = re.search(
            r"\b(?:in|as)\s+(?:an?\s+)?([\w-]+)\s+(?:format|report)\b", text, re.I
        )
        requested_format = requested_format or re.search(
            r"\b(?:results?|reports?)\s+(?:in|as)\s+([\w-]+)\b"
            r"|\bformat\s*(?:is|:|=)\s*([\w-]+)\b",
            text,
            re.I,
        )
        if (
            (re.search(r"\bformat\b", text, re.I) and not formats)
            or unsupported
            or (
                requested_format
                and (requested_format[1] or requested_format[2]).casefold() not in FORMATS
            )
        ):
            blockers["format"] = (
                "Reports support HTML, Markdown, CSV, and JSON. Choose one or more of those formats."
            )
        if re.search(
            r"\b(?:locally|local(?:ly)? (?:only|reports?|workspace|files?)|keep (?:them|it|results|reports) here)\b",
            text,
            re.I,
        ):
            blockers.pop("delivery", None)
        if re.search(
            r"\b(?:email|e-mail|slack|teams|discord|webhook|s3|sharepoint|dropbox|google drive|notion|publish|upload)\b"
            r"|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
            text,
            re.I,
        ):
            blockers["delivery"] = (
                "Automatic external delivery is not configured. Reports can be saved locally "
                "and opened or downloaded here. Say 'save reports locally' to use that delivery."
            )

    def _budgets(self, value: dict[str, Any], text: str) -> None:
        draft, blockers = value["draft"], value["blockers"]
        amount = r"(\d+(?:\.\d+)?)"
        labels = {
            "budget_usd": r"(?:per[ -]run|each run|a run|per evaluation|per benchmark)",
            "lifetime_budget_usd": r"(?:total|lifetime|overall|in total)",
        }
        changed = False
        for field, label in labels.items():
            matches = re.findall(r"\$\s*" + amount + r"\s*(?:USD\s*)?" + label, text, re.I)
            matches += re.findall(
                label + r"(?:\s+(?:budget|cap|of|is|:|=|under|at most))*\s*\$\s*" + amount,
                text,
                re.I,
            )
            if matches:
                if len(set(matches)) > 1:
                    blockers["budget"] = (
                        "Give one per-run cap and one total cap, for example $5 per run and $50 total."
                    )
                    return
                parsed = float(matches[0])
                ceiling = 100000 if field == "budget_usd" else 1000000
                if parsed > ceiling:
                    blockers["budget"] = "That budget exceeds the supported limit."
                    return
                draft[field] = parsed
                if field not in value["explicit_budgets"]:
                    value["explicit_budgets"].append(field)
                changed = True
        if re.search(r"\b(?:zero budget|no spend|free only)\b", text, re.I):
            draft["budget_usd"] = draft["lifetime_budget_usd"] = 0.0
            value["explicit_budgets"] = list(labels)
            changed = True
        if changed:
            blockers.pop("budget", None)
        if re.search(r"\$\s*-|\b(?:unlimited|no (?:budget )?cap)\b", text, re.I):
            blockers["budget"] = "Use finite, nonnegative per-run and total USD caps."
        amounts = re.findall(r"\$\s*" + amount, text)
        if amounts and not changed:
            blockers["budget"] = (
                "Label both caps explicitly: for example $5 per run and $50 total, including the first run."
            )

    def _recompute(self, value: dict[str, Any], catalog: dict[str, Any]) -> None:
        draft, blockers = value["draft"], value["blockers"]
        selected = [item for item in catalog["systems"] if item["id"] in draft["system_ids"]]
        available_ids = {item["id"] for item in selected}
        if set(draft["system_ids"]) != available_ids:
            blockers["models"] = (
                "A selected model configuration is no longer available. Choose a configured model."
            )
        if any(item["kind"] == "replay" for item in selected):
            blockers["models"] = (
                "Recorded submissions cannot produce new evidence on a recurring schedule."
            )
        if selected and len(selected) == len(draft["system_ids"]):
            free = all(item["kind"] in {"reference", "incomplete"} for item in selected)
            for key in ("budget_usd", "lifetime_budget_usd"):
                if key not in value["explicit_budgets"]:
                    draft[key] = 0.0 if free else None
        if draft["collection_id"] and not any(
            item["id"] == draft["collection_id"] for item in catalog["collections"]
        ):
            blockers["data"] = "The selected task collection is no longer available."
        if draft["budget_usd"] is not None and draft["lifetime_budget_usd"] is not None:
            if draft["lifetime_budget_usd"] < draft["budget_usd"]:
                blockers["budget"] = "The total cap must cover at least the first per-run cap."
            elif (
                blockers.get("budget") == "The total cap must cover at least the first per-run cap."
            ):
                blockers.pop("budget", None)
        missing = []
        for key, label in (
            ("system_ids", "models"),
            ("collection_id", "task collection"),
            ("interval_minutes", "schedule"),
            ("budget_usd", "per-run budget"),
            ("lifetime_budget_usd", "total budget"),
        ):
            if draft[key] is None or draft[key] == []:
                missing.append(label)
        value["draft"] = ConversationDraft.model_validate(draft).model_dump(mode="json")
        value["missing"], value["errors"] = missing, list(blockers.values())
        value["ready"] = not missing and not blockers

    @staticmethod
    def _summary(value: dict[str, Any], catalog: dict[str, Any]) -> str:
        draft = value["draft"]
        model_names = [
            item["name"] for item in catalog["systems"] if item["id"] in draft["system_ids"]
        ]
        collection = next(
            (item for item in catalog["collections"] if item["id"] == draft["collection_id"]), None
        )
        details = []
        if model_names:
            details.append("Models: " + ", ".join(model_names) + ".")
        if collection:
            count = collection["scenario_count"]
            noun = "task" if count == 1 else "tasks"
            details.append(f"Tasks: {collection['name']} ({count} {noun}).")
        if draft["interval_minutes"]:
            minutes = draft["interval_minutes"]
            unit, count = (
                ("weeks", minutes // 10080)
                if minutes % 10080 == 0
                else (
                    ("days", minutes // 1440)
                    if minutes % 1440 == 0
                    else (("hours", minutes // 60) if minutes % 60 == 0 else ("minutes", minutes))
                )
            )
            label = unit[:-1] if count == 1 else unit
            details.append(f"Every {count} {label}; first run queues immediately.")
        details.append("Reports: " + ", ".join(draft["report_formats"]) + ", saved locally.")
        if draft["budget_usd"] is not None and draft["lifetime_budget_usd"] is not None:
            details.append(
                f"Caps: ${draft['budget_usd']:g} per run, ${draft['lifetime_budget_usd']:g} total including the first run."
            )
        if value["errors"]:
            details.extend(value["errors"])
        if value["missing"]:
            details.append("Still needed: " + ", ".join(value["missing"]) + ".")
        elif value["ready"] and not value["execution_requested"]:
            details.append("Type start to queue the first run and enable the schedule.")
        return "\n".join(details)

    def send(self, identifier: str, message: str) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip() or len(message) > 8000:
            raise ValueError("Send a nonempty message of at most 8,000 characters")
        if re.search(r"\bsk-[A-Za-z0-9_-]{16,}|\bBearer\s+[A-Za-z0-9_.-]{16,}", message):
            raise ValueError("Configure credentials through model settings, not in chat.")
        text = message.strip()
        command = text.casefold().strip(".!")
        if command in {"pause", "pause it", "stop schedule"}:
            return self.pause(identifier)
        if command in {"resume", "resume it"}:
            return self.resume(identifier)
        catalog = self.catalog()
        current = self.get(identifier)
        activate = False
        with self.store._connect(write=True) as connection:
            value = self._load(connection, identifier)
            if len(value["messages"]) >= MAX_MESSAGES:
                raise ValueError("This conversation is full; start a new conversation")
            value["messages"].append(_message("user", text))
            if command in {"status", "show status"}:
                answer = (
                    "The benchmark is "
                    + current["status"]
                    + f". {len(current.get('reports', []))} report files available."
                )
                schedule = current.get("schedule") or {}
                if schedule.get("next_run_at") and current["status"] == "active":
                    answer += "\nNext scheduled run: " + schedule["next_run_at"] + "."
                if schedule.get("last_error"):
                    answer += "\nSchedule issue: " + schedule["last_error"]
                for error in current.get("report_errors", []):
                    answer += "\nReport issue: " + str(
                        error.get("error", "Report generation is pending.")
                    )
                if current.get("errors"):
                    answer += "\n" + "\n".join(current["errors"])
            elif command in {"help", "models", "systems", "tasks", "collections"}:
                selected_catalog = (
                    ("systems", "collections")
                    if command == "help"
                    else (("systems",) if command in {"models", "systems"} else ("collections",))
                )
                lines = [WELCOME] if command == "help" else []
                for kind in selected_catalog:
                    lines.append(kind.title() + ":")
                    lines += [f"{item['name']} — {item['id']}" for item in catalog[kind]]
                    if not catalog[kind]:
                        lines.append("None configured yet.")
                answer = "\n".join(lines)
            elif value["status"] != "draft" or value.get("activation_requested"):
                if command in {"start", "run it", "start it", "go", "do it"}:
                    activate = True
                    answer = "Checking the existing benchmark; it will not be duplicated."
                else:
                    answer = (
                        "This benchmark has already been queued. Type status, pause, or resume. "
                        "Start a new conversation to change its frozen tasks, models, or budget."
                    )
            else:
                value["blockers"].pop("admission", None)
                self._resources(value, text, catalog)
                schedule_text = text
                for item in [*catalog["systems"], *catalog["collections"]]:
                    for key in ("name", "id", "model"):
                        alias = str(item.get(key) or "")
                        if alias:
                            schedule_text = re.sub(
                                r"(?<![\w.-])" + re.escape(alias) + r"(?![\w-]|\.[\w-])",
                                " ",
                                schedule_text,
                                flags=re.I,
                            )
                self._schedule(value, schedule_text)
                self._formats_and_delivery(value, text)
                self._budgets(value, text)
                repetition = re.search(
                    r"\brepeat(?: each (?:task|case))?\s+(\d+)\s+times?\b|\b(\d+)\s+repetitions\b",
                    text,
                    re.I,
                )
                if repetition:
                    count = int(repetition[1] or repetition[2])
                    if 1 <= count <= 30:
                        value["draft"]["repetitions"] = count
                        value["blockers"].pop("repetitions", None)
                    else:
                        value["blockers"]["repetitions"] = "Choose 1 to 30 repetitions per task."
                elif re.search(r"\b(?:repeat each|repetitions|times per task)\b", text, re.I):
                    value["blockers"]["repetitions"] = (
                        "Specify repetitions as 'repeat each task 3 times'."
                    )
                if re.search(r"\b(?:stop after|until|for \d+ runs|next \d+ runs)\b", text, re.I):
                    value["blockers"]["stop"] = (
                        "Run-count and end-date limits are not supported. Use a total budget and pause the schedule when needed."
                    )
                if re.search(
                    r"\b(?:budget caps only|remove (?:the )?(?:stop|run.count|end.date) limit)\b",
                    text,
                    re.I,
                ):
                    value["blockers"].pop("stop", None)
                if re.search(
                    r"\b(?:plan only|draft only|do not (?:start|run)|don't (?:start|run)|preview only|what if|show me how|create a plan|make a plan)\b|^\s*how\b",
                    text,
                    re.I,
                ):
                    value["execution_requested"] = False
                elif command in {"start", "run it", "start it", "go", "do it"} or re.search(
                    r"(?:^|[.!?;]\s*|\b(?:please|want to|would like to|can you|could you)\s+)(?:compare|run|create|start|benchmark|evaluate|test)\b",
                    text,
                    re.I,
                ):
                    value["execution_requested"] = True
                self._recompute(value, catalog)
                answer = self._summary(value, catalog)
                activate = value["ready"] and value["execution_requested"]
            value["messages"].append(_message("assistant", answer))
            self._save(connection, value)
            append_audit(
                connection,
                "conversation.message",
                identifier,
                {"message_count": len(value["messages"]), "ready": value["ready"]},
            )
        return self.activate(identifier) if activate else self.get(identifier)

    def activate(self, identifier: str) -> dict[str, Any]:
        from cvals.conversation_execution import ExecutionService

        catalog = self.catalog()
        with self.store._connect(write=True) as connection:
            value = self._load(connection, identifier)
            if not value.get("activation_requested"):
                value["blockers"].pop("admission", None)
                self._recompute(value, catalog)
                if not value["ready"]:
                    raise ValueError(
                        "Complete the missing information and resolve the listed issues before starting"
                    )
                value["activation_requested"] = True
                self._save(connection, value)
            plan = dict(value["draft"])
        execution = ExecutionService(self.settings, self.store)
        try:
            result = execution.activate(identifier, plan)
        except (ValueError, KeyError) as exc:
            with self.store._connect(write=True) as connection:
                value = self._load(connection, identifier)
                value["activation_requested"] = False
                value["blockers"]["admission"] = str(exc)[:1000]
                value["errors"] = list(value["blockers"].values())
                value["ready"] = False
                value["messages"].append(
                    _message("assistant", "The benchmark was not started: " + str(exc)[:1000])
                )
                self._save(connection, value)
            return self.get(identifier)
        with self.store._connect(write=True) as connection:
            value = self._load(connection, identifier)
            first = value["experiment_id"] is None
            value.update(result)
            value["id"] = identifier
            value["activation_requested"] = True
            value["ready"] = True
            if first:
                value["messages"].append(
                    _message(
                        "assistant",
                        "Your benchmark is queued and its recurring schedule is enabled. "
                        "Results will appear here when the worker finishes. Keep everval worker running "
                        "for scheduled work. Type status or pause at any time.",
                    )
                )
            self._save(connection, value)
        return self.get(identifier)

    def _set_running(self, identifier: str, running: bool) -> dict[str, Any]:
        from cvals.conversation_execution import ExecutionService

        execution = ExecutionService(self.settings, self.store)
        try:
            result = execution.resume(identifier) if running else execution.pause(identifier)
        except KeyError:
            raise ValueError("This conversation has no running benchmark yet") from None
        with self.store._connect(write=True) as connection:
            value = self._load(connection, identifier)
            value.update(result)
            value["id"] = identifier
            value["messages"].extend(
                [
                    _message("user", "resume" if running else "pause"),
                    _message(
                        "assistant",
                        "The schedule is enabled."
                        if running
                        else (
                            "The schedule is paused. Already queued work remains visible in Experiments."
                        ),
                    ),
                ]
            )
            self._save(connection, value)
        return self.get(identifier)

    def pause(self, identifier: str) -> dict[str, Any]:
        return self._set_running(identifier, False)

    def resume(self, identifier: str) -> dict[str, Any]:
        return self._set_running(identifier, True)
