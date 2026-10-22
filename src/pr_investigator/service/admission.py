"""Admission policy: which pull requests the service analyzes. Cheap checks, before any work.

The service spends LLM quota and fetches code, so it is closed by default: only allowlisted
repositories are analyzed, drafts wait until they are ready, private repositories are refused
unless the operator allows them, and each installation has a daily budget.
"""

from dataclasses import dataclass
from typing import Any

from pr_investigator.service.config import ServiceSettings
from pr_investigator.service.store import ServiceStore


@dataclass(frozen=True)
class Admission:
    admit: bool
    reason: str


def decide(
    payload: dict[str, Any], settings: ServiceSettings, store: ServiceStore, installation_id: int
) -> Admission:
    pr = payload.get("pull_request") or {}
    repository = payload.get("repository") or {}
    repo = str(repository.get("full_name", "")).lower()
    state = store.installation_state(installation_id)
    if state in ("deleted", "suspended"):
        return Admission(False, f"the installation is {state}")
    if repo not in settings.allowlist:
        return Admission(False, f"{repo or 'this repository'} isn't on the allowlist")
    if pr.get("state") != "open":
        return Admission(False, "the pull request isn't open")
    if pr.get("draft"):
        return Admission(False, "drafts are analyzed when they are marked ready for review")
    if repository.get("private") and not settings.allow_private:
        return Admission(False, "private repositories are disabled")
    files = int(pr.get("changed_files") or 0)
    lines = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
    if files > settings.max_changed_files:
        return Admission(
            False, f"{files} changed files is over the limit of {settings.max_changed_files}"
        )
    if lines > settings.max_changed_lines:
        return Admission(
            False, f"{lines} changed lines is over the limit of {settings.max_changed_lines}"
        )
    if store.jobs_today(installation_id) >= settings.daily_runs_per_installation:
        return Admission(False, "the installation's daily budget is used up")
    return Admission(True, "admitted")
