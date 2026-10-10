"""``PatchProposer`` backed by the Anthropic Messages API."""

import logging
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ValidationError

from healer.domain.errors import LLMError
from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import Proposal, TokenUsage

logger = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class _Edit(BaseModel):
    path: str
    old: str
    new: str


class _Answer(BaseModel):
    explanation: str
    edits: list[_Edit]


class AnthropicProposer:
    """Asks Claude for a fix as structured output, so the answer is always valid JSON.

    Server-side refusal fallbacks are enabled: if the model declines on a safety
    category, the API retries the same request on a fallback model in the same call.
    The SDK retries rate limits, overloads and network errors with backoff.
    """

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        effort: Effort = "high",
        max_tokens: int = 16_000,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    def propose(self, system: str, prompt: str) -> Proposal:
        try:
            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={"effort": self._effort},
                output_format=_Answer,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key; check ANTHROPIC_API_KEY") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(f"The API key cannot use {self._model}") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Model {self._model!r} was not found") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached; try again later") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError("The request to Anthropic timed out") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Cannot reach the Anthropic API; check the network") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except ValidationError as exc:
            raise LLMError("Claude returned an answer that does not match the format") from exc

        logger.info("Anthropic request %s served by %s", response._request_id, response.model)
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to answer this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off by the token limit")
        answer = response.parsed_output
        if answer is None:
            raise LLMError("Claude did not return a proposal")
        if not answer.edits:
            raise LLMError(f"Claude did not propose a change: {answer.explanation}")
        return Proposal(
            patch=Patch(
                edits=tuple(FileEdit(path=e.path, old=e.old, new=e.new) for e in answer.edits),
                description=answer.explanation,
            ),
            explanation=answer.explanation,
            model=response.model,
            usage=_usage(response.usage),
        )


def _usage(usage: Any) -> TokenUsage:
    return TokenUsage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
    )
