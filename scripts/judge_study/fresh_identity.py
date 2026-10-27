"""Prospective, explicit collection-identity policy for fresh repository controls."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.judge_study import fresh_repositories as study

POLICY = "django-unique-failed-summary-alias-v1"
_ORIGINAL_NAMES = study.observed_test_names
_CANONICAL = re.compile(
    r"(test_[A-Za-z_0-9]+) \(([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)+)\)"
)


def normalized_names(result: dict[str, Any]) -> frozenset[str] | None:
    names = _ORIGINAL_NAMES(result)
    environment = result.get("environment", {})
    if (
        names is None
        or not isinstance(environment, dict)
        or environment.get("repo") != "django/django"
    ):
        return names
    statuses = result.get("test_statuses", {})
    aliases: dict[str, list[str]] = {}
    for name in statuses:
        match = _CANONICAL.fullmatch(name)
        if match:
            aliases.setdefault(match[1], []).append(name)
    retained = set(names)
    for bare, qualified in aliases.items():
        if bare not in statuses:
            continue
        if len(qualified) != 1 or statuses[bare] != "FAILED" or statuses[qualified[0]] != "FAILED":
            return None
        retained.remove(bare)
    return frozenset(retained)


def source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@contextlib.contextmanager
def policy(root: Path):
    plan = study.load(root)
    amendment = plan.get("amendment") or {}
    declared = amendment.get("identity_policy")
    if declared is None:
        yield
        return
    if (
        declared.get("name") != POLICY
        or declared.get("source_sha256") != source_sha256()
        or plan.get("supporting_source_sha256", {}).get("scripts/judge_study/fresh_identity.py")
        != source_sha256()
    ):
        raise ValueError("Undeclared or changed collection-identity policy")
    previous = study.observed_test_names
    if previous is not _ORIGINAL_NAMES:
        raise ValueError("Another collection-identity policy is active")
    study.observed_test_names = normalized_names
    try:
        yield
    finally:
        study.observed_test_names = previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("controls", "freeze-inference", "status"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--instance-id")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    root = study.private_root(args.root)
    with policy(root):
        if args.command == "controls":
            study.run_controls(root, args.limit, args.instance_id)
        elif args.command == "freeze-inference":
            study.freeze_inference(root)
        else:
            print(json.dumps(study.summary(root), sort_keys=True))


if __name__ == "__main__":
    main()
