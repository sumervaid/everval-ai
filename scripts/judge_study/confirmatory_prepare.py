"""Prepare fresh human-reference families without choosing sample size or calling models.

Raw text and annotations remain private. The metadata manifest contains source hashes
and selection decisions. It is an eligible pool, not a final study freeze.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DATASET = "nvidia/HelpSteer3"
REVISION = "f6d145777bcbde96137596340fab89793acd1031"
SOURCE_SHA256 = "cd0d8b6efd7869a44c1b5ff91701232062555829b3c838abfd770d7dab6c7861"
SOURCE_FILE = "preference/validation.jsonl.gz"
SEED = "everval-judge-confirmation-2026-10-27-v1"
BASE_URL = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}"
LICENSE = "CC-BY-4.0"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def strict_json(data: str | bytes) -> Any:
    return json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def visible(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("context", "response1", "response2")}


def _row_problem(row: Any) -> str | None:
    if not isinstance(row, dict):
        return "row_is_not_an_object"
    if any(
        not isinstance(row.get(key), str) or not row[key].strip() for key in ("domain", "language")
    ):
        return "missing_domain_or_language"
    if any(not isinstance(row.get(key), str) for key in ("response1", "response2")):
        return "missing_candidate_response_string"
    context = row.get("context")
    if not isinstance(context, list) or not context:
        return "missing_context"
    if any(
        not isinstance(message, dict)
        or set(message) != {"role", "content"}
        or message["role"] not in {"system", "user", "assistant"}
        or not isinstance(message["content"], str)
        for message in context
    ):
        return "invalid_context_message"
    if context[-1]["role"] != "user" or not any(m["role"] == "user" for m in context):
        return "context_has_no_final_user_request"
    first_user = next(m["content"] for m in context if m["role"] == "user")
    if not normalize(first_user):
        return "empty_first_user_family_key"
    preferences = row.get("individual_preference")
    if not isinstance(preferences, list) or not 1 <= len(preferences) <= 3:
        return "invalid_retained_vote_count"
    if any(
        not isinstance(vote, dict)
        or type(vote.get("score")) is not int
        or not -3 <= vote["score"] <= 3
        for vote in preferences
    ):
        return "invalid_retained_vote_score"
    return None


def prepare_rows(
    rows: list[Any],
    *,
    excluded_prompts: list[str],
    source_digest: str,
    seed: str = SEED,
    line_hashes: list[str] | None = None,
) -> dict[str, Any]:
    """Retain one label-independently ranked pair per normalized first-user family."""
    if line_hashes is not None and len(line_hashes) != len(rows):
        raise ValueError("Each source row needs exactly one original line hash")
    if not seed:
        raise ValueError("A nonempty sampling seed is required")
    old = {normalize(prompt) for prompt in excluded_prompts}
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded: list[dict[str, Any]] = []
    old_families: set[str] = set()
    seen_rows: set[str] = set()
    normalized_contexts: set[str] = set()
    provenance_keys = ("source_row_index", "canonical_row_sha256", "original_line_sha256")
    for index, row in enumerate(rows):
        row_digest = digest(row)
        record = {
            "source_row_index": index,
            "canonical_row_sha256": row_digest,
            "original_line_sha256": line_hashes[index] if line_hashes else None,
        }
        problem = _row_problem(row)
        if problem:
            excluded.append({**record, "reason": problem})
            continue
        first = next(m["content"] for m in row["context"] if m["role"] == "user")
        family = hashlib.sha256(normalize(first).encode()).hexdigest()
        normalized_context = digest(
            [{"role": m["role"], "content": normalize(m["content"])} for m in row["context"]]
        )
        normalized_contexts.add(normalized_context)
        if any(normalize(m["content"]) in old for m in row["context"] if m["role"] == "user"):
            old_families.add(family)
        record.update(
            {
                "row": row,
                "family_sha256": family,
                "context_sha256": digest(row["context"]),
                "normalized_context_sha256": normalized_context,
                "first_user_sha256": hashlib.sha256(first.encode()).hexdigest(),
                "visible_pair_sha256": digest(visible(row)),
                "duplicate_source_row": row_digest in seen_rows,
            }
        )
        record["pair_rank"] = digest([seed, "pair", record["visible_pair_sha256"]])
        families[family].append(record)
        seen_rows.add(row_digest)

    items: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for family in sorted(families, key=lambda value: digest([seed, "family", value])):
        records = families[family]
        reason = "family_overlaps_previous_study" if family in old_families else None
        pair_votes: dict[str, set[tuple[int, ...]]] = defaultdict(set)
        for record in records:
            pair_votes[record["visible_pair_sha256"]].add(
                tuple(vote["score"] for vote in record["row"]["individual_preference"])
            )
        if reason is None and any(len(scores) != 1 for scores in pair_votes.values()):
            reason = "conflicting_duplicate_pair_references"
        if reason:
            excluded.extend(
                {
                    **{key: record[key] for key in provenance_keys},
                    "family_sha256": family,
                    "reason": reason,
                }
                for record in records
            )
            continue
        chosen = min(records, key=lambda record: (record["pair_rank"], record["source_row_index"]))
        row = chosen["row"]
        item_id = "hs3-" + family
        labels = [
            "A" if vote["score"] < 0 else "B" if vote["score"] > 0 else "tie"
            for vote in row["individual_preference"]
        ]
        counts = Counter(labels)
        winners = [label for label, count in counts.items() if count == max(counts.values())]
        items.append(
            {
                "item_id": item_id,
                "task_id": item_id,
                "family_id": item_id,
                "partition": "holdout",
                "task": {
                    "name": "Public HelpSteer3 response comparison",
                    "instruction": (
                        "Compare the two candidate assistant responses to the final user request "
                        "in the supplied conversation. Use the entire conversation as context."
                    ),
                    "inputs": {"conversation": row["context"]},
                },
                "responses": {"A": row["response1"], "B": row["response2"]},
                "reference": {
                    "source": "human_collected",
                    "label": winners[0] if len(winners) == 1 else None,
                    "reviewer_labels": [
                        {"reviewer_id": f"{item_id}-retained-slot-{index + 1}", "label": label}
                        for index, label in enumerate(labels)
                    ],
                    "provenance": (
                        f"NVIDIA HelpSteer3-Preference validation, revision {REVISION}, {LICENSE}. "
                        "Historical retained human annotations, not new reviewers. Score signs map "
                        "negative to response1/A, positive to response2/B, zero to tie. The release "
                        "filters annotations and keeps up to three that agree most. Slot identifiers "
                        "are local annotation positions, not stable annotator identities. Overall "
                        "rounded preference and human justifications are not used as reference votes."
                    ),
                    "source_digest": source_digest,
                },
            }
        )
        selected.append(
            {
                key: chosen[key]
                for key in (
                    *provenance_keys,
                    "family_sha256",
                    "context_sha256",
                    "normalized_context_sha256",
                    "first_user_sha256",
                    "visible_pair_sha256",
                    "pair_rank",
                )
            }
            | {
                "item_id": item_id,
                "family_rank": digest([seed, "family", family]),
                "domain": row["domain"],
                "language": row["language"],
                "retained_vote_count": len(labels),
                "input_utf8_bytes": len(canonical(visible(row))),
                "context_messages": len(row["context"]),
                "family_source_rows": len(records),
                "source_flags": {
                    "empty_candidate_responses": sum(
                        not row[key].strip() for key in ("response1", "response2")
                    ),
                    "identical_candidate_responses": row["response1"] == row["response2"],
                    "blank_context_messages": sum(
                        not message["content"].strip() for message in row["context"]
                    ),
                },
            }
        )
        for record in records:
            if record is not chosen:
                excluded.append(
                    {
                        **{key: record[key] for key in provenance_keys},
                        "family_sha256": family,
                        "reason": (
                            "duplicate_source_row"
                            if record["duplicate_source_row"]
                            else "same_first_user_family"
                        ),
                    }
                )
    summary = {
        "source_rows": len(rows),
        "eligible_families": len(items),
        "normalized_context_families_before_exclusions": len(normalized_contexts),
        "normalized_first_user_families_before_exclusions": len(families),
        "previous_study_prompt_count": len(old),
        "overlapping_families": len(old_families),
        "excluded_rows": len(excluded),
        "exclusions_by_reason": dict(sorted(Counter(x["reason"] for x in excluded).items())),
        "domains": dict(sorted(Counter(x["domain"] for x in selected).items())),
        "languages": dict(sorted(Counter(x["language"] for x in selected).items())),
        "retained_vote_counts": dict(
            sorted(Counter(x["retained_vote_count"] for x in selected).items())
        ),
        "total_retained_votes": sum(x["retained_vote_count"] for x in selected),
        "source_flag_counts": {
            key: sum(bool(record["source_flags"][key]) for record in selected)
            for key in (
                "empty_candidate_responses",
                "identical_candidate_responses",
                "blank_context_messages",
            )
        },
    }
    if len(items) + len(excluded) != len(rows):
        raise AssertionError("Every source row must be selected or explicitly excluded")
    return {
        "items": items,
        "manifest": {
            "schema_version": 1,
            "purpose": "eligible_pool_not_final_study_freeze",
            "seed": seed,
            "family_rule": "NFKC, casefold and whitespace-normalized first user prompt; across domains.",
            "selection_rule": (
                "One pair per family by SHA-256(seed,pair,visible-pair-hash); families ordered by "
                "SHA-256(seed,family,family-hash). No human score, overall preference or rationale "
                "enters either rank."
            ),
            "vote_mapping": {"negative": "A", "positive": "B", "zero": "tie"},
            "annotation_identity": "Per-item retained slots, not cross-item rater identities.",
            "sample_size_selected": False,
            "summary": summary,
            "selected": selected,
            "exclusions": sorted(excluded, key=lambda record: record["source_row_index"]),
        },
    }


def _fetch(path: Path, url: str, expected: str | None = None) -> bytes:
    if path.exists():
        data = path.read_bytes()
    else:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Everval-confirmatory-study/1"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError("Source exceeds the bounded download size")
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Source hash differs from the pinned release")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o600)
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Cached source hash differs from the pinned release")
    return data


def _write(path: Path, value: Any) -> None:
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text() != data:
            raise ValueError(f"Refusing to replace prepared evidence: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        stream.write(data)
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--previous-protocol", type=Path, required=True)
    parser.add_argument("--seed", default=SEED)
    args = parser.parse_args()
    sources = args.root / "sources"
    raw = _fetch(
        sources / "helpsteer3-validation.jsonl.gz", f"{BASE_URL}/{SOURCE_FILE}", SOURCE_SHA256
    )
    card = _fetch(sources / "helpsteer3-README.md", f"{BASE_URL}/README.md")
    lines = gzip.decompress(raw).splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("Blank JSONL records cannot be silently removed")
    previous_bytes = args.previous_protocol.read_bytes()
    previous = strict_json(previous_bytes)
    excluded_prompts = [item["task"]["instruction"] for item in previous["items"]]
    result = prepare_rows(
        [strict_json(line) for line in lines],
        excluded_prompts=excluded_prompts,
        source_digest=SOURCE_SHA256,
        seed=args.seed,
        line_hashes=[hashlib.sha256(line).hexdigest() for line in lines],
    )
    result["manifest"]["source"] = {
        "dataset": DATASET,
        "revision": REVISION,
        "split": "validation",
        "license": LICENSE,
        "url": f"{BASE_URL}/{SOURCE_FILE}",
        "sha256": SOURCE_SHA256,
        "bytes": len(raw),
        "card_url": f"{BASE_URL}/README.md",
        "card_sha256": hashlib.sha256(card).hexdigest(),
        "paper": "https://arxiv.org/abs/2505.11475v2",
        "previous_protocol_sha256": hashlib.sha256(previous_bytes).hexdigest(),
        "previous_normalized_prompts_sha256": digest(sorted(map(normalize, excluded_prompts))),
    }
    _write(args.root / "eligible-items.json", {"items": result["items"]})
    _write(args.root / "eligible-manifest.json", result["manifest"])
    print(json.dumps(result["manifest"]["summary"], indent=2))


if __name__ == "__main__":
    main()
