"""Parse `git diff` output into files, hunks and lines, and render it with line numbers.

File paths and statuses come from `git diff --raw -z`, whose NUL-separated output is
unambiguous. Hunks come from the patch output. The two are paired in order: git emits one
patch section per raw entry, except for type changes (for example file to symlink), which it
emits as a deletion section followed by an addition section.

GitHub only accepts review comments on lines inside diff hunks, so this module also answers
"is this line range commentable?" for anchoring findings.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from pr_investigator.domain import Side
from pr_investigator.errors import DiffParseError

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_NO_NEWLINE = "\\ No newline at end of file"

SYMLINK_MODE = "120000"
SUBMODULE_MODE = "160000"


class FileStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"


_STATUS_CODES = {
    "A": FileStatus.ADDED,
    "M": FileStatus.MODIFIED,
    "D": FileStatus.DELETED,
    "R": FileStatus.RENAMED,
    "C": FileStatus.COPIED,
    "T": FileStatus.TYPE_CHANGED,
}


class LineKind(StrEnum):
    CONTEXT = " "
    ADDED = "+"
    DELETED = "-"


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: LineKind
    text: str
    old_lineno: int | None
    new_lineno: int | None
    no_newline_at_eof: bool = False


@dataclass(slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def header(self) -> str:
        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@"

    def contains(self, side: Side, line: int) -> bool:
        """True if `line` is commentable on `side` within this hunk."""
        if side is Side.RIGHT:
            return any(dl.new_lineno == line for dl in self.lines)
        return any(dl.kind is LineKind.DELETED and dl.old_lineno == line for dl in self.lines)


@dataclass(slots=True)
class FileDiff:
    status: FileStatus
    old_path: str | None
    new_path: str | None
    old_mode: str
    new_mode: str
    similarity: int | None = None
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        """The path to show and anchor comments on: the new path unless the file was deleted."""
        path = self.new_path if self.new_path is not None else self.old_path
        assert path is not None
        return path

    @property
    def is_submodule(self) -> bool:
        return SUBMODULE_MODE in (self.old_mode, self.new_mode)

    @property
    def is_symlink(self) -> bool:
        return SYMLINK_MODE in (self.old_mode, self.new_mode)

    @property
    def added_lines(self) -> int:
        return sum(1 for h in self.hunks for dl in h.lines if dl.kind is LineKind.ADDED)

    @property
    def deleted_lines(self) -> int:
        return sum(1 for h in self.hunks for dl in h.lines if dl.kind is LineKind.DELETED)

    def commentable_lines(self, side: Side) -> set[int]:
        if side is Side.RIGHT:
            return {
                dl.new_lineno for h in self.hunks for dl in h.lines if dl.new_lineno is not None
            }
        return {
            dl.old_lineno
            for h in self.hunks
            for dl in h.lines
            if dl.kind is LineKind.DELETED and dl.old_lineno is not None
        }

    def is_commentable(self, side: Side, start_line: int, end_line: int) -> bool:
        """GitHub requires both ends of a (multi-line) comment to sit in the same hunk."""
        if start_line > end_line:
            return False
        return any(h.contains(side, start_line) and h.contains(side, end_line) for h in self.hunks)


@dataclass(slots=True)
class ParsedDiff:
    files: list[FileDiff]

    def file(self, path: str) -> FileDiff | None:
        for f in self.files:
            if path in (f.new_path, f.old_path):
                return f
        return None

    def is_commentable(self, path: str, side: Side, start_line: int, end_line: int) -> bool:
        f = self.file(path)
        return f is not None and f.is_commentable(side, start_line, end_line)


@dataclass(frozen=True, slots=True)
class _RawEntry:
    status: FileStatus
    old_mode: str
    new_mode: str
    old_path: str
    new_path: str
    similarity: int | None


def parse_raw(raw: bytes) -> list[_RawEntry]:
    """Parse `git diff --raw -z` output."""
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    entries: list[_RawEntry] = []
    i = 0
    while i < len(tokens):
        meta = tokens[i].decode("ascii", errors="strict")
        if not meta.startswith(":"):
            raise DiffParseError(f"unexpected raw diff token: {meta!r}")
        parts = meta[1:].split()
        if len(parts) != 5:
            raise DiffParseError(f"unexpected raw diff metadata: {meta!r}")
        old_mode, new_mode, _old_sha, _new_sha, code = parts
        status = _STATUS_CODES.get(code[0])
        if status is None:
            raise DiffParseError(f"unsupported diff status {code!r}")
        similarity = int(code[1:]) if code[0] in "RC" and len(code) > 1 else None
        if status in (FileStatus.RENAMED, FileStatus.COPIED):
            old_path, new_path = _decode_path(tokens[i + 1]), _decode_path(tokens[i + 2])
            i += 3
        else:
            old_path = new_path = _decode_path(tokens[i + 1])
            i += 2
        entries.append(_RawEntry(status, old_mode, new_mode, old_path, new_path, similarity))
    return entries


def _decode_path(raw: bytes) -> str:
    # surrogateescape keeps undecodable bytes round-trippable instead of losing them.
    return raw.decode("utf-8", errors="surrogateescape")


def _split_sections(patch: str) -> list[list[str]]:
    """Split patch text into one list of lines per `diff --git` section."""
    lines = patch.split("\n")  # not splitlines(): it would also split on \r, \x0c, U+2028, ...
    if lines and lines[-1] == "":
        lines.pop()
    sections: list[list[str]] = []
    for line in lines:
        if line.startswith("diff --git "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        elif line:
            raise DiffParseError(f"patch text before the first file header: {line[:80]!r}")
    return sections


def _parse_hunks(section: list[str]) -> tuple[list[Hunk], bool]:
    """Parse the hunks of one patch section. Returns (hunks, is_binary)."""
    hunks: list[Hunk] = []
    i = 0
    is_binary = False
    while i < len(section):
        line = section[i]
        match = _HUNK_HEADER.match(line)
        if match is None:
            if line.startswith("Binary files ") or line == "GIT binary patch":
                is_binary = True
            i += 1
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        hunk = Hunk(old_start, old_count, new_start, new_count, match.group(5).strip())
        old_no, new_no = old_start, new_start
        old_left, new_left = old_count, new_count
        i += 1
        # Hunk bodies are read by count, never by prefix: a deleted line can legitimately
        # look like "--- a/file" or even "@@ ... @@".
        while old_left > 0 or new_left > 0:
            if i >= len(section):
                raise DiffParseError(f"hunk {hunk.header} ends early")
            body = section[i]
            if body == _NO_NEWLINE:
                _mark_no_newline(hunk)
            elif body.startswith("+"):
                hunk.lines.append(DiffLine(LineKind.ADDED, body[1:], None, new_no))
                new_no += 1
                new_left -= 1
            elif body.startswith("-"):
                hunk.lines.append(DiffLine(LineKind.DELETED, body[1:], old_no, None))
                old_no += 1
                old_left -= 1
            elif body.startswith(" ") or body == "":
                hunk.lines.append(DiffLine(LineKind.CONTEXT, body[1:], old_no, new_no))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
            else:
                raise DiffParseError(f"unexpected line in hunk {hunk.header}: {body[:80]!r}")
            if old_left < 0 or new_left < 0:
                raise DiffParseError(f"hunk {hunk.header} has more lines than its header says")
            i += 1
        if i < len(section) and section[i] == _NO_NEWLINE:
            _mark_no_newline(hunk)
            i += 1
        hunks.append(hunk)
    return hunks, is_binary


def _mark_no_newline(hunk: Hunk) -> None:
    if not hunk.lines:
        raise DiffParseError("'No newline at end of file' marker with no preceding line")
    last = hunk.lines[-1]
    hunk.lines[-1] = DiffLine(last.kind, last.text, last.old_lineno, last.new_lineno, True)


def parse_diff(raw: bytes, patch: str) -> ParsedDiff:
    """Combine `git diff --raw -z` and `git diff` (patch) output for the same revision range."""
    entries = parse_raw(raw)
    sections = _split_sections(patch)
    expected = sum(2 if e.status is FileStatus.TYPE_CHANGED else 1 for e in entries)
    if expected != len(sections):
        raise DiffParseError(
            f"raw diff lists {len(entries)} files ({expected} patch sections expected) "
            f"but the patch has {len(sections)} sections"
        )
    files: list[FileDiff] = []
    cursor = 0
    for entry in entries:
        count = 2 if entry.status is FileStatus.TYPE_CHANGED else 1
        hunks: list[Hunk] = []
        is_binary = False
        for section in sections[cursor : cursor + count]:
            section_hunks, section_binary = _parse_hunks(section)
            hunks.extend(section_hunks)
            is_binary = is_binary or section_binary
        cursor += count
        files.append(
            FileDiff(
                status=entry.status,
                old_path=None if entry.status is FileStatus.ADDED else entry.old_path,
                new_path=None if entry.status is FileStatus.DELETED else entry.new_path,
                old_mode=entry.old_mode,
                new_mode=entry.new_mode,
                similarity=entry.similarity,
                is_binary=is_binary,
                hunks=hunks,
            )
        )
    return ParsedDiff(files)


def describe_file(f: FileDiff) -> str:
    """One-line label such as `app/x.py (renamed from app/y.py, +3 -1)`."""
    details = [f.status.value]
    if f.status in (FileStatus.RENAMED, FileStatus.COPIED) and f.old_path != f.new_path:
        details = [f"{f.status.value} from {f.old_path}"]
    if f.is_symlink:
        details.append("symlink")
    if f.is_binary:
        details.append("binary")
    elif f.hunks:
        details.append(f"+{f.added_lines} -{f.deleted_lines}")
    return f"{f.path} ({', '.join(details)})"


def render_file(f: FileDiff) -> str:
    """Render one file's hunks with explicit old/new line-number columns.

    Example:
        === app/x.py (modified, +1 -1) ===
        @@ -10,3 +10,3 @@ def handler():
            10     10    unchanged line
            11        -  deleted line
                   11 +  added line
    """
    out = [f"=== {describe_file(f)} ==="]
    if f.is_binary:
        out.append("(binary file, contents not shown)")
        return "\n".join(out)
    if f.is_submodule:
        out.append("(submodule change, contents not shown)")
        return "\n".join(out)
    if not f.hunks:
        out.append("(no content changes)")
    for hunk in f.hunks:
        section = f" {hunk.section}" if hunk.section else ""
        out.append(f"{hunk.header}{section}")
        for dl in hunk.lines:
            old = "" if dl.old_lineno is None else str(dl.old_lineno)
            new = "" if dl.new_lineno is None else str(dl.new_lineno)
            out.append(f"{old:>6} {new:>6} {dl.kind.value}  {dl.text}")
            if dl.no_newline_at_eof:
                out.append(f"{'':>6} {'':>6}    {_NO_NEWLINE}")
    return "\n".join(out)
