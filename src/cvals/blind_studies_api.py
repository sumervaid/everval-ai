"""Dedicated blinded studies with role-scoped phase transitions and reports."""

from __future__ import annotations

import html
import json
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.routing import APIRoute
from pydantic import Field

from cvals.blind_studies import BlindStudies, StudyRequest
from cvals.config import Settings
from cvals.identity import IdentityStore, principal, register_capability
from cvals.models import Manifest
from cvals.review_queues import ReviewQueues


class LockRequest(Manifest):
    expected_hash: str = Field(min_length=64, max_length=64)


class RevealRequest(LockRequest):
    rationale: str = Field(min_length=1, max_length=10000)


def _html_report(report: dict[str, Any]) -> str:
    # Every value is escaped; exports contain no scripts or remote resources.
    study = report.get("study", {})
    review = report.get("review", {})
    title = html.escape(str(study.get("name") or "Double-blind evaluation"))
    rows = "".join(
        f"<tr><td>{html.escape(str(name))}</td><td>{html.escape(str(count))}</td></tr>"
        for name, count in review.get("preferences", {}).items()
    )
    coverage = f"{study.get('completed_reviews', 0)} / {study.get('assigned_reviews', 0)}"
    reason = html.escape(str(report.get("reveal", {}).get("reveal_rationale", "")))
    fixture_note = (
        "<p><strong>Installation controls:</strong> this demonstrates the review workflow, "
        "not model quality.</p>"
        if review.get("fixture_controls_present")
        else ""
    )
    evidence = html.escape(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        f"<title>{title} — Everval</title>"
        "<style>body{max-width:980px;margin:48px auto;padding:0 24px;"
        "font:16px/1.65 system-ui;color:#25382e;background:#f7f8f4}"
        "pre{padding:24px;background:white;border:1px solid #e2e7df;border-radius:12px;"
        "white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}"
        "table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:14px;border-bottom:1px solid #e2e7df}"
        "h1{font-size:34px;line-height:1.2}.label{color:#2c6048;letter-spacing:.12em;font-size:12px}"
        "</style><main><p class=label>EVERVAL / DOUBLE-BLIND EVALUATION</p>"
        f"<h1>{title}</h1>"
        "<p>Released after review judgments were locked. Trusted-custodian mode: "
        "model identities are hidden from reviewers and report readers until reveal; workspace "
        "administrators remain trusted. This is not hardware-attested confidential execution.</p>"
        f"{fixture_note}<h2>Recorded preferences</h2><p>{coverage} judgments recorded.</p>"
        "<p>Judgments are votes, not independent model trials. Multiple reviewers can judge "
        "the same task. Human judgments remain separate from automated scores.</p>"
        f"<table><thead><tr><th>Model / outcome</th><th>Judgments</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<h2>Release reason</h2><p>{reason}</p>"
        f"<details><summary>Frozen protocol and released evidence</summary><pre>{evidence}</pre></details></main></html>"
    )


def register_blind_study_routes(app: FastAPI, settings: Settings) -> None:
    studies = BlindStudies(settings)
    router = APIRouter(prefix="/api/blind-studies")

    @router.get("/catalog")
    def catalog() -> dict[str, Any]:
        identities = IdentityStore(settings)
        return {
            "team_enabled": identities.enabled(),
            "experiments": [
                {key: run.get(key) for key in ("id", "name", "status", "created_at")}
                for run in app.state.store.list_experiments()
                if run["status"] == "completed"
            ],
            "rubrics": ReviewQueues(settings).rubrics(),
            "reviewers": [
                {key: user[key] for key in ("id", "name", "role")}
                for user in identities.users()
                if user["active"] and user["role"] == "reviewer"
            ],
            "modes": [
                {"id": "trusted_custodian", "available": True},
                {
                    "id": "confidential_execution",
                    "available": False,
                    "reason": "A verified confidential runner and participating model owner are required.",
                },
            ],
        }

    @router.get("")
    def listing() -> list[dict[str, Any]]:
        return studies.list()

    @router.post("", status_code=201)
    def create(body: StudyRequest, request: Request) -> dict[str, Any]:
        return studies.create(body, principal(request).id)

    @router.get("/{study_id}")
    def get(study_id: str) -> dict[str, Any]:
        return studies.get(study_id)

    @router.post("/{study_id}/lock")
    def lock(study_id: str, body: LockRequest, request: Request) -> dict[str, Any]:
        return studies.lock(study_id, principal(request).id, body.expected_hash)

    @router.post("/{study_id}/reveal")
    def reveal(study_id: str, body: RevealRequest, request: Request) -> dict[str, Any]:
        return studies.reveal(study_id, principal(request).id, body.expected_hash, body.rationale)

    @router.get("/{study_id}/report")
    def report(study_id: str, format: Literal["json", "html"] = "json") -> Response:
        value = studies.report(study_id)
        body = (
            _html_report(value)
            if format == "html"
            else json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
        )
        return Response(
            body,
            media_type="text/html" if format == "html" else "application/json",
            headers={"Content-Disposition": f'attachment; filename="everval-blind-study.{format}"'},
        )

    app.include_router(router)
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            capability = (
                "workspace.write"
                if method == "POST"
                else "workspace.read"
                if route.path.endswith("/catalog")
                else "summary.read"
            )
            register_capability(app, method, route.path, capability)
