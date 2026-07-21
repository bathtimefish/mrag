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
from mrag.core.retrieval.runner import fetch_filename_map, run_retrieval
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
        load_profile(profile_name, state.project_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        uses_startup_profile = profile_name == state.profile_name
        run = run_retrieval(
            query=req.query,
            project_dir=state.project_dir,
            config=config,
            profile_name=profile_name,
            strategy=req.strategy,
            top_k=req.top_k,
            # The startup provider is valid only for the profile that created
            # it.  Other profiles must resolve their own model and endpoint.
            embedding_provider=(
                state.embedding_provider if uses_startup_profile else None
            ),
            qdrant_client=state.qdrant_client,
            reranker=state.reranker if uses_startup_profile else None,
            load_reranker=(
                state.reranking_allowed
                and (not uses_startup_profile or state.reranker is None)
            ),
            no_rerank=not state.reranking_allowed,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (ConnectionError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    results = run.results
    filename_map = fetch_filename_map(db_path, results)

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
        strategy=run.strategy,
        reranked=run.reranked,
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
        weights=prof.retrieval.weights,
    )
