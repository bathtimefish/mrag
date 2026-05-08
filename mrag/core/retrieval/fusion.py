from mrag.core.retrieval.base import RetrievalResult

_RRF_K = 60


def rrf_fusion(
    result_lists: list[list[RetrievalResult]],
    k: int = _RRF_K,
) -> list[RetrievalResult]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank+1) across all lists."""
    scores: dict[str, float] = {}
    best: dict[str, RetrievalResult] = {}

    for results in result_lists:
        for rank, r in enumerate(results):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            if r.chunk_id not in best:
                best[r.chunk_id] = r

    return [
        RetrievalResult(
            chunk_id=cid,
            document_id=best[cid].document_id,
            content=best[cid].content,
            score=score,
            metadata=best[cid].metadata,
        )
        for cid, score in sorted(scores.items(), key=lambda x: -x[1])
    ]


def _normalize(results: list[RetrievalResult]) -> list[tuple[RetrievalResult, float]]:
    if not results:
        return []
    scores = [r.score for r in results]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [(r, 1.0) for r in results]
    return [(r, (r.score - lo) / (hi - lo)) for r in results]


def weighted_fusion(
    result_lists: list[list[RetrievalResult]],
    weights: list[float] | None = None,
) -> list[RetrievalResult]:
    """Normalize each list's scores to [0,1], then weighted sum."""
    if not result_lists:
        return []
    n = len(result_lists)
    if weights is None:
        weights = [1.0 / n] * n

    scores: dict[str, float] = {}
    best: dict[str, RetrievalResult] = {}

    for results, weight in zip(result_lists, weights):
        for r, norm_score in _normalize(results):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + weight * norm_score
            if r.chunk_id not in best:
                best[r.chunk_id] = r

    return [
        RetrievalResult(
            chunk_id=cid,
            document_id=best[cid].document_id,
            content=best[cid].content,
            score=score,
            metadata=best[cid].metadata,
        )
        for cid, score in sorted(scores.items(), key=lambda x: -x[1])
    ]
