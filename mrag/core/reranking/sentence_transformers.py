try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None  # type: ignore

from mrag.core.reranking.base import BaseReranker
from mrag.core.retrieval.base import RetrievalResult


class SentenceTransformersReranker(BaseReranker):
    def __init__(self, model: str) -> None:
        if CrossEncoder is None:
            raise ImportError(
                'sentence-transformers is not installed. '
                'Run: uv pip install -e ".[reranker]"'
            )
        self.model_name = model
        self._model = CrossEncoder(model)

    def rerank(self, query: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
        if not results:
            return []
        pairs = [(query, r.content) for r in results]
        scores = self._model.predict(pairs)
        reranked = [
            RetrievalResult(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                content=r.content,
                score=float(score),
                metadata={**r.metadata, "retrieval_score": r.score},
            )
            for r, score in zip(results, scores)
        ]
        reranked.sort(key=lambda r: r.score, reverse=True)
        return reranked
