import sqlite3
from importlib.resources import files
from pathlib import Path
from typing import Union

from mrag.db.tokenizer import TOKENIZER_TRIGRAM, fts5_tokenize_clause

# mrag/db/schema.sql is the package-authoritative schema.
# The root schema.sql is the documentation copy — keep them in sync on schema changes.
_SCHEMA_FILENAME = "schema.sql"

# Marker that gets substituted with the actual tokenize= clause at init time
_FTS_TOKENIZE_PLACEHOLDER = "tokenize = 'trigram'"


def _read_schema_sql() -> str:
    """Read the packaged schema.sql.

    Uses importlib.resources (the canonical API for package data — works under
    regular installs, zipapps, and PyInstaller's collect_data_files). Falls back
    to a __file__-relative path for any loader where resources resolution fails.
    """
    try:
        return (files("mrag.db") / _SCHEMA_FILENAME).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        return (Path(__file__).parent / _SCHEMA_FILENAME).read_text(encoding="utf-8")


def apply_schema(
    conn: Union[sqlite3.Connection, "ApswConnection"],
    tokenizer: str = TOKENIZER_TRIGRAM,
) -> None:
    """
    Create all tables and indexes. Safe to call on an existing DB (IF NOT EXISTS).
    The FTS5 fts_chunks table is created with the given tokenizer.
    """
    sql = _read_schema_sql()
    clause = fts5_tokenize_clause(tokenizer)
    sql = sql.replace(_FTS_TOKENIZE_PLACEHOLDER, f"tokenize = '{clause}'")
    conn.executescript(sql)
