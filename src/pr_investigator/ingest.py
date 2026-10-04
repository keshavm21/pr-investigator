"""Turn a GitHub PR or a local branch comparison into a ReviewTarget plus a Workspace."""

from pathlib import Path

from pr_investigator import workspace as ws
from pr_investigator.domain import DataClass, ReviewTarget
from pr_investigator.errors import GitError, PRIError
from pr_investigator.github import PullRequestInfo

BASE_REF = "refs/pri/base"
HEAD_REF = "refs/pri/head"


def data_class_for(info: PullRequestInfo) -> DataClass:
    return DataClass.PRIVATE if info.base_repo_private else DataClass.PUBLIC


def from_github(info: PullRequestInfo, workspaces_dir: Path) -> tuple[ReviewTarget, ws.Workspace]:
    """Fetch the PR's base branch and head into a private mirror.

    Callers check the data policy (`data_class_for`) before calling this, so private code is
    never even fetched for a provider that may not see it.
    """
    mirror = ws.ensure_mirror(workspaces_dir, info.clone_url)
    # The PR head lives at refs/pull/N/head on the base repository, which also covers forks.
    ws.fetch(
        mirror,
        info.clone_url,
        [
            f"+refs/heads/{info.base_ref}:{BASE_REF}",
            f"+refs/pull/{info.ref.number}/head:{HEAD_REF}",
        ],
    )
    base_sha = _resolve(mirror, info.base_sha, "PR base")
    head_sha = _resolve(mirror, info.head_sha, "PR head")
    target = ReviewTarget(
        title=info.title,
        description=info.body,
        base_sha=base_sha,
        head_sha=head_sha,
        merge_base_sha=ws.merge_base(mirror, base_sha, head_sha),
        data_class=data_class_for(info),
        source=info.html_url,
        pull_request=info.ref,
    )
    return target, _workspace(mirror, target, workspaces_dir)


def from_local(
    repo: Path,
    base: str,
    head: str,
    *,
    title: str,
    description: str = "",
    data_class: DataClass,
    workspaces_dir: Path,
) -> tuple[ReviewTarget, ws.Workspace]:
    """Review `head` against `base` in a local repository, without modifying that repository."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise PRIError(f"not a directory: {repo}")
    try:
        base_sha = ws.resolve_commit(base, cwd=repo)
        head_sha = ws.resolve_commit(head, cwd=repo)
    except GitError as exc:
        raise PRIError(f"can't resolve {base!r} or {head!r} in {repo}: {exc}") from exc
    source = str(repo)
    mirror = ws.ensure_mirror(workspaces_dir, source)
    ws.fetch(mirror, source, [base_sha, head_sha], allow_any_sha=True)
    ws.update_ref(mirror, BASE_REF, base_sha)
    ws.update_ref(mirror, HEAD_REF, head_sha)
    target = ReviewTarget(
        title=title,
        description=description,
        base_sha=base_sha,
        head_sha=head_sha,
        merge_base_sha=ws.merge_base(mirror, base_sha, head_sha),
        data_class=data_class,
        source=source,
    )
    return target, _workspace(mirror, target, workspaces_dir)


def _resolve(mirror: Path, sha: str, label: str) -> str:
    try:
        return ws.resolve_commit(sha, git_dir=mirror)
    except GitError as exc:
        raise PRIError(f"{label} commit {sha[:12]} wasn't fetched (force-pushed?)") from exc


def _workspace(mirror: Path, target: ReviewTarget, workspaces_dir: Path) -> ws.Workspace:
    return ws.Workspace(
        mirror=mirror,
        base_sha=target.base_sha,
        head_sha=target.head_sha,
        merge_base_sha=target.merge_base_sha,
        checkout_root=workspaces_dir / "checkouts" / mirror.stem,
    )
