"""The real Anthropic API is never called: a fake client stands in for the SDK."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from healer.adapters.anthropic_proposer import FALLBACK_BETA, AnthropicProposer, _Answer, _Edit
from healer.domain.errors import LLMError
from healer.domain.patch import FileEdit

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def fake_client(messages: FakeMessages) -> Any:
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def response(
    answer: _Answer | None, stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5"
) -> Any:
    usage = SimpleNamespace(
        input_tokens=1200,
        output_tokens=150,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=300,
    )
    return SimpleNamespace(
        parsed_output=answer, stop_reason=stop_reason, model=model, usage=usage, _request_id="req_1"
    )


ANSWER = _Answer(
    explanation="read_db fails when the key is missing; use a default.",
    edits=[
        _Edit(path="app/cfg.py", old='    return config["db"]', new='    return config.get("db")')
    ],
)


def test_answer_becomes_a_proposal_with_usage() -> None:
    messages = FakeMessages(response(ANSWER))
    proposal = AnthropicProposer(fake_client(messages)).propose("system", "prompt")
    assert proposal.patch.edits == (
        FileEdit(
            path="app/cfg.py", old='    return config["db"]', new='    return config.get("db")'
        ),
    )
    assert proposal.patch.description == ANSWER.explanation
    assert proposal.explanation == ANSWER.explanation
    assert proposal.model == "claude-sonnet-5-5"
    assert (proposal.usage.input_tokens, proposal.usage.output_tokens) == (1200, 150)
    assert (proposal.usage.cache_read_tokens, proposal.usage.cache_write_tokens) == (0, 300)
    assert proposal.usage.input_total == 1500


def test_request_uses_sonnet_structured_output_and_fallbacks() -> None:
    messages = FakeMessages(response(ANSWER))
    AnthropicProposer(fake_client(messages), effort="medium", max_tokens=8000).propose(
        "the system prompt", "the prompt"
    )
    [call] = messages.calls
    assert call["model"] == "claude-sonnet-5-5"
    assert call["max_tokens"] == 8000
    assert call["output_format"] is _Answer
    assert call["output_config"] == {"effort": "medium"}
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"
    assert call["system"][0]["text"] == "the system prompt"
    assert call["messages"] == [{"role": "user", "content": "the prompt"}]
    assert "thinking" not in call


@pytest.mark.parametrize(
    ("stop_reason", "message"),
    [("refusal", "declined"), ("max_tokens", "cut off")],
)
def test_unusable_stop_reasons(stop_reason: str, message: str) -> None:
    messages = FakeMessages(response(ANSWER, stop_reason=stop_reason))
    with pytest.raises(LLMError, match=message):
        AnthropicProposer(fake_client(messages)).propose("s", "p")


def test_missing_parsed_output() -> None:
    with pytest.raises(LLMError, match="did not return"):
        AnthropicProposer(fake_client(FakeMessages(response(None)))).propose("s", "p")


def test_answer_without_edits_reports_the_explanation() -> None:
    empty = _Answer(explanation="The fix needs the hidden password.", edits=[])
    with pytest.raises(LLMError, match="hidden password"):
        AnthropicProposer(fake_client(FakeMessages(response(empty)))).propose("s", "p")


def status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    return cls("boom", response=httpx2.Response(status, request=REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (status_error(anthropic.AuthenticationError, 401), "API key"),
        (status_error(anthropic.PermissionDeniedError, 403), "cannot use"),
        (status_error(anthropic.NotFoundError, 404), "not found"),
        (status_error(anthropic.RateLimitError, 429), "rate limit"),
        (status_error(anthropic.InternalServerError, 500), "error 500"),
        (anthropic.APITimeoutError(request=REQUEST), "timed out"),
        (anthropic.APIConnectionError(request=REQUEST), "Cannot reach"),
    ],
)
def test_sdk_errors_become_project_errors(error: Exception, message: str) -> None:
    with pytest.raises(LLMError, match=message):
        AnthropicProposer(fake_client(FakeMessages(error=error))).propose("s", "p")
