import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


def fetch_chunks(db_path: Path, chunk_ids: list[str]) -> dict[str, dict]:
    """Return {chunk_id: row_dict} for the given chunk_ids."""
    if not chunk_ids:
        return {}
    from mrag.db.connection import open_connection
    conn = open_connection(db_path)
    try:
        placeholders = ",".join("?" * len(chunk_ids))
        from mrag.db.exclusions import exclusions_schema_exists

        if exclusions_schema_exists(conn):
            rows = conn.execute(
                f"""SELECT c.* FROM chunks c
                    WHERE c.id IN ({placeholders})
                      AND NOT EXISTS (
                        SELECT 1 FROM document_exclusions e
                        WHERE e.document_id = c.document_id
                          AND e.revoked_at IS NULL
                          AND (e.profile_name IS NULL OR e.profile_name = c.profile_name)
                      )""",
                chunk_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})", chunk_ids
            ).fetchall()
        return {dict(r)["id"]: dict(r) for r in rows}
    finally:
        conn.close()


def fetch_chunk_metadata(db_path: Path, chunk_ids: list[str]) -> dict[str, dict]:
    """Return {chunk_id: metadata_dict} by bulk-selecting metadata_json from chunks."""
    if not chunk_ids:
        return {}
    from mrag.db.connection import open_connection
    conn = open_connection(db_path)
    try:
        placeholders = ",".join("?" * len(chunk_ids))
        rows = conn.execute(
            f"SELECT id, metadata_json FROM chunks WHERE id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, dict] = {}
    for row in rows:
        raw = row["metadata_json"]
        result[row["id"]] = json.loads(raw) if raw else {}
    return result
