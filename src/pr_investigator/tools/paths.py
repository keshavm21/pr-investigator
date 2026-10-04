"""Confine every tool path to the checkout. Paths are untrusted input (they come from the model)."""

from pathlib import Path, PurePosixPath

from pr_investigator.tools.base import ToolError


def validate_relative_path(path: str) -> PurePosixPath:
    """Normalize a repository-relative path, rejecting anything that could point elsewhere."""
    if not path or path.strip() == "":
        raise ToolError("path must not be empty")
    if "\0" in path:
        raise ToolError("path must not contain NUL bytes")
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise ToolError(f"path must be relative to the repository root, got {path!r}")
    parts = [part for part in pure.parts if part != "."]
    if ".." in parts:
        raise ToolError(f"path must not contain '..': {path!r}")
    if ".git" in parts:
        raise ToolError("paths inside .git are not readable")
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def resolve_in_root(root: Path, path: str) -> Path:
    """Resolve `path` under `root`, following symlinks only if they stay inside `root`."""
    relative = validate_relative_path(path)
    try:
        resolved = (root / relative).resolve(strict=True)
    except FileNotFoundError:
        raise ToolError(f"no such file or directory: {relative}") from None
    except (OSError, RuntimeError) as exc:  # RuntimeError: symlink loop on older Pythons
        raise ToolError(f"can't resolve {relative}: {exc}") from None
    if not resolved.is_relative_to(root.resolve()):
        raise ToolError(f"{relative} resolves outside the repository; refusing to read it")
    return resolved
