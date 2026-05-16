import json
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from mrag.config.profile import ProfileConfig, load_profile
from mrag.config.project import ProjectConfig
from mrag.core.chunking.base import ChunkData, get_chunker
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from mrag.core.indexing.diff import plan_indexing
from mrag.db import fts as fts_db
from mrag.db.connection import db_connection, fts_db_connection, find_db, open_connection
from mrag.db.qdrant import collection_name, delete_points, ensure_collection, make_client, upsert_points

if TYPE_CHECKING:
    from rich.console import Console


# Embedding 入力長の警告閾値。bge-m3 等の代表的なモデルは ~8192 トークン上限を
# 持ち、安全側に倒して半分以下の閾値を設定する。テーブル/コードのような
# token-dense なコンテンツでもこの閾値以下なら入力上限を超えにくい。
_EMBEDDING_INPUT_WARN_THRESHOLD = 6000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_ts() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def _load_context_prompt(project_dir: Path) -> str | None:
    """Read profiles/context_prompt.txt if it exists; return None to use the built-in default."""
    prompt_path = project_dir / "profiles" / "context_prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return None


@dataclass
class FailedDocEntry:
    document_id: str
    filename: str
    source_path: str
    source_type: str
    added_at: str
    failure_reason: str
    failure_stage: str
    page_count: int | None
    char_count: int | None


@dataclass
class IndexResult:
    indexed: int = 0
    skipped: int = 0           # diff-skipped: already up-to-date
    skipped_by_list: int = 0   # explicitly skipped via --skip-list-json
    errors: list[tuple[str, str]] = field(default_factory=list)  # (document_id, message)
    raw_fallback_chunks: int = 0
    failed_docs: list[FailedDocEntry] = field(default_factory=list)


def _read_extraction_meta(project_dir: Path, doc: dict) -> dict:
    meta_rel = doc.get("extraction_meta_path")
    if not meta_rel:
        return {}
    meta_path = project_dir / meta_rel
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_index_log(
    result: "IndexResult",
    log_path: Path,
    command: str,
    profile_name: str,
) -> None:
    try:
        from importlib.metadata import version as _pkg_version
        mrag_version = _pkg_version("mrag")
    except Exception:
        mrag_version = "unknown"

    total = result.indexed + result.skipped + result.skipped_by_list + len(result.errors)
    data = {
        "generated_at": _now_iso(),
        "command": command,
        "profile": profile_name,
        "mrag_version": mrag_version,
        "total_documents": total,
        "indexed_count": result.indexed,
        "up_to_date_count": result.skipped,
        "list_skipped_count": result.skipped_by_list,
        "failed_count": len(result.errors),
        "raw_fallback_chunks": result.raw_fallback_chunks,
        "failed_documents": [
            {
                "document_id": fd.document_id,
                "filename": fd.filename,
                "source_path": fd.source_path,
                "source_type": fd.source_type,
                "added_at": fd.added_at,
                "failure_reason": fd.failure_reason,
                "failure_stage": fd.failure_stage,
                "page_count": fd.page_count,
                "char_count": fd.char_count,
            }
            for fd in result.failed_docs
        ],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
        "augmentation": profile.augmentation.model_dump(),
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


def _chunk_qdrant_meta(chunk) -> dict:
    """Extract fields from chunk.metadata to include in Qdrant payload."""
    meta = chunk.metadata
    result: dict = {}
    if meta.get("heading_path_text"):
        result["heading_path_text"] = meta["heading_path_text"]
    if meta.get("section_id"):
        result["section_id"] = meta["section_id"]
    if meta.get("contains_table"):
        result["contains_table"] = True
    if meta.get("contains_code"):
        result["contains_code"] = True
    return result


def _resolve_parent_ids(
    chunks: list[ChunkData],
    chunk_id_list: list[str],
) -> None:
    """Parent チャンクの仮 hint ID を実際の UUID に解決する。in-place で更新する。"""
    hint_to_id: dict[str, str] = {}
    for chunk, chunk_id in zip(chunks, chunk_id_list):
        if chunk.chunk_type == "parent":
            hint = chunk.metadata.pop("_parent_id_hint", "")
            if hint:
                hint_to_id[hint] = chunk_id

    for chunk in chunks:
        if chunk.chunk_type == "child" and chunk.parent_chunk_id in hint_to_id:
            chunk.parent_chunk_id = hint_to_id[chunk.parent_chunk_id]


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
    on_doc_start: Callable[[int], None] | None = None,
    on_chunk_augmented: Callable[[int, int], None] | None = None,
    on_chunk_retry: Callable[[int, int, int, int, Exception], None] | None = None,
    on_chunk_fallback: Callable[[int, int, Exception], None] | None = None,
    on_chunks_oversized: Callable[[int, int, int], None] | None = None,
) -> int:
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
        preserve_heading_path=profile.chunking.preserve_heading_path,
        preserve_tables=profile.chunking.preserve_tables,
        preserve_code_blocks=profile.chunking.preserve_code_blocks,
        source_format=profile.chunking.source_format,
        parent_config=profile.chunking.parent,
        child_config=profile.chunking.child,
    )
    chunks = chunker.chunk(text, {"document_id": doc["id"], "profile_name": profile.name})

    if not chunks:
        return

    # 2b. UUID を先払いで割り当て。parent_child の場合は child→parent 参照を解決する。
    chunk_id_list: list[str] = [str(uuid.uuid4()) for _ in chunks]
    if profile.chunking.strategy == "parent_child":
        _resolve_parent_ids(chunks, chunk_id_list)

    # indexable = variants / FTS / Qdrant の対象（parent chunk を除外）
    indexable_pairs = [
        (cid, c)
        for cid, c in zip(chunk_id_list, chunks)
        if c.chunk_type in ("chunk", "child")
    ]
    indexable_ids: list[str] = [p[0] for p in indexable_pairs]
    indexable_chunks: list[ChunkData] = [p[1] for p in indexable_pairs]

    if on_doc_start:
        # parent_child strategy では parent チャンクを除いた数を報告する
        on_doc_start(len(indexable_chunks))

    # 3. Augment chunks — indexable チャンクのみ（parent は augmentation 対象外）
    context_texts: list[str | None] = [None] * len(indexable_chunks)
    contents_for_embedding: list[str] = [c.content for c in indexable_chunks]
    variant_types: list[str] = ["raw"] * len(indexable_chunks)
    fallback_errors: dict[int, str] = {}  # chunk index (0-based) -> error message

    if profile.augmentation.strategy == "contextual":
        from mrag.core.indexing.augmentation import augment_chunks
        prompt_template = _load_context_prompt(project_dir)

        def _capture_fallback(cur: int, total: int, exc: Exception) -> None:
            fallback_errors[cur - 1] = str(exc)[:200]
            if on_chunk_fallback is not None:
                on_chunk_fallback(cur, total, exc)

        ctx_list = augment_chunks(indexable_chunks, text, profile.augmentation, prompt_template,
                                  on_chunk=on_chunk_augmented,
                                  on_chunk_retry=on_chunk_retry,
                                  on_chunk_fallback=_capture_fallback)
        for i, ctx in enumerate(ctx_list):
            if ctx is not None:
                context_texts[i] = ctx
                contents_for_embedding[i] = ctx + "\n\n" + indexable_chunks[i].content
                variant_types[i] = "contextual"
            # else: remains raw (context_texts[i]=None, original content, "raw" variant_type)

    # 3b. Warn if any content exceeds the embedding model input safety threshold.
    # Silent truncation by the embedding model can degrade retrieval quality
    # without surfacing an error, so we flag this case proactively.
    if on_chunks_oversized is not None:
        oversized_lens = [
            len(c) for c in contents_for_embedding
            if len(c) > _EMBEDDING_INPUT_WARN_THRESHOLD
        ]
        if oversized_lens:
            on_chunks_oversized(
                len(oversized_lens),
                max(oversized_lens),
                _EMBEDDING_INPUT_WARN_THRESHOLD,
            )

    # 4. Embed (with optional cache) — indexable チャンクのみ
    if use_cache:
        from mrag.core.embedding.cache import EmbeddingCache
        cache = EmbeddingCache(cache_dir=cache_dir, db_path=db_path)
        model_id = provider.get_model_id() if provider._dimension else None
        if model_id is None:
            vectors = provider.embed(contents_for_embedding)
        else:
            vectors = cache.get_or_embed(contents_for_embedding, model_id, provider.embed)
    else:
        vectors = provider.embed(contents_for_embedding)

    model_id = provider.get_model_id()

    # 6. Cleanup old data
    _cleanup_document(db_path, doc["id"], profile.name, doc["knowledge_id"], qdrant_client, col_name, tokenizer)

    now = _now_iso()

    # 7. Insert chunks (ALL — parent も含む) + variants (indexable のみ)
    variant_ids: list[str] = []
    point_ids: list[str] = []
    with db_connection(db_path) as conn:
        for chunk_id, chunk in zip(chunk_id_list, chunks):
            chunk_metadata_json = (
                json.dumps(chunk.metadata, ensure_ascii=False)
                if chunk.metadata else None
            )
            conn.execute(
                """INSERT INTO chunks
                   (id, knowledge_id, document_id, profile_name, parent_chunk_id,
                    chunk_type, chunk_index, content, source_format,
                    token_count, char_count, metadata_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)""",
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
                    chunk_metadata_json,
                    now,
                    now,
                ),
            )

        for i, (chunk_id, chunk) in enumerate(zip(indexable_ids, indexable_chunks)):
            variant_id = str(uuid.uuid4())
            point_id = str(uuid.uuid4())
            variant_ids.append(variant_id)
            point_ids.append(point_id)
            var_meta = None
            if i in fallback_errors:
                var_meta = json.dumps(
                    {"augmentation_status": "fallback_raw", "augmentation_error": fallback_errors[i]},
                    ensure_ascii=False,
                )
            conn.execute(
                """INSERT INTO chunk_variants
                   (id, knowledge_id, document_id, chunk_id, profile_name,
                    variant_type, content_for_embedding, context_text,
                    embedding_model_id, qdrant_point_id, qdrant_collection,
                    metadata_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    variant_id,
                    doc["knowledge_id"],
                    doc["id"],
                    chunk_id,
                    profile.name,
                    variant_types[i],
                    contents_for_embedding[i],
                    context_texts[i],
                    model_id,
                    point_id,
                    col_name,
                    var_meta,
                    now,
                ),
            )

    # Insert FTS chunks — indexable のみ
    # When augmentation.strategy=contextual, contents_for_embedding contains
    # the contextualized chunk (LLM-generated context + original content).
    # Anthropic's Contextual Retrieval recommends indexing the same contextualized
    # text in BOTH the embedding and BM25 indexes — known as "Contextual BM25".
    # For non-contextual augmentation (or fallback-raw chunks), contents_for_embedding[i]
    # equals indexable_chunks[i].content, so this preserves the original behavior.
    with fts_db_connection(db_path, tokenizer) as fts_conn:
        for i, chunk_id in enumerate(indexable_ids):
            fts_db.insert_chunk(
                fts_conn,
                contents_for_embedding[i],
                doc["knowledge_id"],
                profile.name,
                chunk_id,
                doc["id"],
            )

    # 8. Upsert Qdrant — indexable のみ
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
                    **_chunk_qdrant_meta(chunk),
                },
            }
            for point_id, chunk_id, chunk, vector in zip(
                point_ids, indexable_ids, indexable_chunks, vectors
            )
        ]
        upsert_points(qdrant_client, col_name, points)

    return len(fallback_errors)


def run_index(
    project_dir: Path,
    config: ProjectConfig,
    profile_name: str,
    document_ids: list[str] | None = None,
    skip_document_ids: set[str] | None = None,
    force: bool = False,
    embedding_provider: BaseEmbeddingProvider | None = None,
    qdrant_client=None,
    console: "Console | None" = None,
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

    # Apply explicit skip list
    skipped_by_list = 0
    if skip_document_ids:
        filtered = []
        for doc, reason in docs_to_index:
            if doc["id"] in skip_document_ids:
                skipped_by_list += 1
                if console:
                    filename = doc.get("filename", doc["id"])
                    console.print(f"[dim]{_now_ts()}[/dim]  [yellow]⤭ skip-list[/yellow]  {filename}")
            else:
                filtered.append((doc, reason))
        docs_to_index = filtered

    result = IndexResult(skipped=skipped, skipped_by_list=skipped_by_list)

    if not docs_to_index:
        return result

    # Build embedding provider
    if embedding_provider is None:
        embedding_provider = OllamaEmbeddingProvider(
            model=profile.embedding.model,
            endpoint=profile.embedding.endpoint,
            max_attempts=profile.embedding.retry.max_attempts,
            initial_delay=profile.embedding.retry.initial_delay_seconds,
            backoff_multiplier=profile.embedding.retry.backoff_multiplier,
            max_delay=profile.embedding.retry.max_delay_seconds,
        )

    # Probe: discover dimension, register model, ensure collection.
    # Use a single space rather than an empty string — some embedding models
    # reject empty input with HTTP 4xx.
    embedding_provider.embed([" "])
    model_id = embedding_provider.get_model_id()
    embedding_provider.ensure_model_registered(db_path)

    # Probe augmentation endpoint before touching any documents.
    # ConnectionError here means Ollama is unreachable — fail the whole run immediately.
    if profile.augmentation.strategy == "contextual":
        from mrag.core.ollama_client import probe_connection
        probe_connection(profile.augmentation.endpoint)

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

    n = len(docs_to_index)
    for idx, (doc, reason) in enumerate(docs_to_index, 1):
        filename = doc.get("filename", doc["id"])
        if console:
            console.print(f"[dim]{_now_ts()}[/dim]  [{idx}/{n}] {filename}")

        _set_indexing_status(
            db_path, doc["id"], profile_name, doc["knowledge_id"],
            doc["file_hash"], doc["extracted_hash"], profile_hash,
        )

        on_doc_start: Callable[[int], None] | None = None
        on_chunk_augmented: Callable[[int, int], None] | None = None
        on_chunk_retry: Callable[[int, int, int, int, Exception], None] | None = None
        on_chunk_fallback: Callable[[int, int, Exception], None] | None = None
        on_chunks_oversized: Callable[[int, int, int], None] | None = None
        if console:
            strategy = profile.augmentation.strategy

            def _make_start_cb(fn: str) -> Callable[[int], None]:
                def _cb(chunk_count: int) -> None:
                    next_step = "augmenting" if strategy == "contextual" else "embedding"
                    console.print(
                        f"[dim]{_now_ts()}[/dim]         {chunk_count} chunks → {next_step}"
                    )
                    if strategy == "contextual" and chunk_count >= 300:
                        console.print(
                            f"[dim]{_now_ts()}[/dim]         [yellow]⚠ large document "
                            f"({chunk_count} chunks) — contextual augmentation may take a long time; "
                            f"retry is enabled[/yellow]"
                        )
                return _cb
            on_doc_start = _make_start_cb(filename)

            def _make_oversized_cb(fn: str) -> Callable[[int, int, int], None]:
                def _cb(count: int, max_len: int, threshold: int) -> None:
                    console.print(
                        f"[dim]{_now_ts()}[/dim]         [yellow]⚠ large chunks[/yellow]  "
                        f"{count} chunks exceed {threshold} chars (max: {max_len}) — "
                        f"may hit embedding model input limit  [dim]{fn}[/dim]"
                    )
                return _cb
            on_chunks_oversized = _make_oversized_cb(filename)

            if strategy == "contextual":
                def _make_chunk_cb(fn: str) -> Callable[[int, int], None]:
                    def _cb(cur: int, tot: int) -> None:
                        console.print(
                            f"[dim]{_now_ts()}[/dim]         augmenting [cyan]{cur:3d}[/cyan]/[cyan]{tot}[/cyan]  [dim]{fn}[/dim]"
                        )
                    return _cb
                on_chunk_augmented = _make_chunk_cb(filename)

                def _make_retry_cb(fn: str) -> Callable[[int, int, int, int, Exception], None]:
                    def _cb(cur: int, tot: int, attempt: int, max_attempts: int, exc: Exception) -> None:
                        reason = str(exc)[:120]
                        console.print(
                            f"[dim]{_now_ts()}[/dim]         [yellow]↻ retry[/yellow]  "
                            f"[cyan]{cur:3d}[/cyan]/[cyan]{tot}[/cyan]  "
                            f"attempt [cyan]{attempt}[/cyan]/[cyan]{max_attempts}[/cyan]  "
                            f"[dim]{reason}[/dim]  [dim]{fn}[/dim]"
                        )
                    return _cb
                on_chunk_retry = _make_retry_cb(filename)

                def _make_fallback_cb(fn: str) -> Callable[[int, int, Exception], None]:
                    def _cb(cur: int, tot: int, exc: Exception) -> None:
                        reason = str(exc)[:120]
                        console.print(
                            f"[dim]{_now_ts()}[/dim]         [yellow]⤵ fallback[/yellow]  "
                            f"[cyan]{cur:3d}[/cyan]/[cyan]{tot}[/cyan]  raw  "
                            f"[dim]{reason}[/dim]  [dim]{fn}[/dim]"
                        )
                    return _cb
                on_chunk_fallback = _make_fallback_cb(filename)

        try:
            fallback_count = _index_document(
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
                on_doc_start=on_doc_start,
                on_chunk_augmented=on_chunk_augmented,
                on_chunk_retry=on_chunk_retry,
                on_chunk_fallback=on_chunk_fallback,
                on_chunks_oversized=on_chunks_oversized,
            )
            _set_indexed_status(db_path, doc["id"], profile_name)
            if console:
                fallback_note = f"  [yellow]({fallback_count} raw fallback)[/yellow]" if fallback_count else ""
                console.print(f"[dim]{_now_ts()}[/dim]  [{idx}/{n}] [green]✓[/green] {filename}{fallback_note}")
            result.indexed += 1
            result.raw_fallback_chunks += fallback_count
        except ConnectionError:
            raise  # Ollama went down mid-run — propagate immediately, do not continue
        except Exception as exc:
            msg = str(exc)
            _set_error_status(db_path, doc["id"], profile_name, msg)
            if console:
                console.print(f"[dim]{_now_ts()}[/dim]  [{idx}/{n}] [red]✗[/red] {filename}: {msg}")
            result.errors.append((doc["id"], msg))
            extraction_meta = _read_extraction_meta(project_dir, doc)
            result.failed_docs.append(FailedDocEntry(
                document_id=doc["id"],
                filename=doc.get("filename", doc["id"]),
                source_path=doc.get("original_path", ""),
                source_type=doc.get("source_type", ""),
                added_at=doc.get("created_at", ""),
                failure_reason=msg,
                failure_stage="indexing",
                page_count=extraction_meta.get("page_count"),
                char_count=extraction_meta.get("char_count"),
            ))

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
