import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_study import fresh_identity as identity  # noqa: E402
from scripts.judge_study import fresh_repositories as study  # noqa: E402


def result(statuses, repo="django/django"):
    return {"test_statuses": statuses, "environment": {"repo": repo}}


def test_unique_exact_failed_alias_leaves_the_canonical_identity():
    before = result({"test_multiple (suite.Example)": "FAILED", "test_multiple": "FAILED"})
    after = result({"test_multiple (suite.Example)": "PASSED"})
    assert identity.normalized_names(before) == identity.normalized_names(after)
    assert before["test_statuses"]["test_multiple"] == "FAILED"


@pytest.mark.parametrize(
    "statuses",
    [
        {"test_x": "FAILED", "test_x (suite.Example)": "PASSED"},
        {"test_x": "PASSED", "test_x (suite.Example)": "PASSED"},
        {"test_x": "ERROR", "test_x (suite.Example)": "ERROR"},
        {"test_x": "SKIPPED", "test_x (suite.Example)": "SKIPPED"},
        {"test_x": "FAILED", "test_x (suite.One)": "FAILED", "test_x (suite.Two)": "FAILED"},
        {"test_x": "FAILED", "test_x (suite.One)": "FAILED", "test_x (suite.Two)": "PASSED"},
    ],
)
def test_ambiguous_or_nonfailed_aliases_are_rejected(statuses):
    assert identity.normalized_names(result(statuses)) is None


@pytest.mark.parametrize(
    "statuses",
    [
        {"test_missing": "FAILED"},
        {
            "Some explanatory docstring": "FAILED",
            "Some explanatory docstring (suite.Example)": "FAILED",
        },
        {"test_x": "FAILED", "test_x (UndottedClass)": "FAILED"},
        {"test_x": "FAILED", "test_x_extra (suite.Example)": "FAILED"},
    ],
)
def test_missing_labels_and_docstrings_are_never_manufactured_or_normalized(statuses):
    assert identity.normalized_names(result(statuses)) == frozenset(statuses)


def test_other_repositories_keep_the_frozen_identity_function():
    raw = result({"test_x": "FAILED", "test_x (suite.Example)": "FAILED"}, repo="other/project")
    assert identity.normalized_names(raw) == study.observed_test_names(raw)
    assert len(identity.normalized_names(raw)) == 2


def test_policy_is_explicit_hash_bound_and_restored(monkeypatch, tmp_path):
    original = study.observed_test_names
    plan = {
        "amendment": {
            "identity_policy": {"name": identity.POLICY, "source_sha256": identity.source_sha256()}
        },
        "supporting_source_sha256": {
            "scripts/judge_study/fresh_identity.py": identity.source_sha256()
        },
    }
    monkeypatch.setattr(study, "load", lambda root: plan)
    with pytest.raises(RuntimeError), identity.policy(tmp_path):
        assert study.observed_test_names is identity.normalized_names
        raise RuntimeError("Interrupted research operation")
    assert study.observed_test_names is original
    plan["amendment"]["identity_policy"]["source_sha256"] = "changed"
    with pytest.raises(ValueError, match="changed"), identity.policy(tmp_path):
        pytest.fail("Changed policy accepted")
    assert study.observed_test_names is original


def test_undeclared_policy_keeps_original_semantics(monkeypatch, tmp_path):
    original = study.observed_test_names
    monkeypatch.setattr(study, "load", lambda root: {"amendment": {}})
    with identity.policy(tmp_path):
        assert study.observed_test_names is original


def test_status_cli_prints_summary(monkeypatch, tmp_path, capsys):
    import json

    monkeypatch.setattr(sys, "argv", ["fresh_identity", "status", "--root", str(tmp_path)])
    monkeypatch.setattr(study, "load", lambda root: {"amendment": {}})
    monkeypatch.setattr(study, "summary", lambda root: {"admitted": 0, "screened": 0})
    identity.main()
    assert json.loads(capsys.readouterr().out) == {"admitted": 0, "screened": 0}
