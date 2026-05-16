from pathlib import Path

from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.retrieval.base import RetrievalResult
from mrag.core.retrieval.fusion import rrf_fusion, weighted_fusion
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search


def hybrid_search(
    query_text: str,
    knowledge_id: str,
    profile_name: str,
    db_path: Path,
    embedding_provider: BaseEmbeddingProvider,
    qdrant_client,
    col_name: str,
    dense_top_k: int = 20,
    keyword_top_k: int = 20,
    top_k: int = 8,
    fusion: str = "rrf",
    weights: list[float] | None = None,
    tokenizer: str = "trigram",
) -> list[RetrievalResult]:
    vector_results = vector_search(
        query_text=query_text,
        knowledge_id=knowledge_id,
        profile_name=profile_name,
        db_path=db_path,
        embedding_provider=embedding_provider,
        qdrant_client=qdrant_client,
        col_name=col_name,
        top_k=dense_top_k,
    )
    keyword_results = keyword_search(
        query_text=query_text,
        knowledge_id=knowledge_id,
        profile_name=profile_name,
        db_path=db_path,
        top_k=keyword_top_k,
        tokenizer=tokenizer,
    )

    if fusion == "weighted":
        fused = weighted_fusion([vector_results, keyword_results], weights=weights)
    else:
        fused = rrf_fusion([vector_results, keyword_results])

    return fused[:top_k]
