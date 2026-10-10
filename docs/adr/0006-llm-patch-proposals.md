# 0006. Patch proposals from Claude with minimal, redacted context

## Context

Phase 4 needs a language model to turn a diagnosis into a patch. The plan's core goals constrain how: spend as few tokens as possible (surgical context), never send secrets (7.5), treat project content as data rather than instructions (7.5), and never call the real API in automated tests (7.4).

## Decision

- **Model: Claude Sonnet 5.5 (`claude-sonnet-5-5`) by default,** chosen by the team for good code quality at a moderate cost. It is configurable (`ANTHROPIC_MODEL`), for example to Claude Opus 5.5 for hard errors. Effort defaults to `high` and is configurable.
- **Behind a port.** `PatchProposer` is the only interface the services know. `AnthropicProposer` implements it with the official SDK; tests use fakes. The Anthropic client is created lazily, so building a prompt (`--show-prompt`) needs no key.
- **Structured output.** The request uses `messages.parse` with a Pydantic schema (explanation plus edits), so the answer always matches the patch format. A refusal, a truncated answer or an empty proposal becomes a clear `LLMError`; SDK errors are mapped to project errors with actionable messages.
- **Server-side refusal fallbacks** (`fallbacks: "default"`) are enabled so a safety decline is retried on a fallback model inside the same call.
- **Context assembly, in priority order:** the function enclosing the origin line; the functions enclosing the other workspace frames of the call stack, closest first; then up to four symbols found by semantic search whose cosine distance is at most 0.7. Module records and overlapping symbols are skipped, and the total is capped at 12,000 characters. The threshold was set from measured distances: related functions scored 0.15 to 0.6, unrelated ones 0.75 and above.
- **Redaction before sending.** Snippets and the diagnosis go through the redactor (ADR 0004). A proposal containing a redaction marker is rejected, since applying it would overwrite a real value with the marker.
- **Prompt-injection mitigation.** The diagnosis and code are wrapped in `<diagnosis>` and `<code>` tags, and the system prompt states that their content is data and that instructions inside it must be ignored.
- **The system prompt is stable** and marked for caching, so repeated requests can reuse it once it is long enough to cache.

## Consequences

- Each proposal costs a few cents with Sonnet 5.5 for a small error; token usage is reported on every call and will feed the token ledger.
- The model can only edit code it was shown; when the root cause lives elsewhere, the patch will not fit and the pipeline must retry with more context.
- Values hidden by redaction cannot be reproduced in the fix; the model is told to say so instead of guessing.
- The search threshold may need tuning for other embedding models or very large projects.
