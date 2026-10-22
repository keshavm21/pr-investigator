"""The review UI: runs, findings with evidence, decisions, a trace viewer and a publish preview.

Local, single user (Phase 5). The server binds to localhost and asks for a token. Pages are
server-rendered by Jinja2 with autoescaping, so repository text and model output are escaped
everywhere. A strict Content Security Policy forbids scripts and external resources; the only
state-changing requests are POST forms carrying a CSRF token. Nothing is posted to GitHub
without a person pressing the confirm button on the preview page.
"""

import hashlib
import hmac
import html
import json
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

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
from pr_investigator.service.runtime import ServiceRuntime
from pr_investigator.service.webhooks import MAX_BODY_BYTES, verify_signature
from pr_investigator.service.webhooks import handle as handle_webhook
from pr_investigator.trace import TRACE_FILE, read_trace, render_trace
from pr_investigator.web.ratelimit import RateLimiter

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


# The form echoes the model's original text back, which can be long. Only a reviewer's *edit* is
# length-checked (FindingEdit); an unchanged field is no edit, and posted comments are cut anyway.
FORM_TEXT_CHARS = 200_000


def create_app(
    settings: Settings,
    *,
    token: str | None = None,
    github: GitHubFactory | None = None,
    runtime: ServiceRuntime | None = None,
) -> FastAPI:
    """The review UI. With `runtime` it is the hosted service: GitHub sign-in, a permission check
    per repository, webhooks, and posting as the App. Without it, the local single-user mode."""
    ui_token = token or secrets.token_urlsafe(24)
    csrf_key = secrets.token_bytes(32) if runtime else ui_token.encode()
    sessions: dict[str, str | None] = {}  # session id -> GitHub login (None in local mode)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")  # autoescape is on for .html
    templates.env.filters["md"] = md_preview
    templates.env.filters["when"] = _when
    app.state.token = ui_token
    login_limit = RateLimiter(10, 60)  # wrong-token guesses and OAuth callbacks, per minute
    webhook_limit = RateLimiter(300, 60)

    def throttle(limiter: RateLimiter, request: Request) -> None:
        host = request.client.host if request.client else "unknown"
        if not limiter.allow(host):
            raise HTTPException(429, "too many requests", headers={"Retry-After": "60"})

    def make_github() -> GitHubClient | None:
        if github is not None:
            return github()
        secret = settings.github_token
        return GitHubClient(secret.get_secret_value()) if secret else None

    def csrf_for(session: str) -> str:
        return hmac.new(csrf_key, f"csrf:{session}".encode(), hashlib.sha256).hexdigest()

    def session_id(request: Request) -> str:
        sid = request.cookies.get("pri_session", "")
        if sid not in sessions:
            raise HTTPException(303, headers={"Location": "/login"})
        return sid

    Session = Annotated[str, Depends(session_id)]

    def login_of(sid: str) -> str | None:
        return sessions.get(sid)

    def allowed(review: AnyReview, run_id: str, sid: str) -> bool:
        """Local mode: always. Service mode: only runs the service made, for people with write
        access to that repository (checked with the App's credentials, not the user's)."""
        if runtime is None:
            return True
        job = runtime.store.for_run(run_id)
        login = login_of(sid)
        if job is None or login is None:
            return False
        try:
            return runtime.access.can_write(job.installation_id, job.repo, login)
        except PRIError:
            return False

    def open_run(run_id: str, sid: str) -> AnyReview:
        review = load_run(settings.runs_dir, run_id)
        if not allowed(review, run_id, sid):
            raise HTTPException(403, "you need write access to this repository")
        return review

    def github_for(run_id: str) -> GitHubClient | None:
        """The client that posts. Service mode: the App, with a one-hour token for this one
        repository and `pull_requests: write` only. Local mode: the configured token."""
        if runtime is None:
            return make_github()
        job = runtime.store.for_run(run_id)
        if job is None:
            return None
        token = runtime.auth.publisher_token(job.installation_id, job.repo.split("/", 1)[1])
        return GitHubClient(token.token, transport=runtime.github_transport)

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
        if runtime is not None and runtime.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}  # no data, no auth: for the container health check

    def page(request: Request, name: str, sid: str | None, **context: Any) -> HTMLResponse:
        csrf = csrf_for(sid) if sid else None
        return templates.TemplateResponse(request, name, {"csrf": csrf, **context})

    def start_session(login: str | None) -> Response:
        sid = secrets.token_urlsafe(24)
        sessions[sid] = login
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            "pri_session",
            sid,
            httponly=True,
            samesite="strict" if runtime is None else "lax",
            secure=bool(runtime and runtime.secure_cookies),
            max_age=28_800,
        )
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, error: str | None = None) -> HTMLResponse:
        return page(request, "login.html", None, github=runtime is not None, error=error)

    @app.post("/login")
    def login(request: Request, token: Annotated[str, Form()]) -> Response:
        if runtime is not None:
            raise HTTPException(404)  # the token login exists for local use only
        throttle(login_limit, request)
        if not hmac.compare_digest(token.encode(), ui_token.encode()):
            return page(request, "login.html", None, error="Wrong token.")
        return start_session(None)

    @app.get("/login/github")
    def login_github() -> Response:
        if runtime is None:
            raise HTTPException(404)
        state = secrets.token_urlsafe(24)
        response = RedirectResponse(runtime.oauth.authorize_url(state), status_code=303)
        response.set_cookie(
            "pri_oauth_state",
            state,
            httponly=True,
            samesite="lax",  # sent when GitHub redirects back
            secure=runtime.secure_cookies,
            max_age=600,
        )
        return response

    @app.get("/oauth/callback")
    def oauth_callback(request: Request, code: str = "", state: str = "") -> Response:
        if runtime is None:
            raise HTTPException(404)
        throttle(login_limit, request)
        expected = request.cookies.get("pri_oauth_state", "")
        if not code or not expected or not hmac.compare_digest(state, expected):
            return RedirectResponse("/login?error=Sign-in+was+not+started+here", status_code=303)
        try:
            login_name = runtime.oauth.login_for_code(code)
        except PRIError:
            return RedirectResponse("/login?error=GitHub+sign-in+failed", status_code=303)
        response = start_session(login_name)
        response.delete_cookie("pri_oauth_state")
        return response

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> Response:
        if runtime is None:
            raise HTTPException(404)
        throttle(webhook_limit, request)  # before reading the body
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413)
        secret = runtime.settings.webhook_secret
        header = request.headers.get("x-hub-signature-256")
        if secret is None or not verify_signature(secret.get_secret_value(), body, header):
            raise HTTPException(401, "bad signature")  # checked before the body is parsed
        event = request.headers.get("x-github-event", "")
        delivery = request.headers.get("x-github-delivery", "")
        if not event or not delivery:
            raise HTTPException(400, "missing event headers")
        outcome = handle_webhook(event, delivery, body, runtime.settings, runtime.store)
        return JSONResponse({"status": outcome.status, "reason": outcome.reason}, status_code=202)

    @app.post("/logout")
    def logout(request: Request, sid: Session, csrf: Annotated[str, Form()]) -> Response:
        check_csrf(sid, csrf)
        sessions.pop(sid, None)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("pri_session")
        return response

    @app.get("/", response_class=HTMLResponse)
    def runs(request: Request, sid: Session) -> HTMLResponse:
        rows: list[dict[str, Any]] = []
        store = DecisionStore(settings.review_db)
        try:
            for run_dir in sorted(settings.runs_dir.glob("*/run.json"), reverse=True):
                run_id = run_dir.parent.name
                try:
                    review = load_run(settings.runs_dir, run_id)
                except (HTTPException, ValueError):
                    continue  # not a run we can read
                if not allowed(review, run_id, sid):
                    continue
                job = runtime.store.for_run(run_id) if runtime else None
                pr = review.target.pull_request
                rows.append(
                    {
                        "id": run_id,
                        "title": review.target.title,
                        "pr": pr.slug if pr else None,
                        "created": _created(run_id),
                        "reviewer": review.reviewer,
                        "findings": len(review.findings),
                        "posted": len(getattr(review, "posted_ids", [])),
                        "decided": len(store.decisions(run_id)),
                        "published": bool(store.publications(run_id)),
                        "state": job.state if job else None,
                    }
                )
        finally:
            store.close()
        totals = {
            "findings": sum(r["findings"] for r in rows),
            "decided": sum(r["decided"] for r in rows),
            "published": sum(1 for r in rows if r["published"]),
        }
        return page(request, "runs.html", sid, runs=rows, totals=totals, active="runs")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run(request: Request, run_id: str, sid: Session) -> HTMLResponse:
        review = open_run(run_id, sid)
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
        pr = review.target.pull_request
        return page(
            request,
            "run.html",
            sid,
            run_id=run_id,
            review=review,
            cards=cards,
            pr_url=pr.url if pr else None,
            pr_label=pr.slug if pr else None,
            budget_count=len(posted),
            rejected=sum(1 for d in decisions.values() if d.decision is Decision.REJECTED),
            refuted=getattr(review, "refuted", []),
            publications=publications,
            approved=sum(1 for d in decisions.values() if d.decision is Decision.APPROVED),
            severities=[s.value for s in Severity],
            reasons=[r.value for r in ReasonCode],
            usage=usage,
            stale=_stale(review, lambda: github_for(run_id)),
        )

    @app.post("/runs/{run_id}/findings/{finding_id}")
    def decide(
        run_id: str,
        finding_id: str,
        sid: Session,
        csrf: Annotated[str, Form()],
        action: Annotated[str, Form()],
        title: Annotated[str, Form(max_length=FORM_TEXT_CHARS)] = "",
        explanation: Annotated[str, Form(max_length=FORM_TEXT_CHARS)] = "",
        severity: Annotated[str, Form()] = "",
        suggested_fix: Annotated[str, Form(max_length=FORM_TEXT_CHARS)] = "",
        reason_code: Annotated[str, Form()] = "",
        reason_text: Annotated[str, Form(max_length=4000)] = "",
    ) -> Response:
        check_csrf(sid, csrf)
        review = open_run(run_id, sid)
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
                        reviewer=login_of(sid) or "local",
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
        open_run(run_id, sid)
        path = settings.runs_dir / run_id / TRACE_FILE
        text = render_trace(read_trace(path)) if path.is_file() else ""
        return page(request, "trace.html", sid, run_id=run_id, text=text)

    def preview(request: Request, run_id: str, sid: str, error: str | None = None) -> HTMLResponse:
        review = open_run(run_id, sid)
        store = DecisionStore(settings.review_db)
        client = github_for(run_id)
        reviewer_login = login_of(sid)
        built = None
        stale = None
        why_not = ""
        try:
            login = reviewer_login
            if client is not None:
                try:
                    login = login or client.get_login()
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
            why_not = (
                "This run has no installation to post through."
                if runtime
                else "Set GITHUB_TOKEN (with pull request write access) to enable posting."
            )
        elif review.target.pull_request is None:
            why_not = "This run isn't a GitHub pull request."
        return page(
            request,
            "publish.html",
            sid,
            run_id=run_id,
            built=built,
            payload_json=json.dumps(built.payload, indent=2) if built else "",
            comments=_comment_views(built.payload["comments"]) if built else [],
            can_post=built is not None and not why_not,
            why_not=why_not,
            stale=stale,
            error=error,
            destination=_destination(review),
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
        review = open_run(run_id, sid)
        client = github_for(run_id)
        if client is None:
            raise HTTPException(400, "no GitHub token is configured")
        store = DecisionStore(settings.review_db)
        try:
            publish(
                client,
                review,
                store,
                run_id=run_id,
                post=True,
                allow_stale=bool(allow_stale),
                approved_by=login_of(sid),
            )
        except PRIError as exc:
            store.close()
            return preview(request, run_id, sid, error=str(exc))
        store.close()
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    return app


def _destination(review: AnyReview) -> str | None:
    pr = review.target.pull_request
    return f"{pr.owner}/{pr.repo}#{pr.number}" if pr else None


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
            "lines": [],
        }
    first = item.start_line or 1
    lines = [(first + i, text) for i, text in enumerate((item.snippet or "").split("\n"))]
    return {
        "role": item.role.value,
        "label": f"{evidence_label(item)} ({item.ref})",
        "link": evidence_url(item, review.target),
        "note": item.note,
        "lines": lines if item.snippet else [],
    }


_PREVIEW_LINK = re.compile(r"\[(`?)([^\]`]+)\1\]\((https://github\.com/[^)\s]+)\)")
_PREVIEW_MARKER = re.compile(r"&lt;!-- pri-publication:([0-9a-f]+) --&gt;")


def md_preview(text: str) -> Markup:
    """A readable preview of comment markdown. Everything is HTML-escaped first, then only
    four constructs are turned back into tags: bold, code spans, links to github.com (the only
    links the sanitizer lets through) and <sub>. Nothing the text contains can become markup."""
    safe = html.escape(text)
    safe = _PREVIEW_LINK.sub(lambda m: f'<a href="{m[3]}" rel="noreferrer">{m[2]}</a>', safe)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"&lt;sub&gt;(.*?)&lt;/sub&gt;", r'<span class="muted small">\1</span>', safe)
    safe = _PREVIEW_MARKER.sub(r'<span class="chip">idempotency marker \1</span>', safe)
    return Markup(safe)


def _when(iso: str) -> str:
    """'2026-10-21 19:51' from an ISO timestamp."""
    return iso.replace("T", " ")[:16]


def _created(run_id: str) -> str:
    """'2026-10-21 19:13' from a run id like 20261021-191323-04ab5b; the id if it isn't one."""
    match = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", run_id)
    return f"{match[1]}-{match[2]}-{match[3]} {match[4]}:{match[5]}" if match else run_id


def _comment_views(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": c["path"],
            "lines": f"{c['start_line']}-{c['line']}" if "start_line" in c else str(c["line"]),
            "side": "deleted lines" if c["side"] == "LEFT" else "new version",
            "body": c["body"],
        }
        for c in comments
    ]


def _stale(review: AnyReview, make_client: GitHubFactory) -> str | None:
    try:
        client = make_client()
    except PRIError:
        return None
    if client is None or review.target.pull_request is None:
        return None
    try:
        return check_stale(client, review)
    except PRIError:
        return None
