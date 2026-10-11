"""Tool output budgets: output stays within the budget, and observations cover exactly the
lines that were shown (the evidence check depends on this)."""

import re
import shutil

import pytest

from pr_investigator.tools import ToolContext, run_tool
from pr_investigator.tools.base import Observation, lines_sha256
from pr_investigator.tools.read_file import load_lines
from tests.support import MakeRepo, OpenWorkspace

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")

LONG_LINE = "x = '" + "y" * 2000 + "'  # needle"


@pytest.fixture
def ctx(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> ToolContext:
    repo = make_repo("repo")
    repo.write("big.py", "".join(f"value_{n} = {n}  # needle\n" for n in range(1, 201)))
    repo.write("long.py", f"a = 1\n{LONG_LINE}\nb = 2\n")
    for name in ("a", "b"):
        repo.write(f"{name}.py", "".join(f"{name}_{n} = {n}\n" for n in range(1, 61)))
    repo.write("c.py", "".join(f"c_{n} = {n}\n" for n in range(1, 21)))
    for i in range(60):
        repo.write(f"dir/file_{i:02}.py", "pass\n")
    repo.commit("base")
    repo.branch("pr")
    a_lines = [f"a_{n} = {n}\n" for n in range(1, 61)]
    for n in (5, 30, 55):  # three separate hunks
        a_lines[n - 1] = f"a_{n} = {n} * 2\n"
    repo.write("a.py", "".join(a_lines))
    b_lines = [f"b_{n} = {n}\n" for n in range(1, 61)]
    b_lines[9] = "b_10 = 'changed'\n"
    repo.write("b.py", "".join(b_lines))
    c_lines = [f"c_{n} = {n}\n" for n in range(1, 21)]
    del c_lines[9:12]  # deletes c_10..c_12
    repo.write("c.py", "".join(c_lines))
    repo.write("huge.py", "".join(f"huge_{n} = {n}\n" for n in range(1, 301)))
    repo.commit("pr")
    _target, workspace = open_workspace(repo)
    return ToolContext(workspace=workspace, diff=workspace.diff())


def with_budget(ctx: ToolContext, chars: int) -> ToolContext:
    return ToolContext(workspace=ctx.workspace, diff=ctx.diff, max_output_chars=chars)


def assert_hash_matches_repository(ctx: ToolContext, observation: Observation) -> None:
    assert observation.path is not None
    assert observation.start_line is not None and observation.end_line is not None
    lines = load_lines(ctx.workspace, observation.path, observation.ref)
    expected = lines_sha256(lines[observation.start_line - 1 : observation.end_line])
    assert observation.content_sha256 == expected


def diff_rows_shown(content: str) -> dict[tuple[str, str], set[int]]:
    """(path, 'head'|'base') -> line numbers displayed in get_diff output."""
    shown: dict[tuple[str, str], set[int]] = {}
    path = ""
    for row in content.split("\n"):
        heading = re.match(r"^=== (\S+) \(", row)
        if heading:
            path = heading.group(1)
            continue
        if len(row) >= 15 and row[13] == " " and row[14] in " +-":
            old, new = row[0:6].strip(), row[7:13].strip()
            if new:
                shown.setdefault((path, "head"), set()).add(int(new))
            if old:
                shown.setdefault((path, "base"), set()).add(int(old))
    return shown


def test_read_file_stops_at_the_budget_and_observes_only_shown_lines(ctx: ToolContext) -> None:
    result = run_tool("read_file", {"path": "big.py"}, with_budget(ctx, 1200))
    (observation,) = result.observations
    assert result.truncated and len(result.content) <= 1200
    assert observation.start_line == 1 and observation.end_line is not None
    rows = re.findall(r"^\s+(\d+)  ", result.content, re.M)
    assert [int(r) for r in rows] == list(range(1, observation.end_line + 1))
    assert f"continue with start_line={observation.end_line + 1}" in result.content
    assert_hash_matches_repository(ctx, observation)


def test_read_file_hashes_full_lines_even_when_display_is_shortened(ctx: ToolContext) -> None:
    result = run_tool("read_file", {"path": "long.py"}, with_budget(ctx, 4000))
    assert "[line truncated]" in result.content
    assert_hash_matches_repository(ctx, result.observations[0])


@needs_rg
def test_search_shows_whole_groups_within_budget(ctx: ToolContext) -> None:
    args = {"pattern": "needle", "max_results": 100, "context_lines": 1}
    result = run_tool("search_code", args, with_budget(ctx, 1500))
    assert result.truncated and len(result.content) <= 1500
    search, *groups = result.observations
    assert search.kind == "search" and search.match_count == 201
    assert groups, "at least one group is shown"
    for observation in groups:
        assert observation.start_line is not None and observation.end_line is not None
        for n in range(observation.start_line, observation.end_line + 1):
            assert re.search(rf"^  {n:>6}[:-] ", result.content, re.M), n
        assert_hash_matches_repository(ctx, observation)
    shown = len(re.findall(r"^  \s*\d+: ", result.content, re.M))
    assert f"; showing {shown} in" in result.content
    assert f"[{201 - shown} more matches not shown" in result.content


@needs_rg
def test_search_hashes_full_lines_even_when_display_is_shortened(ctx: ToolContext) -> None:
    result = run_tool("search_code", {"pattern": "needle", "path_glob": "long.py"}, ctx)
    assert "[line truncated]" in result.content
    (line_observation,) = [o for o in result.observations if o.kind == "lines"]
    assert_hash_matches_repository(ctx, line_observation)


def test_get_diff_stops_on_a_hunk_boundary_and_observes_only_shown_lines(
    ctx: ToolContext,
) -> None:
    result = run_tool("get_diff", {}, with_budget(ctx, 1500))
    assert result.truncated and len(result.content) <= 1500
    assert "Not shown" in result.content
    shown = diff_rows_shown(result.content)
    assert result.observations
    for observation in result.observations:
        assert observation.path is not None
        assert observation.start_line is not None and observation.end_line is not None
        observed = set(range(observation.start_line, observation.end_line + 1))
        assert observed <= shown[(observation.path, observation.ref)]
        assert_hash_matches_repository(ctx, observation)


def test_get_diff_records_the_base_side_of_deletions(ctx: ToolContext) -> None:
    result = run_tool("get_diff", {"path": "c.py"}, ctx)
    base = [o for o in result.observations if o.ref == "base"]
    assert len(base) == 1 and base[0].start_line is not None and base[0].end_line is not None
    assert base[0].start_line <= 10 and base[0].end_line >= 12  # covers the deleted lines
    assert_hash_matches_repository(ctx, base[0])


def test_get_diff_pages_through_a_file_by_hunk(ctx: ToolContext) -> None:
    first = run_tool("get_diff", {"path": "a.py"}, with_budget(ctx, 700))
    assert "start_hunk=2" in first.content
    second = run_tool("get_diff", {"path": "a.py", "start_hunk": 2}, with_budget(ctx, 700))
    assert "@@ -27," in second.content and "@@ -2," not in second.content
    for observation in second.observations:
        assert_hash_matches_repository(ctx, observation)
    beyond = run_tool("get_diff", {"path": "a.py", "start_hunk": 9}, ctx)
    assert beyond.is_error and "only 3 hunks" in beyond.content


def test_get_diff_cuts_an_oversized_hunk_and_observes_only_its_shown_part(
    ctx: ToolContext,
) -> None:
    result = run_tool("get_diff", {"path": "huge.py"}, with_budget(ctx, 1500))
    assert "[hunk cut short after" in result.content
    (observation,) = result.observations
    assert observation.start_line == 1 and observation.end_line is not None
    assert observation.end_line < 300
    assert (
        set(range(1, observation.end_line + 1))
        == diff_rows_shown(result.content)[("huge.py", "head")]
    )
    assert_hash_matches_repository(ctx, observation)


def test_list_directory_stops_at_the_budget(ctx: ToolContext) -> None:
    result = run_tool("list_directory", {"path": "dir", "max_entries": 500}, with_budget(ctx, 800))
    assert result.truncated and len(result.content) <= 800
    assert "listing cut short" in result.content
