import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Union

from mrag.db.tokenizer import (
    TOKENIZER_VAPORETTO,
    _VAPORETTO_ENTRYPOINT,
    find_vaporetto_lib,
)


def open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_connection(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = open_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def find_db(project_dir: Path | None = None) -> Path:
    """Return mrag.db path. Raises FileNotFoundError if not in an mrag project."""
    if project_dir is None:
        project_dir = Path.cwd()
    db_path = project_dir / "mrag.db"
    if not db_path.exists():
        raise FileNotFoundError(
            f"mrag.db not found in {project_dir}. Run 'mrag init' first."
        )
    return db_path


def open_fts_connection(db_path: Path, tokenizer: str) -> Union[sqlite3.Connection, "ApswConnection"]:
    """
    Open a DB connection suitable for FTS5 operations.
    When tokenizer='vaporetto', uses an apsw-backed connection that loads
    the vaporetto extension. Falls back to plain sqlite3 for 'trigram'.
    """
    if tokenizer == TOKENIZER_VAPORETTO:
        lib = find_vaporetto_lib()
        if lib:
            from mrag.db.apsw_compat import ApswConnection
            return ApswConnection(db_path, lib, _VAPORETTO_ENTRYPOINT)
        # lib not found — fall back silently to trigram
    return open_connection(db_path)


@contextmanager
def fts_db_connection(db_path: Path, tokenizer: str):
    """Context-manager variant of open_fts_connection.

    Uses 'with conn:' so that ApswConnection issues an explicit BEGIN/COMMIT
    (apsw is autocommit by default) while sqlite3.Connection uses its own
    implicit transaction handling — both via their __enter__/__exit__.
    """
    conn = open_fts_connection(db_path, tokenizer)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Re-export for type hints in other modules
try:
    from mrag.db.apsw_compat import ApswConnection
except ImportError:
    ApswConnection = None  # type: ignore
