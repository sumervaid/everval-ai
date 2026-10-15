"""Provider-free installation smoke for the pinned repository benchmark runner.

The tiny checkout and scripted answers are original fixtures, not model evidence.
All fixture execution and image preparation occur inside the local Docker VM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from .executor import DockerSandbox, _write_json, bounded_process, grade_patch, run_attempt


class ScriptedModel:
    """Exercise the transport protocol without credentials or provider requests."""

    def __init__(self, commands: list[str]) -> None:
        self.commands = iter(commands)
        self.calls = 0

    def query(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        command = next(self.commands)
        self.calls += 1
        return {
            "role": "assistant",
            "content": "THOUGHT: Verify the installation fixture.\n"
            "```mswea_bash_command\n" + command + "\n```",
            "extra": {"cost": 0.0, "finish_reason": "stop"},
        }

    @staticmethod
    def format_message(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    @staticmethod
    def get_template_vars() -> dict[str, Any]:
        return {"model_name": "scripted-installation-fixture"}

    def serialize(self) -> dict[str, Any]:
        return {"scripted_calls": self.calls, "provider_calls": 0, "known_cost_usd": 0.0}


def run_smoke(*, base_image: str, output_dir: Path) -> dict[str, Any]:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", base_image) is None:
        raise ValueError("Use an immutable prepared runtime image ID")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    context = root / "context"
    context.mkdir(mode=0o700)
    (context / "calc.py").write_text("def add(left, right):\n    return left - right\n")
    (context / ".dockerignore").write_text("*\n!Dockerfile\n!calc.py\n")
    (context / "Dockerfile").write_text(
        f"FROM {base_image}\n"
        "USER 0\n"
        "RUN rm -rf /testbed && mkdir -p /testbed\n"
        "COPY calc.py /testbed/calc.py\n"
        "WORKDIR /testbed\n"
        "RUN git -c init.defaultBranch=main init && "
        "git config user.name Fixture && git config user.email fixture@localhost && "
        "git add calc.py && "
        "GIT_AUTHOR_DATE=2020-01-01T00:00:00Z GIT_COMMITTER_DATE=2020-01-01T00:00:00Z "
        "git -c core.hooksPath=/dev/null commit -m 'Original installation fixture' && "
        "chmod -R a+rwX /testbed\n"
    )
    probe = DockerSandbox(image=base_image, output_dir=root / "probe")
    probe.resolved_endpoint = probe._resolve_endpoint()
    image_file = root / "fixture-image-id.txt"
    build = bounded_process(
        probe._docker(
            "build",
            "--force-rm",
            "--network",
            "none",
            "--memory",
            "1536m",
            "--memory-swap",
            "1536m",
            "--iidfile",
            str(image_file),
            str(context),
        ),
        timeout=300,
        max_bytes=2 * 1024 * 1024,
    )
    _write_json(root / "image-build.json", build, exclusive=True)
    if build["returncode"] or build["exception_info"]:
        raise RuntimeError("Original fixture image preparation failed")
    image = image_file.read_text().strip()
    probe.image = image
    try:
        probe.start()
        commit = probe.shell("git rev-parse HEAD", user="0:0")["output"].strip()
    finally:
        probe.close()
    task = {
        "instance_id": "everval-original-smoke",
        "repo": "everval/original-installation-fixture",
        "version": "1",
        "base_commit": commit,
        "image": image,
        "problem_statement": (
            "The add function subtracts its second argument. Make it add both "
            "integers; keep its public signature."
        ),
        "FAIL_TO_PASS": ["test_smoke.py::test_add"],
        "PASS_TO_PASS": ["test_smoke.py::test_zero"],
        "log_parser": "parse_log_pytest",
        "eval_type": "pass_and_fail",
        "eval_script": (
            "#!/bin/bash\nset -uxo pipefail\ncd /testbed\n"
            "cat > test_smoke.py <<'PY'\n"
            "from calc import add\n"
            "def test_add():\n    assert add(3, 2) == 5\n"
            "def test_zero():\n    assert add(3, 0) == 3\n"
            "PY\n"
            ": '>>>>> Start Test Output'\n"
            "python -m pytest -rA test_smoke.py\n"
            ": '>>>>> End Test Output'\n"
            "true\n"
        ),
    }
    commands = [
        "printf 'def add(left, right):\\n    return left + right\\n' > calc.py",
        "set -e; python -c 'from calc import add; assert add(3, 2) == 5'; "
        "git diff -- calc.py > patch.txt; cat patch.txt",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt",
    ]
    results = []
    for arm in ("baseline", "everval"):
        own_commands = commands.copy()
        if arm == "everval":
            own_commands += [
                "set -e; python -c 'from calc import add; assert add(-1, 1) == 0'; "
                "git diff --check; git diff -- calc.py > patch.txt; cat patch.txt",
                commands[-1],
            ]
        model = ScriptedModel(own_commands)
        attempt = run_attempt(
            task=task, image=image, model=model, output_dir=root / arm / "attempt", arm=arm
        )
        if not attempt["submitted"] or not attempt["cleanup_confirmed"]:
            raise AssertionError("Fixture agent did not submit with confirmed cleanup")
        grade = grade_patch(
            task=task, image=image, patch=attempt["patch"], output_dir=root / arm / "grade"
        )
        expected_calls = 3 if arm == "baseline" else 5
        assert attempt["steps"] == model.calls == expected_calls
        assert attempt["review_checkpoint_used"] is (arm == "everval")
        assert grade["resolved"] and grade["cleanup_confirmed"]
        results.append(
            {
                "arm": arm,
                "scripted_calls": model.calls,
                "review_checkpoint_used": attempt["review_checkpoint_used"],
                "submitted": attempt["submitted"],
                "official_fixture_tests_passed": grade["resolved"],
                "cleanup_confirmed": attempt["cleanup_confirmed"] and grade["cleanup_confirmed"],
                "patch_sha256": grade["patch_sha256"],
            }
        )
    assert results[0]["patch_sha256"] == results[1]["patch_sha256"]
    result = {
        "status": "passed",
        "scope": "original_installation_fixture",
        "provider_calls": 0,
        "provider_cost_usd": 0,
        "image_id": image,
        "source_base_image_id": base_image,
        "checks": results,
    }
    _write_json(root / "result.json", result, exclusive=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--docker-host")
    args = parser.parse_args()
    if args.docker_host:
        os.environ["CVALS_DOCKER_HOST"] = args.docker_host
    result = run_smoke(base_image=args.base_image, output_dir=args.output_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
