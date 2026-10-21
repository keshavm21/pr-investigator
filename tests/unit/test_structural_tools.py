"""The structural tools: what they show, and that observations cover exactly the shown lines."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from pr_investigator.index.python import Import
from pr_investigator.index.store import RepoIndex
from pr_investigator.tools import ToolContext, ToolResult, Toolset, run_tool
from pr_investigator.tools.base import clip_line
from pr_investigator.tools.find_tests import module_names, resolved
from pr_investigator.tools.read_file import load_lines
from tests.support import MakeRepo, OpenWorkspace

SERVICE = """from app.store import load, save


def get_user(user_id):
    return load(user_id)


class Service:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def rename(
        user_id,
        name,
    ):
        user = get_user(user_id)
        user.name = name
        return user
"""
ROUTES = """from .service import Service, get_user


def show(request):
    return get_user(request.args["id"])


def update(request):
    # get_user in a comment
    label = "get_user"
    return Service.rename(request.args["id"], request.args["name"])
"""
STORE = "def load(key):\n    return {'key': key}\n\n\ndef save(record):\n    return record\n"
TEST_SERVICE = """from app.service import get_user


def test_get_user():
    assert get_user(1)["key"] == 1


class TestService:
    def test_rename(self):
        assert Service.rename
"""
TEST_ROUTES = "from app import routes\n\n\ndef test_show():\n    assert routes.show\n"


@pytest.fixture
def ctx(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> Iterator[ToolContext]:
    repo = make_repo("repo")
    repo.write("app/__init__.py", "")
    repo.write("app/service.py", SERVICE)
    repo.write("app/routes.py", ROUTES)
    repo.write("app/store.py", STORE)
    repo.write("tests/test_service.py", TEST_SERVICE)
    repo.write("tests/test_routes.py", TEST_ROUTES)
    repo.write("many.py", "".join(f"value_{n} = get_user({n})\n" for n in range(1, 121)))
    repo.commit("Initial import")
    repo.branch("pr")
    repo.write(
        "app/service.py",
        SERVICE.replace("        return user\n", "        save(user)\n        return user\n"),
    )
    repo.write("notes.txt", "not python\n")
    repo.commit("Save renamed users")
    _target, workspace = open_workspace(repo)
    index = RepoIndex(Path("index.sqlite"), workspace)
    yield ToolContext(workspace=workspace, diff=workspace.diff(), index=index)
    index.close()


def call(ctx: ToolContext, tool: str, **arguments: object) -> ToolResult:
    return run_tool(tool, dict(arguments), ctx, Toolset.STRUCTURAL.tools)


def assert_exact_observations(ctx: ToolContext, result: ToolResult) -> None:
    """Every observed line is displayed with its real text, and every displayed row is observed."""
    rows = [row for row in result.content.split("\n") if re.match(r"^  \s*\d+  ", row)]
    observed = 0
    for o in result.observations:
        if o.kind == "search":
            continue
        assert o.path is not None and o.start_line is not None and o.end_line is not None
        lines = load_lines(ctx.workspace, o.path, o.ref)
        for n in range(o.start_line, o.end_line + 1):
            assert f"  {n:>6}  {clip_line(lines[n - 1])}" in rows, (o.path, n)
            observed += 1
    assert observed == len(rows)


def test_find_symbol_shows_decorators_and_signature(ctx: ToolContext) -> None:
    result = call(ctx, "find_symbol", name="Service.rename")
    assert "app/service.py: method Service.rename, lines 12-20" in result.content
    assert "1 definition of 'Service.rename' at head" in result.content
    search, lines = result.observations
    assert (search.kind, search.match_count) == ("search", 1)
    assert (lines.path, lines.start_line, lines.end_line) == ("app/service.py", 12, 16)
    assert_exact_observations(ctx, result)


def test_find_symbol_filters_by_kind_and_records_absence(ctx: ToolContext) -> None:
    assert "No class definitions" in call(ctx, "find_symbol", name="get_user", kind="class").content
    result = call(ctx, "find_symbol", name="missing_thing")
    (search,) = result.observations
    assert search.kind == "search" and search.match_count == 0  # citable as absence


def test_find_symbol_reads_the_base_version(ctx: ToolContext) -> None:
    result = call(ctx, "find_symbol", name="rename", ref="base")
    assert result.observations[1].ref == "base"
    assert_exact_observations(ctx, result)


def test_find_references_skips_comments_and_strings_and_groups_by_scope(ctx: ToolContext) -> None:
    result = call(ctx, "find_references", name="get_user", path_glob="app/**")
    assert "Name-based" in result.content
    assert "app/routes.py, module level:" in result.content
    assert "app/routes.py, in show:" in result.content
    assert "app/service.py, in Service.rename:" in result.content
    assert "# get_user in a comment" not in result.content
    assert 'label = "get_user"' not in result.content
    assert "many.py" not in result.content  # outside the glob
    assert result.observations[0].match_count == 3  # the def line is a definition
    assert_exact_observations(ctx, result)


def test_find_references_takes_one_identifier(ctx: ToolContext) -> None:
    result = call(ctx, "find_references", name="Service.rename")
    assert result.is_error and "Invalid arguments" in result.content


def test_find_references_stops_at_the_budget(ctx: ToolContext) -> None:
    small = ToolContext(ctx.workspace, ctx.diff, max_output_chars=1200, index=ctx.index)
    result = call(small, "find_references", name="get_user", max_results=100)
    assert result.truncated and len(result.content) <= 1200
    assert "more lines not shown" in result.content
    assert_exact_observations(small, result)


def test_get_symbol_context_shows_the_chain_callers_and_callees(ctx: ToolContext) -> None:
    result = call(ctx, "get_symbol_context", path="app/service.py", line=19)
    content = result.content
    assert "class Service, lines 8-20:" in content
    assert "method Service.rename, lines 12-20:" in content
    assert "Calls to 'rename' elsewhere: 1 line." in content
    assert "app/routes.py, in update:" in content
    assert "get_user -> app/service.py: function get_user" in content
    assert "save -> app/store.py: function save" in content
    assert "Use read_file on app/service.py lines 12-20" in content
    assert_exact_observations(ctx, result)


def test_get_symbol_context_at_module_level_and_bad_input(ctx: ToolContext) -> None:
    result = call(ctx, "get_symbol_context", path="app/service.py", line=1)
    assert "module level" in result.content and not result.observations
    assert "only Python files" in call(ctx, "get_symbol_context", path="notes.txt", line=1).content
    beyond = call(ctx, "get_symbol_context", path="app/store.py", line=99)
    assert beyond.is_error and "only 6 lines" in beyond.content


def test_get_symbol_context_stays_within_a_small_budget(ctx: ToolContext) -> None:
    small = ToolContext(ctx.workspace, ctx.diff, max_output_chars=700, index=ctx.index)
    result = call(small, "get_symbol_context", path="app/service.py", line=19)
    assert result.truncated and len(result.content) <= 700
    assert_exact_observations(small, result)


def test_find_tests_by_symbol_groups_by_test(ctx: ToolContext) -> None:
    result = call(ctx, "find_tests", symbol="Service.rename")
    assert "tests/test_service.py, test TestService.test_rename:" in result.content
    assert "app/" not in result.content  # only test files
    assert_exact_observations(ctx, result)


def test_find_tests_by_symbol_mixes_module_level_and_test_uses(ctx: ToolContext) -> None:
    result = call(ctx, "find_tests", symbol="get_user")  # imported, then used in a test
    assert not result.is_error, result.content
    content = result.content
    assert content.index("tests/test_service.py, module level:") < content.index(
        "tests/test_service.py, test test_get_user:"
    )
    assert_exact_observations(ctx, result)


def test_find_tests_by_module_follows_imports(ctx: ToolContext) -> None:
    service = call(ctx, "find_tests", path="app/service.py")
    assert "tests/test_service.py imports it:" in service.content
    assert "tests in this file: test_get_user (4), TestService.test_rename (9)" in service.content
    assert "test_routes" not in service.content
    routes = call(ctx, "find_tests", path="app/routes.py")  # `from app import routes`
    assert "tests/test_routes.py imports it:" in routes.content
    assert_exact_observations(ctx, service)
    nothing = call(ctx, "find_tests", path="app/store.py")
    assert nothing.observations[0].match_count == 0


def test_find_tests_needs_exactly_one_target(ctx: ToolContext) -> None:
    assert call(ctx, "find_tests").is_error
    assert call(ctx, "find_tests", symbol="x", path="app/store.py").is_error


def test_module_names_and_relative_imports() -> None:
    assert module_names("src/pkg/mod.py") == {"src.pkg.mod", "pkg.mod"}
    assert module_names("pkg/__init__.py") == {"pkg"}
    assert resolved(Import(1, 1, "service", "x", None), "app/routes.py") == {"app.service"}
    assert resolved(Import(1, 2, "", "core", None), "src/pkg/sub/m.py") == {"src.pkg", "pkg"}


def test_git_history_lists_commits_without_observations(ctx: ToolContext) -> None:
    result = call(ctx, "git_history", path="app/service.py", start_line=17, end_line=19)
    assert not result.observations  # commit messages are claims, never evidence
    rows = [row for row in result.content.split("\n") if row.startswith("  ")]
    assert len(rows) == 2
    assert "[this PR]  Save renamed users" in rows[0]
    assert "[this PR]" not in rows[1] and "Initial import" in rows[1]
    whole = call(ctx, "git_history", path="app/store.py")
    assert "Initial import" in whole.content and "[this PR]" not in whole.content


def test_git_history_rejects_bad_ranges(ctx: ToolContext) -> None:
    assert call(ctx, "git_history", path="app/service.py", start_line=5).is_error
    assert call(ctx, "git_history", path="app/service.py", start_line=5, end_line=2).is_error
    assert call(ctx, "git_history", path="../outside.py").is_error
    beyond = call(ctx, "git_history", path="app/store.py", start_line=50, end_line=60)
    assert beyond.is_error and "can't read the history" in beyond.content


def test_structural_tools_need_an_index(ctx: ToolContext) -> None:
    bare = ToolContext(ctx.workspace, ctx.diff)
    result = call(bare, "find_symbol", name="get_user")
    assert result.is_error and "index isn't available" in result.content
