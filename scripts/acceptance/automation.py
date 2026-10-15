#!/usr/bin/env python3
"""Installed-wheel acceptance for continuous evaluation and durable accounting.

Run with the Python executable from an isolated wheel installation. All artifacts
are written beneath a newly created --output-dir. The only HTTP service is a real
loopback fixture subprocess; configured token prices exercise accounting and do
not represent a paid provider invoice. No repository code or Docker jobs execute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PRIVATE_MARKER = "everval-acceptance-private-evidence-7cf620"
REVISION = "acceptance-adapter/1"
PHASES = (
    "autopilot",
    "unknown_usage",
    "optimizer",
    "caps_and_cancellation",
    "recovery_priced",
    "recovery_zero",
    "backup_restore",
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    path.chmod(0o600)


def adapter(directory: Path) -> None:
    """A separate trusted process implements the public adapter wire contract."""
    from cvals.agent_sdk import AgentRequest

    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                assert self.path == "/everval/v1/tasks"
                assert len(raw) <= 128 * 1024
                assert PRIVATE_MARKER.encode() not in raw
                request = AgentRequest.model_validate_json(raw)
                parsed = json.loads(raw)
                assert set(parsed["task"]) == {"id", "name", "domain", "instruction", "inputs"}
                assert request.revision == REVISION
                event = {
                    "request_id": request.request_id,
                    "model": request.model,
                    "task_id": request.task.id,
                    "case": request.task.inputs["acceptance_case"],
                    "public_keys": sorted(parsed["task"]),
                    "revision": request.revision,
                    "received_at": time.time(),
                }
                with lock, (directory / "requests.jsonl").open("a") as stream:
                    stream.write(json.dumps(event) + "\n")
                    stream.flush()
                if request.model.startswith("slow"):
                    time.sleep(2 if request.model == "slow-cancel" else 40)
                value = sum(request.task.inputs["values"])
                if request.model == "regressed":
                    value += 1
                response = {
                    "protocol_version": 1,
                    "revision": REVISION,
                    "output": {"value": value},
                    "trace": [],
                    "error": None,
                    "usage": None
                    if request.model == "unknown"
                    else {"prompt_tokens": 16, "completion_tokens": 8},
                }
                encoded = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                with lock, (directory / "protocol-errors.log").open("a") as stream:
                    stream.write(type(exc).__name__ + "\n")
                self.send_error(400, "Acceptance protocol rejected")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    write_json(directory / "endpoint.json", {"url": f"http://127.0.0.1:{server.server_port}"})
    server.serve_forever(poll_interval=0.1)


class Acceptance:
    def __init__(self, root: Path):
        import cvals
        from cvals.config import Settings
        from cvals.store import Store

        self.Settings, self.Store = Settings, Store
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.logs = self.root / "logs"
        self.logs.mkdir(mode=0o700)
        self.started = time.monotonic()
        self.workers: list[subprocess.Popen[bytes]] = []
        self.counter = 0
        origin = str(Path(cvals.__file__).resolve())
        if "site-packages" not in origin:
            raise ValueError(
                "Acceptance requires an installed wheel, not an editable source import"
            )
        self.results: dict[str, Any] = {
            "suite": "automation-installed-stack",
            "schema_version": 1,
            "version": cvals.__version__,
            "installed_package": True,
            "python": sys.version.split()[0],
            "paid_provider_calls": 0,
            "external_provider_charge_usd": 0,
            "accounting_fixture": "Loopback arithmetic adapter; nonzero configured token rates are synthetic accounting controls, not invoices.",
            "phases": [],
            "status": "running",
        }
        write_json(
            self.root / "private-environment.json", {"python": sys.executable, "package": origin}
        )
        self.adapter_dir = self.root / "adapter"
        self.adapter_dir.mkdir(mode=0o700)
        with (self.logs / "adapter.log").open("wb") as log:
            self.adapter_process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--adapter", str(self.adapter_dir)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=self.environment(self.root / "adapter-workspace"),
            )
        self.wait(lambda: (self.adapter_dir / "endpoint.json").exists(), "adapter startup", 20)
        self.endpoint = json.loads((self.adapter_dir / "endpoint.json").read_text())["url"]
        self.autopilot_workspace: Path | None = None
        self.checkpoint()

    def environment(self, workspace: Path) -> dict[str, str]:
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        for key in list(environment):
            if any(fragment in key.upper() for fragment in ("API_KEY", "ACCESS_TOKEN", "PASSWORD")):
                environment.pop(key)
        environment.update(
            EVERVAL_DATA_DIR=str(workspace),
            CVALS_DATA_DIR=str(workspace),
            EVERVAL_ALLOW_REMOTE_PROVIDERS="false",
            CVALS_ALLOW_REMOTE_PROVIDERS="false",
            EVERVAL_DOCKER_HOST="unix://" + str(self.root / "no-docker.sock"),
            CVALS_DOCKER_HOST="unix://" + str(self.root / "no-docker.sock"),
            PYTHONUNBUFFERED="1",
            NO_COLOR="1",
        )
        return environment

    def checkpoint(self) -> None:
        self.results["duration_seconds"] = round(time.monotonic() - self.started, 3)
        self.results["checks"] = [
            {
                "name": phase["name"],
                "status": phase["status"],
                "evidence": phase.get("details", {"error": phase.get("error")}),
            }
            for phase in self.results["phases"]
        ]
        write_json(self.root / "results.json", self.results)

    def wait(self, predicate: Callable[[], Any], label: str, timeout: float = 90) -> Any:
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            value = predicate()
            if value:
                return value
            time.sleep(0.2)
        raise AssertionError(f"Timed out waiting for {label} after {timeout:g} seconds")

    def command(
        self, workspace: Path, *args: str, allow_failure: bool = False, timeout: float = 90
    ) -> dict[str, Any]:
        self.counter += 1
        name = f"command-{self.counter:03d}"
        result = subprocess.run(
            [sys.executable, "-m", "cvals.cli", *args],
            env=self.environment(workspace),
            capture_output=True,
            timeout=timeout,
        )
        (self.logs / (name + ".stdout")).write_bytes(result.stdout)
        (self.logs / (name + ".stderr")).write_bytes(result.stderr)
        write_json(
            self.logs / (name + ".json"), {"args": list(args), "exit_code": result.returncode}
        )
        if result.returncode and not allow_failure:
            raise AssertionError(
                f"CLI {' '.join(args[:2])} exited {result.returncode}; inspect {name}.stderr"
            )
        try:
            value = json.loads(result.stdout)
        except (ValueError, UnicodeDecodeError):
            value = None
        return {"exit_code": result.returncode, "value": value, "log": name}

    def workspace(self, name: str) -> tuple[Path, Any, Any]:
        path = self.root / name / "workspace"
        settings = self.Settings(
            data_dir=path, docker_host="unix://" + str(self.root / "no-docker.sock")
        )
        store = self.Store(settings)
        store.init()
        return path, settings, store

    def worker(self, path: Path) -> subprocess.Popen[bytes]:
        from cvals.runtime import worker_running

        self.counter += 1
        with (self.logs / f"worker-{self.counter:03d}.log").open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "cvals.cli", "worker"],
                env=self.environment(path),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        self.workers.append(process)
        try:
            self.wait(
                lambda: worker_running(self.Settings(data_dir=path)) or process.poll() is not None,
                "worker lock",
                90,
            )
        except BaseException:
            self.stop(process)
            raise
        if process.poll() is not None:
            raise AssertionError("Dedicated worker exited during startup")
        return process

    @staticmethod
    def stop(process: subprocess.Popen[bytes], *, kill: bool = False) -> None:
        if process.poll() is not None:
            return
        process.kill() if kill else process.terminate()
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=6)

    @contextmanager
    def serving(self, path: Path):
        process = self.worker(path)
        try:
            yield process
        finally:
            self.stop(process)

    def requests(
        self, *, model: str | None = None, case: str | None = None
    ) -> list[dict[str, Any]]:
        path = self.adapter_dir / "requests.jsonl"
        values = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        )
        return [
            v
            for v in values
            if (model is None or v["model"] == model) and (case is None or v["case"] == case)
        ]

    def collection(
        self, store: Any, name: str, count: int, *, offset: int = 0, partition: str = "dev"
    ) -> dict[str, Any]:
        from cvals.models import Check, Collection, Scenario

        scenarios = [
            Scenario(
                id=f"{name}-{i}",
                name=f"Arithmetic contract {offset + i}",
                domain="data",
                instruction=f"Return the sum of values for contract {offset + i}.",
                inputs={"values": [offset + i, 7], "acceptance_case": name},
                expected={"private_marker": PRIVATE_MARKER},
                checks=[
                    Check(
                        id="sum",
                        name="Private arithmetic assertion",
                        kind="numeric",
                        path="value",
                        expected=offset + i + 7,
                    )
                ],
                reference_output={"value": offset + i + 7, "private_marker": PRIVATE_MARKER},
                provenance={"origin": "acceptance_fixture", "private_marker": PRIVATE_MARKER},
            )
            for i in range(count)
        ]
        return store.save_collection(
            Collection(id=name, name=name, benchmark_partition=partition, scenarios=scenarios)
        )

    def system(
        self, store: Any, *, identifier: str = "adapter", model: str = "stable", rate: float = 100
    ) -> dict[str, Any]:
        from cvals.models import System

        return store.save_system(
            System(
                id=identifier,
                name="Local acceptance adapter " + identifier,
                kind="http",
                model=model,
                base_url=self.endpoint,
                harness="external-agent",
                adapter_revision=REVISION,
                adapter_input_token_limit=32,
                adapter_output_token_limit=16,
                timeout_seconds=60,
                input_cost_per_million=rate,
                output_cost_per_million=rate,
                pricing_version="synthetic-acceptance-rates/1",
            )
        )

    def baseline(self, path: Path, store: Any, case: str) -> tuple[str, str]:
        from cvals.models import Collection

        full = self.collection(store, case, 3)
        store.save_collection(
            Collection.model_validate(
                {
                    **full,
                    "id": case + "-screen",
                    "name": "Screening",
                    "scenarios": full["scenarios"][:1],
                }
            )
        )
        self.system(store)
        full_run = self.command(path, "run", case, "--system", "adapter", "--budget", "0.03")[
            "value"
        ]
        screen_run = self.command(
            path, "run", case + "-screen", "--system", "adapter", "--budget", "0.03"
        )["value"]
        assert full_run["status"] == screen_run["status"] == "completed"
        assert all(t["passed"] for t in full_run["trials"] + screen_run["trials"])
        return full_run["id"], screen_run["id"]

    def finished(self, store: Any, identifier: str) -> dict[str, Any] | None:
        result = store.get_experiment(identifier)
        return result if result["status"] not in {"queued", "running"} else None

    def autopilot(self) -> dict[str, Any]:
        from cvals.autopilot import Autopilot

        path, settings, store = self.workspace("autopilot")
        full, screen = self.baseline(path, store, "autopilot-contract")
        controller = Autopilot(settings, store)
        record = controller.create(
            "Acceptance screening",
            full,
            screen,
            lifetime_budget_usd=0.12,
            trigger="change",
            enabled=True,
        )
        self.system(store, model="regressed")
        before = len(self.requests(case="autopilot-contract"))
        with self.serving(path):
            self.wait(lambda: controller.history(record["id"]), "screen admission")
            occurrence = controller.history(record["id"])[0]
            screen_run = self.wait(
                lambda: self.finished(store, occurrence["screen_experiment_id"]),
                "screen completion",
            )
            assert screen_run["trials"][0]["passed"] is False
            assert 0.03 < controller.budget(record["id"])["committed_usd"] <= 0.06
            duplicate = self.command(path, "worker", "--once", allow_failure=True)
            assert duplicate["exit_code"] != 0
            assert len(controller.history(record["id"])) == 1
            # An edited saved system must not alter the already frozen cycle.
            self.system(store, model="stable")
            self.wait(
                lambda: controller.history(record["id"])[0].get("full_experiment_id"),
                "natural worker screening escalation",
                90,
            )
            occurrence = controller.history(record["id"])[0]
            full_run = self.wait(
                lambda: self.finished(store, occurrence["full_experiment_id"]), "full completion"
            )
            assert full_run["snapshot"]["systems"][0]["model"] == "regressed"
            assert len(full_run["trials"]) == 3 and all(
                t["passed"] is False for t in full_run["trials"]
            )
            controller.configure(record["id"], enabled=False)
            self.wait(
                lambda: controller.history(record["id"])[0]["status"] == "completed",
                "final cycle accounting",
                15,
            )
            self.wait(
                lambda: controller.investigations(record["id"]), "automatic investigation", 15
            )
            assert len(controller.history(record["id"])) == 1
            budget = controller.budget(record["id"])
            assert abs(budget["committed_usd"] - 0.0096) < 1e-10
            assert not budget["uncertain"]
        assert len(self.requests(case="autopilot-contract")) - before == 4
        assert not (self.adapter_dir / "protocol-errors.log").exists()
        self.autopilot_workspace = path
        write_json(
            self.root / "autopilot" / "private-evidence.json",
            {
                "policy_id": record["id"],
                "budget": budget,
                "history": controller.history(record["id"]),
                "investigations": controller.investigations(record["id"]),
            },
        )
        return {
            "screen_requests": 1,
            "full_requests": 3,
            "duplicate_worker_rejected": True,
            "frozen_system_survived_saved_edit": True,
            "investigations": len(controller.investigations(record["id"])),
            "synthetic_accounted_usd": budget["accounted_usd"],
            "natural_worker_escalation": True,
        }

    def unknown_usage(self) -> dict[str, Any]:
        from cvals.autopilot import Autopilot

        path, settings, store = self.workspace("unknown_usage")
        full, screen = self.baseline(path, store, "unknown-contract")
        controller = Autopilot(settings, store)
        record = controller.create(
            "Unknown billing",
            full,
            screen,
            lifetime_budget_usd=0.06,
            trigger="change",
            enabled=True,
        )
        self.system(store, model="unknown")
        with self.serving(path):
            self.wait(lambda: controller.history(record["id"]), "unknown screen")
            self.wait(
                lambda: not controller.get(record["id"])["enabled"],
                "automatic unknown-usage halt",
                90,
            )
            occurrence = controller.history(record["id"])[0]
            budget = controller.budget(record["id"])
            assert occurrence["full_experiment_id"] is None
            assert budget["uncertain"] and abs(budget["committed_usd"] - 0.06) < 1e-10
            try:
                controller.configure(record["id"], enabled=True)
            except ValueError:
                pass
            else:
                raise AssertionError("Unknown accounting allowed policy resumption")
            trial = store.get_experiment(occurrence["screen_experiment_id"])["trials"][0]
            assert trial["cost_usd"] is None
        assert len(self.requests(model="unknown", case="unknown-contract")) == 1
        return {
            "unknown_cost_retained": True,
            "combined_reservation_retained_usd": budget["committed_usd"],
            "full_stage_admitted": False,
            "resume_blocked": True,
            "http_requests": 1,
        }

    def optimizer(self) -> dict[str, Any]:
        from cvals.models import Collection, ExperimentCreate
        from cvals.optimization import Optimizer

        path, settings, store = self.workspace("optimizer")
        self.collection(store, "optimizer-dev", 2, offset=20)
        holdout = self.collection(store, "optimizer-holdout", 1, offset=80, partition="holdout")
        self.system(store, identifier="cheaper", rate=100)
        self.system(store, identifier="costlier", rate=200)
        optimizer = Optimizer(settings)
        study = optimizer.create(
            "Finite accounting comparison",
            "optimizer-dev",
            "optimizer-holdout",
            ["cheaper", "costlier"],
            0.10,
        )
        blocked = 0
        renamed = {
            **holdout,
            "id": "renamed-holdout",
            "name": "Renamed",
            "scenarios": [{**s, "id": "renamed-task"} for s in holdout["scenarios"]],
        }
        store.save_collection(Collection.model_validate(renamed))
        for collection_id in ("optimizer-holdout", "renamed-holdout"):
            try:
                store.create_experiment(
                    ExperimentCreate(
                        name="Forbidden leakage",
                        collection_id=collection_id,
                        system_ids=["cheaper"],
                        budget_usd=0.03,
                    )
                )
            except ValueError:
                blocked += 1
        assert blocked == 2
        with self.serving(path):
            result = self.wait(
                lambda: (
                    v
                    if (v := optimizer.get(study["id"]))["status"]
                    not in {"development_queued", "holdout_queued"}
                    else None
                ),
                "optimizer proposal",
                40,
            )
            assert result["status"] == "ready_for_review"
            assert result["selected_system_id"] == "cheaper"
            assert result["cost_complete"]
            final = store.get_experiment(result["holdout_experiment_id"])
            assert len(final["trials"]) == 1 and final["trials"][0]["passed"]
            try:
                store.rerun_experiment(final["id"])
            except ValueError:
                blocked += 1
            assert blocked == 3
        self.collection(store, "cancel-holdout", 1, offset=100, partition="holdout")
        pending = optimizer.create(
            "Cancellation", "optimizer-dev", "cancel-holdout", ["cheaper"], 0.10
        )
        cancelled = optimizer.cancel(pending["id"])
        assert cancelled["status"] == "cancelled"
        assert store.get_experiment(pending["development_experiment_id"])["status"] == "cancelled"
        try:
            store.create_experiment(
                ExperimentCreate(
                    name="Reserved after cancel",
                    collection_id="cancel-holdout",
                    system_ids=["cheaper"],
                    budget_usd=0.03,
                )
            )
        except ValueError:
            blocked += 1
        assert blocked == 4
        write_json(
            self.root / "optimizer" / "private-evidence.json",
            {"study": result, "cancelled": cancelled},
        )
        return {
            "holdout_bypass_attempts_rejected": blocked,
            "selected_lower_configured_cost": True,
            "one_final_holdout_request": True,
            "queued_study_cancelled": True,
            "synthetic_accounted_usd": result["known_cost_usd"],
            "automatic_deployment": False,
        }

    def caps_and_cancellation(self) -> dict[str, Any]:
        from cvals.models import ExperimentCreate
        from cvals.spend_limits import SpendLimits

        path, settings, store = self.workspace("caps")
        self.collection(store, "cap-contract", 3)
        self.system(store)
        SpendLimits(settings).configure(lifetime_usd=0.0048)
        with self.serving(path):
            run = store.create_experiment(
                ExperimentCreate(
                    name="Workspace cap",
                    collection_id="cap-contract",
                    system_ids=["adapter"],
                    budget_usd=0.03,
                )
            )
            result = self.wait(
                lambda: self.finished(store, run["id"]), "workspace budget exhaustion"
            )
            assert len(self.requests(case="cap-contract")) == 1
            assert sum(t["status"] == "budget_exceeded" for t in result["trials"]) == 2
            assert sum(t["cost_usd"] or 0 for t in result["trials"]) <= 0.0048
        path, _, store = self.workspace("cancellation")
        self.collection(store, "cancel-contract", 3)
        self.system(store, model="slow-cancel")
        with self.serving(path):
            run = store.create_experiment(
                ExperimentCreate(
                    name="Cancel inflight",
                    collection_id="cancel-contract",
                    system_ids=["adapter"],
                    budget_usd=0.03,
                )
            )
            self.wait(lambda: self.requests(model="slow-cancel"), "inflight cancellation request")
            self.command(path, "experiments", "cancel", run["id"])
            result = self.wait(lambda: self.finished(store, run["id"]), "cancellation settlement")
            assert result["status"] == "cancelled"
            assert all(t["status"] == "cancelled" for t in result["trials"])
            assert len(self.requests(model="slow-cancel")) == 1
            assert abs(sum(t["cost_usd"] or 0 for t in result["trials"]) - 0.0024) < 1e-10
        return {
            "workspace_cap_prevented_two_requests": True,
            "inflight_cancellation_preserved_charge": True,
            "queued_cancelled_attempts_retained": 2,
        }

    def recovery(self, *, zero: bool) -> dict[str, Any]:
        from cvals.models import ExperimentCreate

        name = "recovery_zero" if zero else "recovery_priced"
        model = "slow-zero" if zero else "slow-priced"
        path, _, store = self.workspace(name)
        self.collection(store, name, 2)
        self.system(store, model=model, rate=0 if zero else 100)
        run = store.create_experiment(
            ExperimentCreate(
                name="Interrupted HTTP attempt",
                collection_id=name,
                system_ids=["adapter"],
                budget_usd=0 if zero else 0.03,
            )
        )
        process = self.worker(path)
        self.wait(lambda: self.requests(model=model), "request before worker loss")
        self.stop(process, kill=True)
        with sqlite3.connect(path / "workspace.sqlite3") as connection:
            expires = connection.execute("SELECT expires_at FROM worker_lease").fetchone()[0]
        self.wait(lambda: time.time() > expires + 0.25, "real worker lease expiration", 40)
        with self.serving(path):
            self.wait(
                lambda: self.finished(store, run["id"]) or len(self.requests(model=model)) > 1,
                "post-crash recovery",
                15,
            )
            result = store.get_experiment(run["id"])
            evidence = {
                "experiment_id": run["id"],
                "state": result["status"],
                "budget_uncertain": result["budget_uncertain"],
                "trials": [
                    {
                        "status": t["status"],
                        "cost_usd": t["cost_usd"],
                        "attempts": len(t["attempts"]),
                    }
                    for t in result["trials"]
                ],
                "http_requests": len(self.requests(model=model)),
            }
            write_json(self.root / name / "private-evidence.json", evidence)
            assert result["budget_uncertain"], (
                "Interrupted HTTP accounting was treated as certain after recovery"
            )
            assert evidence["http_requests"] == 1, (
                "A further HTTP call was admitted after an interrupted attempt"
            )
            assert result["trials"][0]["status"] == "interrupted"
            assert result["trials"][1]["status"] == "budget_exceeded"
            assert len(result["trials"][0]["attempts"]) == 1
        return {
            "sigkill_recovered": True,
            "real_lease_expiration": True,
            "ambiguous_attempt_retried": False,
            "later_http_calls_blocked": True,
            "zero_configured_rate": zero,
        }

    def backup_restore(self) -> dict[str, Any]:
        from cvals.audit import AuditLog
        from cvals.autopilot import Autopilot
        from cvals.models import ExperimentCreate

        if self.autopilot_workspace is None:
            raise AssertionError("Backup acceptance requires the autopilot phase in the same run")
        source = self.autopilot_workspace
        settings = self.Settings(data_dir=source)
        source_store = self.Store(settings)
        artifact = settings.artifacts_dir / "acceptance-artifact.txt"
        artifact.write_text("Private fixture artifact retained outside the database.\n")
        artifact.chmod(0o600)
        head = AuditLog(settings).verify()
        assert head["valid"]
        backup = self.root / "backup_restore" / "records.sqlite3"
        self.command(source, "backup", "--output", str(backup))
        restored = self.root / "backup_restore" / "restored"
        restored.mkdir(mode=0o700)
        shutil.copyfile(backup, restored / "workspace.sqlite3")
        (restored / "workspace.sqlite3").chmod(0o600)
        shutil.copytree(settings.artifacts_dir, restored / "artifacts")
        target_settings = self.Settings(data_dir=restored)
        target_store = self.Store(target_settings)
        target_store.init()
        restored_head = AuditLog(target_settings).verify(head["head"])
        assert restored_head["valid"] and restored_head["count"] == head["count"]
        assert source_store.list_experiments() == target_store.list_experiments()
        assert Autopilot(settings).list() == Autopilot(target_settings).list()
        assert (restored / "artifacts" / artifact.name).read_bytes() == artifact.read_bytes()
        prior = len(source_store.list_experiments())
        queued = target_store.create_experiment(
            ExperimentCreate(
                name="Post-restore evaluation",
                collection_id="autopilot-contract-screen",
                system_ids=["adapter"],
                budget_usd=0.03,
            )
        )
        with self.serving(restored):
            completed = self.wait(
                lambda: self.finished(target_store, queued["id"]), "restored worker progress"
            )
            assert all(t["passed"] for t in completed["trials"])
        assert len(source_store.list_experiments()) == prior
        assert AuditLog(target_settings).verify()["valid"]
        return {
            "database_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "audit_records_verified": head["count"],
            "artifact_copy_verified": True,
            "restored_worker_executed_new_run": True,
            "source_unchanged": True,
            "restore_method": "documented database restore plus separate artifact copy",
        }

    def phase(self, name: str) -> None:
        print(f"START {name}", flush=True)
        started = time.monotonic()
        try:
            details = (
                self.recovery(zero=name == "recovery_zero")
                if name.startswith("recovery_")
                else getattr(self, name)()
            )
            record = {"name": name, "status": "passed", "details": details}
        except Exception as exc:
            (self.logs / (name + ".traceback")).write_text(traceback.format_exc())
            message = str(exc).replace(str(self.root), "[acceptance]")
            record = {
                "name": name,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": message[:500],
            }
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        self.results["phases"].append(record)
        self.checkpoint()
        print(f"{record['status'].upper()} {name} {record['duration_seconds']}s", flush=True)

    def close(self) -> None:
        for process in self.workers:
            self.stop(process)
        self.stop(self.adapter_process)
        for path in self.root.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
        self.results["adapter_requests"] = len(self.requests())
        self.results["status"] = (
            "passed" if all(p["status"] == "passed" for p in self.results["phases"]) else "failed"
        )
        self.checkpoint()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--phase", action="append", choices=PHASES)
    parser.add_argument("--adapter", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.adapter:
        adapter(args.adapter)
        return
    if args.output_dir is None:
        parser.error("--output-dir is required")
    os.umask(0o077)
    suite = Acceptance(args.output_dir)
    try:
        for phase in args.phase or PHASES:
            suite.phase(phase)
    finally:
        suite.close()
    print(
        json.dumps(
            {
                "status": suite.results["status"],
                "phases": len(suite.results["phases"]),
                "duration_seconds": suite.results["duration_seconds"],
            }
        ),
        flush=True,
    )
    raise SystemExit(0 if suite.results["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
