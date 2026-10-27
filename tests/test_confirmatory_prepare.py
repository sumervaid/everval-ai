"""Selection controls for fresh human-reference confirmation."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

from cvals.judge_calibration_models import CalibrationItem

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
preparation = importlib.import_module("scripts.judge_study.confirmatory_prepare")
_fetch = preparation._fetch
_write = preparation._write
prepare_rows = preparation.prepare_rows
strict_json = preparation.strict_json


def example(prompt: str = "Explain this.", suffix: str = "") -> dict:
    return {
        "domain": "general",
        "language": "english",
        "context": [{"role": "user", "content": prompt}],
        "response1": "First candidate" + suffix,
        "response2": "Second candidate" + suffix,
        "overall_preference": 0,
        "individual_preference": [
            {"score": -1, "reasoning": "PRIVATE-HUMAN-REASON"},
            {"score": 1, "reasoning": "PRIVATE-HUMAN-REASON"},
            {"score": 1, "reasoning": "PRIVATE-HUMAN-REASON"},
        ],
    }


def prepare(rows: list, excluded: list[str] | None = None) -> dict:
    return prepare_rows(
        rows, excluded_prompts=excluded or [], source_digest="a" * 64, seed="fixed-test-seed"
    )


def test_first_user_normalization_groups_domains_turns_and_exact_duplicates() -> None:
    first = example("Ａccount   Balance?")
    sibling = example("account balance?", "-variant")
    sibling["domain"] = "code"
    sibling["context"] += [
        {"role": "assistant", "content": "Earlier response."},
        {"role": "user", "content": "Now explain the edge case."},
    ]
    result = prepare([first, copy.deepcopy(first), sibling, example("Another question")])
    assert len(result["items"]) == 2
    assert result["manifest"]["summary"]["excluded_rows"] == 2
    assert result["manifest"]["summary"]["normalized_first_user_families_before_exclusions"] == 2
    assert any(x["reason"] == "duplicate_source_row" for x in result["manifest"]["exclusions"])


def test_pair_and_family_order_do_not_depend_on_human_labels_or_input_order() -> None:
    rows = [
        example("Repeated question", "-one"),
        example("Repeated question", "-two"),
        example("Different question", "-three"),
    ]
    result = prepare(rows)
    changed = copy.deepcopy(rows)
    for row in changed:
        row["overall_preference"] = -3
        for vote in row["individual_preference"]:
            vote["score"] *= -1
            vote["reasoning"] = "Entirely changed human explanation"
    reversed_result = prepare(list(reversed(changed)))
    assert [item["family_id"] for item in result["items"]] == [
        item["family_id"] for item in reversed_result["items"]
    ]
    assert [item["responses"] for item in result["items"]] == [
        item["responses"] for item in reversed_result["items"]
    ]
    assert [row["pair_rank"] for row in result["manifest"]["selected"]] == [
        row["pair_rank"] for row in reversed_result["manifest"]["selected"]
    ]


def test_overlap_in_any_user_turn_excludes_entire_first_user_family() -> None:
    branch = example("Fresh opening", "-branch")
    branch["context"] += [
        {"role": "assistant", "content": "History"},
        {"role": "user", "content": "  Old    Question? "},
    ]
    result = prepare(
        [example("Fresh opening"), branch, example("Independent")], excluded=["OLD QUESTION?"]
    )
    assert len(result["items"]) == 1
    assert result["manifest"]["summary"]["overlapping_families"] == 1
    assert result["manifest"]["summary"]["exclusions_by_reason"] == {
        "family_overlaps_previous_study": 2
    }


def test_full_context_preserved_and_human_reference_never_enters_public_material() -> None:
    row = example("A long question " * 10000)
    result = prepare([row])
    item = result["items"][0]
    CalibrationItem.model_validate(item)
    assert item["task"]["inputs"]["conversation"] == row["context"]
    public = json.dumps({"task": item["task"], "responses": item["responses"]})
    assert "PRIVATE-HUMAN-REASON" not in public
    assert "individual_preference" not in public
    assert "overall_preference" not in public
    assert "source_row_index" not in public
    assert item["reference"]["label"] == "B"
    assert [vote["label"] for vote in item["reference"]["reviewer_labels"]] == ["A", "B", "B"]
    assert len({vote["reviewer_id"] for vote in item["reference"]["reviewer_labels"]}) == 3
    assert result["manifest"]["sample_size_selected"] is False


def test_vote_sign_mapping_retains_real_tie_and_does_not_use_rounded_overall() -> None:
    row = example()
    row["overall_preference"] = 3
    row["individual_preference"] = [{"score": -3}, {"score": 0}, {"score": 2}]
    item = prepare([row])["items"][0]
    assert item["reference"]["label"] is None
    assert [vote["label"] for vote in item["reference"]["reviewer_labels"]] == ["A", "tie", "B"]


def test_conflicting_duplicate_references_are_not_arbitrarily_selected() -> None:
    row = example()
    changed = copy.deepcopy(row)
    changed["individual_preference"][0]["score"] = 3
    result = prepare([row, changed])
    assert result["items"] == []
    assert result["manifest"]["summary"]["exclusions_by_reason"] == {
        "conflicting_duplicate_pair_references": 2
    }


@pytest.mark.parametrize("score", [True, 1.0, 4, -4, "1"])
def test_invalid_vote_records_are_explicit_exclusions(score: object) -> None:
    row = example()
    row["individual_preference"][0]["score"] = score
    result = prepare([row])
    assert result["items"] == []
    assert result["manifest"]["exclusions"][0]["reason"] == "invalid_retained_vote_score"


def test_original_line_hash_retained_and_shape_mismatch_rejected() -> None:
    row = example()
    original = json.dumps(row).encode()
    line_hash = hashlib.sha256(original).hexdigest()
    result = prepare_rows(
        [row], excluded_prompts=[], source_digest="a" * 64, line_hashes=[line_hash]
    )
    assert result["manifest"]["selected"][0]["original_line_sha256"] == line_hash
    with pytest.raises(ValueError, match="original line hash"):
        prepare_rows([row], excluded_prompts=[], source_digest="a" * 64, line_hashes=[])


def test_source_parsing_and_cache_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        strict_json('{"individual_preference": [], "individual_preference": [1]}')
    with pytest.raises(ValueError, match="Non-finite"):
        strict_json('{"value": NaN}')
    cache = tmp_path / "source.gz"
    cache.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="Cached source hash"):
        _fetch(cache, "https://example.invalid/no-network", expected="0" * 64)


def test_prepared_evidence_cannot_be_silently_replaced(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write(path, {"seed": "first"})
    _write(path, {"seed": "first"})
    with pytest.raises(ValueError, match="Refusing to replace"):
        _write(path, {"seed": "second"})
    assert strict_json(path.read_bytes()) == {"seed": "first"}


def test_empty_candidates_and_blank_historical_turns_remain_observed_data() -> None:
    row = example("An initial request")
    row["response1"] = ""
    row["context"] += [
        {"role": "assistant", "content": ""},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "Continuation"},
        {"role": "user", "content": "Now compare"},
    ]
    identical = example("Another independent request")
    identical["response2"] = identical["response1"]
    result = prepare([row, identical])
    assert len(result["items"]) == 2
    retained = next(item for item in result["items"] if item["responses"]["A"] == "")
    assert retained["task"]["inputs"]["conversation"] == row["context"]
    assert result["manifest"]["summary"]["source_flag_counts"] == {
        "empty_candidate_responses": 1,
        "identical_candidate_responses": 1,
        "blank_context_messages": 1,
    }


def test_empty_initial_request_cannot_become_a_family_key() -> None:
    result = prepare([example("  \n\t")])
    assert result["items"] == []
    assert result["manifest"]["exclusions"][0]["reason"] == "empty_first_user_family_key"
