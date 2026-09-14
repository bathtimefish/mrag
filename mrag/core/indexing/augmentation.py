"""Index-time augmentation strategies.

Currently implemented:
  contextual — per-chunk LLM context generation (Anthropic contextual retrieval)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from mrag.core.indexing.context_prompt_template import (
    DEFAULT_CONTEXT_PROMPT_TEMPLATE,
    render_context_prompt,
)
from mrag.core.ollama_client import (
    OllamaInputTooLargeError,
    model_capabilities,
    ollama_post,
)

if TYPE_CHECKING:
    from mrag.config.profile import AugmentationConfig
    from mrag.core.chunking.base import ChunkData


_MAX_DOC_CHARS = 8000

# The document excerpt is capped in characters, but a server refuses prompts by
# tokens, and how many tokens it accepts depends on its batch size, which the
# client cannot ask for. Japanese business prose can pass 2,048 tokens well
# inside 8,000 characters. So a refused prompt is retried with the excerpt
# halved (8,000 → 4,000 → 2,000 → 1,000). Below this floor the excerpt says too
# little about the document to be worth another call, and the chunk fails as it
# did before.
_MIN_DOC_CHARS = 1000


def _excerpt_lengths(full_text: str) -> list[int]:
    """The excerpt lengths to try, longest first, each half the one before."""
    lengths = [min(len(full_text), _MAX_DOC_CHARS)]
    while lengths[-1] // 2 >= _MIN_DOC_CHARS:
        lengths.append(lengths[-1] // 2)
    return lengths


def generate_context(
    chunk_content: str,
    full_text: str,
    config: "AugmentationConfig",
    prompt_template: str | None = None,
    on_retry: Callable[[int, int, Exception], None] | None = None,
) -> str:
    """Call LLM to generate a short context description for a single chunk.

    prompt_template: format string with {document} and {chunk} placeholders.
    Falls back to DEFAULT_CONTEXT_PROMPT_TEMPLATE when None.
    on_retry(attempt, max_attempts, exc) is called before each retry sleep, and
    before each retry with a shorter document excerpt after the server refused
    the prompt as too large. A server that accepts the first prompt receives
    exactly the request earlier releases sent.
    """
    template = (
        DEFAULT_CONTEXT_PROMPT_TEMPLATE
        if prompt_template is None
        else prompt_template
    )

    def _validate(data: dict) -> None:
        if not data.get("response", "").strip():
            raise RuntimeError(f"Empty context response from Ollama: {data}")

    send_think: bool | None = None
    lengths = _excerpt_lengths(full_text)
    for step, length in enumerate(lengths, start=1):
        prompt = render_context_prompt(
            template,
            document=full_text[:length],
            chunk=chunk_content,
        )
        payload = {"model": config.model, "prompt": prompt, "stream": False}
        # `think` is only accepted by models that report the capability; sending
        # it to any other model is an error, so it is omitted rather than
        # assumed. Probed after the first render, so a bad template is still
        # refused before any provider call.
        if send_think is None:
            send_think = "thinking" in model_capabilities(config.endpoint, config.model)
        if send_think:
            payload["think"] = config.think

        try:
            data = ollama_post(
                config.endpoint,
                "/api/generate",
                payload,
                max_attempts=config.retry.max_attempts,
                initial_delay=config.retry.initial_delay_seconds,
                backoff_multiplier=config.retry.backoff_multiplier,
                max_delay=config.retry.max_delay_seconds,
                validate=_validate,
                on_retry=on_retry,
            )
        except OllamaInputTooLargeError as exc:
            if step == len(lengths):
                # The explanation leads because the fallback log shows 120
                # characters of this and chunk metadata keeps 200.
                raise OllamaInputTooLargeError(
                    f"still too large with a {length}-character document excerpt: {exc}"
                ) from exc
            if on_retry is not None:
                on_retry(
                    step,
                    len(lengths),
                    OllamaInputTooLargeError(
                        f"prompt too large for the server with a {length}-character "
                        f"document excerpt; retrying with {lengths[step]}"
                    ),
                )
            continue
        return data["response"].strip()

    raise AssertionError("unreachable: the last excerpt length either returns or raises")


def augment_chunks(
    chunks: list["ChunkData"],
    full_text: str,
    config: "AugmentationConfig",
    prompt_template: str | None = None,
    on_chunk: Callable[[int, int], None] | None = None,
    on_chunk_retry: Callable[[int, int, int, int, Exception], None] | None = None,
    on_chunk_fallback: Callable[[int, int, Exception], None] | None = None,
) -> list[str | None]:
    """Return a list of context_text strings (one per chunk), or None for fallback chunks.

    When failure_policy.mode is 'raw_fallback', failed chunks yield None instead of raising.
    When failure_policy.mode is 'fail_document', failed chunks raise (original behavior).
    on_chunk(current_1based, total) is called after each chunk completes or falls back.
    on_chunk_retry(chunk_cur, chunk_total, attempt, max_attempts, exc) is called before each retry.
    on_chunk_fallback(chunk_cur_1based, total, exc) is called when a chunk falls back to raw.
    """
    results: list[str | None] = []
    total = len(chunks)
    raw_fallback = config.failure_policy.mode == "raw_fallback"

    for i, c in enumerate(chunks):
        cur = i + 1

        on_retry: Callable[[int, int, Exception], None] | None = None
        if on_chunk_retry is not None:
            def _make_on_retry(chunk_cur: int) -> Callable[[int, int, Exception], None]:
                def _cb(attempt: int, max_attempts: int, exc: Exception) -> None:
                    on_chunk_retry(chunk_cur, total, attempt, max_attempts, exc)
                return _cb
            on_retry = _make_on_retry(cur)

        try:
            ctx = generate_context(c.content, full_text, config, prompt_template, on_retry=on_retry)
            results.append(ctx)
        except RuntimeError as exc:
            # RuntimeError covers all retry-exhausted cases: timeout, empty response, HTTP 5xx/4xx.
            # ConnectionError (Ollama not running) is intentionally not caught here — it should
            # propagate and fail the document so the user knows Ollama is unreachable.
            if raw_fallback:
                results.append(None)
                if on_chunk_fallback is not None:
                    on_chunk_fallback(cur, total, exc)
            else:
                raise

        if on_chunk:
            on_chunk(cur, total)

    return results
