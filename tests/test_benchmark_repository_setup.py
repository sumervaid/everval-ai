"""Synthetic repository checks for the fixed Sphinx runtime setup profiles."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmarks import executor  # noqa: E402
from scripts.benchmarks import repository_setup as setup  # noqa: E402

IDS = tuple(setup._PROFILES)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def synthetic(tmp_path, monkeypatch, instance=IDS[0], trailing_newline=True):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic fixture")
    git(repo, "config", "user.email", "fixture@localhost")
    original = {"tox.ini": b"[testenv]\ncommands = pytest --strict\n"}
    if instance.endswith("-8801"):
        original["setup.py"] = (
            b"requirements = [\n'Jinja2>=2.3',\n'sphinxcontrib-applehelp',\n"
            b"'sphinxcontrib-devhelp',\n'sphinxcontrib-qthelp',\n"
            b"'alabaster>=0.7,<0.8',\n'packaging',\n'sphinxcontrib-htmlhelp',\n"
            b"'sphinxcontrib-serializinghtml',\n]\n"
        )
    if not trailing_newline:
        original["tox.ini"] = original["tox.ini"].rstrip(b"\n")
    for name, value in original.items():
        (repo / name).write_bytes(value)
    (repo / "module.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Original synthetic base")
    base = git(repo, "rev-parse", "HEAD")
    _, recipe, commands = setup._PROFILES[instance]
    monkeypatch.setitem(setup._PROFILES, instance, (base, recipe, commands))
    configured = setup.transformed(instance, original)
    for name, value in configured.items():
        (repo / name).write_bytes(value)
    git(repo, "commit", "-qam", "Synthetic upstream installation changes")
    return repo, base, original, configured, tmp_path / "provenance"


@pytest.mark.parametrize("instance", IDS)
def test_capture_cleans_base_and_grader_restores_exact_configuration(
    tmp_path, monkeypatch, instance
):
    repo, base, original, configured, provenance = synthetic(tmp_path, monkeypatch, instance)
    manifest = setup.capture(instance, base, repo, provenance)
    assert git(repo, "rev-parse", "HEAD") == base
    assert git(repo, "diff", "--exit-code", base) == ""
    assert {name: (repo / name).read_bytes() for name in original} == original
    assert set(manifest["configuration"]) == set(original)
    assert all((provenance / name).stat().st_mode & 0o777 == 0o600 for name in setup.ARTIFACTS)
    (repo / "module.py").write_text("value = 2\n")
    evidence = setup.apply(instance, base, repo, provenance)
    assert evidence["patch_sha256"] == manifest["patch_sha256"]
    assert {name: (repo / name).read_bytes() for name in configured} == configured
    assert (repo / "module.py").read_text() == "value = 2\n"


def test_patch_preserves_missing_final_newline(tmp_path, monkeypatch):
    repo, base, _, configured, provenance = synthetic(tmp_path, monkeypatch, trailing_newline=False)
    setup.capture(IDS[0], base, repo, provenance)
    setup.apply(IDS[0], base, repo, provenance)
    assert (repo / "tox.ini").read_bytes() == configured["tox.ini"]


@pytest.mark.parametrize("defect", ["source", "config", "mode", "symlink", "missing_change"])
def test_capture_rejects_unreviewed_changes_without_reset(tmp_path, monkeypatch, defect):
    repo, base, original, _, provenance = synthetic(tmp_path, monkeypatch)
    if defect == "source":
        (repo / "module.py").write_text("value = 99\n")
    elif defect == "config":
        (repo / "tox.ini").write_bytes((repo / "tox.ini").read_bytes() + b"extra = unsafe\n")
    elif defect == "mode":
        (repo / "tox.ini").chmod(0o755)
    elif defect == "symlink":
        (repo / "tox.ini").unlink()
        (repo / "tox.ini").symlink_to(repo / "module.py")
    else:
        (repo / "tox.ini").write_bytes(original["tox.ini"])
    head = git(repo, "rev-parse", "HEAD")
    before = git(repo, "diff", base)
    with pytest.raises(ValueError):
        setup.capture(IDS[0], base, repo, provenance)
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "diff", base) == before
    assert not provenance.exists()


@pytest.mark.parametrize(
    "defect", ["patch", "manifest", "helper", "identity", "permissions", "symlink", "config"]
)
def test_grader_rejects_tampered_evidence_before_applying(tmp_path, monkeypatch, defect):
    repo, base, original, _, provenance = synthetic(tmp_path, monkeypatch)
    setup.capture(IDS[0], base, repo, provenance)
    manifest_path, patch_path = (provenance / name for name in setup.ARTIFACTS)
    if defect == "patch":
        patch_path.write_bytes(patch_path.read_bytes() + b"unreviewed delta\n")
    elif defect in {"manifest", "helper", "identity"}:
        manifest = json.loads(manifest_path.read_text())
        key = {"manifest": "patch_sha256", "helper": "helper_sha256", "identity": "base_commit"}[
            defect
        ]
        manifest[key] = "0" * len(manifest[key])
        manifest_path.write_text(json.dumps(manifest))
    elif defect == "permissions":
        patch_path.chmod(0o644)
    elif defect == "symlink":
        other = provenance / "copied.diff"
        other.write_bytes(patch_path.read_bytes())
        patch_path.unlink()
        patch_path.symlink_to(other)
    else:
        (repo / "tox.ini").write_bytes(b"candidate configuration change\n")
    before = {name: (repo / name).read_bytes() for name in original}
    with pytest.raises((ValueError, OSError)):
        setup.apply(IDS[0], base, repo, provenance)
    assert {name: (repo / name).read_bytes() for name in original} == before


def test_profile_requires_the_declared_base():
    with pytest.raises(ValueError, match="base differs"):
        setup.profile(IDS[0], "a" * 40)
    assert setup.profile("psf__requests-3362", "a" * 40) is None


def test_non_sphinx_grader_has_no_configuration_side_effects(tmp_path):
    class UnusedSandbox:
        def __getattr__(self, name):
            pytest.fail("Non-Sphinx configuration accessed sandbox: " + name)

    assert (
        executor.restore_grader_configuration(
            UnusedSandbox(),
            {"instance_id": "psf__requests-3362", "base_commit": "a" * 40},
            tmp_path,
        )
        is None
    )
    assert list(tmp_path.iterdir()) == []


def test_solver_keeps_base_guard_and_removes_setup_before_admission(tmp_path, monkeypatch):
    commands = []

    def shell(self, command, **kwargs):
        commands.append((command, kwargs.get("user")))
        return {"returncode": 0, "output": "", "exception_info": ""}

    monkeypatch.setattr(executor.DockerSandbox, "shell", shell)
    sandbox = executor.DockerSandbox(image="sha256:" + "a" * 64, output_dir=tmp_path)
    sandbox.prepare_solver("a" * 40, remove_repository_setup=True)
    assert commands[0][0].index("git diff --exit-code") < commands[0][0].index("git reset --hard")
    assert commands[0][0].index("git reset --hard") < commands[0][0].index(
        "rm -f /opt/everval-repository-setup.py"
    )
    assert commands[0][1] == "0:0"
    assert "test ! -e /opt/everval-provenance/repository-setup.diff" in commands[-1][0]
    assert commands[-1][1] is None


def test_grader_configuration_is_staged_after_candidate_before_unchanged_eval(
    tmp_path, monkeypatch
):
    pytest.importorskip("swebench")
    from swebench.harness.utils import make_test_spec

    instance = IDS[0]
    base = setup.profile(instance)["base_commit"]
    task = {
        "instance_id": instance,
        "image": "fixture",
        "repo": "fixture/repo",
        "version": "1",
        "base_commit": base,
        "FAIL_TO_PASS": ["tests/test_a.py::test_fix"],
        "PASS_TO_PASS": ["tests/test_a.py::test_old"],
        "log_parser": "parse_log_pytest",
        "eval_type": "pass_and_fail",
        "eval_script": "#!/bin/bash\n: '>>>>> Start Test Output'\npytest -rA\n: '>>>>> End Test Output'\n",
    }
    original_script = task["eval_script"]
    expected_script = make_test_spec(task).eval_script
    patch = "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    events = []
    staged = {}
    evidence = {
        "revision": setup.REVISION,
        "helper_sha256": setup.digest(Path(setup.__file__).read_bytes()),
        "patch_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "source_dockerfile_sha256": setup.profile(instance)["source_dockerfile_sha256"],
    }

    def put_file(self, source, destination):
        events.append(("stage", destination))
        staged[destination] = source.read_bytes()

    def shell(self, command, **kwargs):
        events.append(("command", command))
        output = ""
        if " apply --instance-id " in command:
            assert events[-3][1].startswith("git -c core.hooksPath=/dev/null apply --check")
            output = json.dumps(evidence)
        elif command == "bash /tmp/everval-eval.sh":
            output = (
                ">>>>> Start Test Output\nPASSED tests/test_a.py::test_fix\n"
                "PASSED tests/test_a.py::test_old\n>>>>> End Test Output\n>>>>> Test Exit Code: 0\n"
            )
        return {"returncode": 0, "output": output, "exception_info": ""}

    monkeypatch.setattr(executor.DockerSandbox, "start", lambda self: None)
    monkeypatch.setattr(executor.DockerSandbox, "close", lambda self: None)
    monkeypatch.setattr(executor.DockerSandbox, "put_file", put_file)
    monkeypatch.setattr(executor.DockerSandbox, "shell", shell)
    result = executor.grade_patch(
        task=task, image="sha256:" + "a" * 64, patch=patch, output_dir=tmp_path
    )
    assert result["resolved"] is True, result
    assert result["repository_setup"] == evidence
    assert staged["/tmp/everval-eval.sh"].decode() == expected_script
    assert staged["/tmp/everval-patch.diff"].decode() == patch
    assert task["eval_script"] == original_script
    assert events[0][1].startswith("git diff --exit-code " + base)
    apply_index = next(i for i, event in enumerate(events) if " apply --instance-id " in event[1])
    evaluate_index = events.index(("command", "bash /tmp/everval-eval.sh"))
    assert apply_index < evaluate_index


def test_isolated_helper_startup_ignores_poisoned_sitecustomize(tmp_path):
    marker = tmp_path / "startup-ran"
    (tmp_path / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    subprocess.run([sys.executable, "-c", "pass"], cwd=tmp_path, env=environment, check=True)
    assert marker.exists()
    marker.unlink()
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(Path(setup.__file__).resolve()), "--help"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "capture-reset" in completed.stdout
    assert not marker.exists()
