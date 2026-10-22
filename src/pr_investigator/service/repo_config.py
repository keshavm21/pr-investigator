"""Repository configuration: `.pr-investigator.yml`, read from the BASE branch only.

A setting that changes what is reviewed or how must not be controllable by the pull request's
author, so the file is read from the base commit's tree, never from the head or the checkout
(docs/security.md, invariant 5). A missing, oversized or invalid file means the defaults, with a
warning. Unknown keys are an error, so a typo can't silently loosen a setting.
"""

from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pr_investigator.diff import ParsedDiff
from pr_investigator.workspace import Workspace

CONFIG_PATH = ".pr-investigator.yml"
MAX_CONFIG_BYTES = 20_000


class RepoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    ignore_paths: list[str] = Field(default_factory=list, max_length=100)
    reviewer: Literal["a1", "a2", "a3"] | None = None
    max_comments: int = Field(default=7, ge=1, le=7)  # D17's cap is a ceiling, not a default

    def model_post_init(self, _context: object) -> None:
        for pattern in self.ignore_paths:
            if not pattern or len(pattern) > 200 or "\0" in pattern:
                raise ValueError(f"invalid ignore pattern {pattern[:40]!r}")


def load_repo_config(workspace: Workspace) -> tuple[RepoConfig, list[str]]:
    """The base branch's config and any warnings about it."""
    try:
        data = workspace.read_blob(workspace.base_sha, CONFIG_PATH)
    except (FileNotFoundError, IsADirectoryError):
        return RepoConfig(), []
    except Exception as exc:  # a git failure must not stop a review
        return RepoConfig(), [f"couldn't read {CONFIG_PATH} on the base branch: {exc}"]
    if len(data) > MAX_CONFIG_BYTES:
        return RepoConfig(), [f"{CONFIG_PATH} is over {MAX_CONFIG_BYTES} bytes; using defaults"]
    try:
        raw = yaml.safe_load(data.decode("utf-8", errors="replace")) or {}
        if not isinstance(raw, dict):
            raise ValueError("the file must be a mapping")
        return RepoConfig.model_validate(raw), []
    except (yaml.YAMLError, ValueError, ValidationError) as exc:
        first = str(exc).splitlines()[0][:200]
        return RepoConfig(), [
            f"{CONFIG_PATH} on the base branch is invalid ({first}); using defaults"
        ]


def apply_ignore(diff: ParsedDiff, patterns: list[str]) -> tuple[ParsedDiff, list[str]]:
    """The diff without the ignored files, and the paths that were left out."""
    if not patterns:
        return diff, []
    from pathlib import PurePosixPath

    def ignored(path: str) -> bool:
        pure = PurePosixPath(path)
        return any(pure.full_match(p) or pure.match(p) for p in patterns)

    kept = [f for f in diff.files if not ignored(f.path)]
    left_out = [f.path for f in diff.files if ignored(f.path)]
    return ParsedDiff(files=kept), left_out
