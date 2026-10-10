"""Use case: turn a diagnosis into a patch proposal with the smallest useful context."""

from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import InvalidPatchError, MissingContextError
from healer.domain.models import Symbol, SymbolKind
from healer.domain.ports import PatchProposer, SymbolLocator, SymbolSearch
from healer.domain.proposal import (
    SYSTEM_PROMPT,
    ContextSnippet,
    FixRequest,
    Proposal,
    render_request,
)
from healer.domain.redaction import PLACEHOLDER, Redactor

_MARKER = PLACEHOLDER.split("{", 1)[0]


class FixProposer:
    """Sends the model a few functions, never whole files.

    In order: the function where the error originates, the functions of the call stack
    that led to it (closest first), and code found by meaning that is close enough to
    the error. Everything is redacted first. A proposal that writes a redaction marker
    into the code is rejected, because applying it would replace a real value with it.
    """

    def __init__(
        self,
        search: SymbolSearch,
        locator: SymbolLocator,
        llm: PatchProposer,
        redactor: Redactor,
        *,
        max_context_chars: int = 12_000,
        max_related: int = 4,
        max_distance: float = 0.7,
    ) -> None:
        self._search = search
        self._locator = locator
        self._llm = llm
        self._redactor = redactor
        self._max_chars = max_context_chars
        self._max_related = max_related
        self._max_distance = max_distance

    def prepare(self, diagnosis: Diagnosis) -> tuple[FixRequest, dict[str, int]]:
        """Build the redacted request without calling the model, plus what was hidden."""
        snippets, counts = self._context(diagnosis)
        if not snippets:
            raise MissingContextError(
                "No indexed project code is related to this error; run 'healer index' first"
            )
        without_source = diagnosis.model_copy(update={"location": None})
        redacted, diagnosis_counts = self._redactor.redact_diagnosis(without_source)
        for kind, n in diagnosis_counts.items():
            counts[kind] = counts.get(kind, 0) + n
        return FixRequest(diagnosis=redacted, snippets=tuple(snippets)), counts

    def propose(self, diagnosis: Diagnosis) -> Proposal:
        request, counts = self.prepare(diagnosis)
        proposal = self._llm.propose(SYSTEM_PROMPT, render_request(request))
        for edit in proposal.patch.edits:
            if _MARKER in edit.old or _MARKER in edit.new:
                raise InvalidPatchError(
                    f"{edit.path}: the proposal contains a redaction marker and cannot be applied"
                )
        return proposal.model_copy(update={"context": request.snippets, "redactions": counts})

    def _context(self, diagnosis: Diagnosis) -> tuple[list[ContextSnippet], dict[str, int]]:
        chosen: list[Symbol] = []

        def add(symbol: Symbol | None) -> bool:
            if symbol is None or symbol.kind is SymbolKind.MODULE:
                return False
            if any(_overlaps(symbol, c) for c in chosen):
                return False
            chosen.append(symbol)
            return True

        add(diagnosis.location)
        for frame in reversed(diagnosis.error.frames):
            if frame.workspace_file:
                add(self._locator.enclosing_symbol(frame.workspace_file, frame.line))

        query = diagnosis.error.summary
        if diagnosis.origin is not None and diagnosis.origin.code:
            query += "\n" + diagnosis.origin.code
        related = 0
        for hit in self._search.search(query, limit=self._max_related * 3):
            if related >= self._max_related or hit.distance > self._max_distance:
                break
            if add(hit.symbol):
                related += 1

        snippets: list[ContextSnippet] = []
        counts: dict[str, int] = {}
        used = 0
        for symbol in chosen:
            if snippets and used + len(symbol.source) > self._max_chars:
                continue
            result = self._redactor.redact(symbol.source)
            for kind, n in result.counts.items():
                counts[kind] = counts.get(kind, 0) + n
            snippets.append(
                ContextSnippet(
                    file=symbol.file,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    label=symbol.qualified_name,
                    source=result.text,
                )
            )
            used += len(symbol.source)
        return snippets, counts


def _overlaps(a: Symbol, b: Symbol) -> bool:
    return a.file == b.file and a.start_line <= b.end_line and b.start_line <= a.end_line
