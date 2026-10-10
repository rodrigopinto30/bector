import pytest

from healer.domain.diagnosis import Diagnosis, StackFrame, TracedError
from healer.domain.errors import InvalidPatchError, MissingContextError
from healer.domain.models import SearchHit, Symbol, SymbolKind
from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import SYSTEM_PROMPT, Proposal, TokenUsage
from healer.domain.redaction import Redactor
from healer.services.fix_proposer import FixProposer

AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def symbol(
    name: str, file: str, start: int, end: int, source: str, kind: SymbolKind = SymbolKind.FUNCTION
) -> Symbol:
    return Symbol(
        file=file,
        kind=kind,
        name=name,
        qualified_name=name,
        start_line=start,
        end_line=end,
        source=source,
    )


READ_DB = symbol("read_db", "app/cfg.py", 1, 3, 'def read_db(config):\n    return config["db"]\n')
PARSE_PORT = symbol(
    "parse_port", "app/cfg.py", 6, 7, "def parse_port(value):\n    return int(value)\n"
)
CONNECT = symbol("connect", "app/db.py", 1, 3, f'def connect():\n    key = "{AWS}"\n')
MODULE = symbol("cfg", "app/cfg.py", 1, 7, "import os", kind=SymbolKind.MODULE)

FRAME = StackFrame(
    file="/w/app/cfg.py",
    line=2,
    function="read_db",
    code='return config["db"]',
    workspace_file="app/cfg.py",
)
DIAGNOSIS = Diagnosis(
    error=TracedError(error_type="KeyError", message="'db'", frames=(FRAME,)),
    origin=FRAME,
    location=READ_DB,
    signature="s" * 64,
    signature_basis="KeyError|<str>|app/cfg.py:read_db",
)


class FakeSearch:
    def __init__(self, symbols: list[Symbol]) -> None:
        self.symbols = symbols
        self.queries: list[str] = []

    def search(self, query: str, limit: int) -> list[SearchHit]:
        self.queries.append(query)
        return [SearchHit(symbol=s, distance=0.1 * i) for i, s in enumerate(self.symbols)][:limit]


class FakeLLM:
    def __init__(
        self, edits: tuple[FileEdit, ...] = (FileEdit(path="app/cfg.py", old="a", new="b"),)
    ) -> None:
        self.edits = edits
        self.calls: list[tuple[str, str]] = []

    def propose(self, system: str, prompt: str) -> Proposal:
        self.calls.append((system, prompt))
        return Proposal(
            patch=Patch(edits=self.edits, description="fix"),
            explanation="fix",
            model="fake",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )


class FakeLocator:
    def __init__(self, symbols: list[Symbol]) -> None:
        self.symbols = symbols

    def enclosing_symbol(self, file: str, line: int) -> Symbol | None:
        found = [s for s in self.symbols if s.file == file and s.start_line <= line <= s.end_line]
        return min(found, key=lambda s: s.end_line - s.start_line, default=None)


def build(
    symbols: list[Symbol],
    llm: FakeLLM | None = None,
    stack: list[Symbol] | None = None,
    **kwargs: float,
) -> tuple[FixProposer, FakeSearch, FakeLLM]:
    search, model = FakeSearch(symbols), llm or FakeLLM()
    proposer = FixProposer(search, FakeLocator(stack or []), model, Redactor(), **kwargs)  # type: ignore[arg-type]
    return proposer, search, model


def test_enclosing_function_comes_first_then_related_ones() -> None:
    proposer, search, llm = build([READ_DB, MODULE, PARSE_PORT])
    proposal = proposer.propose(DIAGNOSIS)
    assert [c.label for c in proposal.context] == ["read_db", "parse_port"]
    assert search.queries == ["KeyError: 'db'\nreturn config[\"db\"]"]
    system, prompt = llm.calls[0]
    assert system == SYSTEM_PROMPT
    assert '<code path="app/cfg.py" lines="1-3" symbol="read_db">' in prompt
    assert 'return config["db"]' in prompt
    assert "<diagnosis>" in prompt and "error: KeyError: 'db'" in prompt


def test_module_records_and_overlapping_symbols_are_skipped() -> None:
    method = symbol("Repo.save", "app/cfg.py", 2, 3, "def save(self): ...")
    proposer, _, _ = build([MODULE, method, PARSE_PORT])
    assert [c.label for c in proposer.propose(DIAGNOSIS).context] == ["read_db", "parse_port"]


def test_related_symbols_are_capped() -> None:
    many = [
        symbol(f"f{i}", "app/other.py", i * 10 + 1, i * 10 + 2, "def f(): ...") for i in range(10)
    ]
    proposer, _, _ = build(many, max_related=3)
    assert len(proposer.propose(DIAGNOSIS).context) == 4


def test_context_respects_the_character_budget() -> None:
    big = symbol("big", "app/big.py", 1, 400, "x = 1\n" * 1000)
    proposer, _, _ = build([big, PARSE_PORT], max_context_chars=500)
    assert [c.label for c in proposer.propose(DIAGNOSIS).context] == ["read_db", "parse_port"]


def test_secrets_never_reach_the_model() -> None:
    leaky = DIAGNOSIS.model_copy(
        update={"error": DIAGNOSIS.error.model_copy(update={"message": f"bad key {AWS}"})}
    )
    proposer, _, llm = build([CONNECT])
    proposal = proposer.propose(leaky)
    _, prompt = llm.calls[0]
    assert AWS not in prompt
    assert "[REDACTED:aws_access_key]" in prompt
    assert proposal.redactions == {"aws_access_key": 2}


def test_proposal_with_a_redaction_marker_is_rejected() -> None:
    llm = FakeLLM(
        (FileEdit(path="app/db.py", old="key = 1", new='key = "[REDACTED:aws_access_key]"'),)
    )
    proposer, _, _ = build([CONNECT], llm=llm)
    with pytest.raises(InvalidPatchError, match="redaction marker"):
        proposer.propose(DIAGNOSIS)


def test_no_context_at_all_is_an_error() -> None:
    orphan = DIAGNOSIS.model_copy(update={"location": None})
    proposer, _, llm = build([])
    with pytest.raises(MissingContextError, match="healer index"):
        proposer.propose(orphan)
    assert llm.calls == []


def test_error_outside_the_workspace_still_gets_related_code() -> None:
    external = DIAGNOSIS.model_copy(update={"location": None, "origin": None})
    proposer, search, _ = build([PARSE_PORT])
    assert [c.label for c in proposer.propose(external).context] == ["parse_port"]
    assert search.queries == ["KeyError: 'db'"]


def test_prepare_builds_the_request_without_calling_the_model() -> None:
    proposer, _, llm = build([PARSE_PORT])
    request, counts = proposer.prepare(DIAGNOSIS)
    assert [s.label for s in request.snippets] == ["read_db", "parse_port"]
    assert counts == {}
    assert llm.calls == []


TEST_READ_DB = symbol(
    "test_read_db", "tests/test_app.py", 4, 5, "def test_read_db():\n    read_db({})\n"
)
CALLER = StackFrame(
    file="tests/test_app.py",
    line=5,
    function="test_read_db",
    code="read_db({})",
    workspace_file="tests/test_app.py",
)
LIBRARY = StackFrame(file="/usr/lib/python3.11/json/decoder.py", line=3, function="decode")


def test_functions_of_the_call_stack_are_included_closest_first() -> None:
    nested = DIAGNOSIS.model_copy(
        update={"error": DIAGNOSIS.error.model_copy(update={"frames": (CALLER, FRAME, LIBRARY)})}
    )
    proposer, _, _ = build([], stack=[READ_DB, TEST_READ_DB])
    assert [c.label for c in proposer.propose(nested).context] == ["read_db", "test_read_db"]


def test_search_results_beyond_the_distance_threshold_are_dropped() -> None:
    proposer, _, _ = build([PARSE_PORT, CONNECT], max_distance=0.05)
    assert [c.label for c in proposer.propose(DIAGNOSIS).context] == ["read_db", "parse_port"]


def test_a_secret_in_the_enclosing_function_is_counted_once() -> None:
    leaky = READ_DB.model_copy(
        update={"source": 'def read_db(config):\n    password = "hunter2"\n'}
    )
    secret_password = DIAGNOSIS.model_copy(update={"location": leaky})
    proposer, _, llm = build([])
    proposal = proposer.propose(secret_password)
    assert proposal.redactions == {"secret_assignment": 1}
    assert "hunter2" not in llm.calls[0][1]
