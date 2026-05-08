from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mrag.config.profile import load_profile
from mrag.core.retrieval.hybrid import hybrid_search
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import open_connection

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
    tokenizer = config.fts_tokenizer
    reranker = state.reranker

    retrieval_top_k = prof.rerank.top_n if reranker is not None else top_k

    if strategy == "keyword":
        results = keyword_search(
            query_text=req.query,
            knowledge_id=config.knowledge_id,
            profile_name=state.profile_name,
            db_path=state.db_path,
            top_k=retrieval_top_k,
            tokenizer=tokenizer,
        )
    elif strategy == "vector":
        results = vector_search(
            query_text=req.query,
            knowledge_id=config.knowledge_id,
            profile_name=state.profile_name,
            db_path=state.db_path,
            embedding_provider=state.embedding_provider,
            qdrant_client=state.qdrant_client,
            col_name=state.col_name,
            top_k=retrieval_top_k,
        )
    else:  # hybrid
        results = hybrid_search(
            query_text=req.query,
            knowledge_id=config.knowledge_id,
            profile_name=state.profile_name,
            db_path=state.db_path,
            embedding_provider=state.embedding_provider,
            qdrant_client=state.qdrant_client,
            col_name=state.col_name,
            dense_top_k=prof.retrieval.dense_top_k,
            keyword_top_k=prof.retrieval.keyword_top_k,
            top_k=retrieval_top_k,
            fusion=prof.retrieval.fusion,
            tokenizer=tokenizer,
        )

    if reranker is not None and results:
        results = reranker.rerank(req.query, results)[:top_k]

    doc_ids = list({r.document_id for r in results})
    conn = open_connection(state.db_path)
    doc_rows = (
        conn.execute(
            "SELECT id, filename FROM documents WHERE id IN (%s)"
            % ",".join("?" * len(doc_ids)),
            doc_ids,
        ).fetchall()
        if doc_ids
        else []
    )
    conn.close()
    filename_map = {r["id"]: r["filename"] for r in doc_rows}

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
