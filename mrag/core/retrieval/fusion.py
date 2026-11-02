from mrag.core.retrieval.base import RetrievalResult

_RRF_K = 60


def _ordered(
    scores: dict[str, float],
    best: dict[str, RetrievalResult],
    result_lists: list[list[RetrievalResult]],
) -> list[RetrievalResult]:
    """Fused results by score; an exact tie goes to the candidate whose
    document more lists returned, then to the candidate seen first.

    Exact ties are structural: two chunks that each lead only one list both
    score 1/(k+1) under RRF, and the same under even weights. A chunk whose
    document the other list also found is the better bet, so *agreement* — how
    many lists returned any chunk of the candidate's document — orders a tie
    first; measured on three labelled sets and two embedding models, it was the
    only rule never worse than arrival order and better on one. What it leaves
    tied keeps the order the lists were walked in (the sort is stable over
    insertion order), never an ID, which differs between projects built from
    the same files.
    """
    returned = [{r.document_id for r in results} for results in result_lists]

    def agreement(chunk_id: str) -> int:
        document_id = best[chunk_id].document_id
        return sum(1 for documents in returned if document_id in documents)

    return [
        RetrievalResult(
            chunk_id=cid,
            document_id=best[cid].document_id,
            content=best[cid].content,
            score=score,
            metadata=best[cid].metadata,
        )
        for cid, score in sorted(scores.items(), key=lambda x: (-x[1], -agreement(x[0])))
    ]


def rrf_fusion(
    result_lists: list[list[RetrievalResult]],
    k: int = _RRF_K,
) -> list[RetrievalResult]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank+1) across all lists.

    Ties are ordered as `_ordered` describes.
    """
    scores: dict[str, float] = {}
    best: dict[str, RetrievalResult] = {}

    for results in result_lists:
        for rank, r in enumerate(results):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            if r.chunk_id not in best:
                best[r.chunk_id] = r

    return _ordered(scores, best, result_lists)


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
    """Normalize each list's scores to [0,1], then weighted sum.

    Ties are ordered as `_ordered` describes.
    """
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

    return _ordered(scores, best, result_lists)
