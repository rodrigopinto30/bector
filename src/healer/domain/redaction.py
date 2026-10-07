"""Secret redaction applied before any text leaves the machine.

The policy errs on the side of hiding too much: a leaked credential cannot be taken
back, while a hidden harmless value only costs a little context.
"""

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from pydantic import BaseModel, ConfigDict

from healer.domain.diagnosis import Diagnosis, StackFrame

PLACEHOLDER = "[REDACTED:{kind}]"
_PLACEHOLDER = re.compile(r"\[REDACTED:[a-z_]+\]")

_KEYWORDS = (
    r"pass(?:word|wd|phrase)?|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key"
    r"|private[_\-]?key|client[_\-]?secret|credentials?|auth[_\-]?key|session[_\-]?key"
)
_KEY = rf"[\w.\-]*(?:{_KEYWORDS})[\w.\-]*"
_ASSIGN = r"(?:=(?!=)|:(?!:))"
_ANNOTATION = r"(?:\s*:\s*[\w\[\]., |]+?)?"
_NOT_A_SECRET = frozenset({"none", "null", "nil", "true", "false", "undefined"})
_TYPE_NAMES = frozenset(
    {"str", "bytes", "int", "float", "bool", "any", "optional", "secretstr", "secretbytes"}
)
_VALUE_STOP = r"\s\"',;#()\[\]{}"
_UNQUOTED_VALUE = (
    rf"(?P<value>[^{_VALUE_STOP}]+)(?![^{_VALUE_STOP}])(?!\s*(?:\||=(?!=)|\[|\(|[/+*%]\s))"
)
_EXPRESSION = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+$|^[A-Za-z_][\w.]*[(\[]|^\$")

_HIGH_ENTROPY_CANDIDATE = re.compile(r"(?<![\w\-+/])[A-Za-z0-9_\-+/]{24,}={0,2}(?![\w\-+/=])")
_MIN_ENTROPY = 3.5
_MIN_UNBROKEN_RUN = 16


class Redaction(BaseModel):
    """Redacted text plus how many secrets of each kind were hidden. Never the values."""

    model_config = ConfigDict(frozen=True)

    text: str
    counts: dict[str, int] = {}

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    accept: Callable[[str], bool] = lambda value: True


_RULES = (
    _Rule(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
            r"(?P<value>[\s\S]*?)(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"
        ),
    ),
    _Rule("anthropic_key", re.compile(r"(?P<value>sk-ant-[A-Za-z0-9_\-]{20,})")),
    _Rule("openai_key", re.compile(r"(?P<value>\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,})")),
    _Rule("aws_access_key", re.compile(r"(?P<value>\b(?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    _Rule(
        "github_token",
        re.compile(r"(?P<value>\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}))"),
    ),
    _Rule("slack_token", re.compile(r"(?P<value>\bxox[abprs]-[A-Za-z0-9\-]{10,})")),
    _Rule("stripe_key", re.compile(r"(?P<value>\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,})")),
    _Rule("google_api_key", re.compile(r"(?P<value>\bAIza[0-9A-Za-z_\-]{35})")),
    _Rule(
        "jwt",
        re.compile(
            r"(?P<value>\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})"
        ),
    ),
    _Rule(
        "url_credentials",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:(?P<value>[^\s@/]+)@"),
    ),
    _Rule(
        "authorization",
        re.compile(
            r"(?i)\b(?:authorization|proxy-authorization)[\"']?\s*[:=]\s*[\"']?"
            r"(?:bearer|basic|token|digest)?\s*(?P<value>[A-Za-z0-9._~+/=\-]{6,})"
        ),
    ),
    _Rule("authorization", re.compile(r"(?i)\bbearer\s+(?P<value>[A-Za-z0-9._~+/=\-]{12,})")),
    _Rule(
        "secret_assignment",
        re.compile(
            rf"(?i)\b{_KEY}[\"']?{_ANNOTATION}\s*{_ASSIGN}\s*"
            r"(?P<quote>[\"'])(?P<value>(?:(?!(?P=quote)).)+)(?P=quote)"
        ),
    ),
    _Rule(
        "secret_assignment",
        re.compile(rf"(?i)(?<![\w\"'])(?:--)?{_KEY}\s*{_ASSIGN}\s*{_UNQUOTED_VALUE}"),
        accept=lambda value: (
            value.lower() not in _NOT_A_SECRET | _TYPE_NAMES and not _EXPRESSION.match(value)
        ),
    ),
)


class Redactor:
    """Replaces credentials with ``[REDACTED:<kind>]`` placeholders.

    Known credential formats are matched first, then values assigned to names that
    look secret (``password``, ``token``, ``api_key``...), then any long random-looking
    string. Redacting already redacted text changes nothing.
    """

    def redact(self, text: str) -> Redaction:
        counts: Counter[str] = Counter()
        for rule in _RULES:
            protected = [m.span() for m in _PLACEHOLDER.finditer(text)]
            replace = partial(_replace, rule=rule, counts=counts, protected=protected)
            text = rule.pattern.sub(replace, text)
        text = _HIGH_ENTROPY_CANDIDATE.sub(lambda m: _replace_random(m, counts), text)
        return Redaction(text=text, counts=dict(counts))

    def redact_diagnosis(self, diagnosis: Diagnosis) -> tuple[Diagnosis, dict[str, int]]:
        """Redact every free-text field of a diagnosis and count what was hidden."""
        counts: Counter[str] = Counter()

        def clean(value: str) -> str:
            result = self.redact(value)
            counts.update(result.counts)
            return result.text

        def clean_frame(frame: StackFrame) -> StackFrame:
            return frame.model_copy(update={"code": clean(frame.code)})

        error = diagnosis.error.model_copy(
            update={
                "message": clean(diagnosis.error.message),
                "frames": tuple(clean_frame(f) for f in diagnosis.error.frames),
                "causes": tuple(clean(c) for c in diagnosis.error.causes),
            }
        )
        update: dict[str, object] = {
            "error": error,
            "signature_basis": clean(diagnosis.signature_basis),
        }
        if diagnosis.origin is not None:
            update["origin"] = clean_frame(diagnosis.origin)
        if diagnosis.location is not None:
            location = diagnosis.location
            update["location"] = location.model_copy(
                update={"source": clean(location.source), "docstring": clean(location.docstring)}
            )
        redacted = diagnosis.model_copy(update=update)
        return redacted, dict(counts)


def _replace(
    match: re.Match[str],
    *,
    rule: _Rule,
    counts: Counter[str],
    protected: list[tuple[int, int]],
) -> str:
    value = match.group("value")
    start, end = match.span("value")
    overlaps = any(p_start < end and start < p_end for p_start, p_end in protected)
    if not value or overlaps or not rule.accept(value):
        return match.group(0)
    counts[rule.kind] += 1
    offset = match.start()
    whole = match.group(0)
    return whole[: start - offset] + PLACEHOLDER.format(kind=rule.kind) + whole[end - offset :]


def _replace_random(match: re.Match[str], counts: Counter[str]) -> str:
    token = match.group(0)
    if not _looks_random(token):
        return token
    counts["high_entropy"] += 1
    return PLACEHOLDER.format(kind="high_entropy")


def _looks_random(token: str) -> bool:
    has_letter = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    separators = r"[_\-/]" if token.count("/") >= 2 else r"[_\-]"
    longest_run = max(len(part) for part in re.split(separators, token))
    return (
        has_letter
        and has_digit
        and longest_run >= _MIN_UNBROKEN_RUN
        and _entropy(token) >= _MIN_ENTROPY
    )


def _entropy(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values())
