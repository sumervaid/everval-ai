"""Behavioral checks for the prospective task generator, without model calls."""

import importlib.util
from pathlib import Path

import pytest

from cvals.models import Collection

SOURCE = Path(__file__).parents[1] / "scripts" / "posttraining" / "build_dataset.py"
SPEC = importlib.util.spec_from_file_location("study_dataset", SOURCE)
assert SPEC and SPEC.loader
DATASET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DATASET)


def test_deterministic_disjoint_frozen_dataset(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    first = DATASET.build_dataset(left)
    second = DATASET.build_dataset(right)
    assert first == second
    assert len((left / "train.jsonl").read_text().splitlines()) == 512
    families = {}
    for split in ("development", "validation"):
        families[split] = set()
        for domain, entry in first["collections"][split].items():
            collection = Collection.model_validate_json((left / entry["path"]).read_text())
            assert len(collection.scenarios) == 64
            groups = {}
            for scenario in collection.scenarios:
                family = scenario.provenance["task_family"]
                groups.setdefault(family, []).append(scenario)
                assert scenario.reference_output["answer"] == scenario.checks[0].expected
                assert scenario.provenance["eval_id"].startswith(domain + ".p")
            assert len(groups) == 32 and all(len(items) == 2 for items in groups.values())
            assert all(
                items[0].reference_output == items[1].reference_output for items in groups.values()
            )
            families[split].update(groups)
    assert not families["development"] & families["validation"]
    with pytest.raises(ValueError, match="new or empty"):
        DATASET.build_dataset(left)


def test_oracles_and_output_contract():
    missing = 0
    for split in ("train", "valid", "development", "validation"):
        for domain in DATASET.DOMAINS:
            for index in range(40):
                case = DATASET.make_case(split, domain, index)
                facts = case["facts"]
                if domain == "extraction":
                    assert case["answer"] == facts["fields"].get(facts["target"], "UNKNOWN")
                    missing += case["answer"] == "UNKNOWN"
                elif domain == "arithmetic":
                    assert (
                        int(case["answer"])
                        == facts["quantity"] * facts["unit_price"] - facts["discount"]
                    )
                elif domain == "injection":
                    assert case["answer"] == facts["trusted_code"]
                    assert case["answer"] != "OVERRIDE"
                else:
                    assert facts["request"] in DATASET.SIGNALS[case["answer"]]
                for paraphrase in (0, 1):
                    prompt = DATASET.render(case, paraphrase)
                    assert DATASET.OUTPUT_CONTRACT in prompt
                    assert facts.get("record", facts.get("ticket", facts.get("order"))) in prompt
    assert missing > 0


def test_declared_grader_accepts_oracle_and_rejects_format_and_answer_mutations(tmp_path):
    from cvals.config import Settings
    from cvals.evaluators import evaluate

    settings = Settings(tmp_path)
    for domain in DATASET.DOMAINS:
        for index in range(32):
            case = DATASET.make_case("validation", domain, index)
            item = DATASET.scenario(case, "validation", 0)
            assert all(
                result["passed"] for result in evaluate(item, {"answer": case["answer"]}, settings)
            )
            for bad in (
                {},
                {"answer": "WRONG"},
                {"answer": 1},
                {"answer": case["answer"], "extra": True},
            ):
                assert not all(result["passed"] for result in evaluate(item, bad, settings))
