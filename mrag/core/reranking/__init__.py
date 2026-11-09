from mrag.core.reranking.base import BaseReranker


def reranker_key(config) -> tuple:
    """The rerank settings a reranker built from `config` stands for.

    `enabled` is part of it: a reranker loaded while a profile enabled reranking
    must not keep reranking after the profile turns it off.
    """
    return (config.enabled, config.provider, config.model, config.max_length)


def get_reranker(config) -> BaseReranker:
    if config.provider == "sentence-transformers":
        from mrag.core.reranking.sentence_transformers import SentenceTransformersReranker
        return SentenceTransformersReranker(model=config.model, max_length=config.max_length)
    raise ValueError(f"Unsupported reranker provider: {config.provider}")
