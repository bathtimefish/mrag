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


def schema_statements() -> list[str]:
    """The packaged schema, one statement per entry, without the comments between them.

    Split where SQLite itself would end each statement
    (`sqlite3.complete_statement`), so a semicolon inside a comment or a
    string literal does not end one.
    """
    statements: list[str] = []
    pending = ""
    for line in _read_schema_sql().splitlines(keepends=True):
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("--")):
            continue
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""
    if pending.strip():
        statements.append(pending.strip())
    return statements


def apply_schema(
    conn: Union[sqlite3.Connection, "ApswConnection"],
    tokenizer: str = TOKENIZER_TRIGRAM,
) -> None:
    """
    Create all tables and indexes. Safe to call on an existing DB (IF NOT EXISTS).
    The FTS5 fts_chunks table is created with the given tokenizer.
    """
    if isinstance(conn, sqlite3.Connection):
        from mrag.db.connection import _migrate_source_identity
        _migrate_source_identity(conn)
    sql = _read_schema_sql()
    clause = fts5_tokenize_clause(tokenizer)
    sql = sql.replace(_FTS_TOKENIZE_PLACEHOLDER, f"tokenize = '{clause}'")
    conn.executescript(sql)
