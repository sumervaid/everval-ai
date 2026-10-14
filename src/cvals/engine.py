"""Single-worker orchestration with leases, explicit attempts, and fail-closed budgets."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from cvals.evaluators import EvaluationError, evaluate
from cvals.models import Scenario, System, new_id
from cvals.providers import reserve_cost, submit
from cvals.store import EXPERIMENT_TERMINAL, LeaseLost, Store

if TYPE_CHECKING:
    from cvals.config import Settings


class Engine:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings

    def recover(self) -> int:
        return self.store.recover_expired()

    def run_pending_once(self) -> bool:
        self.recover()
        owner = new_id()
        id = self.store.claim(owner)
        if id is None:
            return False
        self._run(id, owner)
        return True

    def run_experiment(self, id: str) -> dict[str, Any]:
        state = self.store.execution_state(id)
        if state["status"] in EXPERIMENT_TERMINAL:
            return self.store.get_experiment(id)
        self.recover()
        owner = new_id()
        claimed = self.store.claim(owner, id)
        if claimed is None:
            raise ValueError("Another worker holds the workspace lease")
        self._run(id, owner)
        return self.store.get_experiment(id)

    def _run(self, id: str, owner: str) -> None:
        stopping = threading.Event()
        lost = threading.Event()

        def keep_lease() -> None:
            while not stopping.wait(5):
                try:
                    self.store.heartbeat(id, owner)
                except (LeaseLost, OSError):
                    lost.set()
                    return
                except Exception:
                    # Database contention may outlive one tick; abort conservatively.
                    lost.set()
                    return

        heartbeat = threading.Thread(target=keep_lease, daemon=True, name="cvals-lease")
        heartbeat.start()
        try:
            experiment = self.store.get_experiment(id)
            snapshot = experiment["snapshot"]
            scenarios = {
                s["id"]: Scenario.model_validate(s) for s in snapshot["collection"]["scenarios"]
            }
            systems = {s["id"]: System.model_validate(s) for s in snapshot["systems"]}
            for trial in experiment["trials"]:
                if lost.is_set():
                    raise LeaseLost("Worker heartbeat failed; awaiting durable recovery")
                if trial["status"] != "queued":
                    continue
                state = self.store.execution_state(id)
                if state["cancel_requested"]:
                    continue  # cancel_experiment has already finalized queued trials.
                system = systems[trial["system_id"]]
                if state["budget_uncertain"] and system.kind == "http":
                    self.store.skip_trial(
                        id,
                        owner,
                        trial["id"],
                        "Provider billing is uncertain; further HTTP calls are blocked",
                        "budget_exceeded",
                    )
                    continue
                scenario = scenarios[trial["scenario_id"]]
                try:
                    reservation = reserve_cost(scenario, system)
                except ValueError as exc:
                    self.store.skip_trial(id, owner, trial["id"], str(exc))
                    continue
                try:
                    running = self.store.start_trial(id, owner, trial["id"], reservation)
                except ValueError:
                    if self.store.execution_state(id)["cancel_requested"]:
                        continue
                    raise
                if running["status"] != "running":
                    continue
                started = time.monotonic()
                result: dict[str, Any] = {
                    "status": "error",
                    "score": None,
                    "passed": None,
                    "output": None,
                    "checks": [],
                    "cost_usd": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "error": None,
                }
                source_duration_ms = None
                try:
                    submission = submit(scenario, system, self.settings, snapshot["budget_usd"])
                    source_duration_ms = submission.source_duration_ms
                    result.update(
                        output=submission.output,
                        cost_usd=submission.cost_usd,
                        input_tokens=submission.input_tokens,
                        output_tokens=submission.output_tokens,
                        provider_metadata=submission.metadata,
                        error=submission.error,
                    )
                    if self.store.execution_state(id)["cancel_requested"]:
                        result.update(
                            status="cancelled",
                            error="Cancellation requested during the attempt; recorded usage retained",
                        )
                    elif submission.error is None and submission.output is not None:
                        checks = evaluate(scenario, submission.output, self.settings)
                        score = sum(c["score"] * c["weight"] for c in checks) / sum(
                            c["weight"] for c in checks
                        )
                        required = [c for c in checks if c["required"]]
                        passed = (
                            all(c["passed"] for c in required)
                            if required
                            else all(c["passed"] for c in checks)
                        )
                        result.update(status="completed", score=score, passed=passed, checks=checks)
                except EvaluationError as exc:
                    result.update(status="error", error=f"Verifier unavailable: {exc}")
                except ValueError as exc:
                    result.update(status="error", error=str(exc))
                except Exception as exc:
                    # Provider bodies and credential-bearing request objects must not enter logs.
                    result.update(
                        status="error",
                        error=f"Execution failed ({type(exc).__name__}); inspect local configuration",
                    )
                elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                result["evaluation_duration_ms"] = elapsed_ms
                result["duration_ms"] = (
                    source_duration_ms if system.kind == "replay" else elapsed_ms
                )
                self.store.finish_trial(id, owner, trial["id"], result)
            self.store.finish_experiment(id, owner)
        finally:
            stopping.set()
            heartbeat.join(timeout=2)
