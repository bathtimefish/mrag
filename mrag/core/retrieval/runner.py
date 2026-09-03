"""Shared retrieval runner for CLI, API, and MCP adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mrag.config.profile import (
    ProfileConfig,
    load_profile,
    validate_effective_tokenizer,
)
from mrag.config.project import ProjectConfig
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from mrag.core.retrieval.base import RetrievalResult
from mrag.core.retrieval.hybrid import hybrid_search
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import find_db, open_connection
from mrag.db.qdrant import make_client, normalize_name
from mrag.db.qdrant_migrate import resolve_collection


@dataclass
class RetrievalRun:
    query: str
    profile_name: str
    profile: ProfileConfig
    strategy: str
    requested_top_k: int
    retrieval_top_k: int
    reranked: bool
    results: list[RetrievalResult]


def fetch_filename_map(db_path: Path, results: list[RetrievalResult]) -> dict[str, str]:
    doc_ids = list({r.document_id for r in results})
    if not doc_ids:
        return {}
    conn = open_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, filename FROM documents WHERE id IN (%s)"
            % ",".join("?" * len(doc_ids)),
            doc_ids,
        ).fetchall()
    finally:
        conn.close()
    return {r["id"]: r["filename"] for r in rows}


def _make_provider(profile: ProfileConfig) -> OllamaEmbeddingProvider:
    return OllamaEmbeddingProvider(
        model=profile.embedding.model,
        endpoint=profile.embedding.endpoint,
        max_attempts=profile.embedding.retry.max_attempts,
        initial_delay=profile.embedding.retry.initial_delay_seconds,
        backoff_multiplier=profile.embedding.retry.backoff_multiplier,
        max_delay=profile.embedding.retry.max_delay_seconds,
    )


def run_retrieval(
    *,
    query: str,
    project_dir: Path,
    config: ProjectConfig,
    profile_name: str | None = None,
    strategy: str | None = None,
    top_k: int | None = None,
    no_rerank: bool = False,
    embedding_provider: BaseEmbeddingProvider | None = None,
    qdrant_client=None,
    reranker=None,
    load_reranker: bool = False,
    warn: Callable[[str], None] | None = None,
) -> RetrievalRun:
    """Run retrieval and optional reranking.

    This function centralizes the strategy dispatch that used to be duplicated
    across CLI and API layers.

    `top_k` is the final number of results. Leave it None to use the profile's
    `retrieval.top_k`; callers must not substitute a default of their own, or the
    profile value could never take effect.
    """
    resolved_profile_name = profile_name or config.default_profile
    profile = load_profile(resolved_profile_name, project_dir)
    effective_top_k = profile.retrieval.top_k if top_k is None else top_k
    db_path = find_db(project_dir)
    resolved_strategy = strategy or profile.retrieval.strategy
    tokenizer = validate_effective_tokenizer(profile, config.fts_tokenizer)

    active_reranker = reranker
    if active_reranker is None and load_reranker and profile.rerank.enabled and not no_rerank:
        from mrag.core.reranking import get_reranker

        active_reranker = get_reranker(profile.rerank)

    use_rerank = active_reranker is not None and not no_rerank
    retrieval_top_k = profile.rerank.top_n if use_rerank else effective_top_k

    if use_rerank and resolved_strategy == "parent_child" and warn is not None:
        warn(
            "rerank.enabled=true with strategy: parent_child. "
            "Reranking scores parent chunks (~3000 chars) after parent resolution. "
            "BERT-based rerankers truncate at 512 tokens, discarding most parent content. "
            "Consider disabling rerank for parent_child profiles."
        )

    if resolved_strategy == "keyword":
        results = keyword_search(
            query_text=query,
            knowledge_id=config.knowledge_id,
            profile_name=resolved_profile_name,
            db_path=db_path,
            top_k=retrieval_top_k,
            tokenizer=tokenizer,
        )
    else:
        provider = embedding_provider or _make_provider(profile)
        qdrant = qdrant_client or make_client(
            mode=config.qdrant.mode,
            host=config.qdrant.host,
            port=config.qdrant.port,
            path=project_dir / "qdrant",
        )
        col = resolve_collection(
            qdrant,
            db_path=db_path,
            config=config,
            profile_name=resolved_profile_name,
            model_normalized=normalize_name(profile.embedding.model),
            notify=warn,
        )

        if resolved_strategy == "vector":
            results = vector_search(
                query_text=query,
                knowledge_id=config.knowledge_id,
                profile_name=resolved_profile_name,
                db_path=db_path,
                embedding_provider=provider,
                qdrant_client=qdrant,
                col_name=col,
                top_k=retrieval_top_k,
            )
        elif resolved_strategy == "parent_child":
            results = hybrid_search(
                query_text=query,
                knowledge_id=config.knowledge_id,
                profile_name=resolved_profile_name,
                db_path=db_path,
                embedding_provider=provider,
                qdrant_client=qdrant,
                col_name=col,
                dense_top_k=profile.retrieval.dense_top_k,
                keyword_top_k=profile.retrieval.keyword_top_k,
                top_k=retrieval_top_k * 3,
                fusion=profile.retrieval.fusion,
                weights=profile.retrieval.weights,
                tokenizer=tokenizer,
            )
            from mrag.core.retrieval.parent_child import resolve_to_parent

            results = resolve_to_parent(results, db_path)[:retrieval_top_k]
        else:
            results = hybrid_search(
                query_text=query,
                knowledge_id=config.knowledge_id,
                profile_name=resolved_profile_name,
                db_path=db_path,
                embedding_provider=provider,
                qdrant_client=qdrant,
                col_name=col,
                dense_top_k=profile.retrieval.dense_top_k,
                keyword_top_k=profile.retrieval.keyword_top_k,
                top_k=retrieval_top_k,
                fusion=profile.retrieval.fusion,
                weights=profile.retrieval.weights,
                tokenizer=tokenizer,
            )

    reranked = False
    if use_rerank and results:
        results = active_reranker.rerank(query, results)[:effective_top_k]
        reranked = True

    return RetrievalRun(
        query=query,
        profile_name=resolved_profile_name,
        profile=profile,
        strategy=resolved_strategy,
        requested_top_k=effective_top_k,
        retrieval_top_k=retrieval_top_k,
        reranked=reranked,
        results=results,
    )


__all__ = ["RetrievalRun", "fetch_filename_map", "run_retrieval"]
