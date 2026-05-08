from pathlib import Path

from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.retrieval.base import RetrievalResult, fetch_chunks
from mrag.db.qdrant import search as qdrant_search


def vector_search(
    query_text: str,
    knowledge_id: str,
    profile_name: str,
    db_path: Path,
    embedding_provider: BaseEmbeddingProvider,
    qdrant_client,
    col_name: str,
    top_k: int = 10,
) -> list[RetrievalResult]:
    query_vector = embedding_provider.embed([query_text])[0]
    hits = qdrant_search(qdrant_client, col_name, query_vector, top_k=top_k)

    if not hits:
        return []

    chunk_ids = [
        h["payload"]["chunk_id"]
        for h in hits
        if h.get("payload", {}).get("chunk_id")
    ]
    chunks = fetch_chunks(db_path, chunk_ids)

    results: list[RetrievalResult] = []
    for hit in hits:
        payload = hit.get("payload", {})
        chunk_id = payload.get("chunk_id")
        if chunk_id and chunk_id in chunks:
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    document_id=payload.get("document_id", ""),
                    content=chunks[chunk_id]["content"],
                    score=hit["score"],
                    metadata=payload,
                )
            )
    return results
