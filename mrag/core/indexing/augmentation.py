"""Index-time augmentation strategies.

Currently implemented:
  contextual — per-chunk LLM context generation (Anthropic contextual retrieval)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from mrag.core.indexing.context_prompt_template import DEFAULT_CONTEXT_PROMPT_TEMPLATE
from mrag.core.ollama_client import ollama_post

if TYPE_CHECKING:
    from mrag.config.profile import AugmentationConfig
    from mrag.core.chunking.base import ChunkData


_MAX_DOC_CHARS = 8000


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
    on_retry(attempt, max_attempts, exc) is called before each retry sleep.
    """
    template = prompt_template or DEFAULT_CONTEXT_PROMPT_TEMPLATE
    document_excerpt = full_text[:_MAX_DOC_CHARS]
    prompt = template.format(document=document_excerpt, chunk=chunk_content)

    def _validate(data: dict) -> None:
        if not data.get("response", "").strip():
            raise RuntimeError(f"Empty context response from Ollama: {data}")

    data = ollama_post(
        config.endpoint,
        "/api/generate",
        {"model": config.model, "prompt": prompt, "stream": False},
        max_attempts=config.retry.max_attempts,
        initial_delay=config.retry.initial_delay_seconds,
        backoff_multiplier=config.retry.backoff_multiplier,
        max_delay=config.retry.max_delay_seconds,
        validate=_validate,
        on_retry=on_retry,
    )
    return data["response"].strip()


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
