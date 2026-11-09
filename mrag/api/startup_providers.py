"""Which of the providers `mrag serve` built at startup a request may use.

The server builds the startup profile's embedding provider and reranker once,
but reads the profile from disk on every request, so the two could disagree: a
profile edited while serving had its new strategy and top_k honoured and its
old endpoint, model and reranker kept. A startup provider is now used only for
the startup profile and only while the settings it was built from are the ones
the request read; otherwise the request builds its own, as a request for any
other profile always has.
"""
from __future__ import annotations

from typing import Any

from mrag.config.profile import ProfileConfig
from mrag.core.reranking import reranker_key
from mrag.core.retrieval.runner import embedding_provider_key


def startup_providers(state: Any, profile_name: str, profile: ProfileConfig) -> tuple[Any, Any]:
    """The startup (embedding provider, reranker) this request may use, each or None."""
    if profile_name != state.profile_name:
        return None, None
    embedding = (
        state.embedding_provider
        if embedding_provider_key(profile) == state.embedding_key
        else None
    )
    reranker = (
        state.reranker
        if state.reranker is not None and reranker_key(profile.rerank) == state.reranker_key
        else None
    )
    return embedding, reranker
