"""Python implementation of ``TraceParser``: CPython tracebacks and pytest failure reports."""

import re

from healer.domain.diagnosis import StackFrame, TracedError

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TB_HEADER = re.compile(r"^(?P<indent>\s*)Traceback \(most recent call last\):\s*$")
_TB_FRAME = re.compile(
    r'^(?P<indent>\s*)File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+?))?\s*$'
)
_EXCEPTION = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<message>.*))?$")
_CHAIN_MARKERS = frozenset(
    {
        "During handling of the above exception, another exception occurred:",
        "The above exception was the direct cause of the following exception:",
    }
)
_CARETS = re.compile(r"^[\s~^]+$")
_PYTEST_HEADER = re.compile(r"^_{3,} .+? _{3,}$")
_PYTEST_END = re.compile(r"^(?:={3,}|!{3,})")
_PYTEST_LOCATION = re.compile(r"^(?P<file><[^>]+>|\S+\.py):(?P<line>\d+): ?(?P<rest>.*)$")
_PYTEST_DEF = re.compile(r"^>?\s*(?:async\s+)?def (?P<name>\w+)\(")
_E_PREFIX = re.compile(r"^E(?: {1,3}|$)")


class PythonTraceParser:
    """Finds every exception in a log, whatever mix of scripts and pytest output it holds.

    Supported: standard tracebacks (including chained exceptions and syntax errors
    without a header) and pytest reports in the long, short, native and collection
    error formats. Exception groups are not supported yet.
    """

    def parse(self, text: str) -> list[TracedError]:
        lines = _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        errors: list[TracedError] = []
        outside: list[str] = []
        section: list[str] | None = None
        for line in lines:
            if _PYTEST_HEADER.match(line):
                errors.extend(_parse_tracebacks(outside))
                outside = []
                _flush_section(section, errors)
                section = []
            elif section is not None and _PYTEST_END.match(line):
                _flush_section(section, errors)
                section = None
            elif section is not None:
                section.append(line)
            else:
                outside.append(line)
        _flush_section(section, errors)
        errors.extend(_parse_tracebacks(outside))
        return errors


def _flush_section(section: list[str] | None, errors: list[TracedError]) -> None:
    if section:
        error = _parse_pytest_section(section)
        if error is not None:
            errors.append(error)


def _parse_tracebacks(lines: list[str]) -> list[TracedError]:
    errors: list[TracedError] = []
    frames: list[StackFrame] | None = None
    block_indent = 0
    chained = False
    i = 0
    while i < len(lines):
        line = lines[i]
        header = _TB_HEADER.match(line)
        frame = _TB_FRAME.match(line)
        if header:
            frames, block_indent = [], len(header["indent"])
        elif frame:
            frame_indent = len(frame["indent"])
            if frames is None:
                frames, block_indent = [], max(frame_indent - 2, 0)
            code = ""
            if i + 1 < len(lines) and _is_code(lines[i + 1], frame_indent):
                i += 1
                code = lines[i].strip()
            frames.append(
                StackFrame(
                    file=frame["file"],
                    line=int(frame["line"]),
                    function=frame["func"] or "",
                    code=code,
                )
            )
        elif frames is not None and line.strip() and _indent(line) <= block_indent:
            match = _EXCEPTION.match(line.strip())
            if match and frames:
                error = TracedError(
                    error_type=match["type"],
                    message=match["message"] or "",
                    frames=tuple(frames),
                )
                if chained and errors:
                    previous = errors.pop()
                    error = error.model_copy(
                        update={"causes": (*previous.causes, previous.summary)}
                    )
                errors.append(error)
            frames, chained = None, False
        elif frames is None and line.strip() in _CHAIN_MARKERS:
            chained = True
        i += 1
    return errors


def _parse_pytest_section(lines: list[str]) -> TracedError | None:
    if any(_TB_HEADER.match(line) for line in lines):
        native = _parse_tracebacks(lines)
        return native[-1] if native else None

    frames: list[StackFrame] = []
    e_lines: list[str] = []
    pending_code = ""
    last_def = ""
    error_type = ""
    awaiting_code = False
    for line in lines:
        if _E_PREFIX.match(line):
            e_lines.append(_E_PREFIX.sub("", line))
            awaiting_code = False
            continue
        location = _PYTEST_LOCATION.match(line)
        if location:
            rest = location["rest"].strip()
            if rest.startswith("in "):
                function = rest[3:].strip()
            else:
                function = last_def
                error_type = rest or error_type
            frames.append(
                StackFrame(
                    file=location["file"],
                    line=int(location["line"]),
                    function=function,
                    code=pending_code,
                )
            )
            awaiting_code = not pending_code
            pending_code, last_def = "", ""
            continue
        if line.startswith(">"):
            pending_code = line[1:].strip()
        elif awaiting_code and line.startswith("    ") and not _CARETS.match(line):
            code = line.strip()
            frames[-1] = frames[-1].model_copy(update={"code": "" if code == "???" else code})
            awaiting_code = False
        definition = _PYTEST_DEF.match(line.strip())
        if definition:
            last_def = definition["name"]

    if any(_TB_FRAME.match(line) for line in e_lines):
        nested = _parse_tracebacks(e_lines)
        if nested:
            inner = nested[-1]
            return inner.model_copy(update={"frames": (*frames, *inner.frames)})

    first = next((line.strip() for line in e_lines if line.strip()), "")
    if not frames or not (first or error_type):
        return None
    match = _EXCEPTION.match(first)
    if match and (not error_type or _short(match["type"]) == _short(error_type)):
        return TracedError(
            error_type=match["type"], message=match["message"] or "", frames=tuple(frames)
        )
    return TracedError(error_type=error_type or "UnknownError", message=first, frames=tuple(frames))


def _is_code(line: str, frame_indent: int) -> bool:
    if not line.strip() or _CARETS.match(line) or _TB_FRAME.match(line):
        return False
    return _indent(line) > frame_indent


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _short(error_type: str) -> str:
    return error_type.rsplit(".", 1)[-1]
