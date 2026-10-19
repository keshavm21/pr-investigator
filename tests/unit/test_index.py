"""The structural index: facts extracted by tree-sitter, and storage keyed by blob SHA."""

from pathlib import Path

import pytest

from pr_investigator.index import store
from pr_investigator.index.python import Import, parse_python
from pr_investigator.index.store import RepoIndex
from tests.support import MakeRepo, OpenWorkspace

SOURCE = b'''import os.path as osp
from . import util
from ..core import (Map,
    Rule as R)

@app.route("/x")
def handler(req, limit: int = DEFAULT, *args, flag=True, **kw):
    """handler is mentioned in a docstring"""
    # get_user in a comment
    user = get_user(req.user_id)
    return render(user.name, limit=limit, label="get_user")


class Map(Base):
    def bind(
        self, host,
    ) -> "Adapter":
        def inner():
            return helper()
        return self._adapter(host)
'''


def test_definitions_have_decorator_signature_and_body_lines() -> None:
    symbols = {s.qualname: s for s in parse_python(SOURCE).symbols}
    assert set(symbols) == {"handler", "Map", "Map.bind", "Map.bind.inner"}
    handler = symbols["handler"]
    assert (handler.kind, handler.start_line, handler.def_line) == ("function", 6, 7)
    assert (handler.header_end_line, handler.end_line) == (7, 11)
    bind = symbols["Map.bind"]
    assert (bind.kind, bind.parent, bind.def_line, bind.header_end_line) == (
        "method",
        "Map",
        15,
        17,
    )
    assert bind.end_line == 20
    assert symbols["Map.bind.inner"].kind == "function"  # nested in a method, not a method
    assert symbols["Map"].kind == "class"


def test_references_are_identifiers_in_code_with_their_kind_and_scope() -> None:
    refs = {(r.name, r.line): (r.kind, r.scope) for r in parse_python(SOURCE).references}
    assert refs[("get_user", 10)] == ("call", "handler")
    assert refs[("user_id", 10)] == ("attribute", "handler")
    assert refs[("req", 10)] == ("name", "handler")
    assert refs[("limit", 11)] == ("name", "handler")  # the value of limit=limit
    assert refs[("route", 6)] == ("call", None)  # decorators belong to the enclosing scope
    assert refs[("helper", 19)] == ("call", "Map.bind.inner")
    assert refs[("_adapter", 20)] == ("call", "Map.bind")
    assert refs[("Rule", 4)] == ("import", None)
    assert refs[("DEFAULT", 7)] == ("name", "handler")  # a default value is a use
    kinds = {r.kind for r in parse_python(SOURCE).references if r.name == "limit"}
    assert kinds == {"keyword", "name"}
    names = {r.name for r in parse_python(SOURCE).references}
    # Definitions, strings, comments and docstrings are not references; nor are parameters
    # where they are declared (`self` and `host` count only where the body uses them).
    assert not names & {"flag", "args", "kw", "handler", "bind", "inner"}
    assert not {(n, line) for n, line in refs if line in (7, 16)} & {
        ("req", 7),
        ("limit", 7),
        ("self", 16),
        ("host", 16),
    }
    assert sum(1 for r in parse_python(SOURCE).references if r.name == "get_user") == 1


def test_imports_keep_relative_levels_and_aliases() -> None:
    assert parse_python(SOURCE).imports == (
        Import(1, 0, "os.path", None, "osp"),
        Import(2, 1, "", "util", None),
        Import(3, 2, "core", "Map", None),
        Import(4, 2, "core", "Rule", "R"),
    )


def test_code_with_syntax_errors_still_yields_facts() -> None:
    facts = parse_python(b"def ok():\n    return call()\n\ndef broken(:\n    pass\n")
    assert "ok" in {s.qualname for s in facts.symbols}
    assert ("call", 2) in {(r.name, r.line) for r in facts.references}


def test_deep_nesting_does_not_overflow_the_stack() -> None:
    facts = parse_python(b"x = " + b"(" * 3000 + b"y" + b")" * 3000 + b"\n")
    assert [r.name for r in facts.references] == ["x", "y"]


@pytest.fixture
def two_commits(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> RepoIndex:
    repo = make_repo("repo")
    for i in range(5):
        repo.write(f"pkg/mod_{i}.py", f"def f_{i}():\n    return {i}\n")
    repo.write("README.md", "def not_python(): pass\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("pkg/mod_0.py", "def f_0():\n    return 'changed'\n")
    repo.commit("pr")
    _target, workspace = open_workspace(repo)
    return RepoIndex(Path("index.sqlite"), workspace)


def test_only_unseen_blobs_are_parsed(two_commits: RepoIndex) -> None:
    index = two_commits
    assert len(index.snapshot("head").files) == 5  # README.md isn't Python
    assert index.parsed_blobs == 5
    index.snapshot("base")
    assert index.parsed_blobs == 6  # the base adds one blob: the old mod_0.py
    index.close()

    again = RepoIndex(index.db_path, index.workspace)
    assert [p for p, _ in again.definitions("head", "f_0")] == ["pkg/mod_0.py"]
    assert again.parsed_blobs == 0  # a second review of the same commits parses nothing
    again.close()


def test_head_and_base_see_their_own_versions(two_commits: RepoIndex) -> None:
    index = two_commits
    head = index.references_in("head", "pkg/mod_0.py")
    base = index.references_in("base", "pkg/mod_0.py")
    assert head == base == []  # literals only
    assert (
        index.snapshot("head").files["pkg/mod_0.py"] != index.snapshot("base").files["pkg/mod_0.py"]
    )
    index.close()


def test_a_new_parser_version_reindexes(
    two_commits: RepoIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_commits.snapshot("head")
    two_commits.close()
    monkeypatch.setattr(store, "PARSER_VERSION", 99)
    again = RepoIndex(two_commits.db_path, two_commits.workspace)
    assert len(again.definitions("head", "f_1")) == 1
    assert again.parsed_blobs == 5
    again.close()


def test_large_files_are_listed_but_not_indexed(
    two_commits: RepoIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_INDEXED_BYTES", 10)
    snapshot = two_commits.snapshot("head")
    assert snapshot.files == {}
    assert len(snapshot.too_large) == 5
    two_commits.close()
