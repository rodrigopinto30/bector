"""Use cases of CodeIndex: keep the index in sync with the workspace, query it."""

import logging

from healer.domain.models import IndexReport, SearchHit, Symbol
from healer.domain.ports import CodeParser, SourceScanner, SymbolRepository

logger = logging.getLogger(__name__)


class CodeIndex:
    def __init__(
        self, scanner: SourceScanner, parser: CodeParser, repository: SymbolRepository
    ) -> None:
        self._scanner = scanner
        self._parser = parser
        self._repository = repository

    def index(self) -> IndexReport:
        """Make the index mirror the workspace, re-parsing only files whose content changed."""
        known = self._repository.indexed_files()
        seen: set[str] = set()
        report = IndexReport()
        for source in self._scanner.scan():
            seen.add(source.path)
            if known.get(source.path) == source.content_hash:
                report.files_unchanged += 1
                continue
            file_map = self._parser.parse(source)
            if file_map.has_syntax_errors:
                logger.warning(
                    "%s has syntax errors; indexed what could be recognised", source.path
                )
                report.files_with_syntax_errors += 1
            self._repository.replace_file(source.path, source.content_hash, file_map.symbols)
            report.files_indexed += 1
            report.symbols_indexed += len(file_map.symbols)
        for gone in sorted(set(known) - seen):
            self._repository.remove_file(gone)
            report.files_removed += 1
        return report

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        return self._repository.search(query, limit)

    def outline(self, file: str) -> list[Symbol]:
        """Structural map of one indexed file: every symbol with its signature and lines."""
        return self._repository.symbols_in_file(file)

    def enclosing_symbol(self, file: str, line: int) -> Symbol | None:
        """Narrowest indexed symbol of ``file`` whose line range contains ``line``."""
        containing = [s for s in self.outline(file) if s.start_line <= line <= s.end_line]
        return min(containing, key=lambda s: s.end_line - s.start_line, default=None)
