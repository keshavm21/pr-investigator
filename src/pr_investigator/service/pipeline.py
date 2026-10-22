"""The two worker roles, split by credentials (docs/security.md §4).

Fetcher (control plane): turns a queued job into a local workspace. It mints a one-hour token for
one repository with `contents: read`, fetches, reads the repository config from the base branch,
and stores the workspace's location. The token is never stored.

Analyzer (worker): runs a reviewer on that local workspace. It holds the LLM key and nothing
else: it refuses to start if it can see a GitHub credential, and it makes no GitHub request.
"""

import asyncio
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pr_investigator import ingest
from pr_investigator.config import Settings
from pr_investigator.domain import PullRequestRef, ReviewTarget
from pr_investigator.errors import ConfigError, PRIError
from pr_investigator.github import GitHubClient
from pr_investigator.llm.client import ensure_data_allowed
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.guards import RequestBudget
from pr_investigator.review.report import create_run_dir, save_run
from pr_investigator.review.reviewers import Reviewer, run_reviewer
from pr_investigator.service import store as states
from pr_investigator.service.app_auth import AppAuth
from pr_investigator.service.config import FORBIDDEN_IN_WORKER, ServiceSettings
from pr_investigator.service.repo_config import RepoConfig, apply_ignore, load_repo_config
from pr_investigator.service.store import Job, ServiceStore
from pr_investigator.trace import TRACE_FILE, Trace
from pr_investigator.workspace import Workspace


def assert_no_github_credentials(settings: Settings, environ: Mapping[str, str]) -> None:
    """The analysis worker's guard: it must not be able to talk to GitHub as the App."""
    present = [name for name in FORBIDDEN_IN_WORKER if environ.get(name)]
    if settings.github_token is not None and not {"GITHUB_TOKEN", "PRI_GITHUB_TOKEN"} & set(
        present
    ):
        present.append("GITHUB_TOKEN (from .env)")
    if present:
        raise ConfigError(
            "the analysis worker must not hold GitHub credentials, but found: "
            + ", ".join(present)
            + ". Run it in its own environment with only the LLM key."
        )


# Fetcher --------------------------------------------------------------------------------------


def fetch_job(
    job: Job,
    store: ServiceStore,
    auth: AppAuth,
    settings: Settings,
    service: ServiceSettings,
    *,
    github: GitHubClient | None = None,
) -> str:
    """Fetch one claimed job (state `fetching`). Returns the state it ended in."""
    owner, name = job.repo.split("/", 1)
    token = auth.fetcher_token(job.installation_id, name)
    client = github or GitHubClient(token.token)
    try:
        info = client.get_pull_request(PullRequestRef(owner=owner, repo=name, number=job.pr_number))
    finally:
        if github is None:
            client.close()
    if info.state != "open" or info.head_sha != job.head_sha:
        # A newer push (or a close) arrived: this analysis would be of a stale head.
        store.advance(job.id, states.FETCHING, states.SUPERSEDED, error="head moved before fetch")
        return states.SUPERSEDED
    if info.base_repo_private and not service.allow_private:
        store.advance(
            job.id, states.FETCHING, states.SKIPPED, error="private repositories are disabled"
        )
        return states.SKIPPED
    target, workspace = ingest.from_github(info, settings.workspaces_dir, token=token.token)
    config, warnings = load_repo_config(workspace)
    if not config.enabled:
        store.advance(
            job.id, states.FETCHING, states.SKIPPED, error="disabled by .pr-investigator.yml"
        )
        return states.SKIPPED
    payload = {
        "target": target.model_dump(mode="json"),
        "workspace": {
            "mirror": str(workspace.mirror),
            "base_sha": workspace.base_sha,
            "head_sha": workspace.head_sha,
            "merge_base_sha": workspace.merge_base_sha,
            "checkout_root": str(workspace.checkout_root),
        },
        "config": config.model_dump(mode="json"),
        "warnings": warnings,
    }
    store.advance(job.id, states.FETCHING, states.READY, payload=payload)
    return states.READY


# Analyzer -------------------------------------------------------------------------------------


def workspace_from(payload: dict[str, Any], workspaces_dir: Path) -> Workspace:
    """Rebuild the workspace the fetcher made, refusing paths outside the workspaces directory."""
    raw = payload["workspace"]
    mirror = Path(raw["mirror"]).resolve()
    root = Path(raw["checkout_root"]).resolve()
    base = workspaces_dir.resolve()
    if not (mirror.is_relative_to(base) and root.is_relative_to(base)):
        raise PRIError("the job's workspace is outside the workspaces directory")
    return Workspace(
        mirror=mirror,
        base_sha=raw["base_sha"],
        head_sha=raw["head_sha"],
        merge_base_sha=raw["merge_base_sha"],
        checkout_root=root,
    )


def analyze_job(job: Job, store: ServiceStore, settings: Settings, service: ServiceSettings) -> str:
    """Analyze one claimed job (state `analyzing`). Returns the state it ended in."""
    payload = job.payload
    target = ReviewTarget.model_validate(payload["target"])
    config = RepoConfig.model_validate(payload["config"])
    workspace = workspace_from(payload, settings.workspaces_dir)
    reviewer = Reviewer(config.reviewer or service.reviewer)
    client = build_client(settings, budget=RequestBudget(service.max_requests_per_run))
    ensure_data_allowed(target.data_class, client.capabilities, client.provider)
    diff, ignored = apply_ignore(workspace.diff(), config.ignore_paths)
    run_dir = create_run_dir(settings.runs_dir)
    with Trace(run_dir / TRACE_FILE) as trace:
        review = asyncio.run(
            run_reviewer(
                reviewer,
                target,
                workspace,
                diff,
                client,
                max_diff_chars=settings.max_diff_chars,
                trace=trace,
            )
        )
    skipped = {path: "ignored by .pr-investigator.yml" for path in ignored}
    coverage = review.coverage.model_copy(
        update={"skipped": {**review.coverage.skipped, **skipped}}
    )
    update: dict[str, Any] = {"coverage": coverage}
    notes = [f"Note: {w}" for w in payload.get("warnings", [])]
    if notes:
        update["summary"] = review.summary + " " + " ".join(notes)
    if hasattr(review, "posted_ids"):
        update["posted_ids"] = review.posted_ids[: config.max_comments]
    save_run(review.model_copy(update=update), run_dir)
    if not store.advance(job.id, states.ANALYZING, states.AWAITING_REVIEW, run_id=run_dir.name):
        # A newer push superseded this job while it ran: keep the run, but record the link.
        store.advance(job.id, states.SUPERSEDED, states.SUPERSEDED, run_id=run_dir.name)
        return states.SUPERSEDED
    return states.AWAITING_REVIEW


# Loops ----------------------------------------------------------------------------------------


def fetch_once(
    store: ServiceStore, auth: AppAuth, settings: Settings, service: ServiceSettings
) -> bool:
    job = store.claim(states.QUEUED, states.FETCHING)
    if job is None:
        return False
    try:
        fetch_job(job, store, auth, settings, service)
    except PRIError as exc:
        store.fail(job.id, f"fetch failed: {exc}")
    return True


def analyze_once(store: ServiceStore, settings: Settings, service: ServiceSettings) -> bool:
    job = store.claim(states.READY, states.ANALYZING)
    if job is None:
        return False
    try:
        analyze_job(job, store, settings, service)
    except PRIError as exc:
        store.fail(job.id, f"analysis failed: {exc}")
    return True


def run_loop(
    step: Callable[[], bool], *, interval: float = 5.0, stop: Callable[[], bool] | None = None
) -> None:
    """Call `step()` forever; sleep only when it found no work."""
    while stop is None or not stop():
        if not step():
            time.sleep(interval)
