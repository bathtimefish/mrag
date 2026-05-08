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
        rows = conn.execute(
            f"SELECT * FROM chunks WHERE id IN ({placeholders})", chunk_ids
        ).fetchall()
        return {dict(r)["id"]: dict(r) for r in rows}
    finally:
        conn.close()
