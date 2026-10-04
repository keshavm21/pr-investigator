"""Diff parsing, commentable lines and rendering."""

import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pr_investigator import ingest
from pr_investigator.diff import FileStatus, LineKind, ParsedDiff, parse_diff, render_file
from pr_investigator.domain import DataClass, Side
from pr_investigator.errors import DiffParseError
from tests.support import GitRepo, MakeRepo, OpenWorkspace


def diff_between(repo: GitRepo, open_workspace: OpenWorkspace) -> ParsedDiff:
    _target, workspace = open_workspace(repo)
    return workspace.diff()


def test_parses_statuses_paths_and_special_files(
    make_repo: MakeRepo, open_workspace: OpenWorkspace
) -> None:
    repo = make_repo("repo")
    repo.write("modified.txt", "one\ntwo\nthree\n")
    repo.write("deleted.txt", "gone\n")
    repo.write("old name.txt", "r1\nr2\nr3\nr4\n")
    repo.write("image.bin", b"\x00\x01binary")
    repo.write("script.sh", "echo hi\n")
    repo.write("becomes_link", "plain file\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("modified.txt", "one\nTWO\nthree\n")
    repo.remove("deleted.txt")
    repo.git("mv", "old name.txt", "new name.txt")
    repo.write("image.bin", b"\x00\x02binary")
    (repo.path / "script.sh").chmod(0o755)
    repo.remove("becomes_link")
    repo.symlink("becomes_link", "modified.txt")
    repo.write("tab\tname.txt", "hello\n")
    repo.commit("pr")

    diff = diff_between(repo, open_workspace)
    by_path = {f.path: f for f in diff.files}

    assert by_path["modified.txt"].status is FileStatus.MODIFIED
    assert by_path["modified.txt"].added_lines == 1
    assert by_path["deleted.txt"].status is FileStatus.DELETED
    assert by_path["deleted.txt"].new_path is None
    renamed = by_path["new name.txt"]
    assert renamed.status is FileStatus.RENAMED and renamed.old_path == "old name.txt"
    assert renamed.similarity == 100
    assert by_path["image.bin"].is_binary
    assert by_path["script.sh"].hunks == [] and by_path["script.sh"].new_mode == "100755"
    link = by_path["becomes_link"]
    assert link.status is FileStatus.TYPE_CHANGED and link.is_symlink
    assert by_path["tab\tname.txt"].status is FileStatus.ADDED


def test_hunk_bodies_are_read_by_count_not_prefix() -> None:
    # A deleted line whose content is "-- a/x" renders as "--- a/x" and an added "++ b" as
    # "+++ b": they must be treated as content, not as file headers.
    raw = b":100644 100644 aaaa bbbb M\0f.txt\0"
    patch = (
        "diff --git a/f.txt b/f.txt\n"
        "index 1..2 100644\n"
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " keep\n"
        "--- a/x\n"
        "+++ b\n"
        " tail\n"
        "\\ No newline at end of file\n"
    )
    (file,) = parse_diff(raw, patch).files
    kinds = [(dl.kind, dl.text) for dl in file.hunks[0].lines]
    assert kinds == [
        (LineKind.CONTEXT, "keep"),
        (LineKind.DELETED, "-- a/x"),
        (LineKind.ADDED, "++ b"),
        (LineKind.CONTEXT, "tail"),
    ]
    assert file.hunks[0].lines[-1].no_newline_at_eof


def test_mismatched_section_count_is_an_error() -> None:
    raw = b":100644 100644 aaaa bbbb M\0a.txt\0:100644 100644 cccc dddd M\0b.txt\0"
    patch = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-x\n+y\n"
    with pytest.raises(DiffParseError):
        parse_diff(raw, patch)


def test_truncated_hunk_is_an_error() -> None:
    raw = b":100644 100644 aaaa bbbb M\0a.txt\0"
    patch = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n x\n"
    with pytest.raises(DiffParseError):
        parse_diff(raw, patch)


def test_commentable_lines_and_same_hunk_rule(
    make_repo: MakeRepo, open_workspace: OpenWorkspace
) -> None:
    repo = make_repo("repo")
    repo.write("f.py", "".join(f"line {n}\n" for n in range(1, 41)))
    repo.commit("base")
    repo.branch("pr")
    lines = [f"line {n}\n" for n in range(1, 41)]
    lines[4] = "changed 5\n"  # first hunk around line 5
    lines[34] = "changed 35\n"  # second hunk around line 35
    del lines[19]  # deletion of old line 20
    repo.write("f.py", "".join(lines))
    repo.commit("pr")

    diff = diff_between(repo, open_workspace)
    f = diff.file("f.py")
    assert f is not None
    right = f.commentable_lines(Side.RIGHT)
    assert {2, 5, 8} <= right and 15 not in right
    assert f.commentable_lines(Side.LEFT) == {5, 20, 35}
    assert f.is_commentable(Side.RIGHT, 4, 6)
    assert not f.is_commentable(Side.RIGHT, 5, 34)  # spans two hunks
    assert not f.is_commentable(Side.RIGHT, 6, 4)  # reversed
    assert f.is_commentable(Side.LEFT, 20, 20)
    assert not f.is_commentable(Side.LEFT, 21, 21)  # unchanged line, not a deletion


def test_render_shows_both_line_number_columns() -> None:
    raw = b":100644 100644 aaaa bbbb M\0f.py\0"
    patch = (
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        "@@ -10,2 +10,2 @@ def handler():\n context\n-old\n+new\n"
    )
    (file,) = parse_diff(raw, patch).files
    rendered = render_file(file)
    assert "=== f.py (modified, +1 -1) ===" in rendered
    assert "@@ -10,2 +10,2 @@ def handler():" in rendered
    assert "    10     10    context" in rendered
    assert "    11        -  old" in rendered
    assert "           11 +  new" in rendered


_LINES = st.lists(
    st.sampled_from(["a", "b", "c", "", "x y", "--- a/f", "+++ b", "@@ -1 +1 @@", "\\ text"]),
    max_size=25,
)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(old=_LINES, new=_LINES, old_eol=st.booleans(), new_eol=st.booleans())
def test_parsed_hunks_reproduce_the_new_file(
    old: list[str], new: list[str], old_eol: bool, new_eol: bool
) -> None:
    """Property: applying the parsed hunks to the old file yields the new file, and every
    parsed line number points at the right text."""
    old_text = "\n".join(old) + ("\n" if old and old_eol else "")
    new_text = "\n".join(new) + ("\n" if new and new_eol else "")
    with tempfile.TemporaryDirectory() as tmp:
        repo = GitRepo(Path(tmp) / "repo")
        repo.write("f.txt", old_text)
        repo.commit("base")
        repo.branch("pr")
        repo.write("f.txt", new_text)
        repo.commit("pr")
        _target, workspace = ingest.from_local(
            repo.path,
            "main",
            "pr",
            title="t",
            data_class=DataClass.TEST,
            workspaces_dir=Path(tmp) / "ws",
        )
        diff = workspace.diff()

    old_lines = old_text.split("\n")[:-1] if old_text.endswith("\n") else old_text.split("\n")
    new_lines = new_text.split("\n")[:-1] if new_text.endswith("\n") else new_text.split("\n")
    if old_text == "":
        old_lines = []
    if new_text == "":
        new_lines = []
    if old_text == new_text:
        assert diff.files == []
        return
    (file,) = diff.files
    rebuilt: list[str] = []
    cursor = 1
    for hunk in file.hunks:
        stop = hunk.old_start + 1 if hunk.old_count == 0 else hunk.old_start
        rebuilt += old_lines[cursor - 1 : stop - 1]
        cursor = stop
        for dl in hunk.lines:
            if dl.old_lineno is not None:
                assert old_lines[dl.old_lineno - 1] == dl.text
            if dl.new_lineno is not None:
                assert new_lines[dl.new_lineno - 1] == dl.text
            if dl.kind is LineKind.CONTEXT:
                rebuilt.append(dl.text)
                cursor += 1
            elif dl.kind is LineKind.DELETED:
                cursor += 1
            else:
                rebuilt.append(dl.text)
    rebuilt += old_lines[cursor - 1 :]
    assert rebuilt == new_lines
