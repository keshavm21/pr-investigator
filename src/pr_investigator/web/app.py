"""The review UI: runs, findings with evidence, decisions, a trace viewer and a publish preview.

Local, single user (Phase 5). The server binds to localhost and asks for a token. Pages are
server-rendered by Jinja2 with autoescaping, so repository text and model output are escaped
everywhere. A strict Content Security Policy forbids scripts and external resources; the only
state-changing requests are POST forms carrying a CSRF token. Nothing is posted to GitHub
without a person pressing the confirm button on the preview page.
"""

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pr_investigator.agent.models import AgentReview
from pr_investigator.config import Settings
from pr_investigator.domain import Finding, Severity
from pr_investigator.errors import PRIError
from pr_investigator.github import GitHubClient
from pr_investigator.publish import build_review, check_stale, publish
from pr_investigator.review.baseline import ReviewResult
from pr_investigator.review.comments import evidence_label, evidence_url
from pr_investigator.review.decisions import (
    Decision,
    DecisionStore,
    FindingEdit,
    ReasonCode,
)
from pr_investigator.review.report import sorted_findings
from pr_investigator.review.reviewers import AnyReview
from pr_investigator.trace import TRACE_FILE, read_trace, render_trace

RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$")
FINDING_ID = re.compile(r"^F[0-9]{1,4}$")
HERE = Path(__file__).parent
CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)

GitHubFactory = Callable[[], GitHubClient | None]


def load_run(runs_dir: Path, run_id: str) -> AnyReview:
    if not RUN_ID.match(run_id):
        raise HTTPException(404)
    path = runs_dir / run_id / "run.json"
    if not path.is_file():
        raise HTTPException(404)
    data = json.loads(path.read_text(encoding="utf-8"))
    model = ReviewResult if data.get("reviewer") in ("b0", "b1") else AgentReview
    return model.model_validate(data)


def create_app(
    settings: Settings, *, token: str | None = None, github: GitHubFactory | None = None
) -> FastAPI:
    ui_token = token or secrets.token_urlsafe(24)
    sessions: set[str] = set()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")  # autoescape is on for .html
    app.state.token = ui_token

    def make_github() -> GitHubClient | None:
        if github is not None:
            return github()
        secret = settings.github_token
        return GitHubClient(secret.get_secret_value()) if secret else None

    def csrf_for(session: str) -> str:
        return hmac.new(ui_token.encode(), f"csrf:{session}".encode(), hashlib.sha256).hexdigest()

    def session_id(request: Request) -> str:
        sid = request.cookies.get("pri_session", "")
        if sid not in sessions:
            raise HTTPException(303, headers={"Location": "/login"})
        return sid

    Session = Annotated[str, Depends(session_id)]

    def check_csrf(sid: str, supplied: str) -> None:
        if not hmac.compare_digest(supplied, csrf_for(sid)):
            raise HTTPException(403, "bad CSRF token")

    @app.middleware("http")
    async def secure_headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def page(request: Request, name: str, sid: str | None, **context: Any) -> HTMLResponse:
        csrf = csrf_for(sid) if sid else None
        return templates.TemplateResponse(request, name, {"csrf": csrf, **context})

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        return page(request, "login.html", None)

    @app.post("/login")
    def login(request: Request, token: Annotated[str, Form()]) -> Response:
        if not hmac.compare_digest(token.encode(), ui_token.encode()):
            return page(request, "login.html", None, error="Wrong token.")
        sid = secrets.token_urlsafe(24)
        sessions.add(sid)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("pri_session", sid, httponly=True, samesite="strict", max_age=28_800)
        return response

    @app.post("/logout")
    def logout(request: Request, sid: Session, csrf: Annotated[str, Form()]) -> Response:
        check_csrf(sid, csrf)
        sessions.discard(sid)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("pri_session")
        return response

    @app.get("/", response_class=HTMLResponse)
    def runs(request: Request, sid: Session) -> HTMLResponse:
        rows = []
        store = DecisionStore(settings.review_db)
        try:
            for run_dir in sorted(settings.runs_dir.glob("*/run.json"), reverse=True):
                run_id = run_dir.parent.name
                try:
                    review = load_run(settings.runs_dir, run_id)
                except (HTTPException, ValueError):
                    continue  # not a run we can read
                rows.append(
                    {
                        "id": run_id,
                        "title": review.target.title,
                        "reviewer": review.reviewer,
                        "findings": len(review.findings),
                        "decided": len(store.decisions(run_id)),
                    }
                )
        finally:
            store.close()
        return page(request, "runs.html", sid, runs=rows)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run(request: Request, run_id: str, sid: Session) -> HTMLResponse:
        review = load_run(settings.runs_dir, run_id)
        store = DecisionStore(settings.review_db)
        try:
            decisions = store.decisions(run_id)
            publications = store.publications(run_id)
        finally:
            store.close()
        skeptic = {v.finding_id: v for v in getattr(review, "verifications", [])}
        posted = set(getattr(review, "posted_ids", []))
        findings = (
            review.findings if isinstance(review, AgentReview) else sorted_findings(review.findings)
        )
        cards = [
            {
                "finding": f,
                "decision": decisions.get(f.id),
                "skeptic": skeptic.get(f.id),
                "posted": f.id in posted,
                "evidence": [_evidence_view(e, review) for e in f.evidence],
            }
            for f in findings
        ]
        usage = ""
        if isinstance(review, AgentReview):
            usage = f"{review.requests} requests, {review.usage.input_tokens} tokens in"
        return page(
            request,
            "run.html",
            sid,
            run_id=run_id,
            review=review,
            cards=cards,
            refuted=getattr(review, "refuted", []),
            publications=publications,
            approved=sum(1 for d in decisions.values() if d.decision is Decision.APPROVED),
            severities=[s.value for s in Severity],
            reasons=[r.value for r in ReasonCode],
            usage=usage,
            stale=_stale(review, make_github),
        )

    @app.post("/runs/{run_id}/findings/{finding_id}")
    def decide(
        run_id: str,
        finding_id: str,
        sid: Session,
        csrf: Annotated[str, Form()],
        action: Annotated[str, Form()],
        title: Annotated[str, Form(max_length=200)] = "",
        explanation: Annotated[str, Form(max_length=4000)] = "",
        severity: Annotated[str, Form()] = "",
        suggested_fix: Annotated[str, Form(max_length=4000)] = "",
        reason_code: Annotated[str, Form()] = "",
        reason_text: Annotated[str, Form(max_length=4000)] = "",
    ) -> Response:
        check_csrf(sid, csrf)
        review = load_run(settings.runs_dir, run_id)
        if not FINDING_ID.match(finding_id):
            raise HTTPException(404)
        original = next((f for f in review.findings if f.id == finding_id), None)
        if original is None:
            raise HTTPException(404)
        store = DecisionStore(settings.review_db)
        try:
            if action == "clear":
                store.clear(run_id, finding_id)
            elif action in ("approve", "reject"):
                try:
                    edit = _edit(original, title, explanation, severity, suggested_fix)
                    code = ReasonCode(reason_code) if action == "reject" else None
                    store.decide(
                        run_id,
                        finding_id,
                        Decision.APPROVED if action == "approve" else Decision.REJECTED,
                        reviewer="local",
                        reason_code=code,
                        reason_text=reason_text,
                        edit=edit,
                    )
                except (ValueError, PRIError) as exc:
                    raise HTTPException(400, str(exc)) from exc
            else:
                raise HTTPException(400, "unknown action")
        finally:
            store.close()
        return RedirectResponse(f"/runs/{run_id}#{finding_id}", status_code=303)

    @app.get("/runs/{run_id}/trace", response_class=HTMLResponse)
    def trace(request: Request, run_id: str, sid: Session) -> HTMLResponse:
        load_run(settings.runs_dir, run_id)
        path = settings.runs_dir / run_id / TRACE_FILE
        text = render_trace(read_trace(path)) if path.is_file() else ""
        return page(request, "trace.html", sid, run_id=run_id, text=text)

    def preview(request: Request, run_id: str, sid: str, error: str | None = None) -> HTMLResponse:
        review = load_run(settings.runs_dir, run_id)
        store = DecisionStore(settings.review_db)
        client = make_github()
        built = None
        stale = None
        why_not = ""
        try:
            login = None
            if client is not None:
                try:
                    login = client.get_login()
                    stale = check_stale(client, review)
                except PRIError as exc:
                    why_not = str(exc)
            try:
                built = build_review(
                    review, store.decisions(run_id), run_id=run_id, approved_by=login
                )
            except PRIError as exc:
                error = error or str(exc)
        finally:
            store.close()
        if client is None:
            why_not = "Set GITHUB_TOKEN (with pull request write access) to enable posting."
        elif review.target.pull_request is None:
            why_not = "This run isn't a GitHub pull request."
        return page(
            request,
            "publish.html",
            sid,
            run_id=run_id,
            built=built,
            payload_json=json.dumps(built.payload, indent=2) if built else "",
            can_post=built is not None and not why_not,
            why_not=why_not,
            stale=stale,
            error=error,
        )

    @app.get("/runs/{run_id}/publish", response_class=HTMLResponse)
    def publish_preview(request: Request, run_id: str, sid: Session) -> HTMLResponse:
        return preview(request, run_id, sid)

    @app.post("/runs/{run_id}/publish")
    def publish_confirm(
        request: Request,
        run_id: str,
        sid: Session,
        csrf: Annotated[str, Form()],
        allow_stale: Annotated[str, Form()] = "",
    ) -> Response:
        check_csrf(sid, csrf)
        review = load_run(settings.runs_dir, run_id)
        client = make_github()
        if client is None:
            raise HTTPException(400, "no GitHub token is configured")
        store = DecisionStore(settings.review_db)
        try:
            publish(client, review, store, run_id=run_id, post=True, allow_stale=bool(allow_stale))
        except PRIError as exc:
            store.close()
            return preview(request, run_id, sid, error=str(exc))
        store.close()
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    return app


def _edit(
    original: Finding, title: str, explanation: str, severity: str, suggested_fix: str
) -> FindingEdit:
    """Only what the reviewer changed; a form that matches the original is no edit."""
    fields: dict[str, Any] = {}
    if title.strip() and title.strip() != original.title:
        fields["title"] = title.strip()
    if explanation.strip() and explanation.strip() != original.explanation:
        fields["explanation"] = explanation.strip()
    if severity and severity != original.severity.value:
        fields["severity"] = Severity(severity)
    if suggested_fix.strip() != (original.suggested_fix or ""):
        fields["suggested_fix"] = suggested_fix.strip()  # "" removes the fix
    return FindingEdit(**fields)


def _evidence_view(item: Any, review: AnyReview) -> dict[str, Any]:
    if item.path is None:
        return {
            "role": item.role.value,
            "label": f"search {item.query}",
            "link": None,
            "note": item.note,
            "snippet": None,
        }
    return {
        "role": item.role.value,
        "label": f"{evidence_label(item)} ({item.ref})",
        "link": evidence_url(item, review.target),
        "note": item.note,
        "snippet": item.snippet,
    }


def _stale(review: AnyReview, make_github: GitHubFactory) -> str | None:
    client = make_github()
    if client is None or review.target.pull_request is None:
        return None
    try:
        return check_stale(client, review)
    except PRIError:
        return None
