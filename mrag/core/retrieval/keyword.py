import unicodedata
from pathlib import Path

from mrag.core.retrieval.base import RetrievalResult, fetch_chunk_metadata, fetch_chunks
from mrag.db.connection import open_fts_connection
from mrag.db.exclusions import exclusions_schema_exists
from mrag.db.tokenizer import TOKENIZER_TRIGRAM


def keyword_search(
    query_text: str,
    knowledge_id: str,
    profile_name: str,
    db_path: Path,
    top_k: int = 20,
    tokenizer: str = TOKENIZER_TRIGRAM,
) -> list[RetrievalResult]:
    """FTS5 MATCH search. BM25 is negated so higher = better. tokenizer selects
    the FTS5 tokenizer (vaporetto uses apsw + loaded extension)."""
    fts_query = _prepare_query(unicodedata.normalize("NFKC", query_text), tokenizer)
    conn = open_fts_connection(db_path, tokenizer)
    try:
        exclusion_clause = ""
        if exclusions_schema_exists(conn):
            exclusion_clause = (
                "AND NOT EXISTS ("
                "SELECT 1 FROM document_exclusions e "
                "WHERE e.document_id=fts_chunks.document_id "
                "AND e.revoked_at IS NULL "
                "AND (e.profile_name IS NULL OR e.profile_name=fts_chunks.profile_name)"
                ") "
            )
        rows = conn.execute(
            "SELECT chunk_id, document_id, bm25(fts_chunks) AS bm25_score "
            "FROM fts_chunks "
            "WHERE fts_chunks MATCH ? AND knowledge_id=? AND profile_name=? "
            f"{exclusion_clause}"
            "ORDER BY bm25_score "
            "LIMIT ?",
            (fts_query, knowledge_id, profile_name, top_k),
        ).fetchall()
    except Exception as exc:
        # sqlite3.OperationalError (stdlib) and apsw.SQLError (vaporetto) both
        # signal FTS5 syntax errors — degrade to empty result; re-raise others.
        if type(exc).__name__ not in ("OperationalError", "SQLError"):
            raise
        rows = []
    finally:
        conn.close()

    if not rows:
        return []

    chunk_ids = [r["chunk_id"] for r in rows]
    chunks = fetch_chunks(db_path, chunk_ids)
    chunk_meta = fetch_chunk_metadata(db_path, chunk_ids)

    results: list[RetrievalResult] = []
    for row in rows:
        chunk_id = row["chunk_id"]
        if chunk_id in chunks:
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    document_id=row["document_id"],
                    content=chunks[chunk_id]["content"],
                    score=-row["bm25_score"],
                    metadata=chunk_meta.get(chunk_id, {}),
                )
            )
    return results


def _prepare_query(text: str, tokenizer: str) -> str:
    """Wrap each whitespace-delimited token as an FTS5 string literal so that
    operators (*, %, :, ^, ~, !, -, /, \\, (, )) are neutralized regardless of
    tokenizer. ASCII " is stripped, so user-level phrase syntax is unsupported."""
    del tokenizer
    tokens = [t for t in text.replace('"', " ").split() if t]
    if not tokens:
        return text
    return " ".join(f'"{t}"' for t in tokens)
