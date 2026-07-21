"""Read-only tool implementations for the mrag MCP server."""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mrag.config.mcp import EffectiveMcpConfig
from mrag.config.profile import load_profile
from mrag.core.retrieval.runner import fetch_filename_map, run_retrieval
from mrag.db.connection import find_db, open_connection
from mrag.db.inspect_queries import (
    fetch_chunk_single,
    fetch_chunks,
    fetch_document,
    fetch_document_summary,
    fetch_sections,
    list_profiles_for_document,
)


@dataclass(frozen=True)
class McpToolContext:
    effective: EffectiveMcpConfig

    @property
    def project_dir(self) -> Path:
        return self.effective.project_dir

    @property
    def db_path(self) -> Path:
        return find_db(self.project_dir)


def _truncate_text(text: str | None, max_chars: int) -> tuple[str | None, dict[str, Any]]:
    if text is None:
        return None, {"truncated": False, "content_chars": 0, "returned_chars": 0}
    total = len(text)
    if max_chars <= 0 or total <= max_chars:
        return text, {
            "truncated": False,
            "content_chars": total,
            "returned_chars": total,
        }
    return text[:max_chars], {
        "truncated": True,
        "content_chars": total,
        "returned_chars": max_chars,
    }


def _result_payload(
    *,
    query: str,
    profile_name: str,
    strategy: str,
    reranked: bool,
    results: list,
    filename_map: dict[str, str],
    content_max_chars: int,
) -> dict[str, Any]:
    entries = []
    for i, r in enumerate(results, 1):
        content, trunc = _truncate_text(r.content, content_max_chars)
        entry = {
            "rank": i,
            "chunk_id": r.chunk_id,
            "document_id": r.document_id,
            "filename": filename_map.get(r.document_id, ""),
            "score": float(r.score),
            "content": content,
            "metadata": dict(r.metadata) if r.metadata else {},
            **trunc,
        }
        if "retrieval_score" in entry["metadata"]:
            entry["retrieval_score"] = float(entry["metadata"]["retrieval_score"])
        entries.append(entry)

    score_stats = None
    doc_distribution: dict[str, int] = {}
    if results:
        scores = [float(r.score) for r in results]
        score_stats = {
            "min": min(scores),
            "max": max(scores),
            "mean": statistics.mean(scores),
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        }
        for r in results:
            name = filename_map.get(r.document_id, r.document_id[:8])
            doc_distribution[name] = doc_distribution.get(name, 0) + 1

    return {
        "query": query,
        "profile": profile_name,
        "strategy": strategy,
        "reranked": reranked,
        "result_count": len(results),
        "results": entries,
        "score_stats": score_stats,
        "document_distribution": doc_distribution,
    }


def search_tool(
    ctx: McpToolContext,
    *,
    query: str,
    profile: str | None = None,
    strategy: str | None = None,
    top_k: int | None = None,
    no_rerank: bool | None = None,
) -> dict[str, Any]:
    cfg = ctx.effective.raw
    resolved_profile = profile or ctx.effective.profile_name
    selected_profile = load_profile(resolved_profile, ctx.project_dir)
    resolved_top_k = top_k
    if resolved_top_k is None:
        resolved_top_k = cfg.retrieval.top_k_default
    if resolved_top_k is None:
        resolved_top_k = selected_profile.retrieval.top_k
    if resolved_top_k > cfg.retrieval.top_k_max:
        raise ValueError(
            f"top_k must be <= retrieval.top_k_max ({cfg.retrieval.top_k_max})"
        )
    run = run_retrieval(
        query=query,
        project_dir=ctx.project_dir,
        config=ctx.effective.project_config,
        profile_name=resolved_profile,
        # An explicit MCP-wide strategy remains an override. Otherwise let
        # run_retrieval resolve the selected profile instead of leaking the
        # startup profile's strategy into an alternate-profile request.
        strategy=strategy or cfg.retrieval.strategy,
        top_k=resolved_top_k,
        no_rerank=cfg.retrieval.no_rerank if no_rerank is None else no_rerank,
        load_reranker=True,
    )
    filename_map = fetch_filename_map(ctx.db_path, run.results)
    return _result_payload(
        query=query,
        profile_name=run.profile_name,
        strategy=run.strategy,
        reranked=run.reranked,
        results=run.results,
        filename_map=filename_map,
        content_max_chars=cfg.limits.content_max_chars,
    )


def list_documents_tool(
    ctx: McpToolContext,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    limit = limit or 100
    conn = open_connection(ctx.db_path)
    try:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM documents").fetchone()["cnt"]
        rows = conn.execute(
            """
            SELECT id, filename, file_hash, source_type, status, created_at
            FROM documents
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "documents": [
            {
                "id": r["id"],
                "filename": r["filename"],
                "file_hash": r["file_hash"],
                "source_type": r["source_type"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


def list_profiles_tool(ctx: McpToolContext) -> dict[str, Any]:
    profiles = []
    for path in sorted((ctx.project_dir / "profiles").glob("*.yaml")):
        try:
            from mrag.config.profile import load_profile

            prof = load_profile(path.stem, ctx.project_dir)
        except Exception:
            continue
        profiles.append(
            {
                "name": prof.name,
                "path": str(path.relative_to(ctx.project_dir)),
                "retrieval_strategy": prof.retrieval.strategy,
                "chunking_strategy": prof.chunking.strategy,
                "embedding_model": prof.embedding.model,
                "rerank_enabled": prof.rerank.enabled,
            }
        )
    return {
        "default_profile": ctx.effective.project_config.default_profile,
        "profiles": profiles,
    }


def _document_summary_payload(summary) -> dict[str, Any]:
    return {
        "document": {
            "id": summary.document.id,
            "filename": summary.document.filename,
            "source_type": summary.document.source_type,
            "extraction_provider": summary.document.extraction_provider,
            "status": summary.document.status,
            "extracted_hash": summary.document.extracted_hash,
            "created_at": summary.document.created_at,
            "updated_at": summary.document.updated_at,
        },
        "profiles": [
            {
                "name": p.profile_name,
                "status": p.status,
                "indexed_at": p.indexed_at,
                "chunk_counts": {
                    "chunk": p.chunk_counts.chunk,
                    "parent": p.chunk_counts.parent,
                    "child": p.chunk_counts.child,
                    "total": p.chunk_counts.total,
                },
                "augmentation": {
                    "succeeded": p.augmentation.succeeded,
                    "raw_fallback": p.augmentation.raw_fallback,
                },
                "embedding": {
                    "embedded": p.embedding.embedded,
                    "fallback_no_vector": p.embedding.fallback_no_vector,
                },
            }
            for p in summary.profiles
        ],
    }


def inspect_document_tool(
    ctx: McpToolContext,
    *,
    document_id: str,
    profile: str | None = None,
) -> dict[str, Any]:
    conn = open_connection(ctx.db_path)
    try:
        summary = fetch_document_summary(conn, document_id, profile_name=profile)
    finally:
        conn.close()
    if summary is None:
        raise ValueError(f"document_id '{document_id}' not found")
    return _document_summary_payload(summary)


def _resolve_profile(conn, document_id: str, requested: str | None) -> str:
    if requested is not None:
        return requested
    profiles = list_profiles_for_document(conn, document_id)
    if not profiles:
        raise ValueError(f"document '{document_id}' has no indexed chunks")
    if len(profiles) == 1:
        return profiles[0]
    raise ValueError(
        f"document '{document_id}' is indexed under multiple profiles. "
        f"Specify profile. Available: {', '.join(profiles)}"
    )


def _chunk_entry(row, *, include_content: bool, include_context: bool, max_chars: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "chunk_id": row.chunk_id,
        "document_id": row.document_id,
        "profile": row.profile_name,
        "chunk_type": row.chunk_type,
        "chunk_index": row.chunk_index,
        "parent_chunk_id": row.parent_chunk_id,
        "char_count": row.char_count,
        "token_count": row.token_count,
        "source_format": row.source_format,
        "metadata": row.metadata,
        "variant": {
            "type": row.variant.variant_type,
            "qdrant_collection": row.variant.qdrant_collection,
            "augmentation_status": row.variant.augmentation_status,
            "embedding_status": row.variant.embedding_status,
            "embedding_error": row.variant.embedding_error,
            "has_qdrant_point": row.variant.has_qdrant_point,
        },
    }
    if include_content:
        content, trunc = _truncate_text(row.content, max_chars)
        entry["content"] = content
        entry.update(trunc)
    if include_context:
        entry["context_text"] = row.variant.context_text
    return entry


def inspect_chunks_tool(
    ctx: McpToolContext,
    *,
    document_id: str,
    profile: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    show_content: bool = False,
    show_context: bool = False,
) -> dict[str, Any]:
    conn = open_connection(ctx.db_path)
    try:
        if fetch_document(conn, document_id) is None:
            raise ValueError(f"document_id '{document_id}' not found")
        profile_name = _resolve_profile(conn, document_id, profile)
        rows = fetch_chunks(
            conn,
            document_id,
            profile_name,
            limit=limit,
            offset=offset,
            include_content=show_content,
            include_context=show_context,
        )
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM chunks WHERE document_id = ? AND profile_name = ?",
            (document_id, profile_name),
        ).fetchone()["cnt"]
    finally:
        conn.close()
    return {
        "document_id": document_id,
        "profile": profile_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "chunks": [
            _chunk_entry(
                row,
                include_content=show_content,
                include_context=show_context,
                max_chars=ctx.effective.raw.limits.content_max_chars,
            )
            for row in rows
        ],
    }


def inspect_chunk_tool(ctx: McpToolContext, *, chunk_id: str) -> dict[str, Any]:
    conn = open_connection(ctx.db_path)
    try:
        row = fetch_chunk_single(conn, chunk_id)
        if row is None:
            raise ValueError(f"chunk_id '{chunk_id}' not found")
        doc = fetch_document(conn, row.document_id)
    finally:
        conn.close()
    entry = _chunk_entry(
        row,
        include_content=True,
        include_context=True,
        max_chars=ctx.effective.raw.limits.content_max_chars,
    )
    entry["document_filename"] = doc.filename if doc else None
    return entry


def inspect_sections_tool(
    ctx: McpToolContext,
    *,
    document_id: str,
    profile: str | None = None,
) -> dict[str, Any]:
    from mrag.cli.inspect import (
        _build_heading_tree,
        _build_parent_child_tree,
        _has_any_heading_path,
        _is_parent_child_profile,
    )

    conn = open_connection(ctx.db_path)
    try:
        if fetch_document(conn, document_id) is None:
            raise ValueError(f"document_id '{document_id}' not found")
        profile_name = _resolve_profile(conn, document_id, profile)
        rows = fetch_sections(conn, document_id, profile_name)
    finally:
        conn.close()

    if _is_parent_child_profile(rows):
        return {
            "document_id": document_id,
            "profile": profile_name,
            "mode": "parent_child",
            "parents": _build_parent_child_tree(rows),
        }
    if not _has_any_heading_path(rows):
        raise ValueError(
            f"profile '{profile_name}' has no section structure "
            "(preserve_heading_path is disabled)"
        )
    return {
        "document_id": document_id,
        "profile": profile_name,
        "mode": "heading",
        "sections": _build_heading_tree(rows),
    }


def json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


__all__ = [
    "McpToolContext",
    "inspect_chunk_tool",
    "inspect_chunks_tool",
    "inspect_document_tool",
    "inspect_sections_tool",
    "json_text",
    "list_documents_tool",
    "list_profiles_tool",
    "search_tool",
]
