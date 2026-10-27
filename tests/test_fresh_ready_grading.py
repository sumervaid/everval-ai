import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.judge_study import fresh_ready_grading as grading  # noqa: E402
from scripts.judge_study import fresh_repositories as study  # noqa: E402


def completed(root, job):
    folder = root / "requests" / job["id"]
    folder.mkdir(parents=True)
    result = {"instance_id": job["instance_id"], "model": job["model"], "patch": ""}
    (folder / "result.json").write_text(json.dumps(result))
    validation = {
        "job_id": job["id"],
        "body_sha256": job["body_sha256"],
        "adapter_sha256": "adapter",
        "result_sha256": study.digest(result),
    }
    (folder / "dispatch-validation.json").write_text(json.dumps(validation))
    return folder


def jobs(count):
    return [
        {
            "id": str(i),
            "instance_id": "task-" + str(i),
            "model": "model",
            "body_sha256": hashlib.sha256(str(i).encode()).hexdigest(),
        }
        for i in range(count)
    ]


def test_unfinished_validation_is_waited_for_not_counted_as_failure(tmp_path):
    job = jobs(1)[0]
    assert not grading.ready(tmp_path, job, "adapter")
    folder = completed(tmp_path, job)
    (folder / "dispatch-validation.json").write_text('{"job_id":')
    assert not grading.ready(tmp_path, job, "adapter")


def test_changed_completed_result_is_rejected(tmp_path):
    job = jobs(1)[0]
    folder = completed(tmp_path, job)
    assert grading.ready(tmp_path, job, "adapter")
    (folder / "result.json").write_text(
        json.dumps({"instance_id": job["instance_id"], "model": job["model"], "patch": "changed"})
    )
    with pytest.raises(ValueError, match="differs"):
        grading.ready(tmp_path, job, "adapter")


def test_partitions_are_disjoint_and_exhaustive(tmp_path):
    planned = jobs(6)
    for job in planned:
        completed(tmp_path, job)
    left = list(grading.available_jobs(tmp_path, planned, 0, "adapter"))
    right = list(grading.available_jobs(tmp_path, planned, 1, "adapter"))
    assert [j["id"] for j in left] == ["0", "2", "4"]
    assert [j["id"] for j in right] == ["1", "3", "5"]


def test_dispatch_stop_keeps_ready_work_but_never_waits_for_unattempted_jobs(tmp_path):
    planned = jobs(4)
    completed(tmp_path, planned[0])
    (tmp_path / "dispatch-STOP.json").write_text("{}")
    assert [j["id"] for j in grading.available_jobs(tmp_path, planned, 0, "adapter")] == ["0"]


def test_pause_and_grader_stop_admit_no_new_containers(tmp_path):
    planned = jobs(1)
    completed(tmp_path, planned[0])
    for marker in ("pause-requested", "grading-STOP.json"):
        (tmp_path / marker).write_text("stop")
        assert list(grading.available_jobs(tmp_path, planned, 0, "adapter")) == []
        (tmp_path / marker).unlink()


def test_parent_lock_excludes_another_supervisor(tmp_path):
    with grading.lock(tmp_path) as descriptor:
        grading.verify_lease(tmp_path, descriptor)
        with pytest.raises(BlockingIOError), grading.lock(tmp_path):
            pytest.fail("A second grader supervisor acquired the lock")
