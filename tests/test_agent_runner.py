"""Tool loops retain accounting, isolate private graders and submit actual edits."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import httpx
import pytest

from cvals.agent_runner import MAX_TOOL_OUTPUT, PublicWorkspace, reservation
from cvals.config import Settings
from cvals.evaluators import evaluate
from cvals.models import Check, Scenario, System, canonical_json
from cvals.providers import MAX_PROMPT_BYTES, reserve_cost, submit

DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def scenario():
    return Scenario(
        id="repair",
        name="Repair totals",
        domain="coding",
        instruction="Correct numeric totals. Return the repaired public files.",
        inputs={
            "files": {
                "solution.py": "def total(values):\n    return len(values)\n",
                "obsolete.py": "unused = True\n",
            }
        },
        checks=[
            Check(
                id="secret",
                name="Private behavior",
                kind="python_tests",
                path="files",
                expected={
                    "entrypoint": "solution:total",
                    "cases": [{"args": [[2, 3]], "expected": 5}],
                },
            )
        ],
        expected={"value": "CANARY_EXPECTED"},
        reference_output={"files": {"solution.py": "CANARY_REFERENCE"}},
        provenance={"private": "CANARY_PROVENANCE"},
    )


@pytest.fixture
def system():
    return System(
        name="Local tools",
        kind="http",
        harness="tools",
        model="fixture-model",
        base_url="http://127.0.0.1:9000/v1",
        max_steps=5,
        max_tokens=100,
        input_cost_per_million=2,
        output_cost_per_million=4,
    )


def call(name: str, arguments: dict, identifier: str = "call-1") -> dict:
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": canonical_json(arguments)},
    }


def response(calls: list | None = None, **overrides: Any) -> dict:
    return {
        "choices": [
            {
                "message": {"content": None if calls else "Complete", "tool_calls": calls},
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        **overrides,
    }


def transport(monkeypatch: pytest.MonkeyPatch, responses: list, captured: list) -> None:
    client = httpx.Client

    def handler(request):
        captured.append(json.loads(request.content))
        item = responses[len(captured) - 1]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    monkeypatch.setattr(
        "cvals.agent_runner.httpx.Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_tool_loop_edits_final_snapshot_and_every_request_excludes_private_material(
    scenario, system, tmp_path, monkeypatch
):
    captured = []
    transport(
        monkeypatch,
        [
            response([call("read_file", {"path": "solution.py"})]),
            response(
                [
                    call(
                        "write_file",
                        {
                            "path": "solution.py",
                            "content": "def total(values):\n    return sum(values)\n",
                        },
                        "write",
                    ),
                    call("delete_file", {"path": "obsolete.py"}, "delete"),
                ]
            ),
            response(),
        ],
        captured,
    )
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert result.error is None
    assert result.output == {
        "files": {"solution.py": "def total(values):\n    return sum(values)\n"},
        "replace_files": True,
    }
    assert result.input_tokens == 30 and result.output_tokens == 15
    assert result.cost_usd == pytest.approx(0.00012)
    for request in captured:
        encoded = canonical_json(request)
        assert "CANARY_" not in encoded and "reference_output" not in encoded
        assert (
            '"expected"' not in encoded
            and '"checks"' not in encoded
            and '"provenance"' not in encoded
        )
        assert request["parallel_tool_calls"] is False and request["store"] is False
    assert len([step for step in result.metadata["trace"] if "step" in step]) == 3

    graded_files = []

    def grade(files, *args):
        graded_files.append(files)
        return {"passed": True, "tests_run": 1, "image_digest": DIGEST}

    monkeypatch.setattr("cvals.sandbox.run_python_cases", grade)
    assert evaluate(scenario, result.output, Settings(tmp_path))[0]["passed"]
    assert "obsolete.py" not in graded_files[0]
    assert "obsolete.py" in scenario.inputs["files"]  # The frozen scenario was never edited.


@pytest.mark.parametrize(
    "path",
    [
        "../private.py",
        "/etc/passwd",
        ".git/config",
        "a/../x.py",
        "a\\x.py",
        "__cvals_grader.py",
        "sitecustomize.py",
    ],
)
def test_workspace_path_traversal_is_rejected_without_mutation(scenario, tmp_path, path):
    workspace = PublicWorkspace(scenario, Settings(tmp_path))
    original = dict(workspace.files)
    with pytest.raises(ValueError):
        workspace.dispatch("write_file", {"path": path, "content": "pass"}, 30)
    assert workspace.files == original
    with pytest.raises(ValueError, match="public snapshot"):
        workspace.dispatch("read_file", {"path": path}, 30)


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_file", {"path": []}),
        ("delete_file", {"path": {}}),
        ("read_file", {"path": "solution.py", "offset": True}),
        ("read_file", {"path": "solution.py", "limit": 16001}),
        ("read_file", {"path": "solution.py", "unexpected": 1}),
        ("write_file", {"path": "solution.py"}),
        ("list_files", {"extra": 1}),
        ("search", {"text": ""}),
    ],
)
def test_tool_arguments_match_declared_contract(scenario, tmp_path, name, args):
    with pytest.raises(ValueError):
        PublicWorkspace(scenario, Settings(tmp_path)).dispatch(name, args, 30)


def test_unknown_usage_stops_before_another_request_and_preserves_known_lower_bound(
    scenario, system, tmp_path, monkeypatch
):
    captured = []
    transport(
        monkeypatch,
        [
            response([call("list_files", {})]),
            response([call("read_file", {"path": "solution.py"})], usage=None),
        ],
        captured,
    )
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert len(captured) == 2 and result.error
    assert result.input_tokens is None and result.output_tokens is None and result.cost_usd is None
    assert result.metadata["known_cost_usd"] == pytest.approx(0.00004)
    assert result.metadata["budget_uncertain"]
    assert len([row for row in result.metadata["trace"] if "tool" in row]) == 1


def test_known_usage_survives_malformed_tool_envelope(scenario, system, tmp_path, monkeypatch):
    captured = []
    transport(monkeypatch, [response(choices=[])], captured)
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert len(captured) == 1 and result.error
    assert result.cost_usd == pytest.approx(0.00004)
    assert result.input_tokens == 10 and result.output_tokens == 5


def test_duplicate_tool_call_ids_are_rejected_before_any_mutation(
    scenario, system, tmp_path, monkeypatch
):
    captured = []
    dispatched = []
    transport(
        monkeypatch,
        [
            response(
                [
                    call("delete_file", {"path": "obsolete.py"}),
                    call("write_file", {"path": "solution.py", "content": "incorrect"}),
                ]
            )
        ],
        captured,
    )
    monkeypatch.setattr(PublicWorkspace, "dispatch", lambda *args: dispatched.append(True))
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert result.error and result.output is None
    assert result.cost_usd is not None and not dispatched


def test_step_limit_and_upfront_reservation_cover_all_allowed_requests(
    scenario, system, tmp_path, monkeypatch
):
    system = system.model_copy(update={"max_steps": 2})
    captured = []
    transport(monkeypatch, [response([call("list_files", {})])] * 2, captured)
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert len(captured) == 2 and "step limit" in result.error
    expected = 2 * ((MAX_PROMPT_BYTES + 16384) * 2 + system.max_tokens * 4) / 1_000_000
    assert reserve_cost(scenario, system) == reservation(system) == pytest.approx(expected)
    assert result.cost_usd == pytest.approx(0.00008)
    assert result.output is None


def test_context_limit_stops_without_an_outbound_request(scenario, system, tmp_path, monkeypatch):
    scenario.inputs["context"] = "x" * MAX_PROMPT_BYTES
    captured = []
    transport(monkeypatch, [], captured)
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert "context" in result.error and not captured
    assert result.cost_usd == 0


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": MAX_PROMPT_BYTES + 16385, "completion_tokens": 1},
        {"prompt_tokens": 10, "completion_tokens": 101},
    ],
)
def test_provider_token_overrun_halts_further_tools(scenario, system, tmp_path, monkeypatch, usage):
    captured = []
    transport(monkeypatch, [response([call("list_files", {})], usage=usage)], captured)
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert len(captured) == 1 and "reserved limit" in result.error
    assert result.metadata["budget_uncertain"] and result.cost_usd is not None


def test_unbounded_usage_is_treated_as_unknown_not_a_float_overflow(
    scenario, system, tmp_path, monkeypatch
):
    captured = []
    transport(
        monkeypatch, [response(usage={"prompt_tokens": 10**300, "completion_tokens": 1})], captured
    )
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert result.error and result.cost_usd is None and result.metadata["budget_uncertain"]


def test_unicode_tool_outputs_remain_bounded(scenario, system, tmp_path, monkeypatch):
    scenario.inputs["files"]["solution.py"] = "🛠" * 16000
    captured = []
    transport(
        monkeypatch,
        [
            response([call("read_file", {"path": "solution.py", "limit": 16000})]),
            response(),
        ],
        captured,
    )
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert result.error is None
    tool_message = captured[-1]["messages"][-1]["content"]
    assert len(tool_message.encode()) <= MAX_TOOL_OUTPUT
    assert json.loads(tool_message)["truncated"]


def test_public_python_has_only_public_source_and_pinned_environment(
    scenario, tmp_path, monkeypatch
):
    scenario.inputs["environment"] = {"image_digest": DIGEST, "kind": "prepared-pytest"}
    captured = []

    def execute(files, grader_files, settings, timeout_seconds, **kwargs):
        captured.append((files, grader_files, settings, timeout_seconds, kwargs))
        return {"output": "public result", "exit_code": 0, "image_digest": settings.sandbox_image}

    monkeypatch.setattr("cvals.agent_runner._execute", execute)
    workspace = PublicWorkspace(scenario, Settings(tmp_path))
    result = workspace.dispatch("run_python", {"code": "print('hello')"}, 9.9)
    assert result["output"] == "public result"
    files, grade, settings, timeout, options = captured[0]
    assert files == scenario.inputs["files"]
    assert set(grade) == {"runner.py"}
    assert "CANARY_" not in canonical_json([files, grade])
    assert settings.sandbox_image == DIGEST and timeout == 9 and options["repository_mode"]


def test_http_error_and_transport_failure_do_not_echo_provider_bodies(
    scenario, system, tmp_path, monkeypatch
):
    for failure in (
        httpx.Response(
            302, headers={"Location": "https://attacker.example"}, text="SECRET_RESPONSE_BODY"
        ),
        httpx.ConnectError("SECRET_TRANSPORT_CONFIGURATION"),
    ):
        captured = []
        transport(monkeypatch, [failure], captured)
        result = submit(scenario, system, Settings(tmp_path), 10)
        assert len(captured) == 1 and result.error and "SECRET_" not in result.error
        assert result.cost_usd is None and result.metadata["budget_uncertain"]
        monkeypatch.undo()


def test_single_turn_truncated_json_is_not_accepted_as_completed(
    scenario, system, tmp_path, monkeypatch
):
    system = system.model_copy(update={"harness": "single-turn"})
    captured = []
    transport(
        monkeypatch,
        [
            {
                "choices": [
                    {
                        "message": {"content": '{"files":{"solution.py":"pass"}}'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ],
        captured,
    )
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert result.output is None and result.error and result.cost_usd is not None


@pytest.mark.sandbox
@pytest.mark.skipif(os.getenv("CVALS_TEST_SANDBOX") != "1", reason="Requires isolated Docker")
def test_live_public_python_cannot_read_private_grader_or_git_history(scenario, tmp_path):
    workspace = PublicWorkspace(
        scenario, replace(Settings.from_env(), data_dir=tmp_path / "workspace")
    )
    result = workspace.dispatch(
        "run_python",
        {
            "code": "from pathlib import Path\nimport json\nprint(json.dumps({'grade': sorted(p.name for p in Path('/grade').iterdir()), 'git': Path('/workspace/.git').exists(), 'private': Path('/grade/tests.py').exists()}))",
        },
        30,
    )
    assert result["exit_code"] == 0
    actual = json.loads(result["output"].strip())
    assert actual == {"grade": ["runner.py"], "git": False, "private": False}
    assert "CANARY_" not in result["output"]


def test_public_workspace_rejects_unpinned_image_and_new_private_harness_paths(scenario, tmp_path):
    scenario.inputs["environment"] = {"image_digest": "mutable:latest"}
    with pytest.raises(ValueError, match="SHA-256"):
        PublicWorkspace(scenario, Settings(tmp_path))
    scenario.inputs["environment"] = {"image_digest": DIGEST, "kind": "prepared-pytest"}
    workspace = PublicWorkspace(scenario, Settings(tmp_path))
    for path in ("conftest.py", "pyproject.toml", "tests/helper.py"):
        with pytest.raises(ValueError, match="private"):
            workspace.dispatch("write_file", {"path": path, "content": "pass"}, 30)


def test_total_deadline_prevents_followup_call_after_a_tool(
    scenario, system, tmp_path, monkeypatch
):
    captured = []
    transport(monkeypatch, [response([call("list_files", {})])], captured)
    clock = [0.0]
    monkeypatch.setattr("cvals.agent_runner.time.monotonic", lambda: clock[0])
    original = PublicWorkspace.dispatch

    def slow(workspace, *args):
        output = original(workspace, *args)
        clock[0] += 301
        return output

    monkeypatch.setattr(PublicWorkspace, "dispatch", slow)
    result = submit(scenario, system, Settings(tmp_path), 10)
    assert len(captured) == 1 and "deadline" in result.error
    assert result.cost_usd == pytest.approx(0.00004)
