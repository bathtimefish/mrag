from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from mrag.config.profile import load_profile
from mrag.core.retrieval.runner import fetch_filename_map, run_retrieval

router = APIRouter()


# ---------------------------------------------------------------------------
# Dify-compatible error — raised inside the router, caught by app-level handler
# ---------------------------------------------------------------------------

class DifyError(Exception):
    def __init__(self, status_code: int, error_code: int, error_msg: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.error_msg = error_msg


# ---------------------------------------------------------------------------
# Request / Response models (Dify External Knowledge API spec)
# ---------------------------------------------------------------------------

class RetrievalSetting(BaseModel):
    top_k: int = Field(ge=1, le=100)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)


class DifyRetrieveRequest(BaseModel):
    knowledge_id: str
    query: str
    retrieval_setting: RetrievalSetting
    metadata_condition: dict[str, Any] | None = None  # accepted, not applied


class DifyRecord(BaseModel):
    content: str
    score: float
    title: str
    metadata: dict[str, Any]


class DifyRetrieveResponse(BaseModel):
    records: list[DifyRecord]


# ---------------------------------------------------------------------------
# Score normalization: map all strategies to [0, 1]
# ---------------------------------------------------------------------------

def _normalize_score(score: float, strategy: str) -> float:
    if strategy == "keyword":
        # BM25 scores are positive and unbounded; map to (0, 1) via score/(1+score)
        return score / (1.0 + score)
    # vector: cosine similarity already in [0, 1]
    # hybrid: RRF scores already in [0, 1]
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# POST /retrieval
# ---------------------------------------------------------------------------

@router.post("/retrieval", response_model=DifyRetrieveResponse)
async def dify_retrieve(req: DifyRetrieveRequest, request: Request) -> DifyRetrieveResponse:
    state = request.app.state
    config = state.config

    if req.knowledge_id != config.knowledge_id:
        raise DifyError(
            status_code=404,
            error_code=2001,
            error_msg=f"Knowledge base '{req.knowledge_id}' not found.",
        )

    prof = load_profile(state.profile_name, state.project_dir)
    strategy = prof.retrieval.strategy
    top_k = req.retrieval_setting.top_k
    score_threshold = req.retrieval_setting.score_threshold
    run = run_retrieval(
        query=req.query,
        project_dir=state.project_dir,
        config=config,
        profile_name=state.profile_name,
        strategy=strategy,
        top_k=top_k,
        embedding_provider=state.embedding_provider,
        qdrant_client=state.qdrant_client,
        reranker=state.reranker,
    )
    results = run.results
    filename_map = fetch_filename_map(state.db_path, results)

    records: list[DifyRecord] = []
    for r in results:
        normalized = _normalize_score(r.score, strategy)
        if normalized < score_threshold:
            continue
        records.append(
            DifyRecord(
                content=r.content,
                score=round(normalized, 6),
                title=filename_map.get(r.document_id, r.document_id[:8]),
                metadata=r.metadata if r.metadata else {},
            )
        )

    return DifyRetrieveResponse(records=records)
