import sqlite3
from pathlib import Path
from typing import Union

from mrag.db.tokenizer import TOKENIZER_TRIGRAM, fts5_tokenize_clause

# mrag/db/schema.sql is the package-authoritative schema.
# The root schema.sql is the documentation copy — keep them in sync on schema changes.
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Marker that gets substituted with the actual tokenize= clause at init time
_FTS_TOKENIZE_PLACEHOLDER = "tokenize = 'trigram'"


def apply_schema(
    conn: Union[sqlite3.Connection, "ApswConnection"],
    tokenizer: str = TOKENIZER_TRIGRAM,
) -> None:
    """
    Create all tables and indexes. Safe to call on an existing DB (IF NOT EXISTS).
    The FTS5 fts_chunks table is created with the given tokenizer.
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    clause = fts5_tokenize_clause(tokenizer)
    sql = sql.replace(_FTS_TOKENIZE_PLACEHOLDER, f"tokenize = '{clause}'")
    conn.executescript(sql)
