"""Read-only tools: confinement, output shape, observations and error handling."""

import shutil

import pytest

from pr_investigator.tools import READ_ONLY_TOOLS, ToolContext, run_tool, tool_specs
from pr_investigator.tools.base import ToolError
from pr_investigator.tools.paths import validate_relative_path
from tests.support import GitRepo, MakeRepo, OpenWorkspace

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


@pytest.fixture
def ctx(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> ToolContext:
    repo: GitRepo = make_repo("repo")
    repo.write("app/service.py", "def handler(user):\n    return lookup(user)\n")
    repo.write("app/old_module.py", "OLD = True\n")
    repo.write("big.txt", "".join(f"row {n}\n" for n in range(1, 501)))
    repo.write(".gitignore", "secret_*.py\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("app/service.py", "def handler(user):\n    check(user)\n    return lookup(user)\n")
    repo.remove("app/old_module.py")
    repo.write(".github/workflows/ci.yml", "run: deploy --token lookup\n")
    repo.write("secret_config.py", "lookup = 'committed despite .gitignore'\n")
    repo.write("data.bin", b"\x00\x01\x02 lookup")
    repo.symlink("escape_link", "/etc/hosts")
    repo.symlink("inner_link", "app/service.py")
    repo.commit("pr")
    _target, workspace = open_workspace(repo)
    return ToolContext(workspace=workspace, diff=workspace.diff())


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../outside.txt", "a/../../b", ".git/config"])
def test_rejects_paths_that_could_escape(bad: str) -> None:
    with pytest.raises(ToolError):
        validate_relative_path(bad)


def test_normalizes_harmless_paths() -> None:
    assert str(validate_relative_path("./app//service.py")) == "app/service.py"


def test_read_file_numbers_lines_and_records_an_observation(ctx: ToolContext) -> None:
    result = run_tool("read_file", {"path": "app/service.py"}, ctx)
    assert not result.is_error
    assert "     2      check(user)" in result.content
    (obs,) = result.observations
    assert (obs.path, obs.ref, obs.start_line, obs.end_line) == ("app/service.py", "head", 1, 3)
    assert obs.content_sha256 is not None


def test_read_file_caps_lines_per_call(ctx: ToolContext) -> None:
    result = run_tool("read_file", {"path": "big.txt", "start_line": 50}, ctx)
    assert result.truncated
    assert "lines 50-449 of 500" in result.content
    assert "continue with start_line=450" in result.content


def test_read_file_base_version_and_deleted_files(ctx: ToolContext) -> None:
    base = run_tool("read_file", {"path": "app/service.py", "ref": "base"}, ctx)
    assert "check(user)" not in base.content and base.observations[0].ref == "base"
    deleted = run_tool("read_file", {"path": "app/old_module.py", "ref": "base"}, ctx)
    assert "OLD = True" in deleted.content
    missing = run_tool("read_file", {"path": "app/old_module.py"}, ctx)
    assert missing.is_error and "no such file" in missing.content


def test_read_file_refuses_symlink_escape_binary_and_bad_ranges(ctx: ToolContext) -> None:
    escape = run_tool("read_file", {"path": "escape_link"}, ctx)
    assert escape.is_error and "outside the repository" in escape.content
    inner = run_tool("read_file", {"path": "inner_link"}, ctx)
    assert not inner.is_error and "check(user)" in inner.content
    assert run_tool("read_file", {"path": "data.bin"}, ctx).is_error
    assert run_tool("read_file", {"path": "app"}, ctx).is_error
    beyond = run_tool("read_file", {"path": "app/service.py", "start_line": 99}, ctx)
    assert beyond.is_error and "only 3 lines" in beyond.content


@needs_rg
def test_search_finds_hidden_and_gitignored_committed_files(ctx: ToolContext) -> None:
    result = run_tool("search_code", {"pattern": "lookup"}, ctx)
    assert not result.is_error
    assert "app/service.py" in result.content
    assert ".github/workflows/ci.yml" in result.content  # hidden directories are searched
    assert "secret_config.py" in result.content  # .gitignore can't hide committed files
    assert "data.bin" not in result.content  # binary files are skipped
    assert any(o.kind == "search" and o.match_count for o in result.observations)
    assert any(o.kind == "lines" and o.path == "app/service.py" for o in result.observations)


@needs_rg
def test_search_regex_glob_context_and_limits(ctx: ToolContext) -> None:
    result = run_tool(
        "search_code",
        {"pattern": r"check\(\w+\)", "regex": True, "path_glob": "app/*.py", "context_lines": 1},
        ctx,
    )
    assert "     2: " in result.content and "     1- " in result.content
    capped = run_tool("search_code", {"pattern": "row", "max_results": 3}, ctx)
    assert capped.truncated and "more matches not shown" in capped.content


@needs_rg
def test_search_reports_no_matches_and_invalid_regex(ctx: ToolContext) -> None:
    none = run_tool("search_code", {"pattern": "definitely_not_present"}, ctx)
    assert not none.is_error and none.content.startswith("No matches")
    assert none.observations[0].match_count == 0
    bad = run_tool("search_code", {"pattern": "(unclosed", "regex": True}, ctx)
    assert bad.is_error and bad.content.startswith("search failed")


def test_list_directory_shows_tree_without_following_symlinks(ctx: ToolContext) -> None:
    result = run_tool("list_directory", {"path": "."}, ctx)
    assert "app/" in result.content and "service.py" in result.content
    assert "escape_link -> (symlink)" in result.content
    assert ".git/" not in result.content
    assert run_tool("list_directory", {"path": "big.txt"}, ctx).is_error


def test_get_diff_for_one_file_and_unknown_paths(ctx: ToolContext) -> None:
    result = run_tool("get_diff", {"path": "app/service.py"}, ctx)
    assert "     2 +      check(user)" in result.content
    assert {(o.ref, o.path) for o in result.observations} == {("head", "app/service.py")}
    unknown = run_tool("get_diff", {"path": "README.md"}, ctx)
    assert unknown.is_error and "app/service.py" in unknown.content
    everything = run_tool("get_diff", {}, ctx)
    assert "app/old_module.py (deleted" in everything.content


def test_registry_validates_arguments_and_names(ctx: ToolContext) -> None:
    assert run_tool("write_file", {}, ctx).is_error
    invalid = run_tool("read_file", {"path": "app/service.py", "start_line": 0}, ctx)
    assert invalid.is_error and "Invalid arguments" in invalid.content
    specs = {spec.name: spec for spec in tool_specs()}
    assert set(specs) == {t.name for t in READ_ONLY_TOOLS}
    assert specs["read_file"].parameters["properties"]["path"]["type"] == "string"
