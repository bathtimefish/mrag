from pathlib import Path

from mrag.core.retrieval.base import RetrievalResult
from mrag.db.connection import open_connection


def resolve_to_parent(
    results: list[RetrievalResult],
    db_path: Path,
) -> list[RetrievalResult]:
    """
    For child chunks (those with a parent_chunk_id), replace content with the
    parent chunk's content. Deduplicates by parent chunk_id (highest-ranked hit wins).

    MVP: all chunks have chunk_type='chunk' and parent_chunk_id=NULL, so this is a no-op.
    Needed once parent_child chunking strategy is enabled (MVP+1).
    """
    if not results:
        return results

    chunk_ids = [r.chunk_id for r in results]
    conn = open_connection(db_path)
    try:
        placeholders = ",".join("?" * len(chunk_ids))
        rows = conn.execute(
            f"SELECT id, parent_chunk_id FROM chunks WHERE id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        parent_map: dict[str, str | None] = {r["id"]: r["parent_chunk_id"] for r in rows}

        parent_ids = [pid for pid in parent_map.values() if pid]
        parent_content: dict[str, str] = {}
        if parent_ids:
            pp = ",".join("?" * len(parent_ids))
            prows = conn.execute(
                f"SELECT id, content FROM chunks WHERE id IN ({pp})", parent_ids
            ).fetchall()
            parent_content = {r["id"]: r["content"] for r in prows}
    finally:
        conn.close()

    seen: set[str] = set()
    resolved: list[RetrievalResult] = []

    for r in results:
        pid = parent_map.get(r.chunk_id)
        if pid:
            if pid in seen:
                continue
            seen.add(pid)
            resolved.append(
                RetrievalResult(
                    chunk_id=pid,
                    document_id=r.document_id,
                    content=parent_content.get(pid, r.content),
                    score=r.score,
                    metadata=r.metadata,
                )
            )
        else:
            resolved.append(r)

    return resolved
