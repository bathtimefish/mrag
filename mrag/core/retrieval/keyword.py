import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mrag.core.retrieval.base import RetrievalResult, fetch_chunk_metadata, fetch_chunks
from mrag.db.connection import open_fts_connection
from mrag.db.exclusions import exclusions_schema_exists
from mrag.db.tokenizer import TOKENIZER_TRIGRAM, TOKENIZER_VAPORETTO

# Function words removed from a segmented Japanese query before its AND.
#
# Vaporetto segments a question into every morpheme it contains, and joining
# them all with AND demands that the answer contain the question's particles:
# `熱電対モジュールの型番は` requires `の` and `は`, which a specification table
# row does not carry, so the row that holds the answer cannot match. The rule
# for this list: a word appears in questions without being expected in the
# passage that answers them. Particles, auxiliary fragments and interrogatives
# qualify; anything that could name content does not. A closed list, because
# Vaporetto reports segments and nothing else.
JAPANESE_STOP_SEGMENTS = frozenset(
    [
        # Particles.
        "の", "は", "が", "を", "に", "で", "と", "へ", "も", "や", "か",
        "から", "まで", "より", "など", "ね", "よ",
        # Auxiliary and copula fragments, as Vaporetto cuts them.
        "さ", "れ", "て", "た", "だ", "な", "し", "いる", "ある", "する",
        "です", "ます", "でし", "まし", "だっ", "であ", "ください",
        # Interrogatives: they shape the question, never the answer.
        "何", "どの", "どれ", "どう", "なぜ", "いつ", "どこ", "いくつ",
    ]
)


class _HeldFtsConnection:
    """One Vaporetto FTS connection and one thread, kept for the life of the
    process.

    The sqlite-vaporetto extension deserializes its segmentation model — about
    2.6 s on a 2026 workstation — in two caches that never meet: the FTS5
    tokenizer builds one per connection, the first time the connection touches
    the table, and `vaporetto_split` builds one per thread, the first time the
    thread calls it. A search that opened a connection per call paid both every
    time (5.2 s per request on the API server, which runs handlers on a pool of
    threads). Holding the connection pays the first once; running every search
    on this one thread pays the second once. Each statement is its own read, so
    the held connection still sees what a later `mrag index` commits.

    The connection follows the catalog last searched: a search of another
    catalog closes it and opens that one, so a process that touches many
    catalogs — a test run — holds one model, not one per catalog. A server and
    a command search one catalog, and never pay for the switch.
    """

    def __init__(self) -> None:
        self._key: tuple[str, str] | None = None
        self._conn = None
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mrag-keyword")

    def run(self, db_path: Path, tokenizer: str, work):
        """Runs `work(conn)` on the connection to `db_path`, on the one thread."""
        key = (str(Path(db_path).resolve()), tokenizer)
        return self._thread.submit(self._on_thread, key, db_path, tokenizer, work).result()

    def _on_thread(self, key, db_path, tokenizer, work):
        if self._key != key:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._conn = open_fts_connection(db_path, tokenizer)
            self._key = key
        return work(self._conn)


_HELD = _HeldFtsConnection()


def _match_rows(
    conn,
    normalized: str,
    knowledge_id: str,
    profile_name: str,
    top_k: int,
    tokenizer: str,
) -> list:
    """The FTS5 MATCH on an open connection: the query prepared for the
    tokenizer, exclusions honoured, rows in BM25 order."""
    try:
        if tokenizer == TOKENIZER_VAPORETTO:
            fts_query = _prepare_segmented_query(normalized, lambda text: _vaporetto_segments(conn, text))
            if fts_query is None:
                # Nothing but function words: no content to match, so zero
                # hits rather than a search for the question's grammar.
                return []
        else:
            fts_query = _prepare_query(normalized, tokenizer)
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
        return conn.execute(
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
        return []


def keyword_search(
    query_text: str,
    knowledge_id: str,
    profile_name: str,
    db_path: Path,
    top_k: int = 20,
    tokenizer: str = TOKENIZER_TRIGRAM,
) -> list[RetrievalResult]:
    """FTS5 MATCH search. BM25 is negated so higher = better. tokenizer selects
    the FTS5 tokenizer (vaporetto uses apsw + loaded extension, through one
    connection and one thread held for the process; see `_HeldFtsConnection`).
    """
    normalized = unicodedata.normalize("NFKC", query_text)
    if tokenizer == TOKENIZER_VAPORETTO:
        rows = _HELD.run(
            db_path, tokenizer,
            lambda conn: _match_rows(conn, normalized, knowledge_id, profile_name, top_k, tokenizer),
        )
    else:
        conn = open_fts_connection(db_path, tokenizer)
        try:
            rows = _match_rows(conn, normalized, knowledge_id, profile_name, top_k, tokenizer)
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
    tokenizer. ASCII " is stripped, so user-level phrase syntax is unsupported.

    Used as is for trigram and porter, whose phrases match runs of characters
    or whitespace-delimited words. The vaporetto path segments first; see
    `_prepare_segmented_query`."""
    del tokenizer
    tokens = [t for t in text.replace('"', " ").split() if t]
    if not tokens:
        return text
    return " ".join(f'"{t}"' for t in tokens)


def _vaporetto_segments(conn, text: str) -> list[str]:
    """Segment `text` with the vaporetto extension loaded on `conn` — the same
    tokenizer that segmented every indexed row."""
    row = conn.execute("SELECT vaporetto_split(?)", (text,)).fetchone()
    return (row[0] or "").split() if row else []


def _prepare_segmented_query(text: str, segment) -> str | None:
    """Build the MATCH expression for a vaporetto table.

    Japanese carries no whitespace, so quoting each whitespace-delimited run as
    a phrase (`_prepare_query`) made a whole question one phrase that only an
    identically worded passage matches — nothing at all, over labelled queries
    (before 1.5.0). Instead the question is segmented by the tokenizer that
    indexed the rows, its function words are removed, and the remaining
    segments are each quoted and joined with AND. Returns None when nothing
    remains: a question of only function words has no content to match.

    Quoting each segment keeps FTS5 operators inert, as before; ASCII " is
    still stripped."""
    cleaned = text.replace('"', " ")
    segments = [s for s in segment(cleaned) if s and s not in JAPANESE_STOP_SEGMENTS]
    if not segments:
        return None
    return " AND ".join(f'"{s}"' for s in segments)
