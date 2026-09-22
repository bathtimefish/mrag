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
    metadata_condition: Any = None


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


_FILTERABLE = {"document_id", "source", "chunk_id", "heading_path"}
_COMPARISONS = {"contains", "not contains", "start with", "end with", "is", "is not", "empty", "not empty"}


def _compile_filter(raw: Any) -> tuple[str, list[tuple[list[str], str, str]]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"logical_operator", "conditions"}:
        raise DifyError(400, 4001, "Unsupported metadata_condition structure.")
    logical = raw.get("logical_operator", "and")
    conditions = raw.get("conditions")
    if logical not in ("and", "or") or not isinstance(conditions, list) or not conditions:
        raise DifyError(400, 4001, "metadata_condition needs an and/or operator and nonempty conditions.")
    clauses = []
    for item in conditions:
        if not isinstance(item, dict) or set(item) - {"name", "comparison_operator", "value"}:
            raise DifyError(400, 4001, "Unsupported metadata condition.")
        names = item.get("name")
        if isinstance(names, str):
            names = [names]
        op = item.get("comparison_operator")
        if not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in _FILTERABLE for n in names):
            raise DifyError(400, 4001, "Condition names an unsupported field.")
        if not isinstance(op, str) or op not in _COMPARISONS:
            raise DifyError(400, 4001, f"Unsupported comparison_operator: {op}.")
        if op not in ("empty", "not empty") and not isinstance(item.get("value"), str):
            raise DifyError(400, 4001, f"comparison_operator '{op}' requires a string value.")
        clauses.append((names, op, item.get("value", "")))
    return logical, clauses


def _filter_admits(compiled, metadata: dict[str, str]) -> bool:
    if compiled is None:
        return True
    logical, clauses = compiled

    def matches(clause) -> bool:
        names, op, value = clause
        for name in names:
            field = metadata[name]
            if (op == "contains" and value in field or
                op == "not contains" and value not in field or
                op == "start with" and field.startswith(value) or
                op == "end with" and field.endswith(value) or
                op == "is" and field == value or
                op == "is not" and field != value or
                op == "empty" and not field or
                op == "not empty" and bool(field)):
                return True
        return False

    return (all if logical == "and" else any)(matches(c) for c in clauses)


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
    compiled_filter = _compile_filter(req.metadata_condition)
    run = run_retrieval(
        query=req.query,
        project_dir=state.project_dir,
        config=config,
        profile_name=state.profile_name,
        strategy=strategy,
        top_k=min(top_k * 4, 400) if compiled_filter is not None else top_k,
        embedding_provider=state.embedding_provider,
        qdrant_client=state.qdrant_client,
        reranker=state.reranker,
    )
    results = run.results
    filename_map = fetch_filename_map(state.db_path, results)

    records: list[DifyRecord] = []
    for r in results:
        source = filename_map.get(r.document_id, r.document_id[:8])
        heading = r.metadata.get("heading_path", []) if r.metadata else []
        if isinstance(heading, list):
            heading = " > ".join(str(part) for part in heading)
        metadata = {
            "document_id": r.document_id,
            "source": source,
            "chunk_id": r.chunk_id,
            "heading_path": str(heading),
        }
        if not _filter_admits(compiled_filter, metadata):
            continue
        normalized = _normalize_score(r.score, strategy)
        if normalized < score_threshold:
            continue
        records.append(
            DifyRecord(
                content=r.content,
                score=round(normalized, 6),
                title=source,
                metadata=metadata,
            )
        )
        if len(records) >= top_k:
            break

    return DifyRetrieveResponse(records=records)
