from fastapi import APIRouter, HTTPException, Request

from mrag.api.models import (
    ChunkResult,
    DocumentDetail,
    DocumentItem,
    ProfileDetail,
    ProfileItem,
    RetrieveRequest,
    RetrieveResponse,
)
from mrag.config.profile import load_profile
from mrag.core.retrieval.hybrid import hybrid_search
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import open_connection

router = APIRouter(prefix="/api/v1")


def _get_state(request: Request):
    return request.app.state


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest, request: Request) -> RetrieveResponse:
    state = _get_state(request)
    config = state.config
    db_path = state.db_path

    profile_name = req.profile or config.default_profile

    try:
        prof = load_profile(profile_name, state.project_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    retrieval_strategy = req.strategy or prof.retrieval.strategy
    top_k = req.top_k
    tokenizer = config.fts_tokenizer
    reranker = state.reranker

    retrieval_top_k = prof.rerank.top_n if reranker is not None else top_k

    try:
        if retrieval_strategy == "keyword":
            results = keyword_search(
                query_text=req.query,
                knowledge_id=config.knowledge_id,
                profile_name=profile_name,
                db_path=db_path,
                top_k=retrieval_top_k,
                tokenizer=tokenizer,
            )
        elif retrieval_strategy == "vector":
            results = vector_search(
                query_text=req.query,
                knowledge_id=config.knowledge_id,
                profile_name=profile_name,
                db_path=db_path,
                embedding_provider=state.embedding_provider,
                qdrant_client=state.qdrant_client,
                col_name=state.col_name,
                top_k=retrieval_top_k,
            )
        else:  # hybrid
            results = hybrid_search(
                query_text=req.query,
                knowledge_id=config.knowledge_id,
                profile_name=profile_name,
                db_path=db_path,
                embedding_provider=state.embedding_provider,
                qdrant_client=state.qdrant_client,
                col_name=state.col_name,
                dense_top_k=prof.retrieval.dense_top_k,
                keyword_top_k=prof.retrieval.keyword_top_k,
                top_k=retrieval_top_k,
                fusion=prof.retrieval.fusion,
                tokenizer=tokenizer,
            )
    except (ConnectionError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    reranked = False
    if reranker is not None and results:
        results = reranker.rerank(req.query, results)[:top_k]
        reranked = True

    doc_ids = list({r.document_id for r in results})
    conn = open_connection(db_path)
    doc_rows = conn.execute(
        "SELECT id, filename FROM documents WHERE id IN (%s)"
        % ",".join("?" * len(doc_ids)),
        doc_ids,
    ).fetchall() if doc_ids else []
    conn.close()
    filename_map = {r["id"]: r["filename"] for r in doc_rows}

    chunk_results = [
        ChunkResult(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            filename=filename_map.get(r.document_id, r.document_id[:8]),
            score=r.score,
            content=r.content,
            metadata=r.metadata,
        )
        for r in results
    ]

    return RetrieveResponse(
        query=req.query,
        profile=profile_name,
        strategy=retrieval_strategy,
        reranked=reranked,
        results=chunk_results,
    )


@router.post("/search", response_model=RetrieveResponse)
async def search(req: RetrieveRequest, request: Request) -> RetrieveResponse:
    return await retrieve(req, request)


@router.get("/documents", response_model=list[DocumentItem])
async def list_documents(request: Request) -> list[DocumentItem]:
    state = _get_state(request)
    conn = open_connection(state.db_path)
    rows = conn.execute(
        "SELECT id, filename, file_hash, status, created_at FROM documents ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [
        DocumentItem(
            id=r["id"],
            filename=r["filename"],
            file_hash=r["file_hash"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, request: Request) -> DocumentDetail:
    state = _get_state(request)
    conn = open_connection(state.db_path)
    row = conn.execute(
        "SELECT id, filename, file_hash, status, created_at, extracted_text_path FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")

    chunk_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
    ).fetchone()[0]
    conn.close()

    return DocumentDetail(
        id=row["id"],
        filename=row["filename"],
        file_hash=row["file_hash"],
        status=row["status"],
        created_at=row["created_at"],
        extracted_text_path=row["extracted_text_path"],
        chunk_count=chunk_count,
    )


@router.get("/profiles", response_model=list[ProfileItem])
async def list_profiles(request: Request) -> list[ProfileItem]:
    state = _get_state(request)
    conn = open_connection(state.db_path)
    rows = conn.execute("SELECT name FROM profiles ORDER BY name").fetchall()
    conn.close()

    items: list[ProfileItem] = []
    for row in rows:
        try:
            prof = load_profile(row["name"], state.project_dir)
            items.append(
                ProfileItem(
                    name=prof.name,
                    strategy=prof.retrieval.strategy,
                    embedding_model=prof.embedding.model,
                    chunking_strategy=prof.chunking.strategy,
                )
            )
        except FileNotFoundError:
            pass
    return items


@router.get("/profiles/{profile_name}", response_model=ProfileDetail)
async def get_profile(profile_name: str, request: Request) -> ProfileDetail:
    state = _get_state(request)
    try:
        prof = load_profile(profile_name, state.project_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ProfileDetail(
        name=prof.name,
        strategy=prof.retrieval.strategy,
        embedding_model=prof.embedding.model,
        chunking_strategy=prof.chunking.strategy,
        chunk_size=prof.chunking.chunk_size,
        overlap=prof.chunking.overlap,
        dense_top_k=prof.retrieval.dense_top_k,
        keyword_top_k=prof.retrieval.keyword_top_k,
        fusion=prof.retrieval.fusion,
    )
