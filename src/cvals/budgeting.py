"""Evidence-based budget forecasts. Reservations and estimated bills remain distinct."""

from __future__ import annotations

import json
import math
from typing import Any

from cvals.config import Settings
from cvals.models import Scenario, System, utc_now
from cvals.providers import reserve_cost
from cvals.spend_limits import SpendLimits
from cvals.store import Store


class BudgetPlanner:
    def __init__(self, settings: Settings):
        self.store = Store(settings)
        self.store.init()

    def status(self) -> dict[str, Any]:
        """Report the provider ledger without treating absent billing as free."""
        with self.store._connect() as connection:
            ledger = connection.execute(
                "SELECT coalesce(sum(committed),0), coalesce(sum(uncertain),0), count(*) FROM spend_ledger"
            ).fetchone()
            known = 0.0
            # Decode each immutable snapshot once, not once per potentially large trial set.
            for experiment in connection.execute("SELECT id,snapshot FROM experiments"):
                snapshot = json.loads(experiment["snapshot"])
                live = {s["id"] for s in snapshot["systems"] if s["kind"] == "http"}
                del snapshot
                if not live:
                    continue
                for row in connection.execute(
                    "SELECT data FROM trials WHERE experiment_id=?", (experiment["id"],)
                ):
                    trial = json.loads(row[0])
                    if trial["system_id"] not in live:
                        continue
                    cost = trial.get("cost_usd")
                    partial = trial.get("provider_metadata", {}).get("known_cost_usd")
                    known += max(
                        (
                            float(value)
                            for value in (cost, partial)
                            if type(value) in {int, float} and math.isfinite(value) and value >= 0
                        ),
                        default=0.0,
                    )
        limits = SpendLimits(self.store.settings).list()
        return {
            "as_of": utc_now(),
            "known_provider_cost_usd": known,
            "committed_provider_usd": float(ledger[0]),
            "unsettled_reservations": int(ledger[1]),
            "provider_admissions": int(ledger[2]),
            "limits": limits,
            "accounting_basis": "reported provider usage at frozen configured prices; not an invoice",
            "external_service_cost_usd": None,
            "scope": "Everval provider calls only; excludes hosting, electricity, tools and development-service billing",
        }

    def _basis(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.store.get_experiment(experiment_id)
        snapshot = experiment["snapshot"]
        systems = [System.model_validate(s) for s in snapshot["systems"]]
        scenarios = [Scenario.model_validate(s) for s in snapshot["collection"]["scenarios"]]
        live = all(s.kind == "http" for s in systems)
        priced = all(
            s.kind != "http"
            or (s.input_cost_per_million is not None and s.output_cost_per_million is not None)
            for s in systems
        )
        reservation = (
            math.fsum(reserve_cost(task, system) for task in scenarios for system in systems)
            * experiment["repetitions"]
            if priced
            else None
        )
        trials = experiment["trials"]
        completed = bool(trials) and all(t["status"] == "completed" for t in trials)
        measured = live and completed and not experiment.get("budget_uncertain")
        costs = [t.get("cost_usd") for t in trials]
        durations = [t.get("duration_ms") for t in trials]
        cost_known = measured and all(
            type(v) in {int, float} and math.isfinite(v) and v >= 0 for v in costs
        )
        duration_known = measured and all(
            type(v) in {int, float} and math.isfinite(v) and v >= 0 for v in durations
        )
        return {
            "experiment_id": experiment_id,
            "snapshot_hash": experiment["snapshot_hash"],
            "budget_scope": snapshot.get("budget_scope", "workspace"),
            "task_count": len(scenarios),
            "attempt_count": len(trials),
            "repetitions": experiment["repetitions"],
            "per_run_admission_budget_usd": experiment["budget_usd"],
            "per_run_reservation_estimate_usd": reservation,
            "observed_cost_per_run_usd": math.fsum(costs) if cost_known else None,
            "observed_sequential_attempt_seconds": math.fsum(durations) / 1000
            if duration_known
            else None,
            "pricing": [
                {
                    "system_id": s.id,
                    "model": s.model,
                    "version": s.pricing_version,
                    "input_usd_per_million": s.input_cost_per_million,
                    "output_usd_per_million": s.output_cost_per_million,
                }
                for s in systems
            ],
            "uses_installation_controls_or_replay": not live,
            "full_run_fits_admission_budget": reservation is not None
            and reservation <= experiment["budget_usd"] + 1e-12,
        }

    def project(
        self,
        experiment_id: str,
        *,
        runs: int = 30,
        screening_experiment_id: str | None = None,
        escalation_rate: float = 1.0,
        cap_usd: float | None = None,
    ) -> dict[str, Any]:
        if type(runs) is not int or not 1 <= runs <= 100000:
            raise ValueError("Runs must be an integer from 1 to 100000")
        if (
            type(escalation_rate) not in {int, float}
            or not math.isfinite(escalation_rate)
            or not 0 <= escalation_rate <= 1
        ):
            raise ValueError("Escalation rate must be a finite fraction between zero and one")
        if cap_usd is not None and (
            type(cap_usd) not in {int, float} or not math.isfinite(cap_usd) or cap_usd < 0
        ):
            raise ValueError("Cap must be a finite nonnegative USD amount")
        full = self._basis(experiment_id)
        screen = self._basis(screening_experiment_id) if screening_experiment_id else None
        if screen is None and escalation_rate != 1:
            raise ValueError("An escalation rate requires a screening experiment")
        multiplier = escalation_rate if screen else 1.0

        def expected(field: str) -> float | None:
            full_value = full[field]
            screen_value = screen[field] if screen else 0.0
            if (full_value is None and multiplier > 0) or screen_value is None:
                return None
            return runs * ((full_value or 0.0) * multiplier + screen_value)

        full_reserve = full["per_run_reservation_estimate_usd"]
        screen_reserve = screen["per_run_reservation_estimate_usd"] if screen else 0.0
        maximum = (
            runs * (full_reserve + screen_reserve)
            if full_reserve is not None and screen_reserve is not None
            else None
        )
        one_cycle = full["per_run_admission_budget_usd"] + (
            screen["per_run_admission_budget_usd"] if screen else 0.0
        )
        status = self.status()
        scopes = {"workspace", full["budget_scope"]}
        if screen:
            scopes.add(screen["budget_scope"])
        governing = [limit for limit in status["limits"] if limit["scope"] in scopes]
        remaining_limits = [
            max(0.0, limit[key] - limit["usage"][key])
            for limit in governing
            for key in ("daily_usd", "monthly_usd", "lifetime_usd")
            if limit.get(key) is not None
        ]
        if cap_usd is not None:
            remaining_limits.append(cap_usd)
        remaining = min(remaining_limits) if remaining_limits else None
        return {
            "generated_at": utc_now(),
            "runs": runs,
            "full": full,
            "screening": screen,
            "assumed_escalation_rate": multiplier,
            "expected_cost_usd": expected("observed_cost_per_run_usd"),
            "expected_sequential_attempt_seconds": expected("observed_sequential_attempt_seconds"),
            "all_escalations_reservation_estimate_usd": maximum,
            "all_escalations_admission_budget_usd": runs * one_cycle,
            "available_budget_now_usd": remaining,
            "governing_scopes": sorted(scopes),
            "fully_reserved_cycles_affordable_now": (
                min(runs, math.floor((remaining + 1e-12) / one_cycle))
                if remaining is not None and one_cycle > 0
                else (runs if remaining is not None and one_cycle == 0 else None)
            ),
            "unsettled_reservations": status["unsettled_reservations"],
            "assumptions": [
                "Expected cost requires completed live attempts with fully known accounting.",
                "Future workload, saved system configurations and prices are assumed unchanged.",
                "Escalation probability is supplied by the operator, not inferred from a small sample.",
                "Reservation estimates are admission bounds, not guaranteed provider invoices.",
                "Affordable cycles use current caps; UTC daily/monthly resets are not projected.",
                "Sequential time excludes queueing and is not an end-to-end service latency guarantee.",
                "This projection does not authorize calls or include external service or development-service charges.",
            ],
        }
