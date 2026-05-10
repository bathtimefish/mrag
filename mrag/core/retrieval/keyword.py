import sqlite3
import unicodedata
from pathlib import Path

from mrag.core.retrieval.base import RetrievalResult, fetch_chunk_metadata, fetch_chunks
from mrag.db.connection import open_fts_connection
from mrag.db.tokenizer import TOKENIZER_TRIGRAM


def keyword_search(
    query_text: str,
    knowledge_id: str,
    profile_name: str,
    db_path: Path,
    top_k: int = 20,
    tokenizer: str = TOKENIZER_TRIGRAM,
) -> list[RetrievalResult]:
    """
    FTS5 MATCH search. BM25 scores are negative — negated so higher = better.

    When tokenizer='vaporetto', uses an apsw-backed connection that loads the
    vaporetto extension so that the query is tokenized identically to the index.
    With vaporetto, natural language queries (e.g. "熱電対の基本仕様") work
    without any manual particle splitting.

    When tokenizer='trigram', applies minimal FTS5 sanitization to prevent
    syntax errors from special characters like '/', '*', '"'.
    """
    fts_query = _prepare_query(unicodedata.normalize("NFKC", query_text), tokenizer)
    conn = open_fts_connection(db_path, tokenizer)
    try:
        rows = conn.execute(
            "SELECT chunk_id, document_id, bm25(fts_chunks) AS bm25_score "
            "FROM fts_chunks "
            "WHERE fts_chunks MATCH ? AND knowledge_id=? AND profile_name=? "
            "ORDER BY bm25_score "
            "LIMIT ?",
            (fts_query, knowledge_id, profile_name, top_k),
        ).fetchall()
    except (sqlite3.OperationalError, Exception):
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
    """
    With vaporetto: pass through unchanged — the tokenizer handles segmentation.
    With trigram: strip FTS5 special characters that would cause syntax errors.
    """
    if tokenizer != TOKENIZER_TRIGRAM:
        return text

    import re
    # Remove characters that break FTS5 query syntax
    _SPECIAL = re.compile(r'["\*\(\)\:\^\~\!\-\/\\]')
    sanitized = _SPECIAL.sub(" ", text).strip()
    return sanitized if sanitized else text
