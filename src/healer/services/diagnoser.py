"""Use case of incident diagnosis: raw output in, structured error specifications out."""

from healer.domain.diagnosis import Diagnosis, TracedError
from healer.domain.ports import PathResolver, SymbolLocator, TraceParser
from healer.domain.signature import compute_signature, signature_basis


class Diagnoser:
    def __init__(
        self,
        parser: TraceParser,
        resolver: PathResolver,
        locator: SymbolLocator | None = None,
    ) -> None:
        self._parser = parser
        self._resolver = resolver
        self._locator = locator

    def diagnose(self, text: str) -> list[Diagnosis]:
        """Return one diagnosis per distinct error, in order of first appearance."""
        found: dict[str, Diagnosis] = {}
        for error in self._parser.parse(text):
            diagnosis = self._diagnose_one(error)
            seen = found.get(diagnosis.signature)
            if seen is None:
                found[diagnosis.signature] = diagnosis
            else:
                found[diagnosis.signature] = seen.model_copy(
                    update={"occurrences": seen.occurrences + 1}
                )
        return list(found.values())

    def _diagnose_one(self, error: TracedError) -> Diagnosis:
        frames = tuple(
            frame.model_copy(update={"workspace_file": self._resolver.resolve(frame.file)})
            for frame in error.frames
        )
        error = error.model_copy(update={"frames": frames})
        origin_index = next(
            (i for i in reversed(range(len(frames))) if frames[i].workspace_file), None
        )
        origin = frames[origin_index] if origin_index is not None else None
        location = None
        if origin is not None and origin.workspace_file and self._locator is not None:
            location = self._locator.enclosing_symbol(origin.workspace_file, origin.line)
        basis = signature_basis(error, origin_index)
        return Diagnosis(
            error=error,
            origin=origin,
            location=location,
            signature=compute_signature(basis),
            signature_basis=basis,
        )
