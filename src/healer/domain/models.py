"""Pure data contracts for CodeIndex. No I/O and no third-party parsing here."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class Symbol(BaseModel):
    """A named, addressable piece of code: the unit that is indexed and retrieved."""

    model_config = ConfigDict(frozen=True)

    file: str = Field(description="Path relative to the workspace root, with '/' separators.")
    kind: SymbolKind
    name: str
    qualified_name: str = Field(description="Dotted path inside the file, e.g. 'Repo.save'.")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    signature: str = ""
    docstring: str = ""
    decorators: tuple[str, ...] = ()
    imports: tuple[str, ...] = Field(default=(), description="Only filled for MODULE symbols.")
    source: str
    truncated: bool = False

    @property
    def id(self) -> str:
        return f"{self.file}:{self.qualified_name}:{self.start_line}"


class SourceFile(BaseModel):
    """A file read from the workspace, ready to be parsed."""

    model_config = ConfigDict(frozen=True)

    path: str
    content: str
    content_hash: str


class FileMap(BaseModel):
    """Structural map of one file: its module record plus every class and function."""

    model_config = ConfigDict(frozen=True)

    file: str
    symbols: tuple[Symbol, ...]
    has_syntax_errors: bool = False


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: Symbol
    distance: float = Field(description="Cosine distance; lower means more similar.")


class IndexReport(BaseModel):
    """Outcome of one synchronisation between the workspace and the index."""

    files_indexed: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    files_with_syntax_errors: int = 0
    symbols_indexed: int = 0
