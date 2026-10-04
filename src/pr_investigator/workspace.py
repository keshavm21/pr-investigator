"""Git access for reviews: a private bare mirror per source, an optional head checkout, and diffs.

Repository content is untrusted, so git runs hardened:
- the user's global/system git config is ignored, so behaviour doesn't depend on it;
- hooks, fsmonitor, submodule recursion and LFS smudging are disabled;
- only the https and file transports are allowed;
- pathspecs are literal, so file names can't act as globs or pathspec magic;
- inherited GIT_* environment variables are dropped.
Nothing from a reviewed repository is ever executed.
"""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pr_investigator.diff import ParsedDiff, parse_diff
from pr_investigator.errors import GitError

_HARDENING_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "submodule.recurse=false",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "protocol.file.allow=always",
    "color.ui=never",
    "advice.detachedHead=false",
)

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_LITERAL_PATHSPECS": "1",
}

REGULAR_FILE_MODES = ("100644", "100755")


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    git_dir: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    timeout: float = 300,
) -> bytes:
    """Run a hardened git command and return its stdout. Raises GitError on failure."""
    cmd = ["git"]
    for item in _HARDENING_CONFIG:
        cmd += ["-c", item]
    if git_dir is not None:
        cmd.append(f"--git-dir={git_dir}")
    cmd += list(args)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_GIT_ENV)
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args[:2])} failed: {stderr}")
    return proc.stdout


@dataclass
class Workspace:
    """Review data for one base/head pair, backed by a private bare mirror."""

    mirror: Path
    base_sha: str
    head_sha: str
    merge_base_sha: str
    checkout_root: Path
    _checkout: Path | None = field(default=None, init=False)
    _diff: ParsedDiff | None = field(default=None, init=False)

    def diff(self) -> ParsedDiff:
        """The PR diff: merge base to head, as GitHub shows it."""
        if self._diff is None:
            common = ["--no-color", "--no-ext-diff", "--no-textconv", "-M", "--no-abbrev"]
            raw = run_git(
                ["diff", "--raw", "-z", *common, self.merge_base_sha, self.head_sha],
                git_dir=self.mirror,
            )
            patch = run_git(
                ["diff", "-U3", *common, self.merge_base_sha, self.head_sha], git_dir=self.mirror
            )
            self._diff = parse_diff(raw, patch.decode("utf-8", errors="replace"))
        return self._diff

    def ensure_checkout(self) -> Path:
        """Check out the head commit (once) and return the directory."""
        if self._checkout is None:
            dest = self.checkout_root / self.head_sha[:12]
            if dest.exists():
                self._remove_worktree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            run_git(
                ["worktree", "add", "--detach", "--force", str(dest), self.head_sha],
                git_dir=self.mirror,
            )
            self._checkout = dest
        return self._checkout

    def read_blob(self, rev: str, path: str) -> bytes:
        """Read a regular file's contents at `rev` straight from git objects."""
        listing = run_git(["ls-tree", "-z", rev, "--", path], git_dir=self.mirror)
        entry = listing.split(b"\0", 1)[0]
        if not entry:
            raise FileNotFoundError(path)
        meta, _, _name = entry.partition(b"\t")
        mode, kind, sha = meta.decode().split()
        if kind != "blob" or mode not in REGULAR_FILE_MODES:
            raise IsADirectoryError(f"{path} is not a regular file at {rev[:12]} (mode {mode})")
        return run_git(["cat-file", "blob", sha], git_dir=self.mirror)

    def blob_size(self, rev: str, path: str) -> int:
        out = run_git(["cat-file", "-s", f"{rev}:{path}"], git_dir=self.mirror)
        return int(out.strip())

    def cleanup(self) -> None:
        if self._checkout is not None:
            self._remove_worktree(self._checkout)
            self._checkout = None

    def _remove_worktree(self, dest: Path) -> None:
        try:
            run_git(["worktree", "remove", "--force", str(dest)], git_dir=self.mirror)
        except GitError:
            shutil.rmtree(dest, ignore_errors=True)
        run_git(["worktree", "prune"], git_dir=self.mirror)


def mirror_path(workspaces_dir: Path, source: str) -> Path:
    key = hashlib.sha256(source.encode()).hexdigest()[:16]
    return workspaces_dir / "mirrors" / f"{key}.git"


def ensure_mirror(workspaces_dir: Path, source: str) -> Path:
    mirror = mirror_path(workspaces_dir, source)
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        run_git(["init", "--bare", "--quiet", str(mirror)])
    return mirror


def fetch(
    mirror: Path, source: str, refspecs: Sequence[str], *, allow_any_sha: bool = False
) -> None:
    """Fetch `refspecs` from `source` (an https URL or a local path) into the mirror."""
    args = ["fetch", "--no-tags", "--no-recurse-submodules", "--force", "--quiet", source]
    extra: list[str] = []
    if allow_any_sha:
        # Lets a local upload-pack serve commits by SHA (passed down via GIT_CONFIG_PARAMETERS).
        extra = ["-c", "uploadpack.allowAnySHA1InWant=true"]
    run_git([*extra, *args, *refspecs], git_dir=mirror)


def resolve_commit(rev: str, *, git_dir: Path | None = None, cwd: Path | None = None) -> str:
    out = run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"],
        git_dir=git_dir,
        cwd=cwd,
    )
    return out.decode().strip()


def merge_base(mirror: Path, base: str, head: str) -> str:
    return run_git(["merge-base", base, head], git_dir=mirror).decode().strip()


def update_ref(mirror: Path, ref: str, sha: str) -> None:
    run_git(["update-ref", ref, sha], git_dir=mirror)
