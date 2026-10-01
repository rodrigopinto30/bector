"""Tree-sitter implementation of ``CodeParser`` for Python source."""

import ast
import inspect
from collections.abc import Iterator

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Point

from healer.domain.models import FileMap, SourceFile, Symbol, SymbolKind

MAX_SOURCE_CHARS = 20_000

_TRANSPARENT = frozenset(
    {
        "block",
        "if_statement",
        "elif_clause",
        "else_clause",
        "try_statement",
        "except_clause",
        "except_group_clause",
        "finally_clause",
        "with_statement",
        "for_statement",
        "while_statement",
        "match_statement",
        "case_clause",
    }
)


class PythonParser:
    """Extracts modules, classes, functions and methods without executing the code.

    Tree-sitter is error tolerant, so files that fail to compile (the usual input of a
    remediation tool) still yield every definition that could be recognised.
    """

    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_python.language()))

    def parse(self, file: SourceFile) -> FileMap:
        source = file.content.encode("utf-8")
        tree = self._parser.parse(source)
        root = tree.root_node
        symbols = [self._module_symbol(file.path, root, source)]
        symbols.extend(self._definitions(file.path, root, source, parent=None))
        return FileMap(file=file.path, symbols=tuple(symbols), has_syntax_errors=root.has_error)

    def _module_symbol(self, path: str, root: Node, source: bytes) -> Symbol:
        name = path.rsplit("/", 1)[-1].removesuffix(".py")
        imports = tuple(dict.fromkeys(self._imports(root, source)))
        text = "\n".join(
            _text(node, source) for node in self._scope_nodes(root) if _is_import(node)
        )
        body, truncated = _cap(text)
        return Symbol(
            file=path,
            kind=SymbolKind.MODULE,
            name=name,
            qualified_name=name,
            start_line=1,
            end_line=_line(root.end_point),
            docstring=self._docstring(root, source),
            imports=imports,
            source=body,
            truncated=truncated,
        )

    def _definitions(
        self, path: str, scope: Node, source: bytes, parent: Symbol | None
    ) -> Iterator[Symbol]:
        for node in self._scope_nodes(scope):
            definition = (
                node.child_by_field_name("definition")
                if node.type == "decorated_definition"
                else node
            )
            if definition is None or definition.type not in {
                "function_definition",
                "class_definition",
            }:
                continue
            symbol = self._symbol(path, node, definition, source, parent)
            yield symbol
            if definition.type == "class_definition":
                body = definition.child_by_field_name("body")
                if body is not None:
                    yield from self._definitions(path, body, source, parent=symbol)

    def _scope_nodes(self, scope: Node) -> Iterator[Node]:
        """Yield the statements of one scope, looking through ``if``/``try``/``with`` blocks."""
        for child in scope.children:
            if child.type in _TRANSPARENT:
                yield from self._scope_nodes(child)
            else:
                yield child

    def _symbol(
        self, path: str, outer: Node, definition: Node, source: bytes, parent: Symbol | None
    ) -> Symbol:
        is_class = definition.type == "class_definition"
        name = _text(definition.child_by_field_name("name"), source)
        if is_class:
            kind = SymbolKind.CLASS
        elif parent is not None and parent.kind is SymbolKind.CLASS:
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION
        body = definition.child_by_field_name("body")
        header_end = body.start_byte if body is not None else definition.end_byte
        signature = " ".join(source[definition.start_byte : header_end].decode().split())
        text, truncated = _cap(_text(outer, source))
        return Symbol(
            file=path,
            kind=kind,
            name=name,
            qualified_name=f"{parent.qualified_name}.{name}" if parent else name,
            start_line=_line(outer.start_point),
            end_line=_line(outer.end_point),
            signature=signature,
            docstring=self._docstring(body, source),
            decorators=tuple(
                _text(child, source) for child in outer.children if child.type == "decorator"
            ),
            source=text,
            truncated=truncated,
        )

    def _docstring(self, body: Node | None, source: bytes) -> str:
        if body is None or not body.children:
            return ""
        first = next((c for c in body.children if c.type != "comment"), None)
        if first is None or first.type != "expression_statement" or not first.children:
            return ""
        literal = first.children[0]
        if literal.type != "string":
            return ""
        try:
            value = ast.literal_eval(_text(literal, source))
        except (ValueError, SyntaxError):
            return ""
        return inspect.cleandoc(value) if isinstance(value, str) else ""

    def _imports(self, root: Node, source: bytes) -> Iterator[str]:
        for node in self._scope_nodes(root):
            if node.type == "import_from_statement":
                module = node.child_by_field_name("module_name")
                if module is not None:
                    yield _text(module, source)
            elif node.type == "import_statement":
                for target in node.children_by_field_name("name"):
                    name = (
                        target.child_by_field_name("name")
                        if target.type == "aliased_import"
                        else target
                    )
                    if name is not None:
                        yield _text(name, source)


def _line(point: Point) -> int:
    return point[0] + 1


def _is_import(node: Node) -> bool:
    return node.type in {"import_statement", "import_from_statement"}


def _text(node: Node | None, source: bytes) -> str:
    return "" if node is None else source[node.start_byte : node.end_byte].decode()


def _cap(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_SOURCE_CHARS:
        return text, False
    return text[:MAX_SOURCE_CHARS], True
