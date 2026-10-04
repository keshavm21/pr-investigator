"""Diagnose a broken install. Deliberately imports nothing from pr_investigator."""

import os
import stat
import sys
import sysconfig
from pathlib import Path


def site_packages() -> Path:
    return Path(sysconfig.get_paths()["purelib"])


def hidden_pth_files() -> list[str]:
    """.pth files with the macOS hidden flag, which Python 3.13+ skips at startup."""
    hidden_flag = getattr(stat, "UF_HIDDEN", 0)
    return sorted(
        path.name
        for path in site_packages().glob("*.pth")
        if getattr(os.lstat(path), "st_flags", 0) & hidden_flag
    )


def broken_install_message() -> str:
    lines = [
        f"pr_investigator can't be imported by {sys.executable}.",
        f"The editable install puts src/ on sys.path through a .pth file in {site_packages()}.",
    ]
    hidden = hidden_pth_files()
    if hidden:
        lines += [
            f"These .pth files have the macOS 'hidden' flag, and Python 3.13+ skips hidden .pth "
            f"files: {', '.join(hidden)}.",
            "iCloud Drive sets that flag on everything inside dot-folders such as .venv when the "
            "project lives in a synced folder (Desktop or Documents). See 'macOS and iCloud' in "
            "CLAUDE.md.",
        ]
    else:
        lines.append("Run `uv sync` to install the project, then try again.")
    return "\n".join(lines)
