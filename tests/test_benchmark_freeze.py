"""Free campaign preparation binds every selected task and never resets accounting."""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
freezer = importlib.import_module("scripts.benchmarks.freeze")
campaign = importlib.import_module("scripts.benchmarks.campaign")
IMAGE = "sha256:" + "a" * 64


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def controls(task):
    identity = task["instance_id"]
    common = {
        "instance_id": identity,
        "image_id": IMAGE,
        "status": "graded",
        "all_required_tests_observed": True,
        "fail_to_pass_count": 1,
        "pass_to_pass_count": 1,
        "missing_required_tests": [],
    }
    return {
        **common,
        "valid": True,
        "base": {
            **common,
            "resolved": False,
            "patch_sha256": digest(""),
            "test_statuses": {"quantity": "FAILED", "empty": "PASSED"},
            "container_script_returncode": 1,
        },
        "gold": {
            **common,
            "resolved": True,
            "patch_sha256": digest(task["patch"]),
            "test_statuses": {"quantity": "PASSED", "empty": "PASSED"},
            "container_script_returncode": 0,
        },
    }


@pytest.fixture(autouse=True)
def no_provider_access(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Free campaign freezing reached a provider or executor")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(campaign, "MeteredOpenAI", forbidden)


@pytest.fixture
def workspace(tmp_path):
    selection = {"campaign": "original-freeze-fixture", "tasks": []}
    index = {"tasks": []}
    images = {}
    for number, split in enumerate(("development", "test", "test"), start=1):
        identity = f"fixture__cart-{number}"
        task = {
            "instance_id": identity,
            "repo": "original/fixture",
            "base_commit": "b" * 40,
            "problem_statement": "Count original fixture items by quantity.",
            "patch": "original repair",
            "test_patch": "original private check",
            "FAIL_TO_PASS": ["quantity"],
            "PASS_TO_PASS": ["empty"],
        }
        task_path = tmp_path / "tasks" / f"{identity}.json"
        write_json(task_path, task)
        task_hash = campaign.sha256(task_path)
        selection["tasks"].append(
            {"instance_id": identity, "split": split, "task_sha256": task_hash}
        )
        index["tasks"].append(
            {
                "instance_id": identity,
                "split": split,
                "task_path": str(task_path),
                "task_sha256": task_hash,
            }
        )
        images[identity] = {
            "preparation_status": "failed" if number == 2 else "ready",
            "image_id": None if number == 2 else IMAGE,
        }
        control = controls(task) if number == 1 else {"instance_id": identity, "valid": False}
        write_json(tmp_path / "controls" / identity / "controls.json", control)
    selection_path = tmp_path / "selection" / "selection-public.json"
    write_json(selection_path, selection)
    index["selection_sha256"] = campaign.sha256(selection_path)
    write_json(tmp_path / "selection" / "selection-private-index.json", index)
    write_json(tmp_path / "runtime-images.json", images)
    return tmp_path


def freeze(workspace, *, opening=Decimal("0"), prior=None):
    return freezer.freeze(workspace, opening_spend_usd=opening, prior_accounting=prior)


def read(path):
    return json.loads(path.read_text())


def test_every_selected_task_is_retained_with_an_explicit_admission_or_exclusion(workspace):
    plan_path = freeze(workspace)
    plan, plan_hash = campaign.verify_plan(plan_path)
    assert [task["instance_id"] for task in plan["tasks"]] == [
        "fixture__cart-1",
        "fixture__cart-2",
        "fixture__cart-3",
    ]
    assert [task["admitted"] for task in plan["tasks"]] == [True, False, False]
    assert [task["exclusion_reason"] for task in plan["tasks"]] == [
        None,
        "preparation_failed",
        "control_contract_failed",
    ]
    assert plan["tasks"][1]["image_id"] is None
    assert plan["tasks"][1]["split"] == "test"
    assert plan["budget"]["opening_spend_usd"] == "0"
    assert plan["source_sha256"].keys() >= campaign.REQUIRED_SOURCES
    assert plan_hash == hashlib.sha256(campaign.canonical(plan)).hexdigest()
    assert plan_path.stat().st_mode & 0o777 == 0o600
    assert not (workspace / "budget.sqlite3").exists()
    assert not (workspace / "attempts").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "index_selection_hash",
        "changed_task_bytes",
        "private_task_hash",
        "public_task_hash",
        "partition",
        "missing_private_task",
        "duplicate_private_task",
    ],
)
def test_selection_and_private_task_bindings_fail_before_a_plan_is_written(workspace, mutation):
    selection_path = workspace / "selection" / "selection-public.json"
    index_path = workspace / "selection" / "selection-private-index.json"
    selection = read(selection_path)
    index = read(index_path)
    if mutation == "index_selection_hash":
        index["selection_sha256"] = "0" * 64
    elif mutation == "changed_task_bytes":
        with Path(index["tasks"][0]["task_path"]).open("a") as stream:
            stream.write(" ")
    elif mutation == "private_task_hash":
        index["tasks"][0]["task_sha256"] = "0" * 64
    elif mutation == "public_task_hash":
        selection["tasks"][0]["task_sha256"] = "0" * 64
        write_json(selection_path, selection)
        index["selection_sha256"] = campaign.sha256(selection_path)
    elif mutation == "partition":
        index["tasks"][0]["split"] = "test"
    elif mutation == "missing_private_task":
        index["tasks"].pop()
    elif mutation == "duplicate_private_task":
        index["tasks"].append(index["tasks"][0])
    write_json(index_path, index)
    with pytest.raises(ValueError):
        freeze(workspace)
    assert not (workspace / "campaign.json").exists()
    assert not (workspace / "budget.sqlite3").exists()


@pytest.mark.parametrize("status", ["pending", "running", None])
def test_nonterminal_preparation_cannot_be_silently_excluded(workspace, status):
    path = workspace / "runtime-images.json"
    images = read(path)
    images["fixture__cart-2"]["preparation_status"] = status
    write_json(path, images)
    with pytest.raises(ValueError, match="terminal preparation"):
        freeze(workspace)
    assert not (workspace / "campaign.json").exists()


def test_passing_controls_cannot_overrule_failed_preparation(workspace):
    path = workspace / "controls" / "fixture__cart-2" / "controls.json"
    control = read(path)
    control["valid"] = True
    write_json(path, control)
    with pytest.raises(ValueError, match="failed preparation"):
        freeze(workspace)
    assert not (workspace / "campaign.json").exists()


def test_full_control_validation_happens_before_exclusive_plan_write(workspace):
    path = workspace / "controls" / "fixture__cart-1" / "controls.json"
    control = read(path)
    control["base"]["test_statuses"]["quantity"] = "PASSED"
    write_json(path, control)
    with pytest.raises(ValueError, match="FAIL_TO_PASS"):
        freeze(workspace)
    assert not (workspace / "campaign.json").exists()


def test_nonzero_opening_spend_requires_retained_prior_evidence(workspace):
    with pytest.raises(ValueError, match="accounting evidence"):
        freeze(workspace, opening=Decimal("0.482999"))
    assert not (workspace / "campaign.json").exists()


@pytest.mark.parametrize("amount", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_opening_spend_never_writes_a_plan(workspace, amount):
    with pytest.raises(ValueError):
        freeze(workspace, opening=amount)
    assert not (workspace / "campaign.json").exists()


@pytest.mark.parametrize("existing", ["campaign.json", "budget.sqlite3"])
def test_existing_plan_or_ledger_is_never_reset(workspace, existing):
    path = workspace / existing
    original = b"retained original accounting evidence\n"
    path.write_bytes(original)
    with pytest.raises(ValueError, match="already frozen"):
        freeze(workspace)
    assert path.read_bytes() == original
    other = workspace / ("budget.sqlite3" if existing == "campaign.json" else "campaign.json")
    assert not other.exists()


def test_prepared_tasks_cannot_be_read_from_outside_the_campaign_directory(workspace, tmp_path):
    index_path = workspace / "selection" / "selection-private-index.json"
    index = read(index_path)
    index["tasks"][0]["task_path"] = str(tmp_path.parent / "unowned-private-task.json")
    write_json(index_path, index)
    with pytest.raises(ValueError, match="campaign directory"):
        freeze(workspace)
    assert not (workspace / "campaign.json").exists()


def reconciliation():
    return {
        "status": "passed",
        "all_usage_known": True,
        "provider_admissions": 1,
        "known_provider_cost_usd": 0.00044999999999996,
        "recomputed_cost_usd": 0.00045,
        "absolute_difference_usd": 4e-17,
        "tolerance_usd": 1e-9,
        "models": [
            {
                "model": "original-accounting-fixture",
                "calls": 1,
                "input_tokens": 100,
                "output_tokens": 20,
                "input_cost_per_million": 2.5,
                "output_cost_per_million": 10,
                "pricing_version": "original-conservative-fixture",
                "recomputed_cost_usd": 0.00045,
            }
        ],
    }


def test_prior_tokens_are_recomputed_exactly_and_the_same_evidence_bytes_are_hashed(workspace):
    path = workspace / "prior-accounting.json"
    write_json(path, reconciliation())
    plan_path = freeze(workspace, opening=Decimal("0.00045"), prior=path)
    plan = read(plan_path)
    assert plan["budget"]["opening_spend_usd"] == "0.00045"
    assert plan["prior_accounting_sha256"] == campaign.sha256(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "failed",
        "unknown",
        "negative_tokens",
        "boolean_tokens",
        "fractional_tokens",
        "missing_tokens",
        "zero_price",
        "nonfinite_price",
        "model_cost",
        "total_cost",
        "opening_cost",
        "known_cost_above_opening",
        "model_calls",
        "provider_calls",
        "large_tolerance",
        "incorrect_difference",
        "missing_models",
        "duplicate_models",
    ],
)
def test_incomplete_or_unreconciled_prior_spend_never_admits_a_new_campaign(workspace, mutation):
    evidence = reconciliation()
    model = evidence["models"][0]
    opening = Decimal("0.00045")
    if mutation == "failed":
        evidence["status"] = "failed"
    elif mutation == "unknown":
        evidence["all_usage_known"] = False
    elif mutation == "negative_tokens":
        model["input_tokens"] = -1
    elif mutation == "boolean_tokens":
        model["input_tokens"] = True
    elif mutation == "fractional_tokens":
        model["input_tokens"] = 100.5
    elif mutation == "missing_tokens":
        model.pop("output_tokens")
    elif mutation == "zero_price":
        model["input_cost_per_million"] = 0
    elif mutation == "nonfinite_price":
        model["output_cost_per_million"] = float("nan")
    elif mutation == "model_cost":
        model["recomputed_cost_usd"] = 0
    elif mutation == "total_cost":
        evidence["recomputed_cost_usd"] = 0
    elif mutation == "opening_cost":
        opening = Decimal("0.0004")
    elif mutation == "known_cost_above_opening":
        evidence["known_provider_cost_usd"] = 0.00045000000000004
    elif mutation == "model_calls":
        model["calls"] = 2
    elif mutation == "provider_calls":
        evidence["provider_admissions"] = 2
    elif mutation == "large_tolerance":
        evidence["tolerance_usd"] = 1
    elif mutation == "incorrect_difference":
        evidence["absolute_difference_usd"] = 0
    elif mutation == "missing_models":
        evidence.pop("models")
    elif mutation == "duplicate_models":
        evidence["models"].append(model)
    path = workspace / "prior-accounting.json"
    write_json(path, evidence)
    with pytest.raises(ValueError):
        freeze(workspace, opening=opening, prior=path)
    assert not (workspace / "campaign.json").exists()
    assert not (workspace / "budget.sqlite3").exists()


@pytest.mark.parametrize("independent_marker", [False, True])
def test_unconfirmed_preparation_cleanup_cannot_be_bypassed_by_ready_status(
    workspace, independent_marker
):
    images = read(workspace / "runtime-images.json")
    images["fixture__cart-1"]["preparation_status"] = "ready"
    if independent_marker:
        write_json(workspace / "preparation-blocked.json", {"reason": "cleanup_unknown"})
    else:
        images["fixture__cart-1"]["cleanup_complete"] = False
    write_json(workspace / "runtime-images.json", images)
    with pytest.raises(ValueError, match="cleanup"):
        freeze(workspace)
    assert not (workspace / "campaign.json").exists()
    assert not (workspace / "budget.sqlite3").exists()


def probe_preparation_lock(workspace):
    """Use an independent process to observe the real operating-system lock."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, os, sys\n"
                "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)\n"
                "try:\n"
                "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "except BlockingIOError:\n"
                "    sys.exit(73)\n"
                "finally:\n"
                "    os.close(fd)\n"
            ),
            str(workspace / ".preparation.lock"),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode in {0, 73}, result.stderr
    return result.returncode


def test_preparation_lock_blocks_freeze_before_any_evidence_read(workspace, monkeypatch):
    lock = workspace / ".preparation.lock"
    original = b"Existing preparation lock identity\n"
    lock.write_bytes(original)
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def forbidden(*args, **kwargs):
            pytest.fail("Freezing read evidence while preparation owned the lock")

        monkeypatch.setattr(freezer, "read_json", forbidden)
        monkeypatch.setattr(freezer, "validate_prior_accounting", forbidden)
        with pytest.raises(ValueError, match="owns this directory"):
            freeze(workspace, opening=Decimal("1"), prior=workspace / "unread-accounting.json")
    finally:
        os.close(descriptor)
    assert lock.read_bytes() == original
    assert not (workspace / "campaign.json").exists()
    assert not (workspace / "budget.sqlite3").exists()
    assert probe_preparation_lock(workspace) == 0


def test_preparation_lock_spans_accounting_artifacts_validation_and_exclusive_write(
    workspace, monkeypatch
):
    prior = workspace / "prior-accounting.json"
    write_json(prior, reconciliation())
    observed = set()

    def guarded(name, function):
        def wrapped(*args, **kwargs):
            if name not in observed:
                assert probe_preparation_lock(workspace) == 73
                observed.add(name)
            result = function(*args, **kwargs)
            if name == "private_json":
                assert (workspace / "campaign.json").exists()
                assert probe_preparation_lock(workspace) == 73
            return result

        return wrapped

    for name in (
        "validate_prior_accounting",
        "read_json",
        "sha256",
        "verify_plan_data",
        "private_json",
    ):
        monkeypatch.setattr(freezer, name, guarded(name, getattr(freezer, name)))
    path = freeze(workspace, opening=Decimal("0.00045"), prior=prior)
    assert path == workspace / "campaign.json"
    assert observed == {
        "validate_prior_accounting",
        "read_json",
        "sha256",
        "verify_plan_data",
        "private_json",
    }
    assert (workspace / ".preparation.lock").stat().st_mode & 0o777 == 0o600
    assert probe_preparation_lock(workspace) == 0


def test_failed_freeze_releases_preparation_lock_and_preserves_cleanup_marker(workspace):
    marker = workspace / "preparation-blocked.json"
    original = b'{"state":"cleanup_unresolved","ownership_token":"original"}\n'
    marker.write_bytes(original)
    with pytest.raises(ValueError, match="cleanup"):
        freeze(workspace)
    assert marker.read_bytes() == original
    assert probe_preparation_lock(workspace) == 0
    assert not (workspace / "campaign.json").exists()
    assert not (workspace / "budget.sqlite3").exists()


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_preparation_lock_rejects_symlinks_and_nonregular_files(workspace, kind):
    path = workspace / ".preparation.lock"
    target = workspace / "untouched-lock-target"
    target.write_bytes(b"Original target")
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path, 0o600)
    with pytest.raises(ValueError, match="regular file"):
        freeze(workspace)
    assert target.read_bytes() == b"Original target"
    assert not (workspace / "campaign.json").exists()
    assert not (workspace / "budget.sqlite3").exists()
