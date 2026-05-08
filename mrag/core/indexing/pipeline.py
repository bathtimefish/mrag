import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mrag.config.profile import ProfileConfig, load_profile
from mrag.config.project import ProjectConfig
from mrag.core.chunking.base import get_chunker
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from mrag.core.indexing.diff import plan_indexing
from mrag.db import fts as fts_db
from mrag.db.connection import db_connection, fts_db_connection, find_db, open_connection
from mrag.db.qdrant import collection_name, delete_points, ensure_collection, make_client, upsert_points


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class IndexResult:
    indexed: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (document_id, message)


def _get_documents(db_path: Path, document_ids: list[str] | None) -> list[dict]:
    conn = open_connection(db_path)
    try:
        if document_ids:
            placeholders = ",".join("?" * len(document_ids))
            rows = conn.execute(
                f"SELECT * FROM documents WHERE status='extracted' AND id IN ({placeholders})",
                document_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE status='extracted'"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _upsert_profile(db_path: Path, profile: ProfileConfig, knowledge_id: str, profile_hash: str) -> None:
    import json
    now = _now_iso()
    relevant = {
        "chunking": profile.chunking.model_dump(),
        "embedding": profile.embedding.model_dump(),
        "retrieval": profile.retrieval.model_dump(),
        "contextual": profile.contextual.model_dump(),
        "keyword": profile.keyword.model_dump(),
        "rerank": profile.rerank.model_dump(),
    }
    config_json = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    with db_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO profiles (name, knowledge_id, config_json, profile_hash, created_at, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 config_json=excluded.config_json,
                 profile_hash=excluded.profile_hash,
                 updated_at=excluded.updated_at""",
            (profile.name, knowledge_id, config_json, profile_hash, now, now),
        )


def _set_indexing_status(db_path: Path, document_id: str, profile_name: str, knowledge_id: str,
                         file_hash: str, extracted_hash: str, profile_hash: str) -> None:
    now = _now_iso()
    row_id = str(uuid.uuid4())
    with db_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO document_indexes
               (id, knowledge_id, document_id, profile_name,
                document_file_hash, extracted_hash, profile_hash, status, indexed_at, error_message)
               VALUES (?,?,?,?,?,?,?,'indexing',NULL,NULL)
               ON CONFLICT(document_id, profile_name) DO UPDATE SET
                 document_file_hash=excluded.document_file_hash,
                 extracted_hash=excluded.extracted_hash,
                 profile_hash=excluded.profile_hash,
                 status='indexing',
                 indexed_at=NULL,
                 error_message=NULL""",
            (row_id, knowledge_id, document_id, profile_name,
             file_hash, extracted_hash, profile_hash),
        )


def _set_indexed_status(db_path: Path, document_id: str, profile_name: str) -> None:
    now = _now_iso()
    with db_connection(db_path) as conn:
        conn.execute(
            "UPDATE document_indexes SET status='indexed', indexed_at=? WHERE document_id=? AND profile_name=?",
            (now, document_id, profile_name),
        )


def _set_error_status(db_path: Path, document_id: str, profile_name: str, message: str) -> None:
    with db_connection(db_path) as conn:
        conn.execute(
            "UPDATE document_indexes SET status='error', error_message=? WHERE document_id=? AND profile_name=?",
            (message, document_id, profile_name),
        )


def _cleanup_document(db_path: Path, document_id: str, profile_name: str,
                       knowledge_id: str, qdrant_client, col_name: str,
                       tokenizer: str = "trigram") -> None:
    """Remove existing index data for a document before re-indexing."""
    conn = open_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT qdrant_point_id FROM chunk_variants WHERE document_id=? AND profile_name=?",
            (document_id, profile_name),
        ).fetchall()
        point_ids = [r[0] for r in rows if r[0]]
    finally:
        conn.close()

    if point_ids and qdrant_client is not None:
        delete_points(qdrant_client, col_name, point_ids)

    with fts_db_connection(db_path, tokenizer) as fts_conn:
        fts_db.delete_by_document(fts_conn, knowledge_id, profile_name, document_id)

    with db_connection(db_path) as conn:
        conn.execute(
            "DELETE FROM chunk_variants WHERE document_id=? AND profile_name=?",
            (document_id, profile_name),
        )
        conn.execute(
            "DELETE FROM chunks WHERE document_id=? AND profile_name=?",
            (document_id, profile_name),
        )


def _index_document(
    doc: dict,
    profile: ProfileConfig,
    profile_hash: str,
    project_dir: Path,
    db_path: Path,
    provider: BaseEmbeddingProvider,
    qdrant_client,
    col_name: str,
    use_cache: bool,
    cache_dir: Path,
    tokenizer: str = "trigram",
) -> None:
    # 1. Read extracted content
    source_format = profile.chunking.source_format
    path_key = "extracted_markdown_path" if source_format == "markdown" else "extracted_text_path"
    rel_path = doc.get(path_key)
    if not rel_path:
        raise ValueError(f"Extracted file path not recorded for document {doc['id']}")
    text = unicodedata.normalize("NFKC", (project_dir / rel_path).read_text(encoding="utf-8"))

    # 2. Chunk
    chunker = get_chunker(
        profile.chunking.strategy,
        profile.chunking.chunk_size,
        profile.chunking.overlap,
    )
    chunks = chunker.chunk(text, {"document_id": doc["id"], "profile_name": profile.name})

    if not chunks:
        return

    contents = [c.content for c in chunks]

    # 3. Embed (with optional cache)
    if use_cache:
        from mrag.core.embedding.cache import EmbeddingCache
        cache = EmbeddingCache(cache_dir=cache_dir, db_path=db_path)
        model_id = provider.get_model_id() if provider._dimension else None
        if model_id is None:
            # Need to discover dimension first
            vectors = provider.embed(contents)
        else:
            vectors = cache.get_or_embed(contents, model_id, provider.embed)
    else:
        vectors = provider.embed(contents)

    model_id = provider.get_model_id()

    # 4. Cleanup old data
    _cleanup_document(db_path, doc["id"], profile.name, doc["knowledge_id"], qdrant_client, col_name, tokenizer)

    now = _now_iso()

    # 5. Insert new chunks + variants
    chunk_ids: list[str] = []
    variant_ids: list[str] = []
    point_ids: list[str] = []
    with db_connection(db_path) as conn:
        for chunk in chunks:
            chunk_id = str(uuid.uuid4())
            chunk_ids.append(chunk_id)
            conn.execute(
                """INSERT INTO chunks
                   (id, knowledge_id, document_id, profile_name, parent_chunk_id,
                    chunk_type, chunk_index, content, source_format,
                    token_count, char_count, metadata_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL,?,?)""",
                (
                    chunk_id,
                    doc["knowledge_id"],
                    doc["id"],
                    profile.name,
                    chunk.parent_chunk_id,
                    chunk.chunk_type,
                    chunk.chunk_index,
                    chunk.content,
                    source_format,
                    len(chunk.content),
                    now,
                    now,
                ),
            )

        for chunk_id, chunk in zip(chunk_ids, chunks):
            variant_id = str(uuid.uuid4())
            point_id = str(uuid.uuid4())
            variant_ids.append(variant_id)
            point_ids.append(point_id)
            conn.execute(
                """INSERT INTO chunk_variants
                   (id, knowledge_id, document_id, chunk_id, profile_name,
                    variant_type, content_for_embedding, context_text,
                    embedding_model_id, qdrant_point_id, qdrant_collection,
                    metadata_json, created_at)
                   VALUES (?,?,?,?,?,?,?,NULL,?,?,?,NULL,?)""",
                (
                    variant_id,
                    doc["knowledge_id"],
                    doc["id"],
                    chunk_id,
                    profile.name,
                    "raw",
                    chunk.content,
                    model_id,
                    point_id,
                    col_name,
                    now,
                ),
            )

    # Insert FTS chunks using tokenizer-aware connection
    with fts_db_connection(db_path, tokenizer) as fts_conn:
        for chunk_id, chunk in zip(chunk_ids, chunks):
            fts_db.insert_chunk(
                fts_conn,
                chunk.content,
                doc["knowledge_id"],
                profile.name,
                chunk_id,
                doc["id"],
            )

    # 6. Upsert Qdrant
    if qdrant_client is not None:
        points = [
            {
                "id": point_id,
                "vector": vector,
                "payload": {
                    "chunk_id": chunk_id,
                    "document_id": doc["id"],
                    "profile_name": profile.name,
                    "knowledge_id": doc["knowledge_id"],
                    "chunk_index": chunk.chunk_index,
                },
            }
            for point_id, chunk_id, chunk, vector in zip(
                point_ids, chunk_ids, chunks, vectors
            )
        ]
        upsert_points(qdrant_client, col_name, points)


def run_index(
    project_dir: Path,
    config: ProjectConfig,
    profile_name: str,
    document_ids: list[str] | None = None,
    force: bool = False,
    embedding_provider: BaseEmbeddingProvider | None = None,
    qdrant_client=None,
) -> IndexResult:
    db_path = find_db(project_dir)
    profile = load_profile(profile_name, project_dir)
    profile_hash = profile.compute_hash()

    _upsert_profile(db_path, profile, config.knowledge_id, profile_hash)

    documents = _get_documents(db_path, document_ids)
    if not documents:
        return IndexResult()

    if force:
        decisions = [(doc, True, "force") for doc in documents]
    else:
        plans = plan_indexing(documents, profile_name, profile_hash, db_path)
        doc_map = {d["id"]: d for d in documents}
        decisions = [(doc_map[p.document_id], p.needs_index, p.reason) for p in plans]

    docs_to_index = [(doc, reason) for doc, needs, reason in decisions if needs]
    skipped = sum(1 for _, needs, _ in decisions if not needs)

    result = IndexResult(skipped=skipped)

    if not docs_to_index:
        return result

    # Build embedding provider
    if embedding_provider is None:
        embedding_provider = OllamaEmbeddingProvider(
            model=profile.embedding.model,
            endpoint=profile.embedding.endpoint,
        )

    # Probe: discover dimension, register model, ensure collection
    embedding_provider.embed([""])
    model_id = embedding_provider.get_model_id()
    embedding_provider.ensure_model_registered(db_path)

    col_name = collection_name(
        config.knowledge_id,
        profile_name,
        embedding_provider.get_normalized_name(),
    )

    if qdrant_client is None:
        qdrant_client = make_client(
            mode=config.qdrant.mode,
            host=config.qdrant.host,
            port=config.qdrant.port,
            path=project_dir / "qdrant",
        )

    ensure_collection(qdrant_client, col_name, embedding_provider.get_dimension())

    use_cache = profile.embedding.cache.enabled
    cache_dir = project_dir / "cache" / "embeddings"

    tokenizer = config.fts_tokenizer

    for doc, reason in docs_to_index:
        _set_indexing_status(
            db_path, doc["id"], profile_name, doc["knowledge_id"],
            doc["file_hash"], doc["extracted_hash"], profile_hash,
        )
        try:
            _index_document(
                doc=doc,
                profile=profile,
                profile_hash=profile_hash,
                project_dir=project_dir,
                db_path=db_path,
                provider=embedding_provider,
                qdrant_client=qdrant_client,
                col_name=col_name,
                use_cache=use_cache,
                cache_dir=cache_dir,
                tokenizer=tokenizer,
            )
            _set_indexed_status(db_path, doc["id"], profile_name)
            result.indexed += 1
        except Exception as exc:
            msg = str(exc)
            _set_error_status(db_path, doc["id"], profile_name, msg)
            result.errors.append((doc["id"], msg))

    return result


def cleanup_profile_index(
    project_dir: Path,
    config: ProjectConfig,
    profile_name: str,
    qdrant_client=None,
) -> None:
    """Delete all index data for a profile (used by mrag reindex)."""
    db_path = find_db(project_dir)

    conn = open_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT qdrant_point_id, qdrant_collection FROM chunk_variants WHERE profile_name=?",
            (profile_name,),
        ).fetchall()
        point_ids = [r[0] for r in rows if r[0]]
        col_names = {r[1] for r in rows if r[1]}
    finally:
        conn.close()

    if point_ids and qdrant_client is not None:
        for col in col_names:
            col_ids = [r[0] for r in rows if r[1] == col and r[0]]
            if col_ids:
                delete_points(qdrant_client, col, col_ids)

    with fts_db_connection(db_path, config.fts_tokenizer) as fts_conn:
        fts_db.delete_by_profile(fts_conn, config.knowledge_id, profile_name)

    with db_connection(db_path) as conn:
        conn.execute("DELETE FROM chunk_variants WHERE profile_name=?", (profile_name,))
        conn.execute("DELETE FROM chunks WHERE profile_name=?", (profile_name,))
        conn.execute("DELETE FROM document_indexes WHERE profile_name=?", (profile_name,))
