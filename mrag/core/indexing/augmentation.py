"""Index-time augmentation strategies.

Currently implemented:
  contextual — per-chunk LLM context generation (Anthropic contextual retrieval)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import httpx

from mrag.core.indexing.context_prompt_template import DEFAULT_CONTEXT_PROMPT_TEMPLATE

if TYPE_CHECKING:
    from mrag.config.profile import AugmentationConfig
    from mrag.core.chunking.base import ChunkData


_MAX_DOC_CHARS = 8000


def generate_context(
    chunk_content: str,
    full_text: str,
    config: "AugmentationConfig",
    prompt_template: str | None = None,
) -> str:
    """Call LLM to generate a short context description for a single chunk.

    prompt_template: format string with {document} and {chunk} placeholders.
    Falls back to DEFAULT_CONTEXT_PROMPT_TEMPLATE when None.
    """
    template = prompt_template or DEFAULT_CONTEXT_PROMPT_TEMPLATE
    document_excerpt = full_text[:_MAX_DOC_CHARS]
    prompt = template.format(document=document_excerpt, chunk=chunk_content)
    endpoint = config.endpoint.rstrip("/")
    try:
        resp = httpx.post(
            f"{endpoint}/api/generate",
            json={"model": config.model, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError as e:
        raise ConnectionError(
            f"Cannot connect to Ollama at {endpoint}. "
            "Is Ollama running? (ollama serve)"
        ) from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Ollama returned HTTP {e.response.status_code}: {e.response.text}"
        ) from e

    data = resp.json()
    context_text = data.get("response", "").strip()
    if not context_text:
        raise RuntimeError(f"Empty context response from Ollama: {data}")
    return context_text


def augment_chunks(
    chunks: list["ChunkData"],
    full_text: str,
    config: "AugmentationConfig",
    prompt_template: str | None = None,
    on_chunk: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Return a list of context_text strings (one per chunk).

    Each string is the LLM-generated context for that chunk.
    prompt_template is forwarded to generate_context; None uses the default.
    on_chunk(current_1based, total) is called after each chunk completes.
    Raises on first LLM error; callers should catch and handle.
    """
    results = []
    total = len(chunks)
    for i, c in enumerate(chunks):
        ctx = generate_context(c.content, full_text, config, prompt_template)
        results.append(ctx)
        if on_chunk:
            on_chunk(i + 1, total)
    return results
