import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Union

from mrag.db.tokenizer import (
    TOKENIZER_VAPORETTO,
    _VAPORETTO_ENTRYPOINT,
    VaporettoLibraryAmbiguityError,
    find_vaporetto_lib,
)


class VaporettoDependencyError(RuntimeError):
    """Raised when a project requires Vaporetto but its runtime is unavailable."""


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
    the vaporetto extension. A missing Vaporetto runtime is an explicit error
    because an existing FTS5 table cannot safely change tokenizer. Trigram
    projects use the standard sqlite3 connection.
    """
    if tokenizer == TOKENIZER_VAPORETTO:
        try:
            lib = find_vaporetto_lib()
        except VaporettoLibraryAmbiguityError as exc:
            raise VaporettoDependencyError(str(exc)) from exc
        if lib is None:
            raise VaporettoDependencyError(
                "vaporetto is configured for this project, but the "
                "sqlite-vaporetto library was not found. Restore it under "
                "~/.mrag/extensions/ or set MRAG_VAPORETTO_LIB, then run "
                "'mrag doctor'. The existing FTS index cannot fall back to "
                "trigram."
            )
        from mrag.db.apsw_compat import ApswConnection
        try:
            return ApswConnection(db_path, lib, _VAPORETTO_ENTRYPOINT)
        except ModuleNotFoundError as exc:
            if exc.name != "apsw":
                raise
            raise VaporettoDependencyError(
                "vaporetto is configured for this project, but APSW is not "
                "installed. Install the 'vaporetto' optional dependency and "
                "run 'mrag doctor'."
            ) from exc
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
