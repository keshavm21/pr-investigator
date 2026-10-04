"""The installed package and the `pri` entry point work the way a user runs them.

These run a fresh interpreter, so nothing pytest put on sys.path can hide an install problem.
"""

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

from tests.environment import broken_install_message, hidden_pth_files
from tests.support import REPO_ROOT


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def test_site_packages_has_no_hidden_pth_files() -> None:
    assert hidden_pth_files() == [], broken_install_message()


def test_fresh_interpreter_imports_the_working_tree(tmp_path: Path) -> None:
    result = _run(
        [sys.executable, "-c", "import pr_investigator.cli as cli; print(cli.__file__)"], tmp_path
    )
    assert result.returncode == 0, f"{result.stderr}\n{broken_install_message()}"
    # An editable install points at src/, so the code under test is the code being edited.
    assert Path(result.stdout.strip()).is_relative_to(REPO_ROOT / "src")


def test_pri_entry_point_runs(tmp_path: Path) -> None:
    pri = Path(sysconfig.get_path("scripts")) / "pri"
    assert pri.is_file(), f"no console script at {pri}; run `uv sync`"
    result = _run([str(pri), "--help"], tmp_path)
    assert result.returncode == 0, f"{result.stderr}\n{broken_install_message()}"
    assert "review" in result.stdout and "eval" in result.stdout
