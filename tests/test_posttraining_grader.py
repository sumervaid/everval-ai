from copy import deepcopy

import pytest

from cvals.posttraining_grader import compare_graders


def evidence():
    return {
        "old_grader_revision": "rubric-v1",
        "new_grader_revision": "rubric-v2",
        "provenance": "original synthetic grading control",
        "old": [
            {"item_id": "a", "output_sha256": "a" * 64, "passed": True},
            {"item_id": "b", "output_sha256": "b" * 64, "passed": False},
        ],
        "new": [
            {"item_id": "b", "output_sha256": "b" * 64, "passed": True},
            {"item_id": "a", "output_sha256": "a" * 64, "passed": False},
        ],
    }


def test_unchanged_mean_does_not_hide_opposite_grader_flips():
    result = compare_graders(evidence())
    assert result["pass_rate_delta"] == 0
    assert result["pass_to_fail"] == result["fail_to_pass"] == 1
    assert result["agreement"] == 0
    assert [row["item_id"] for row in result["flips"]] == ["a", "b"]


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "output", "revision", "boolean"])
def test_no_confounded_or_incomplete_comparisons(mutation):
    data = deepcopy(evidence())
    if mutation == "duplicate":
        data["old"].append(data["old"][0])
    elif mutation == "missing":
        data["new"].pop()
    elif mutation == "output":
        data["new"][0]["output_sha256"] = "c" * 64
    elif mutation == "revision":
        data["new_grader_revision"] = data["old_grader_revision"]
    else:
        data["new"][0]["passed"] = 1
    with pytest.raises(ValueError):
        compare_graders(data)
