"""Pinned mini-SWE-agent execution and official SWE-bench grading.

Repository code executes only inside Docker. The solver receives a base checkout
with one synthetic commit; evaluator scripts and reference patches enter separate
fresh containers. Upstream prompts, parsing and grading remain upstream-owned.
This harness is deliberately separate from Everval's curated task runtime.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import selectors
import subprocess
import tarfile
import time
import uuid
from contextlib import nullcontext, suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import repository_setup

MINI_REVISION = "a83fcae82d2a08f0ee0c688f9d137b3566c097f8"
SWEBENCH_REVISION = "02e7a74ffd0b707aab73d203fe87bdc7c76afc8e"
SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
MAX_PATCH_BYTES = 512 * 1024
MAX_COMMAND_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
EDIT_POLICY_REVISION = "python-source-v1"
REVIEW_NOTICE = (
    "\n\nThis run has one additional review checkpoint: your first completion "
    "command requests review rather than ending the run. You will receive a "
    "review instruction and may use the remaining shared budget to inspect "
    "your work. The second completion command submits your final patch."
)
REVIEW_MESSAGE = (
    "Review checkpoint (one time). Before final submission, inspect your current "
    "diff for unintended changes and assess whether it fixes the reported behavior. "
    "Run relevant existing visible regression tests or a focused reproduction if "
    "feasible. Check one plausible edge case. Prefer the smallest correct fix; "
    "remove accidental changes. Do not edit tests or configuration. No private "
    "tests or reference solution are available. Then regenerate patch.txt, inspect "
    "it, and submit using the original completion command. Your original total "
    "step, time, and spending limits still apply."
)


UPSTREAM_FILE_HASHES = {
    "minisweagent/__init__.py": "0a708308b1b62cc020b60799c3d54f665845f795b3f0c4fdac787c96a38d12c7",
    "minisweagent/agents/default.py": "e8ef8aa365942d739c2ec5cb0879f60f377d2dc2de8ec670aaedf3bafb45a4c2",
    "minisweagent/models/utils/actions_text.py": "e5997bba3ae3d541ff418317cc8dd9e9e17657de806679ef38ab1ad74e294047",
    "minisweagent/models/utils/openai_multimodal.py": "c498ec2e97c0bf45c212a10de9f5d1b2d24d4de46a05fd0eaea975c1abe3f81f",
    "minisweagent/utils/serialize.py": "3035b27cfeffdf91117c72bc8131340bd9c5f67a3010baeb4ecd6b0c7406f462",
    "minisweagent/exceptions.py": "0590393c56bee873c79a691dcb4f15cb39c85f1658598b84bfb295bfde56921d",
    "minisweagent/config/benchmarks/swebench_backticks.yaml": "fdc45d1310b045814a0bca7fa4c9e0ed42d86142b5b5f6139d204fd4a0546677",
    "swebench/types.py": "558b6e7193306c304f9847a8a83dcab6a03ee333f6d51e61152e8821f89e0725",
    "swebench/harness/grading.py": "a095273010175001a6779e365b3b77abb8657c7ac2e4f80b4bc360faa6f09483",
    "swebench/harness/infra_failure.py": "72eaca5e8f76390ff9ac1d2d606f5390820b88efe6eed56efed8ff520c593eac",
    "swebench/harness/log_parsers/__init__.py": "2227a8696ce43a4266dccfa434374afaff43df6bd0b1769f431f5efe76500c14",
    "swebench/harness/log_parsers/python.py": "cd56156414f8327221e525665ace9b184f7d73e83b272d9eb3f545fb17c2d9bc",
    "swebench/harness/utils.py": "c22f38fdd4ffd34301da7f837b82926cf261d81ca0387d92dd2990b393517c79",
    "swebench/harness/constants/__init__.py": "8895ab5313d874349c4b07bd223bb9eb00acaee377b432e67cc7ad840341e79b",
}


class SandboxFailure(RuntimeError):
    """Execution cannot safely continue; retain the incomplete attempt."""


class SandboxCleanupFailure(SandboxFailure):
    """The campaign must stop: termination of owned work is unconfirmed."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


@lru_cache(maxsize=1)
def _upstream() -> None:
    # Neither a personal global config nor a checkout .env belongs in a benchmark.
    os.environ["MSWEA_SILENT_STARTUP"] = "1"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = str(
        Path(__file__).resolve().parents[2] / ".local/benchmark-tooling-config"
    )
    from importlib.metadata import version

    if version("mini-swe-agent") != "2.4.6" or version("swebench") != "5.0.2":
        raise RuntimeError("Install the frozen mini-SWE-agent and SWE-bench tooling")
    import importlib.util

    for name, expected in UPSTREAM_FILE_HASHES.items():
        package, relative = name.split("/", 1)
        spec = importlib.util.find_spec(package)
        if spec is None or spec.origin is None:
            raise RuntimeError("Missing pinned benchmark package")
        path = Path(spec.origin).parent / relative
        if _digest(path.read_bytes()) != expected:
            raise RuntimeError(f"Pinned upstream file differs: {name}")


def baseline_config() -> dict[str, Any]:
    """Load the official textual-action baseline without rewriting its prompts."""
    _upstream()
    import minisweagent
    import yaml

    path = Path(minisweagent.__file__).parent / "config/benchmarks/swebench_backticks.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TextActionModel:
    """Use upstream action parsing around a separately metered text transport."""

    def __init__(self, inner: Any, config: dict[str, Any]) -> None:
        _upstream()
        self.inner = inner
        self.config = config

    def query(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        from minisweagent.exceptions import FormatError
        from minisweagent.models.utils.actions_text import parse_regex_actions

        message = self.inner.query(messages)
        extra = message.setdefault("extra", {})
        try:
            extra["actions"] = parse_regex_actions(
                message["content"],
                action_regex=self.config.get(
                    "action_regex", r"```mswea_bash_command\s*\n(.*?)\n```"
                ),
                format_error_template=self.config["format_error_template"],
                template_kwargs={"finish_reason": extra.get("finish_reason")},
            )
        except FormatError as exc:
            # DefaultAgent charges parse failures from the interrupt message.
            # The transport's durable ledger already owns the actual charge.
            exc.messages[0].setdefault("extra", {})["cost"] = extra.get("cost", 0.0)
            exc.messages[0]["extra"]["request_id"] = extra.get("request_id")
            raise
        return message

    def format_message(self, **kwargs: Any) -> dict[str, Any]:
        return self.inner.format_message(**kwargs)

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {**self.inner.get_template_vars(), **kwargs}

    def serialize(self) -> dict[str, Any]:
        return self.inner.serialize()

    def format_observation_messages(
        self,
        message: dict[str, Any],
        outputs: list[dict[str, Any]],
        template_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        from minisweagent.models.utils.actions_text import format_observation_messages

        return format_observation_messages(
            outputs,
            observation_template=self.config["observation_template"],
            template_vars=template_vars,
            multimodal_regex="",
        )


def bounded_process(
    argv: list[str],
    *,
    timeout: float,
    max_bytes: int = MAX_OUTPUT_BYTES,
    input_path: Path | None = None,
) -> dict[str, Any]:
    """Drain bounded output; kill the client on timeout or oversized output."""
    started = time.monotonic()
    chunks = bytearray()
    reason = ""
    input_context = input_path.open("rb") if input_path else nullcontext(subprocess.DEVNULL)
    with (
        input_context as input_stream,
        subprocess.Popen(
            argv, stdin=input_stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        ) as process,
    ):
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    reason = "Command exceeded its time limit"
                    break
                for key, _ in selector.select(min(remaining, 0.2)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if len(chunks) + len(data) > max_bytes:
                        chunks.extend(data[: max_bytes - len(chunks)])
                        reason = "Command exceeded its output limit"
                        break
                    chunks.extend(data)
                if reason:
                    break
            if reason:
                process.kill()
            try:
                returncode = process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait()
                reason = "Command exceeded its time limit"
    return {
        "output": chunks.decode("utf-8", errors="replace"),
        "returncode": returncode,
        "exception_info": reason,
        "duration_seconds": time.monotonic() - started,
    }


def _solver_snapshot_command(base_commit: str) -> str:
    """Recreate exactly the tracked base tree without retaining its Git history."""
    if re.fullmatch(r"[0-9a-f]{40}", base_commit) is None:
        raise ValueError("Invalid frozen base commit")
    return (
        "set -eu\n"
        f"git diff --exit-code {base_commit}\n"
        f"git reset --hard {base_commit}\n"
        "git clean -ffd\n"
        f"everval_base_tree=$(git rev-parse --verify {base_commit}^{{tree}})\n"
        "everval_snapshot_dir=$(umask 077; mktemp -d /tmp/everval-snapshot.XXXXXX)\n"
        "trap 'rm -rf \"$everval_snapshot_dir\"' EXIT\n"
        'git ls-files --stage > "$everval_snapshot_dir/stages"\n'
        "if grep -q '^160000 ' \"$everval_snapshot_dir/stages\"; then\n"
        "  echo 'Tracked submodules cannot be isolated by this runner' >&2\n"
        "  exit 1\n"
        "fi\n"
        'git ls-files -z > "$everval_snapshot_dir/paths"\n'
        "rm -rf .git\n"
        "git -c init.defaultBranch=main init\n"
        "git config user.email benchmark@localhost\n"
        "git config user.name Benchmark\n"
        "git --literal-pathspecs add --force "
        '--pathspec-from-file="$everval_snapshot_dir/paths" --pathspec-file-nul\n'
        'test "$(git write-tree)" = "$everval_base_tree"\n'
        "git -c core.hooksPath=/dev/null commit -qm 'Benchmark starting point'\n"
        'test "$(git rev-parse HEAD^{tree})" = "$everval_base_tree"\n'
        'rm -rf "$everval_snapshot_dir"\n'
        "trap - EXIT\n"
    )


class DockerSandbox:
    """One resource-bounded, network-isolated container with no host mounts."""

    def __init__(
        self,
        *,
        image: str,
        output_dir: Path,
        context: str | None = None,
        command_timeout: int = 60,
        review_checkpoint: bool = False,
    ) -> None:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
            raise ValueError("Execution requires an immutable local Docker image ID")
        if not 1 <= command_timeout <= 600:
            raise ValueError("Invalid command timeout")
        self.image = image
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.context = context
        self.command_timeout = command_timeout
        self.review_checkpoint = review_checkpoint
        self.review_used = False
        self.name = "everval-benchmark-" + uuid.uuid4().hex
        self.started = False
        self.create_attempted = False
        self.resolved_endpoint: str | None = None
        self.sequence = 0
        self.config = {"cwd": "/testbed", "timeout": command_timeout}

    def _docker(self, *args: str) -> list[str]:
        endpoint = self.resolved_endpoint or os.environ.get("CVALS_DOCKER_HOST")
        if endpoint:
            if not endpoint.startswith("unix:///"):
                raise ValueError("The benchmark requires a local Unix Docker endpoint")
            return ["docker", "--host", endpoint, *args]
        return ["docker", "--context", self.context, *args] if self.context else ["docker", *args]

    def _resolve_endpoint(self) -> str:
        endpoint = os.environ.get("CVALS_DOCKER_HOST")
        if not endpoint and not self.context:
            endpoint = os.environ.get("DOCKER_HOST")
        if not endpoint:
            arguments = ["docker", "context", "inspect"]
            if self.context:
                arguments.append(self.context)
            arguments.extend(["--format", "{{.Endpoints.docker.Host}}"])
            resolution = bounded_process(arguments, timeout=15, max_bytes=8192)
            if resolution["returncode"] or resolution["exception_info"]:
                raise SandboxFailure("Cannot resolve the local Docker endpoint")
            endpoint = resolution["output"].strip()
        if not endpoint.startswith("unix:///") or "\n" in endpoint:
            raise SandboxFailure("The benchmark requires a local Unix Docker endpoint")
        return endpoint

    def start(self) -> None:
        self.resolved_endpoint = self._resolve_endpoint()
        _write_json(
            self.output_dir / "container-ownership.json",
            {
                "container_name": self.name,
                "image_id": self.image,
                "docker_endpoint": self.resolved_endpoint,
            },
            exclusive=True,
        )
        self.create_attempted = True
        result = bounded_process(
            self._docker(
                "run",
                "--detach",
                "--pull",
                "never",
                "--name",
                self.name,
                "--label",
                "ai.everval.benchmark=true",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
                "--pids-limit",
                "256",
                "--memory",
                "1536m",
                "--memory-swap",
                "1536m",
                "--cpus",
                "1.5",
                "--ulimit",
                "nofile=1024:1024",
                "--workdir",
                "/testbed",
                "--user",
                "0:0",
                "--env",
                "PAGER=cat",
                "--env",
                "MANPAGER=cat",
                "--env",
                "PIP_PROGRESS_BAR=off",
                "--env",
                "TQDM_DISABLE=1",
                "--env",
                "PIP_NO_INDEX=1",
                "--env",
                "PIP_DISABLE_PIP_VERSION_CHECK=1",
                "--env",
                "PYTHONUNBUFFERED=1",
                "--env",
                "PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin",
                "--entrypoint",
                "/bin/sleep",
                self.image,
                "7200",
            ),
            timeout=60,
        )
        _write_json(self.output_dir / "container-start.json", result)
        self.started = result["returncode"] == 0 and not result["exception_info"]
        if not self.started:
            self.close()
            raise SandboxFailure("Container could not start")
        inspect = bounded_process(self._docker("inspect", self.name), timeout=30)
        _write_json(self.output_dir / "container-inspect.json", json.loads(inspect["output"]))

    def shell(
        self,
        command: str,
        *,
        user: str = "1000:1000",
        timeout: int | None = None,
        max_bytes: int = MAX_OUTPUT_BYTES,
        cwd: str = "/testbed",
    ) -> dict[str, Any]:
        if not self.started:
            raise SandboxFailure("Container is unavailable")
        if len(command.encode()) > MAX_COMMAND_BYTES or "\x00" in command:
            raise SandboxFailure("Command exceeds its input boundary")
        self.sequence += 1
        result = bounded_process(
            self._docker(
                "exec",
                "--user",
                user,
                "--workdir",
                cwd,
                "--env",
                "HOME=/tmp/everval-home",
                self.name,
                "bash",
                "-c",
                command,
            ),
            timeout=timeout or self.command_timeout,
            max_bytes=max_bytes,
        )
        _write_json(
            self.output_dir / f"command-{self.sequence:04d}.json",
            {"command": command, **result},
            exclusive=True,
        )
        if result["exception_info"]:
            # Killing docker exec alone leaves its process running. Destroy the
            # whole owned sandbox before allowing the exception to leave.
            self.close()
            raise SandboxFailure(result["exception_info"])
        return result

    def prepare_solver(self, base_commit: str, *, remove_repository_setup: bool = False) -> None:
        command = _solver_snapshot_command(base_commit) + (
            "mkdir -p /tmp/everval-home\n"
            "chmod -R a+rwX /testbed /tmp/everval-home\n"
            "rm -f /eval.sh /tmp/patch.diff /tmp/test.patch /tmp/gold.patch\n"
        )
        if remove_repository_setup:
            command += (
                "rm -f /opt/everval-repository-setup.py "
                "/opt/everval-provenance/repository-setup.json "
                "/opt/everval-provenance/repository-setup.diff\n"
            )
        result = self.shell(command, user="0:0", timeout=120)
        if result["returncode"]:
            raise SandboxFailure("Could not prepare a history-free solver checkout")
        self.verify_solver_boundary()
        if remove_repository_setup:
            removed = self.shell(
                "test ! -e /opt/everval-repository-setup.py && "
                "test ! -e /opt/everval-provenance/repository-setup.json && "
                "test ! -e /opt/everval-provenance/repository-setup.diff"
            )
            if removed["returncode"]:
                raise SandboxFailure("Repository setup artifacts remain visible to the solver")

    def verify_solver_boundary(self) -> None:
        result = self.shell(
            "set -eu\n"
            "git config --global --add safe.directory /testbed\n"
            'test "$(git rev-list --all --count)" = 1\n'
            'test -z "$(git remote)"\n'
            'test -z "$(git status --porcelain)"\n'
            "test ! -e /var/run/docker.sock\n"
            "test ! -e /eval.sh\n"
            'test "$(id -u)" = 1000\n'
        )
        if result["returncode"]:
            raise SandboxFailure("Solver isolation preflight failed")

    def put_file(self, source: Path, destination: str) -> None:
        if destination not in {
            "/tmp/everval-patch.diff",
            "/tmp/everval-eval.sh",
            "/tmp/everval-repository-setup.py",
        }:
            raise ValueError("Unsupported evaluator destination")
        with source.open("rb") as stream:
            material = stream.read(MAX_PATCH_BYTES + 1)
        if len(material) > MAX_PATCH_BYTES:
            raise SandboxFailure("Evaluator material exceeds its input boundary")
        # Docker can retain host ownership on copied private files. Normalize only
        # the transfer archive; the host source stays private and unchanged.
        archive_path = self.output_dir / ("staged-" + Path(destination).name + ".tar")
        with archive_path.open("xb") as stream:
            archive_path.chmod(0o600)
            with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                member = tarfile.TarInfo(Path(destination).name)
                member.uid = member.gid = 0
                member.mode = 0o600
                member.size = len(material)
                archive.addfile(member, io.BytesIO(material))
        result = bounded_process(
            self._docker("cp", "-", self.name + ":/tmp/"),
            timeout=30,
            input_path=archive_path,
        )
        _write_json(
            self.output_dir / ("staged-" + Path(destination).name + ".json"),
            {"sha256": _digest(material), "uid": 0, "gid": 0, "mode": "0600", **result},
            exclusive=True,
        )
        if result["returncode"] or result["exception_info"]:
            raise SandboxFailure("Could not stage evaluator material")

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]:
        from minisweagent.exceptions import Submitted

        command = action.get("command")
        if not isinstance(command, str):
            raise SandboxFailure("Only one textual shell action is supported")
        result = self.shell(command, cwd=cwd or "/testbed")
        lines = result["output"].lstrip().splitlines(keepends=True)
        if result["returncode"] == 0 and lines and lines[0].strip() == SUBMIT_MARKER:
            submission = "".join(lines[1:])
            if len(submission.encode()) > MAX_PATCH_BYTES:
                raise SandboxFailure("Submitted patch exceeds the retained patch limit")
            if self.review_checkpoint and not self.review_used:
                self.review_used = True
                (self.output_dir / "first-submission.diff").write_text(submission)
                return {"output": REVIEW_MESSAGE, "returncode": 0, "exception_info": ""}
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )
        return result

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {"cwd": "/testbed", **kwargs}

    def serialize(self) -> dict[str, Any]:
        return {
            "environment": {
                "image_id": self.image,
                "docker_endpoint_mode": (
                    "explicit_local_unix"
                    if os.environ.get("CVALS_DOCKER_HOST")
                    else "context"
                    if self.context
                    else "docker_default"
                ),
                "network": "none",
                "host_mounts": False,
                "agent_uid": 1000,
                "review_checkpoint": self.review_checkpoint,
                "review_used": self.review_used,
                "command_timeout_seconds": self.command_timeout,
                "output_limit_bytes": MAX_OUTPUT_BYTES,
            }
        }

    def close(self) -> None:
        if not self.create_attempted:
            return
        try:
            removal = bounded_process(self._docker("rm", "--force", self.name), timeout=30)
        except Exception as exc:
            raise SandboxCleanupFailure("Owned container removal could not be confirmed") from exc
        confirmed = not removal["exception_info"] and (
            removal["returncode"] == 0 or "No such container: " + self.name in removal["output"]
        )
        _write_json(self.output_dir / "container-removal.json", {**removal, "confirmed": confirmed})
        if not confirmed:
            raise SandboxCleanupFailure("Owned container removal could not be confirmed")
        self.started = False
        self.create_attempted = False


def _close_and_record(sandbox: DockerSandbox, result: dict[str, Any], path: Path) -> None:
    cleanup_error: Exception | None = None
    try:
        sandbox.close()
        result["cleanup_confirmed"] = True
    except Exception as exc:
        cleanup_error = exc
        result.update(
            status="cleanup_error",
            cleanup_confirmed=False,
            cleanup_error_type=type(exc).__name__,
            resolved=False,
            submitted=False,
            patch="",
        )
    try:
        _write_json(path, result, exclusive=True)
    except Exception:
        if cleanup_error is not None:
            raise SandboxCleanupFailure(
                "Cleanup and evidence retention both failed; stop the campaign"
            ) from cleanup_error
        raise
    if cleanup_error is not None:
        raise SandboxCleanupFailure(
            "Owned container cleanup could not be confirmed; stop the campaign"
        ) from cleanup_error


def run_attempt(
    *,
    task: dict[str, Any],
    image: str,
    model: Any,
    output_dir: Path,
    arm: str,
    step_limit: int = 250,
    cost_limit: float = 6.0,
    wall_time_limit_seconds: int = 1800,
    command_timeout: int = 60,
) -> dict[str, Any]:
    """Retain one terminal or incomplete attempt; never retry a provider call."""
    if arm not in {"baseline", "everval"}:
        raise ValueError("Unknown comparison arm")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    _write_json(
        output_dir / "started.json",
        {"arm": arm, "instance_id": task["instance_id"]},
        exclusive=True,
    )
    config = baseline_config()
    agent_config = config["agent"].copy()
    agent_config.update(
        step_limit=step_limit,
        cost_limit=cost_limit,
        wall_time_limit_seconds=wall_time_limit_seconds,
        output_path=output_dir / "trajectory.json",
    )
    if arm == "everval":
        agent_config["instance_template"] += REVIEW_NOTICE
    from minisweagent.agents.default import DefaultAgent

    sandbox = DockerSandbox(
        image=image,
        output_dir=output_dir / "sandbox",
        command_timeout=command_timeout,
        review_checkpoint=arm == "everval",
    )
    agent = DefaultAgent(TextActionModel(model, config["model"]), sandbox, **agent_config)
    started = time.monotonic()
    result: dict[str, Any] = {
        "instance_id": task["instance_id"],
        "arm": arm,
        "exit_status": "not_started",
        "submission": "",
        "mini_revision": MINI_REVISION,
        "swebench_revision": SWEBENCH_REVISION,
        "image_id": image,
    }
    try:
        sandbox.start()
        if repository_setup.profile(task["instance_id"], task["base_commit"]) is None:
            sandbox.prepare_solver(task["base_commit"])
        else:
            sandbox.prepare_solver(task["base_commit"], remove_repository_setup=True)
        result.update(agent.run(task=task["problem_statement"]))
    except BaseException as exc:
        result.update(exit_status=type(exc).__name__, error_type=type(exc).__name__)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            result["interrupted"] = True
            raise
    finally:
        try:
            result["status"] = result["exit_status"]
            result["submitted"] = result["exit_status"] == "Submitted"
            if sandbox.started:
                with suppress(Exception):
                    partial = sandbox.shell("git diff --binary HEAD", max_bytes=MAX_PATCH_BYTES)
                    if partial["returncode"] == 0:
                        (output_dir / "filesystem-diff.diff").write_text(partial["output"])
            result["patch"] = result["submission"] if result["submitted"] else ""
            result["steps"] = agent.n_calls
            result["duration_seconds"] = time.monotonic() - started
            result["agent_stats"] = {"calls": agent.n_calls, "known_cost_usd": agent.cost}
            result["model_accounting"] = model.serialize()
            result["review_checkpoint_used"] = sandbox.review_used
            agent.save(output_dir / "trajectory.json")
        except BaseException as exc:
            result.update(
                status="evidence_error",
                evidence_error_type=type(exc).__name__,
                submitted=False,
                patch="",
            )
            raise
        finally:
            _close_and_record(sandbox, result, output_dir / "result.json")
    return result


def check_edit_policy(patch: str) -> list[str]:
    """Reject test/config/build edits without substring-matching source paths."""
    from pathlib import PurePosixPath

    from unidiff import PatchSet
    from unidiff.errors import UnidiffParseError

    reasons: set[str] = set()
    if not patch.strip():
        return []
    if chr(0) in patch or len(patch.encode()) > MAX_PATCH_BYTES:
        return ["invalid_patch_encoding_or_size"]
    if "GIT binary patch" in patch or re.search(r"(?m)^Binary files .* differ$", patch):
        reasons.add("binary_patch")
    if re.search(r"(?m)^(?:.*file mode |old mode |new mode |index .* )(?:120000|160000)$", patch):
        reasons.add("symlink_or_gitlink_patch")
    try:
        changes = PatchSet(patch)
    except (UnidiffParseError, ValueError) as exc:
        return ["unparseable_patch:" + type(exc).__name__]
    if not changes:
        reasons.add("no_file_changes")
    exact_names = {
        "conftest.py",
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "tox.ini",
        "pytest.ini",
        "Makefile",
        "makefile",
        "Dockerfile",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        ".coveragerc",
        ".gitignore",
        ".gitattributes",
        ".gitmodules",
        "MANIFEST.in",
    }
    for change in changes:
        for raw in (change.source_file, change.target_file):
            if raw == "/dev/null":
                continue
            name = raw[2:] if raw.startswith(("a/", "b/")) else raw
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                reasons.add("unsafe_path")
                continue
            if any(
                part in {".git", ".github", "tests", "test", "__tests__"} for part in path.parts
            ):
                reasons.add("protected_directory:" + name)
            if (
                path.name in exact_names
                or path.name.startswith("test_")
                or path.name.endswith("_test.py")
                or path.suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml"}
                or path.name.startswith("requirements")
            ):
                reasons.add("protected_file:" + name)
    return sorted(reasons)


def setup_error_signals(output: str) -> list[str]:
    """Identify explicit pre-test failures; absence is not a setup-exit proof."""
    prefix = output.split(">>>>> Start Test Output", 1)[0]
    patterns = {
        "installer_or_setup_error": r"(?m)^(?:ERROR:|CondaError:|EnvironmentNameNotFound:)",
        "git_error": r"(?m)^(?:error: |fatal: )",
        "missing_command": r"(?m)^.*: command not found$",
    }
    return sorted(name for name, pattern in patterns.items() if re.search(pattern, prefix))


def restore_grader_configuration(
    sandbox: DockerSandbox, task: dict[str, Any], output_dir: Path
) -> dict[str, str] | None:
    selected = repository_setup.profile(task["instance_id"], task["base_commit"])
    if selected is None:
        return None
    helper = Path(repository_setup.__file__).read_bytes()
    staged = output_dir / "repository-setup.py"
    staged.write_bytes(helper)
    staged.chmod(0o600)
    sandbox.put_file(staged, "/tmp/everval-repository-setup.py")
    applied = sandbox.shell(
        "/opt/miniconda3/envs/testbed/bin/python -I -S /tmp/everval-repository-setup.py "
        "apply --instance-id "
        + selected["instance_id"]
        + " --base-commit "
        + selected["base_commit"],
        user="0:0",
        timeout=120,
    )
    if applied["returncode"]:
        raise SandboxFailure("Could not restore verified upstream grader configuration")
    evidence = json.loads(applied["output"])
    if (
        not isinstance(evidence, dict)
        or set(evidence)
        != {
            "revision",
            "helper_sha256",
            "patch_sha256",
            "manifest_sha256",
            "source_dockerfile_sha256",
        }
        or evidence.get("revision") != repository_setup.REVISION
        or evidence.get("helper_sha256") != _digest(helper)
        or evidence.get("source_dockerfile_sha256") != selected["source_dockerfile_sha256"]
        or any(
            not isinstance(evidence.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", evidence[key]) is None
            for key in (
                "helper_sha256",
                "patch_sha256",
                "manifest_sha256",
                "source_dockerfile_sha256",
            )
        )
    ):
        raise SandboxFailure("Verified grader configuration evidence differs")
    return evidence


def grade_patch(
    *,
    task: dict[str, Any],
    image: str,
    patch: str,
    output_dir: Path,
    timeout: int = 600,
    enforce_edit_policy: bool = True,
) -> dict[str, Any]:
    """Grade exact official FTP/PTP with the official script and log parser."""
    _upstream()
    from swebench.harness.grading import get_eval_report, get_logs_eval, parse_test_exit_code
    from swebench.harness.utils import make_test_spec

    if len(patch.encode()) > MAX_PATCH_BYTES:
        raise ValueError("Patch exceeds the evaluator limit")
    if task.get("image_assets"):
        raise ValueError("This bounded runner does not support binary image assets")
    spec = make_test_spec(task)
    if spec.eval_type != "pass_and_fail" or not spec.FAIL_TO_PASS or not spec.PASS_TO_PASS:
        raise ValueError("This campaign requires explicit nonempty FTP and PTP test sets")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    _write_json(output_dir / "started.json", {"instance_id": task["instance_id"]}, exclusive=True)
    patch_path = output_dir / "candidate.diff"
    patch_path.write_text(patch, encoding="utf-8")
    eval_path = output_dir / "eval.sh"
    eval_path.write_text(spec.eval_script, encoding="utf-8")
    sandbox = DockerSandbox(image=image, output_dir=output_dir / "sandbox")
    result: dict[str, Any] = {
        "instance_id": task["instance_id"],
        "resolved": False,
        "status": "infrastructure_error",
        "patch_sha256": _digest(patch.encode()),
        "eval_script_sha256": _digest(spec.eval_script.encode()),
        "dataset_eval_script_sha256": _digest(task["eval_script"].encode()),
        "edit_policy_revision": EDIT_POLICY_REVISION,
        "edit_policy_enforced": enforce_edit_policy,
        "image_id": image,
        "swebench_revision": SWEBENCH_REVISION,
        "fail_to_pass_count": len(spec.FAIL_TO_PASS),
        "pass_to_pass_count": len(spec.PASS_TO_PASS),
        "all_required_tests_observed": False,
    }
    started = time.monotonic()
    try:
        policy_rejections = check_edit_policy(patch) if enforce_edit_policy else []
        if policy_rejections:
            result.update(status="invalid_patch", edit_policy_rejections=policy_rejections)
            return result
        sandbox.start()
        if re.fullmatch(r"[0-9a-f]{40}", task["base_commit"]) is None:
            raise ValueError("Invalid base commit")
        ready = sandbox.shell(
            f"git diff --exit-code {task['base_commit']} && "
            f"git reset --hard {task['base_commit']} && "
            "mkdir -p /tmp/everval-home",
            user="0:0",
        )
        if ready["returncode"]:
            raise SandboxFailure("Grader image is not at the frozen base")
        if patch.strip():
            sandbox.put_file(patch_path, "/tmp/everval-patch.diff")
            apply = sandbox.shell(
                "git -c core.hooksPath=/dev/null apply --check /tmp/everval-patch.diff && "
                "git -c core.hooksPath=/dev/null apply /tmp/everval-patch.diff",
                user="0:0",
            )
            if apply["returncode"]:
                result["status"] = "invalid_patch"
                return result
        configuration = restore_grader_configuration(sandbox, task, output_dir)
        if configuration is not None:
            result["repository_setup"] = configuration
        sandbox.put_file(eval_path, "/tmp/everval-eval.sh")
        execution = sandbox.shell(
            "bash /tmp/everval-eval.sh", user="0:0", timeout=timeout, max_bytes=16 * 1024 * 1024
        )
        log_path = output_dir / "test-output.txt"
        log_path.write_text(execution["output"], encoding="utf-8")
        prediction = {
            "instance_id": task["instance_id"],
            "model_name_or_path": "everval-local-comparison",
            "model_patch": patch,
        }
        official = get_eval_report(spec, prediction, str(log_path), include_tests_status=True)
        statuses, parsed = get_logs_eval(spec, str(log_path))
        required = set(spec.FAIL_TO_PASS) | set(spec.PASS_TO_PASS)
        observed = parsed and required <= statuses.keys()
        setup_errors = setup_error_signals(execution["output"])
        required_passed = observed and all(statuses[test] == "PASSED" for test in required)
        result.update(
            status="graded"
            if parsed and execution["returncode"] == 0 and not setup_errors
            else "infrastructure_error",
            resolved=bool(official[task["instance_id"]]["resolved"])
            and required_passed
            and execution["returncode"] == 0
            and not setup_errors,
            official_report=official,
            setup_error_signals=setup_errors,
            all_setup_exit_codes_independently_recorded=False,
            test_statuses=statuses,
            all_required_tests_observed=observed,
            all_required_tests_passed=required_passed,
            missing_required_tests=sorted(required - statuses.keys()),
            container_script_returncode=execution["returncode"],
            test_suite_exit_code=parse_test_exit_code(execution["output"]),
            observed_status_counts={
                status: list(statuses.values()).count(status)
                for status in sorted(set(statuses.values()))
            },
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        result["duration_seconds"] = time.monotonic() - started
        _close_and_record(sandbox, result, output_dir / "result.json")
    return result


def validate_controls(
    *, task: dict[str, Any], image: str, output_dir: Path, timeout: int = 600
) -> dict[str, Any]:
    """Require every official FTP to fail before and pass after, with all PTP."""
    output_dir = Path(output_dir)
    base = grade_patch(
        task=task, image=image, patch="", output_dir=output_dir / "base", timeout=timeout
    )
    gold = grade_patch(
        task=task, image=image, patch=task["patch"], output_dir=output_dir / "gold", timeout=timeout
    )
    before = base.get("test_statuses", {})
    after = gold.get("test_statuses", {})
    gold_policy_rejections = check_edit_policy(task["patch"])
    valid = (
        not gold_policy_rejections
        and base["status"] == "graded"
        and gold["status"] == "graded"
        and base["all_required_tests_observed"]
        and gold["all_required_tests_observed"]
        and all(before.get(test) == "FAILED" for test in task["FAIL_TO_PASS"])
        and all(before.get(test) == "PASSED" for test in task["PASS_TO_PASS"])
        and all(after.get(test) == "PASSED" for test in task["FAIL_TO_PASS"])
        and all(after.get(test) == "PASSED" for test in task["PASS_TO_PASS"])
        and not base["resolved"]
        and gold["resolved"]
    )
    result = {
        "instance_id": task["instance_id"],
        "valid": valid,
        "base": base,
        "gold": gold,
        "base_resolved": base["resolved"],
        "gold_resolved": gold["resolved"],
        "base_status": base["status"],
        "gold_status": gold["status"],
        "image_id": image,
        "fail_to_pass_count": len(task["FAIL_TO_PASS"]),
        "pass_to_pass_count": len(task["PASS_TO_PASS"]),
        "controls_are_stricter_than_official_skip_xfail_semantics": True,
        "gold_edit_policy_rejections": gold_policy_rejections,
        "edit_policy_revision": EDIT_POLICY_REVISION,
    }
    _write_json(output_dir / "controls.json", result, exclusive=True)
    return result


def run_controls(
    *,
    campaign_root: Path,
    instance_id: str | None = None,
    run_id: str = "initial",
    timeout: int = 600,
) -> list[dict[str, Any]]:
    """Hold the shared preparation lock until canonical controls are published."""
    from .freeze import preparation_lock

    root = Path(campaign_root)
    with preparation_lock(root):
        return _run_controls_locked(
            campaign_root=root, instance_id=instance_id, run_id=run_id, timeout=timeout
        )


def _run_controls_locked(
    *,
    campaign_root: Path,
    instance_id: str | None = None,
    run_id: str = "initial",
    timeout: int = 600,
) -> list[dict[str, Any]]:
    """Validate prepared tasks serially and retain every explicitly named run."""
    root = Path(campaign_root)
    if (root / "campaign.json").exists():
        raise ValueError("A frozen campaign prevents further control mutation")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", run_id) is None:
        raise ValueError("Invalid control run identity")
    if not 1 <= timeout <= 3600:
        raise ValueError("Control timeout must be between 1 and 3600 seconds")
    _upstream()
    index = json.loads((root / "selection/selection-private-index.json").read_text())
    selection = (root / "selection/selection-public.json").read_bytes()
    if _digest(selection) != index["selection_sha256"]:
        raise ValueError("Frozen selection digest changed")
    images = json.loads((root / "runtime-images.json").read_text())
    entries = index["tasks"]
    if instance_id is not None:
        entries = [entry for entry in entries if entry["instance_id"] == instance_id]
        if len(entries) != 1:
            raise ValueError("Instance is not uniquely present in the frozen selection")
    validated = []
    # Validate every requested preparation and input before starting any container.
    for entry in entries:
        identifier = entry["instance_id"]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identifier) is None:
            raise ValueError("Invalid instance identity")
        path = root / "selection/tasks-private" / (identifier + ".json")
        data = path.read_bytes()
        if _digest(data) != entry["task_sha256"]:
            raise ValueError("Frozen task digest changed: " + identifier)
        task = json.loads(data)
        if task["instance_id"] != identifier:
            raise ValueError("Task identity mismatch")
        preparation = images.get(identifier, {})
        if preparation.get("preparation_status") not in {"ready", "failed"}:
            raise ValueError("Preparation is not terminal: " + identifier)
        run_dir = root / "controls" / identifier / "runs" / run_id
        if run_dir.exists():
            raise FileExistsError("Control run already exists: " + identifier + "/" + run_id)
        validated.append((entry, task, preparation, run_dir))
    results = []
    for entry, task, preparation, run_dir in validated:
        run_dir.mkdir(parents=True, mode=0o700)
        if preparation["preparation_status"] == "failed":
            result = {
                "instance_id": task["instance_id"],
                "valid": False,
                "reason": "preparation_failed",
                "preparation_reason": preparation.get("reason"),
                "base": {"status": "not_prepared", "resolved": False},
                "gold": {"status": "not_prepared", "resolved": False},
                "image_id": preparation.get("image_id"),
            }
            _write_json(run_dir / "controls.json", result, exclusive=True)
        else:
            result = validate_controls(
                task=task,
                image=preparation["image_id"],
                output_dir=run_dir,
                timeout=timeout,
            )
        run_record = run_dir / "controls.json"
        bound = {
            **result,
            "control_run_id": run_id,
            "control_run_path": str(run_record.relative_to(root)),
            "control_run_sha256": _digest(run_record.read_bytes()),
            "task_sha256": entry["task_sha256"],
            "selection_sha256": index["selection_sha256"],
            "executor_sha256": _digest(Path(__file__).read_bytes()),
            "preparation_sha256": _digest(
                json.dumps(preparation, sort_keys=True, separators=(",", ":")).encode()
            ),
        }
        destination = root / "controls" / task["instance_id"] / "controls.json"
        temporary = destination.with_name("controls." + uuid.uuid4().hex + ".tmp")
        _write_json(temporary, bound, exclusive=True)
        os.replace(temporary, destination)
        results.append(bound)
    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docker-host",
        help="Explicit local Unix Docker endpoint; otherwise CVALS_DOCKER_HOST or Docker default",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    controls = subparsers.add_parser("controls", help="Run provider-free frozen base/gold controls")
    controls.add_argument("--campaign-root", type=Path, required=True)
    controls.add_argument("--instance-id")
    controls.add_argument("--run-id", default="initial")
    controls.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.docker_host:
        if not args.docker_host.startswith("unix:///"):
            parser.error("Only local Unix Docker endpoints are supported")
        os.environ["CVALS_DOCKER_HOST"] = args.docker_host
    results = run_controls(
        campaign_root=args.campaign_root,
        instance_id=args.instance_id,
        run_id=args.run_id,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                "provider_calls": 0,
                "controls": [
                    {"instance_id": row["instance_id"], "valid": row["valid"]} for row in results
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
