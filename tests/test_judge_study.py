"""Research transport acceptance: frozen inputs, spending and interrupted calls."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
driver = importlib.import_module("scripts.judge_study.driver")


def study(tmp_path: Path) -> Path:
    rubric = {
        "name": "Test rubric",
        "version": "1",
        "instructions": "Compare correctness.",
        "dimensions": [{"id": "correct", "name": "Correctness", "guidance": "Satisfy the task."}],
    }
    judges = [
        {"id": str(i) + "-" + arm, "model": model, "revision": model, "prompt_variant": arm}
        for i, model in enumerate(driver.PROFILES)
        for arm in ["standard", "reference_context"]
    ]
    for cohort in ["human", "execution"]:
        items = [
            {
                "item_id": cohort + "-" + p,
                "task_id": cohort + "-" + p,
                "family_id": cohort + "-" + p,
                "partition": p,
                "task": {"instruction": "Return two for " + cohort + "-" + p, "inputs": {}},
                "responses": {"A": "2", "B": "3"},
                "reference_context": {"criteria": "Arithmetic correctness."},
                "reference": {
                    "source": "scripted",
                    "label": "A",
                    "provenance": "Explicit installation control.",
                },
            }
            for p in ["dev", "holdout"]
        ]
        value = {
            "name": cohort,
            "rubric": rubric,
            "judges": judges,
            "items": items,
            "repetitions": 1,
            "planned_comparisons": 6,
        }
        (tmp_path / (cohort + "-protocol.json")).write_text(json.dumps(value))
    driver.freeze(tmp_path)
    return tmp_path


def mock_client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        driver.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_completed_smoke_is_not_reissued(tmp_path, monkeypatch):
    root = study(tmp_path)
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "service_tier": "default",
                "status": "completed",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "preference": "A",
                                        "rationale": "The response satisfies the task.",
                                    }
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    mock_client(monkeypatch, handle)
    driver.run(root, partition="dev", smoke=True)
    driver.run(root, partition="dev", smoke=True)
    assert len(calls) == 6
    status = driver.snapshot(root, driver.load_plan(root))
    assert status["requests"] == 6 and status["unknown_requests"] == 0
    assert float(status["conservative_committed_usd"]) < 0.01
    assert all(x["store"] is False and x["service_tier"] == "default" for x in calls)


def test_unknown_usage_stops_new_requests_and_restart(tmp_path, monkeypatch):
    root = study(tmp_path)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(502, json={"error": {"message": "No usage available"}})

    mock_client(monkeypatch, handle)
    with pytest.raises(RuntimeError, match="Study stopped"):
        driver.run(root, partition="dev", smoke=True)
    before = len(calls)
    assert 1 <= before <= 3
    with pytest.raises(driver.BudgetError, match="Unsettled"):
        driver.run(root, partition="dev", smoke=True)
    assert len(calls) == before


def test_frozen_plan_tampering_is_rejected(tmp_path):
    root = study(tmp_path)
    plan = driver.read(root / "plan.json")
    plan["jobs"][0]["body"]["store"] = True
    (root / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="plan changed"):
        driver.load_plan(root)


def test_holdout_cannot_run_before_release(tmp_path, monkeypatch):
    root = study(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(ValueError, match="Holdout selection"):
        driver.run(root, partition="holdout")


def test_budget_caps_cannot_increase(tmp_path):
    root = study(tmp_path)
    plan = driver.load_plan(root)
    assert sum(float(p["cap"]) for p in plan["profiles"].values()) == 30
    plan["profiles"]["gpt-6-luna"]["cap"] = "3"
    with pytest.raises(driver.BudgetError, match="Frozen pricing"):
        driver.configuration(plan, "gpt-6-luna")


def test_atomic_concurrent_admission_never_exceeds_cap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal

    from scripts.judge_study.concurrent_budget import ConcurrentLedger

    config = driver.BudgetConfig(
        campaign_id="concurrent-test",
        campaign_config_hash="a" * 64,
        pricing_version="test",
        user_ceiling_usd=Decimal(".031"),
        admission_ceiling_usd=Decimal(".031"),
        attempt_cap_usd=Decimal(".031"),
        input_rate_per_million=Decimal("100"),
        output_rate_per_million=Decimal("100"),
    )
    ledger = ConcurrentLedger(tmp_path / "budget.sqlite3", config)

    def admit(index):
        identifier = "job-" + str(index)
        ledger.register_attempt(identifier, driver.digest(index))
        try:
            return ledger.reserve(identifier, driver.digest(index), 100, 1)
        except driver.BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        admitted = [x for x in pool.map(admit, range(20)) if x]
    assert len(admitted) == 3
    assert Decimal(ledger.status()["committed_spend_usd"]) == Decimal(".0303")
    restarted = ConcurrentLedger(tmp_path / "budget.sqlite3", config, create=False)
    restarted.register_attempt("restart", driver.digest("restart"))
    with pytest.raises(driver.BudgetError, match="Unowned pending"):
        restarted.reserve("restart", driver.digest("restart"), 1, 1)
    ledger.uncertain(admitted[0], "missing_usage")
    ledger.register_attempt("unknown", driver.digest("unknown"))
    with pytest.raises(driver.BudgetError, match="Uncertain"):
        ledger.reserve("unknown", driver.digest("unknown"), 1, 1)


def test_parallel_runner_limits_inflight_and_resumes_without_reissue(tmp_path, monkeypatch):
    import threading
    import time

    from scripts.judge_study import parallel

    root = study(tmp_path)
    (root / "requests").mkdir()
    parallel.amendment(root, create=True)
    counts = {model: 0 for model in driver.PROFILES}
    peak = {model: 0 for model in driver.PROFILES}
    lock = threading.Lock()
    calls = []

    def handle(request):
        body = json.loads(request.content)
        model = body["model"]
        with lock:
            calls.append(body)
            counts[model] += 1
            peak[model] = max(peak[model], counts[model])
        time.sleep(0.03)
        with lock:
            counts[model] -= 1
        return httpx.Response(
            200,
            json={
                "model": model,
                "status": "completed",
                "service_tier": "default",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"preference":"A","rationale":"Correct."}',
                            }
                        ],
                    }
                ],
            },
        )

    mock_client(monkeypatch, handle)
    parallel.run(root, "dev")
    parallel.run(root, "dev")
    assert len(calls) == 24
    assert all(1 < count <= 4 for count in peak.values())
    assert driver.snapshot(root, driver.load_plan(root))["unknown_requests"] == 0


def test_parallel_unknown_usage_stops_dispatch(tmp_path, monkeypatch):
    from scripts.judge_study import parallel

    root = study(tmp_path)
    (root / "requests").mkdir()
    parallel.amendment(root, create=True)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(502, json={"error": {"message": "usage unavailable"}})

    mock_client(monkeypatch, handle)
    with pytest.raises(RuntimeError, match="Study stopped"):
        parallel.run(root, "dev")
    assert 1 <= len(calls) <= 12
    before = len(calls)
    with pytest.raises(driver.BudgetError, match="Unsettled"):
        parallel.run(root, "dev")
    assert len(calls) == before


def test_public_study_export_removes_reviewer_and_item_records():
    from scripts.judge_study.analyze import public_report

    original = {
        "analysis": {
            "judges": [
                {
                    "judge_id": "judge",
                    "item_scores": [{"item_id": "private-case"}],
                    "primary": {"estimate": 0.5},
                }
            ],
            "human_reliability": {
                "reviewer_pairs": [{"left_reviewer": "private-reviewer"}],
                "alpha": {"alpha": 0.4},
            },
        }
    }
    exported = public_report(original)
    assert "private-case" not in json.dumps(exported)
    assert "private-reviewer" not in json.dumps(exported)
    assert exported["analysis"]["human_reliability"]["reviewer_pair_count"] == 1
    assert original["analysis"]["judges"][0]["item_scores"]


def test_observed_panel_bound_preserves_disagreement():
    from scripts.judge_study.analyze import panel_agreement_bound

    items = [
        {"reference": {"reviewer_labels": [{"label": x} for x in votes]}}
        for votes in [("A", "A"), ("A", "B", None)]
    ]
    value = panel_agreement_bound(items)
    assert value["observed_panel_vote_agreement_upper_bound"] == 0.75
    assert value["observed_votes"] == 4
    assert value["divided_panels"] == 1


def test_delivered_agreement_distinguishes_observed_failure_from_missing():
    from scripts.judge_study.analyze import delivered_agreement

    protocol = {
        "primary_endpoint": "human_votes",
        "repetitions": 1,
        "alpha": 0.05,
        "resamples": 1000,
        "seed": 17,
        "min_families": 2,
        "planned_comparisons": 1,
        "primary_contrasts": [{"left": "base", "right": "guided"}],
        "judges": [{"id": name, "model": name} for name in ("base", "guided")],
        "items": [
            {
                "item_id": "one",
                "task_id": "one",
                "family_id": "one",
                "partition": "holdout",
                "reference": {
                    "source": "human_collected",
                    "label": None,
                    "reviewer_labels": [{"label": "A"}, {"label": "B"}],
                },
            }
        ],
    }
    jobs = [
        {
            "item_id": "one",
            "judge_id": judge,
            "status": status,
            "result": {"preference": "A"} if status == "completed" else None,
        }
        for judge, status in [
            ("base", "completed"),
            ("base", "invalid"),
            ("guided", "completed"),
            ("guided", "completed"),
        ]
    ]
    result = delivered_agreement(protocol, jobs, "holdout")
    base = result["judges"][0]
    assert base["primary"]["estimate"] == 0.25
    assert base["primary"]["complete_cohort"] is True
    assert base["coverage"]["attempted"] == 2
    assert base["coverage"]["valid_delivery_rate"] == 0.5
    assert result["paired_contrasts"][0]["difference"]["estimate"] == 0.25
    jobs[1]["status"] = "queued"
    missing = delivered_agreement(protocol, jobs, "holdout")["judges"][0]
    assert missing["primary"]["complete_cohort"] is False
    assert missing["primary"]["interval"] is None
    assert missing["coverage"]["attempted"] == 1
    assert missing["coverage"]["statuses"]["missing"] == 1


def test_secondary_endpoint_cannot_be_added_after_holdout_dispatch(tmp_path):
    from scripts.judge_study import parallel

    root = study(tmp_path)
    request_dir = root / "requests"
    request_dir.mkdir()
    job = next(j for j in driver.load_plan(root)["jobs"] if j["partition"] == "holdout")
    (request_dir / (job["job_id"] + ".request.json")).write_text("{}")
    with pytest.raises(ValueError, match="before any holdout"):
        parallel.amendment(root, create=True)
