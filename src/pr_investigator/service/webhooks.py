"""Webhook handling: verify the signature before parsing, dedupe deliveries, enqueue, return.

Nothing slow happens here. GitHub gives a delivery 10 seconds; the handler only writes rows.
Out-of-order events are tolerated because the fetcher re-reads the PR's current head before
doing any work and discards a job whose head is no longer current.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from pr_investigator.service.admission import decide
from pr_investigator.service.config import ServiceSettings
from pr_investigator.service.store import ServiceStore

TRIGGER_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}
INSTALLATION_STATES = {
    "created": "active",
    "unsuspend": "active",
    "suspend": "suspended",
    "deleted": "deleted",
}
MAX_BODY_BYTES = 5_000_000


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """HMAC-SHA256 over the raw body, compared in constant time."""
    if not header or not header.startswith("sha256=") or not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass(frozen=True)
class Outcome:
    status: str  # "queued", "ignored", "duplicate", "rejected", "recorded"
    reason: str = ""
    job_id: int | None = None
    superseded: tuple[int, ...] = ()


def handle(
    event: str,
    delivery_id: str,
    body: bytes,
    settings: ServiceSettings,
    store: ServiceStore,
) -> Outcome:
    """Process one verified delivery."""
    try:
        payload = json.loads(body)
    except ValueError:
        return Outcome("rejected", "the body isn't JSON")
    if not isinstance(payload, dict):
        return Outcome("rejected", "the body isn't a JSON object")
    action = payload.get("action")
    action = action if isinstance(action, str) else None
    if not store.record_delivery(delivery_id, event, action):
        return Outcome("duplicate", "this delivery was already handled")
    if event == "ping":
        return Outcome("ignored", "ping")
    if event in ("installation", "installation_repositories"):
        return _installation(event, action, payload, store)
    if event == "pull_request":
        return _pull_request(action, delivery_id, payload, settings, store)
    return Outcome("ignored", f"{event} events aren't used")


def _installation(
    event: str, action: str | None, payload: dict[str, Any], store: ServiceStore
) -> Outcome:
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if not isinstance(installation_id, int):
        return Outcome("rejected", "no installation id")
    if event == "installation" and action in INSTALLATION_STATES:
        account = str((installation.get("account") or {}).get("login", "unknown"))
        store.set_installation(installation_id, account, INSTALLATION_STATES[action])
        return Outcome("recorded", f"installation {action}")
    return Outcome("ignored", f"{event} {action}")


def _pull_request(
    action: str | None,
    delivery_id: str,
    payload: dict[str, Any],
    settings: ServiceSettings,
    store: ServiceStore,
) -> Outcome:
    pr = payload.get("pull_request") or {}
    repository = payload.get("repository") or {}
    repo = str(repository.get("full_name", "")).lower()
    number = pr.get("number") if isinstance(pr.get("number"), int) else payload.get("number")
    installation_id = (payload.get("installation") or {}).get("id")
    if not (isinstance(number, int) and repo and isinstance(installation_id, int)):
        return Outcome("rejected", "the pull request event is missing its identifiers")
    if action == "closed":
        # Nothing to analyze any more; stop whatever is queued for it.
        superseded = store.supersede_pull_request(repo, number)
        return Outcome("ignored", "pull request closed", superseded=tuple(superseded))
    if action not in TRIGGER_ACTIONS:
        return Outcome("ignored", f"pull_request {action} doesn't start an analysis")
    head_sha = str((pr.get("head") or {}).get("sha", ""))
    if len(head_sha) != 40:
        return Outcome("rejected", "no head commit")
    admission = decide(payload, settings, store, installation_id)
    if not admission.admit:
        return Outcome("rejected", admission.reason)
    job, superseded = store.enqueue(repo, number, head_sha, installation_id, delivery_id)
    return Outcome("queued", "queued", job_id=job.id, superseded=tuple(superseded))
