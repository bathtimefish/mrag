import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mrag import __version__
from mrag.api.routers.dify import DifyError
from mrag.api.routers.dify import router as dify_router
from mrag.api.routers.native import router as native_router
from mrag.config.profile import load_profile, validate_effective_tokenizer
from mrag.config.project import ProjectConfig
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from mrag.db.connection import find_db
from mrag.db.qdrant import collection_name, make_client, normalize_name


def create_app(
    project_dir: Path,
    profile_name: str,
    config: ProjectConfig,
    _embedding_provider: BaseEmbeddingProvider | None = None,
    _qdrant_client=None,
    reranker=None,
    reranking_allowed: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        db_path = find_db(project_dir)
        prof = load_profile(profile_name, project_dir)
        validate_effective_tokenizer(prof, config.fts_tokenizer)

        if _embedding_provider is not None:
            provider = _embedding_provider
        else:
            provider = OllamaEmbeddingProvider(
                model=prof.embedding.model,
                endpoint=prof.embedding.endpoint,
                max_attempts=prof.embedding.retry.max_attempts,
                initial_delay=prof.embedding.retry.initial_delay_seconds,
                backoff_multiplier=prof.embedding.retry.backoff_multiplier,
                max_delay=prof.embedding.retry.max_delay_seconds,
            )

        if _qdrant_client is not None:
            qdrant = _qdrant_client
        else:
            qdrant = make_client(
                mode=config.qdrant.mode,
                host=config.qdrant.host,
                port=config.qdrant.port,
                path=project_dir / "qdrant",
            )

        col = collection_name(
            config.knowledge_id,
            profile_name,
            normalize_name(prof.embedding.model),
        )

        app.state.project_dir = project_dir
        app.state.config = config
        app.state.db_path = db_path
        app.state.profile_name = profile_name
        app.state.embedding_provider = provider
        app.state.qdrant_client = qdrant
        app.state.col_name = col
        app.state.reranker = reranker
        app.state.reranking_allowed = reranking_allowed

        yield

    app = FastAPI(
        title="MRAG API",
        description="Micro RAG — A lightweight, local-first retrieval runtime.",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api_key = os.environ.get("MRAG_API_KEY", "")

    if api_key:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next) -> Response:
            if request.url.path in ("/", "/docs", "/openapi.json", "/redoc"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            is_dify = request.url.path == "/retrieval"
            if not auth.startswith("Bearer "):
                if is_dify:
                    return JSONResponse(
                        status_code=401,
                        content={"error_code": 1001, "error_msg": "Invalid Authorization header format."},
                    )
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            if auth != f"Bearer {api_key}":
                if is_dify:
                    return JSONResponse(
                        status_code=401,
                        content={"error_code": 1002, "error_msg": "Authorization failed. Please check your API key."},
                    )
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return await call_next(request)

    @app.exception_handler(DifyError)
    async def dify_error_handler(request: Request, exc: DifyError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "error_msg": exc.error_msg},
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(dify_router)
    app.include_router(native_router)

    return app
