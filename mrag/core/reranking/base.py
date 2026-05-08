from abc import ABC, abstractmethod

from mrag.core.retrieval.base import RetrievalResult


class BaseReranker(ABC):
    @abstractmethod
    def rerank(self, query: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
        """Rescore and re-sort results. Returns list sorted by reranker score descending."""
        ...
