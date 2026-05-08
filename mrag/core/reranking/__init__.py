from mrag.core.reranking.base import BaseReranker


def get_reranker(config) -> BaseReranker:
    if config.provider == "sentence-transformers":
        from mrag.core.reranking.sentence_transformers import SentenceTransformersReranker
        return SentenceTransformersReranker(model=config.model)
    raise ValueError(f"Unsupported reranker provider: {config.provider}")
