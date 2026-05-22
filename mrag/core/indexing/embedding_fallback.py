"""Embedding fallback — handle Ollama / provider failures at chunk granularity.

When the embedding provider returns a hard failure for a batch (e.g. Ollama
HTTP 500 from `bge-m3` returning NaN values), this module isolates the failing
chunk(s) via recursive bisection and lets the rest of the document index
proceed. Failed chunks are recorded in `chunk_variants.metadata_json` with
`embedding_status: fallback_no_vector` and are excluded from Qdrant upserts.
The chunk text itself is still inserted into SQLite and FTS5, so keyword
retrieval continues to work.

See: dev_docs/01_EXTENSION_STAGE_1/DESIGN_V21_EMBEDDING_FALLBACK.md
"""
from __future__ import annotations

import logging
from typing import Optional

from mrag.core.embedding.base import BaseEmbeddingProvider


_logger = logging.getLogger(__name__)

_ERROR_TRUNCATE_LIMIT = 500    # max chars stored in metadata_json.embedding_error
_WARN_SAMPLE_LIMIT = 200       # max chars of failing input shown in WARN log


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def embed_with_fallback(
    texts: list[str],
    provider: BaseEmbeddingProvider,
    *,
    mode: str = "fallback_no_vector",
) -> tuple[list[Optional[list[float]]], dict[int, str]]:
    """Embed `texts` with bisection-based fallback for failing chunks.

    Behavior:
      1. Try `provider.embed(texts)` with the provider's normal retry budget.
      2. On success, return all vectors.
      3. On RuntimeError:
         - If `mode == "fail_document"`, re-raise (preserves v0.20.0 behavior).
         - Otherwise, enter bisection (see _bisect_embed).

    Returns:
      (vectors, failures) where:
        - vectors[i] is the vector for texts[i], or None if embedding failed
        - failures[i] is the truncated error message for failed indices
          (indices not in `failures` have a valid vector)

    The provider's `max_attempts` attribute (if present) is temporarily set to 1
    during bisection for fast failure isolation, and restored to its original
    value on exit. The final isolated singleton gets one more attempt at the
    full retry budget before being marked as fallback.
    """
    if not texts:
        return [], {}

    # First try: normal batch with full retry budget
    try:
        vectors = provider.embed(texts)
        return list(vectors), {}
    except RuntimeError as exc:
        if mode == "fail_document":
            raise
        first_failure = exc

    # Edge case: single text already failed with full retry — no point bisecting
    if len(texts) == 1:
        msg = _truncate_error(str(first_failure))
        _warn_failed_chunk(texts[0], str(first_failure))
        return [None], {0: msg}

    # Recursive bisection for multi-input batches
    return _bisect_embed(texts, provider)


# ---------------------------------------------------------------------------
# Bisection
# ---------------------------------------------------------------------------


def _bisect_embed(
    texts: list[str],
    provider: BaseEmbeddingProvider,
) -> tuple[list[Optional[list[float]]], dict[int, str]]:
    """Recursively isolate failing chunks with a 2-stage retry strategy.

    - During bisection: `max_attempts=1` (fast failure isolation)
    - For final isolated singleton: full `max_attempts` (true failure confirmation)

    The provider's `max_attempts` is restored to its original value on exit.
    Providers without a `max_attempts` attribute skip the 2-stage optimization.
    """
    vectors: list[Optional[list[float]]] = [None] * len(texts)
    failures: dict[int, str] = {}

    original_max_attempts = getattr(provider, "max_attempts", None)
    has_attempts_attr = original_max_attempts is not None

    def _set_attempts(full_retry: bool) -> None:
        if has_attempts_attr:
            provider.max_attempts = original_max_attempts if full_retry else 1

    def _try_range(indices: list[int], *, full_retry: bool) -> None:
        subset = [texts[i] for i in indices]
        _set_attempts(full_retry)
        try:
            subset_vectors = provider.embed(subset)
        except RuntimeError as exc:
            if len(indices) == 1:
                # Singleton failed. If we haven't yet used the full retry budget,
                # try once more to confirm it's a deterministic failure.
                if not full_retry and has_attempts_attr:
                    _try_range(indices, full_retry=True)
                    return
                failures[indices[0]] = _truncate_error(str(exc))
                _warn_failed_chunk(texts[indices[0]], str(exc))
                return
            mid = len(indices) // 2
            _try_range(indices[:mid], full_retry=False)
            _try_range(indices[mid:], full_retry=False)
            return

        for i, vec in zip(indices, subset_vectors):
            vectors[i] = vec

    try:
        # Skip re-trying the full batch (caller already exhausted full retries
        # on the whole batch). Split immediately.
        mid = len(texts) // 2
        _try_range(list(range(mid)), full_retry=False)
        _try_range(list(range(mid, len(texts))), full_retry=False)
    finally:
        # Always restore the provider's original max_attempts setting
        if has_attempts_attr:
            provider.max_attempts = original_max_attempts

    return vectors, failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_error(msg: str, limit: int = _ERROR_TRUNCATE_LIMIT) -> str:
    """Truncate an error message for storage in chunk_variants.metadata_json."""
    return msg if len(msg) <= limit else msg[:limit] + "..."


def _warn_failed_chunk(text: str, error: str) -> None:
    """Log a WARN entry with the failing chunk's input prefix.

    Useful when filing bug reports against the embedding provider (e.g. the
    Ollama / bge-m3 NaN failure documented in EMBEDDING_NAN_FALLBACK_PROPOSAL.md).
    """
    sample = text[:_WARN_SAMPLE_LIMIT].replace("\n", " ")
    if len(text) > _WARN_SAMPLE_LIMIT:
        sample += "..."
    _logger.warning(
        "Embedding fallback for chunk (input prefix: %r) — error: %s",
        sample,
        _truncate_error(error, 200),
    )


__all__ = [
    "embed_with_fallback",
]
