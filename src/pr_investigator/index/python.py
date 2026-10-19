"""Facts about one Python file, from tree-sitter: definitions, name references and imports.

tree-sitter is error-tolerant, so code that doesn't parse cleanly (common in PRs) still yields
facts for the parts that do. Nothing is executed or imported. The facts depend only on the file's
bytes, never on its path, so they can be stored under the blob SHA and shared between commits.

References are name-based: every identifier in code, outside strings and comments, except the
names that definitions and parameters introduce. They are not resolved to a definition.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from typing import Literal

import tree_sitter
import tree_sitter_python

# Bump when the extracted facts change, so stored facts are rebuilt rather than reused.
PARSER_VERSION = 1

SymbolKind = Literal["class", "function", "method"]
RefKind = Literal["call", "attribute", "name", "keyword", "import"]

_IMPORT_PARTS = ("dotted_name", "aliased_import", "relative_import")
_IMPORTS = ("import_statement", "import_from_statement", "future_import_statement")
_PARAMETER_LISTS = ("parameters", "lambda_parameters")
_NAMED_PARAMETERS = ("default_parameter", "typed_default_parameter")
_SPLATS = ("list_splat_pattern", "dictionary_splat_pattern")


@dataclass(frozen=True)
class Symbol:
    name: str
    qualname: str  # nested names joined with dots: "Map.bind", "outer.inner"
    kind: SymbolKind
    start_line: int  # the first decorator, if any
    def_line: int  # the `def` or `class` keyword
    header_end_line: int  # the line ending the signature (its colon)
    end_line: int
    parent: str | None  # the enclosing symbol's qualname


@dataclass(frozen=True)
class Reference:
    name: str
    line: int
    kind: RefKind
    scope: str | None  # qualname of the innermost enclosing symbol; None at module level


@dataclass(frozen=True)
class Import:
    line: int
    level: int  # leading dots of a relative import; 0 for an absolute one
    module: str  # as written, without the leading dots ("" for `from . import x`)
    name: str | None  # the imported name for `from m import name`; None for `import m`
    alias: str | None


@dataclass(frozen=True)
class FileFacts:
    symbols: tuple[Symbol, ...]
    references: tuple[Reference, ...]
    imports: tuple[Import, ...]


@cache
def _parser() -> tree_sitter.Parser:
    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))


def parse_python(data: bytes) -> FileFacts:
    tree = _parser().parse(data)
    symbols: list[Symbol] = []
    references: list[Reference] = []
    imports: list[Import] = []
    # An explicit stack rather than recursion: untrusted files may nest arbitrarily deep.
    stack: list[tuple[tree_sitter.Node, str | None, SymbolKind | None]] = [
        (tree.root_node, None, None)
    ]
    while stack:
        node, scope, scope_kind = stack.pop()
        children = node.children
        if node.type in ("function_definition", "class_definition"):
            symbol = _symbol(node, scope, scope_kind)
            if symbol is not None:
                symbols.append(symbol)
                name_node = node.child_by_field_name("name")
                stack += [(c, symbol.qualname, symbol.kind) for c in children if c != name_node]
                continue
        elif node.type == "identifier":
            kind = _reference_kind(node)
            if kind is not None:
                references.append(Reference(_text(node), _line(node), kind, scope))
        elif node.type in ("import_statement", "import_from_statement"):
            imports += _imports(node)
        stack += [(c, scope, scope_kind) for c in children]
    return FileFacts(
        symbols=tuple(sorted(symbols, key=lambda s: (s.start_line, s.qualname))),
        references=tuple(sorted(references, key=lambda r: (r.line, r.name, r.kind))),
        imports=tuple(sorted(imports, key=lambda i: (i.line, i.module, i.name or ""))),
    )


def _symbol(
    node: tree_sitter.Node, scope: str | None, scope_kind: SymbolKind | None
) -> Symbol | None:
    name_node = node.child_by_field_name("name")
    body = node.child_by_field_name("body")
    if name_node is None or body is None:
        return None
    name = _text(name_node)
    kind: SymbolKind = "class" if node.type == "class_definition" else "function"
    if kind == "function" and scope_kind == "class":
        kind = "method"
    outer = node.parent if node.parent and node.parent.type == "decorated_definition" else node
    colon = body.prev_sibling or body
    return Symbol(
        name=name,
        qualname=f"{scope}.{name}" if scope else name,
        kind=kind,
        start_line=_line(outer),
        def_line=_line(node),
        header_end_line=max(_line(node), _last_line(colon)),
        end_line=max(_line(node), _last_line(node)),
        parent=scope,
    )


def _reference_kind(node: tree_sitter.Node) -> RefKind | None:
    parent = node.parent
    if parent is None:
        return "name"
    kind = parent.type
    if kind == "attribute":
        if parent.child_by_field_name("attribute") != node:
            return "name"  # the object: `x` in `x.y`
        return "call" if _calls(parent) else "attribute"
    if kind == "call" and parent.child_by_field_name("function") == node:
        return "call"
    if kind == "keyword_argument" and parent.child_by_field_name("name") == node:
        return "keyword"
    if _is_parameter(node):
        return None
    ancestor: tree_sitter.Node | None = parent
    while ancestor is not None and ancestor.type in _IMPORT_PARTS:
        ancestor = ancestor.parent
    if ancestor is not None and ancestor.type in _IMPORTS:
        return "import"
    return "name"


def _calls(attribute: tree_sitter.Node) -> bool:
    parent = attribute.parent
    return (
        parent is not None
        and parent.type == "call"
        and (parent.child_by_field_name("function") == attribute)
    )


def _is_parameter(node: tree_sitter.Node) -> bool:
    """True for a name a parameter introduces (not for names in defaults or annotations)."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type in _SPLATS:
        node, parent = parent, parent.parent
        if parent is None:
            return False
    if parent.type in _PARAMETER_LISTS:
        return True
    if parent.type in _NAMED_PARAMETERS:
        return parent.child_by_field_name("name") == node
    if parent.type == "typed_parameter":
        return parent.child_by_field_name("type") != node
    return False


def _imports(node: tree_sitter.Node) -> Iterator[Import]:
    if node.type == "import_statement":
        for part in node.named_children:
            module, alias = _aliased(part)
            if module is not None:
                yield Import(_line(part), 0, module, None, alias)
        return
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return
    level, module = 0, _text(module_node)
    if module_node.type == "relative_import":
        prefix = next((c for c in module_node.children if c.type == "import_prefix"), None)
        dotted = next((c for c in module_node.children if c.type == "dotted_name"), None)
        level = len(_text(prefix)) if prefix is not None else 0
        module = _text(dotted) if dotted is not None else ""
    if any(c.type == "wildcard_import" for c in node.children):
        yield Import(_line(node), level, module, "*", None)
        return
    for part in node.children_by_field_name("name"):
        name, alias = _aliased(part)
        if name is not None:
            yield Import(_line(part), level, module, name, alias)


def _aliased(node: tree_sitter.Node) -> tuple[str | None, str | None]:
    if node.type == "dotted_name":
        return _text(node), None
    if node.type == "aliased_import":
        name, alias = node.child_by_field_name("name"), node.child_by_field_name("alias")
        return (_text(name) if name else None), (_text(alias) if alias else None)
    return None, None


def _text(node: tree_sitter.Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _line(node: tree_sitter.Node) -> int:
    return node.start_point.row + 1


def _last_line(node: tree_sitter.Node) -> int:
    """The last line a node covers. An end at column 0 belongs to the line before."""
    end = node.end_point
    return end.row + 1 if end.column > 0 else max(end.row, node.start_point.row + 1)
