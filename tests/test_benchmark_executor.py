"""Provider-free checks for the separate public-repository benchmark executor."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmarks.executor import (  # noqa: E402
    REVIEW_MESSAGE,
    DockerSandbox,
    SandboxFailure,
    TextActionModel,
    baseline_config,
    bounded_process,
    check_edit_policy,
    grade_patch,
    run_attempt,
    validate_controls,
)


class TextModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def query(self, messages: list[dict]) -> dict:
        self.calls += 1
        return {
            "role": "assistant",
            "content": next(self.responses),
            "extra": {"cost": 0.25, "finish_reason": "stop", "request_id": str(self.calls)},
        }

    def get_template_vars(self) -> dict:
        return {"model_name": "provider-free-fixture"}

    def serialize(self) -> dict:
        return {"calls": self.calls}

    @staticmethod
    def format_message(**kwargs: object) -> dict:
        return dict(kwargs)


@pytest.mark.usefixtures("optional_upstream")
def test_upstream_parser_retains_cost_when_billed_output_is_malformed() -> None:
    from minisweagent.exceptions import FormatError

    model = TextActionModel(TextModel(["No action"]), baseline_config()["model"])
    with pytest.raises(FormatError) as caught:
        model.query([])
    assert caught.value.messages[0]["extra"]["cost"] == 0.25
    assert caught.value.messages[0]["extra"]["request_id"] == "1"
    assert caught.value.messages[0]["extra"]["n_actions"] == 0


@pytest.mark.usefixtures("optional_upstream")
def test_upstream_text_action_and_observation_are_used() -> None:
    model = TextActionModel(
        TextModel(["THOUGHT: Inspect.\n```mswea_bash_command\nls -la\n```"]),
        baseline_config()["model"],
    )
    message = model.query([])
    assert message["extra"]["actions"] == [{"command": "ls -la"}]
    observations = model.format_observation_messages(
        message, [{"output": "file.py", "returncode": 0, "exception_info": ""}]
    )
    assert "<returncode>0</returncode>" in observations[0]["content"]
    assert "<output>\nfile.py" in observations[0]["content"]


def test_output_ceiling_kills_the_producer() -> None:
    result = bounded_process(
        [sys.executable, "-c", "import os,time; os.write(1,b'x'*50000); time.sleep(20)"],
        timeout=5,
        max_bytes=1024,
    )
    assert result["exception_info"] == "Command exceeded its output limit"
    assert len(result["output"]) == 1024
    assert result["duration_seconds"] < 5
    assert result["returncode"] != 0


def test_timeout_kills_the_producer() -> None:
    result = bounded_process([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.15)
    assert result["exception_info"] == "Command exceeded its time limit"
    assert result["duration_seconds"] < 3


@pytest.mark.usefixtures("optional_upstream")
def test_checkpoint_uses_same_loop_and_respects_remaining_step_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from minisweagent.agents.default import DefaultAgent

    sandbox = DockerSandbox(
        image="sha256:" + "a" * 64,
        output_dir=tmp_path,
        review_checkpoint=True,
    )
    monkeypatch.setattr(
        sandbox,
        "shell",
        lambda *args, **kwargs: {
            "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\ncandidate",
            "returncode": 0,
            "exception_info": "",
        },
    )
    model = TextModel(["```mswea_bash_command\necho completion\n```"])
    config = baseline_config()
    agent = DefaultAgent(
        TextActionModel(model, config["model"]),
        sandbox,
        **{**config["agent"], "step_limit": 1},
    )
    result = agent.run(task="Fix the described behavior")
    assert result["exit_status"] == "LimitsExceeded"
    assert result["submission"] == ""
    assert sandbox.review_used
    assert any(REVIEW_MESSAGE in message["content"] for message in agent.messages)
    assert agent.n_calls == 1
    assert agent.cost == 0.25
    assert (tmp_path / "first-submission.diff").read_text() == "candidate"


@pytest.mark.usefixtures("optional_upstream")
def test_second_completion_submits_without_resetting_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from minisweagent.agents.default import DefaultAgent

    sandbox = DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path, review_checkpoint=True)
    results = iter(["first-patch", "reviewed-patch"])
    monkeypatch.setattr(
        sandbox,
        "shell",
        lambda *args, **kwargs: {
            "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n" + next(results),
            "returncode": 0,
            "exception_info": "",
        },
    )
    model = TextModel(["```mswea_bash_command\necho done\n```"] * 2)
    config = baseline_config()
    agent = DefaultAgent(
        TextActionModel(model, config["model"]),
        sandbox,
        **{**config["agent"], "step_limit": 2},
    )
    result = agent.run(task="Fix the described behavior")
    assert result["submission"] == "reviewed-patch"
    assert agent.n_calls == 2
    assert agent.cost == 0.5


@pytest.mark.usefixtures("optional_upstream")
def test_failed_sandbox_is_retained_without_model_calls_or_submitted_patch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_start(self: DockerSandbox) -> None:
        raise SandboxFailure("unavailable fixture")

    monkeypatch.setattr(DockerSandbox, "start", fail_start)
    monkeypatch.setattr(DockerSandbox, "close", lambda self: None)
    model = TextModel([])
    result = run_attempt(
        task={"instance_id": "fixture", "base_commit": "a" * 40, "problem_statement": "Fix"},
        image="sha256:" + "b" * 64,
        model=model,
        output_dir=tmp_path,
        arm="baseline",
    )
    assert model.calls == 0
    assert result["exit_status"] == "SandboxFailure"
    assert not result["submitted"]
    assert result["patch"] == ""
    assert json.loads((tmp_path / "result.json").read_text()) == result
    assert (tmp_path / "trajectory.json").is_file()
    with pytest.raises(FileExistsError):
        run_attempt(
            task={"instance_id": "fixture"},
            image="sha256:" + "b" * 64,
            model=model,
            output_dir=tmp_path,
            arm="baseline",
        )


@pytest.mark.parametrize("missing,wrong_before", [(True, False), (False, True), (False, False)])
def test_control_admission_requires_all_official_tests_and_actual_fail_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: bool, wrong_before: bool
) -> None:
    task = {"instance_id": "fixture", "patch": "", "FAIL_TO_PASS": ["f"], "PASS_TO_PASS": ["p"]}

    def grade(**kwargs: object) -> dict:
        gold = Path(kwargs["output_dir"]).name == "gold"
        return {
            "status": "graded",
            "resolved": gold,
            "all_required_tests_observed": not missing,
            "test_statuses": {"f": "PASSED" if gold or wrong_before else "FAILED", "p": "PASSED"},
        }

    monkeypatch.setattr("scripts.benchmarks.executor.grade_patch", grade)
    # This case tests admission from observed grades; patch parsing has its own cases.
    monkeypatch.setattr("scripts.benchmarks.executor.check_edit_policy", lambda patch: [])
    result = validate_controls(task=task, image="sha256:" + "a" * 64, output_dir=tmp_path)
    assert result["valid"] == (not missing and not wrong_before)
    assert result["base"]["resolved"] is False
    assert result["gold"]["resolved"] is True


def test_image_requires_local_immutable_digest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="immutable"):
        DockerSandbox(image="python:latest", output_dir=tmp_path)


@pytest.mark.skipif(
    not os.getenv("EVERVAL_BENCHMARK_DOCKER_TEST_IMAGE"), reason="explicit Docker smoke image"
)
def test_real_sandbox_has_no_network_mounts_and_destroys_timed_out_command(tmp_path: Path) -> None:
    sandbox = DockerSandbox(
        image=os.environ["EVERVAL_BENCHMARK_DOCKER_TEST_IMAGE"],
        output_dir=tmp_path,
        command_timeout=1,
    )
    try:
        sandbox.start()
        inspect = json.loads((tmp_path / "container-inspect.json").read_text())[0]
        assert inspect["HostConfig"]["NetworkMode"] == "none"
        assert inspect["HostConfig"]["Binds"] is None
        assert inspect["Mounts"] == []
        assert inspect["HostConfig"]["CapDrop"] == ["ALL"]
        assert inspect["HostConfig"]["Memory"] == 1536 * 1024 * 1024
        sandbox.shell("mkdir -p /testbed /tmp/everval-home", user="0:0")
        identity = sandbox.shell("id -u; test ! -e /var/run/docker.sock")
        assert identity["returncode"] == 0
        assert identity["output"].strip() == "1000"
        with pytest.raises(SandboxFailure, match="time limit"):
            sandbox.shell("sleep 30")
        assert not sandbox.started
        inspected = bounded_process(sandbox._docker("inspect", sandbox.name), timeout=10)
        assert inspected["returncode"] != 0
    finally:
        sandbox.close()


def _patch(path: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("src/_pytest/config/__init__.py", True),
        ("sphinx/testing/util.py", True),
        ("requests/utils.py", True),
        ("tests/test_requests.py", False),
        ("conftest.py", False),
        ("pyproject.toml", False),
        ("requirements-dev.txt", False),
        ("../../escape.py", False),
    ],
)
@pytest.mark.usefixtures("optional_patch_parser")
def test_edit_policy_preserves_source_paths_and_rejects_test_or_config_edits(
    path: str, allowed: bool
) -> None:
    assert bool(check_edit_policy(_patch(path))) is not allowed


@pytest.mark.parametrize(
    "returncode,missing,ftp_status,ptp_status",
    [
        (7, False, "PASSED", "PASSED"),
        (0, True, "PASSED", "PASSED"),
        (0, False, "PASSED", "PASSED"),
        (0, False, "XFAIL", "PASSED"),
        (0, False, "PASSED", "SKIPPED"),
    ],
)
@pytest.mark.usefixtures("optional_upstream")
def test_official_grades_need_complete_cases_and_clean_evaluator_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    missing: bool,
    ftp_status: str,
    ptp_status: str,
) -> None:
    task = {
        "instance_id": "fixture-1",
        "image": "fixture",
        "repo": "fixture/repo",
        "version": "1",
        "base_commit": "a" * 40,
        "FAIL_TO_PASS": ["tests/test_a.py::test_fix"],
        "PASS_TO_PASS": ["tests/test_a.py::test_old"],
        "log_parser": "parse_log_pytest",
        "eval_type": "pass_and_fail",
        "eval_script": "#!/bin/bash\n: '>>>>> Start Test Output'\npytest -rA\n: '>>>>> End Test Output'\n",
    }
    output = (
        f">>>>> Start Test Output\n{ftp_status} tests/test_a.py::test_fix\n"
        + ("" if missing else f"{ptp_status} tests/test_a.py::test_old\n")
        + ">>>>> End Test Output\n>>>>> Test Exit Code: 0\n"
    )
    monkeypatch.setattr(DockerSandbox, "start", lambda self: None)
    monkeypatch.setattr(DockerSandbox, "close", lambda self: None)
    monkeypatch.setattr(DockerSandbox, "put_file", lambda *args: None)

    def shell(self: DockerSandbox, command: str, **kwargs: object) -> dict:
        grading = command == "bash /tmp/everval-eval.sh"
        return {
            "output": output if grading else "",
            "returncode": returncode if grading else 0,
            "exception_info": "",
        }

    monkeypatch.setattr(DockerSandbox, "shell", shell)
    result = grade_patch(
        task=task, image="sha256:" + "a" * 64, patch=_patch("module.py"), output_dir=tmp_path
    )
    expected = returncode == 0 and not missing and ftp_status == ptp_status == "PASSED"
    assert result["resolved"] == expected
    assert result["all_required_tests_passed"] == (
        not missing and ftp_status == ptp_status == "PASSED"
    )
    assert result["all_required_tests_observed"] is not missing
    assert result["status"] == ("infrastructure_error" if returncode else "graded")
    assert result["official_report"]["fixture-1"]["resolved"] is not missing
    assert result["dataset_eval_script_sha256"] != result["eval_script_sha256"]


@pytest.mark.usefixtures("optional_upstream")
def test_upstream_source_hash_mismatch_blocks_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.benchmarks import executor

    executor._upstream.cache_clear()
    monkeypatch.setitem(executor.UPSTREAM_FILE_HASHES, "minisweagent/agents/default.py", "0" * 64)
    with pytest.raises(RuntimeError, match="Pinned upstream file differs"):
        executor._upstream()
    executor._upstream.cache_clear()


def test_docker_endpoint_is_portable_and_rejects_remote_daemons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sandbox = DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path)
    monkeypatch.delenv("CVALS_DOCKER_HOST", raising=False)
    assert sandbox._docker("version") == ["docker", "version"]
    monkeypatch.setenv("CVALS_DOCKER_HOST", "unix:///tmp/fixture.sock")
    assert sandbox._docker("version") == ["docker", "--host", "unix:///tmp/fixture.sock", "version"]
    monkeypatch.setenv("CVALS_DOCKER_HOST", "tcp://example.com:1234")
    with pytest.raises(ValueError, match="local Unix"):
        sandbox._docker("version")


def test_controls_keep_failed_preparation_and_refuse_rewrite_or_post_freeze_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib

    from scripts.benchmarks import executor

    tasks_dir = tmp_path / "selection/tasks-private"
    tasks_dir.mkdir(parents=True)
    selection = b'{"fixture":true}\n'
    (tmp_path / "selection/selection-public.json").write_bytes(selection)
    task_bytes = b'{"instance_id":"fixture-1"}\n'
    (tasks_dir / "fixture-1.json").write_bytes(task_bytes)
    index = {
        "selection_sha256": hashlib.sha256(selection).hexdigest(),
        "tasks": [
            {"instance_id": "fixture-1", "task_sha256": hashlib.sha256(task_bytes).hexdigest()}
        ],
    }
    (tmp_path / "selection/selection-private-index.json").write_text(json.dumps(index))
    (tmp_path / "runtime-images.json").write_text(
        json.dumps(
            {"fixture-1": {"preparation_status": "failed", "reason": "missing_native_build"}}
        )
    )
    monkeypatch.setattr(executor, "_upstream", lambda: None)
    results = executor.run_controls(campaign_root=tmp_path)
    assert not results[0]["valid"]
    assert results[0]["reason"] == "preparation_failed"
    evidence = tmp_path / results[0]["control_run_path"]
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == results[0]["control_run_sha256"]
    canonical = tmp_path / "controls/fixture-1/controls.json"
    before = canonical.read_bytes()
    with pytest.raises(FileExistsError):
        executor.run_controls(campaign_root=tmp_path)
    assert canonical.read_bytes() == before
    (tmp_path / "campaign.json").write_text("{}")
    with pytest.raises(ValueError, match="frozen campaign"):
        executor.run_controls(campaign_root=tmp_path, run_id="second")
    assert canonical.read_bytes() == before


@pytest.mark.parametrize("mode", ["120000", "160000"])
@pytest.mark.usefixtures("optional_patch_parser")
def test_symlink_and_gitlink_patches_are_rejected(mode: str) -> None:
    patch = (
        "diff --git a/link.py b/link.py\n"
        f"new file mode {mode}\n"
        "--- /dev/null\n+++ b/link.py\n"
        "@@ -0,0 +1 @@\n+/tmp/target\n"
    )
    assert "symlink_or_gitlink_patch" in check_edit_policy(patch)


@pytest.mark.usefixtures("optional_upstream")
def test_trajectory_write_failure_still_destroys_owned_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from minisweagent.agents.default import DefaultAgent

    closed = []
    monkeypatch.setattr(DockerSandbox, "start", lambda self: None)
    monkeypatch.setattr(DockerSandbox, "prepare_solver", lambda *args: None)
    monkeypatch.setattr(DockerSandbox, "close", lambda self: closed.append(self.name))
    monkeypatch.setattr(
        DefaultAgent,
        "run",
        lambda *args, **kwargs: {"exit_status": "Submitted", "submission": ""},
    )

    def fail_save(*args: object, **kwargs: object) -> None:
        raise OSError("fixture evidence disk failure")

    monkeypatch.setattr(DefaultAgent, "save", fail_save)
    with pytest.raises(OSError, match="fixture evidence disk failure"):
        run_attempt(
            task={"instance_id": "fixture-1", "base_commit": "a" * 40, "problem_statement": "Fix"},
            image="sha256:" + "b" * 64,
            model=TextModel([]),
            output_dir=tmp_path,
            arm="baseline",
        )
    assert len(closed) == 1
    assert (tmp_path / "result.json").is_file()


@pytest.fixture
def optional_upstream() -> None:
    missing = [
        name
        for name in ("minisweagent", "swebench", "unidiff")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        pytest.skip("separately pinned optional benchmark tooling: " + ", ".join(missing))
    from scripts.benchmarks.executor import _upstream

    _upstream()


@pytest.fixture
def optional_patch_parser() -> None:
    if importlib.util.find_spec("unidiff") is None:
        pytest.skip("optional benchmark patch parser: unidiff")


def test_unconfirmed_container_removal_is_retained_and_propagated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.benchmarks import executor

    sandbox = DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path / "sandbox")
    sandbox.create_attempted = True
    monkeypatch.setattr(
        executor,
        "bounded_process",
        lambda *args, **kwargs: {
            "returncode": 1,
            "exception_info": "",
            "output": "Cannot connect to Docker",
        },
    )
    result = {"status": "Submitted", "submitted": True, "patch": "candidate"}
    with pytest.raises(executor.SandboxCleanupFailure):
        executor._close_and_record(sandbox, result, tmp_path / "result.json")
    retained = json.loads((tmp_path / "result.json").read_text())
    assert retained["status"] == "cleanup_error"
    assert retained["cleanup_confirmed"] is False
    assert retained["submitted"] is False
    assert retained["patch"] == ""


def test_default_remote_docker_context_is_rejected_before_container_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.benchmarks import executor

    sandbox = DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path)
    monkeypatch.delenv("CVALS_DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(
        executor,
        "bounded_process",
        lambda *args, **kwargs: {
            "returncode": 0,
            "exception_info": "",
            "output": "ssh://remote.example\n",
        },
    )
    with pytest.raises(SandboxFailure, match="local Unix"):
        sandbox.start()
    assert not sandbox.create_attempted


def test_failed_result_write_cannot_mask_unconfirmed_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.benchmarks import executor

    sandbox = DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path)

    def broken_close() -> None:
        raise OSError("fixture removal log failure")

    def broken_write(*args: object, **kwargs: object) -> None:
        raise OSError("fixture result disk failure")

    monkeypatch.setattr(sandbox, "close", broken_close)
    monkeypatch.setattr(executor, "_write_json", broken_write)
    with pytest.raises(executor.SandboxCleanupFailure, match="both failed"):
        executor._close_and_record(sandbox, {}, tmp_path / "result.json")


def test_setup_failure_signals_are_separate_from_expected_test_failures() -> None:
    from scripts.benchmarks.executor import setup_error_signals

    prefix = "ERROR: Could not build wheels for fixture\n"
    tests = ">>>>> Start Test Output\nERROR: a failing test\n>>>>> End Test Output\n"
    assert setup_error_signals(prefix + tests) == ["installer_or_setup_error"]
    assert setup_error_signals("Successfully installed fixture\n" + tests) == []
    assert setup_error_signals("fatal: patch could not be applied\n" + tests) == ["git_error"]


def test_controls_cannot_race_with_preparation_or_freezing(tmp_path: Path) -> None:
    from scripts.benchmarks.executor import run_controls
    from scripts.benchmarks.freeze import preparation_lock

    with (
        preparation_lock(tmp_path),
        pytest.raises(ValueError, match="Another preparation or freezing process"),
    ):
        run_controls(campaign_root=tmp_path)
    assert not (tmp_path / "controls").exists()
    # The lock is released after the failed admission; no selected data was read.
    with preparation_lock(tmp_path):
        assert not (tmp_path / "campaign.json").exists()


def test_private_evaluator_transfer_normalizes_archive_without_relaxing_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import scripts.benchmarks.executor as executor

    source = tmp_path / "private.sh"
    source.write_bytes(b"private evaluator fixture\n")
    source.chmod(0o600)
    sandbox = DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path / "sandbox")
    sandbox.resolved_endpoint = "unix:///fixture/docker.sock"
    calls = []

    def transfer(argv: list[str], **kwargs: object) -> dict:
        calls.append((argv, kwargs))
        return {"returncode": 0, "exception_info": "", "output": ""}

    monkeypatch.setattr(executor, "bounded_process", transfer)
    sandbox.put_file(source, "/tmp/everval-eval.sh")
    assert source.stat().st_mode & 0o777 == 0o600
    assert source.read_bytes() == b"private evaluator fixture\n"
    argv, options = calls[0]
    assert argv[-3:] == ["cp", "-", sandbox.name + ":/tmp/"]
    archive_path = options["input_path"]
    assert isinstance(archive_path, Path)
    assert archive_path.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive_path) as archive:
        (member,) = archive.getmembers()
        assert (member.name, member.uid, member.gid, member.mode) == (
            "everval-eval.sh",
            0,
            0,
            0o600,
        )
        stream = archive.extractfile(member)
        assert stream is not None
        assert stream.read() == source.read_bytes()


def test_bounded_process_reads_explicit_input_file(tmp_path: Path) -> None:
    source = tmp_path / "stdin"
    source.write_bytes(b"bounded input")
    result = bounded_process(
        [sys.executable, "-c", "import sys; print(sys.stdin.buffer.read().decode())"],
        timeout=5,
        input_path=source,
    )
    assert result["returncode"] == 0
    assert result["exception_info"] == ""
    assert result["output"] == "bounded input\n"


@pytest.mark.skipif(
    not os.getenv("EVERVAL_BENCHMARK_DOCKER_TEST_IMAGE"), reason="explicit Docker smoke image"
)
def test_real_sandbox_stages_private_evaluator_material_for_root_only(tmp_path: Path) -> None:
    source = tmp_path / "private-evaluator.sh"
    source.write_text("printf 'private grader fixture\\n'\n")
    source.chmod(0o600)
    sandbox = DockerSandbox(
        image=os.environ["EVERVAL_BENCHMARK_DOCKER_TEST_IMAGE"],
        output_dir=tmp_path / "sandbox",
    )
    try:
        sandbox.start()
        sandbox.put_file(source, "/tmp/everval-eval.sh")
        grader = sandbox.shell(
            "stat -c '%u:%g %a' /tmp/everval-eval.sh; bash /tmp/everval-eval.sh",
            user="0:0",
        )
        assert grader["returncode"] == 0
        assert grader["output"] == "0:0 600\nprivate grader fixture\n"
        solver = sandbox.shell("test ! -r /tmp/everval-eval.sh", user="1000:1000")
        assert solver["returncode"] == 0
        assert source.stat().st_mode & 0o777 == 0o600
    finally:
        sandbox.close()
