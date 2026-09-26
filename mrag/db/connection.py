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


def _migrate_source_identity(conn: sqlite3.Connection) -> None:
    """Add and backfill source identities without changing document IDs or indexes.

    A catalog from before source identities gets its first identities here, under
    the current scheme. A catalog that already holds identities keeps the scheme
    it records: converting them is `mrag catalog migrate-identities`'s job, never
    a side effect of opening the catalog (SPEC-DATA-004). The one exception is a
    catalog holding no document, which holds no identity of any scheme and is
    recorded as current.
    """
    from mrag.core.ingestion.source_identity import SCHEME_KEY, SCHEME_UNRECORDED, SCHEME_VERSION

    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone():
        return

    def has_documents() -> bool:
        return conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone() is not None

    def recorded() -> str | None:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_settings'").fetchone():
            return None
        row = conn.execute("SELECT value FROM catalog_settings WHERE key = ?", (SCHEME_KEY,)).fetchone()
        return row[0] if row else None

    def current() -> bool:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        if "source_identity" not in columns:
            return False
        scheme = recorded()
        if scheme is None:
            return False
        return scheme == str(SCHEME_VERSION) or has_documents()

    if current():
        return
    # A legacy catalog kept only a copied original under data/documents, so
    # its prior external source path cannot be reconstructed honestly.
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        if "source_identity" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN source_identity TEXT")
            # First assignment, so it is made under the current scheme.
            conn.execute("UPDATE documents SET source_identity = 'identities/legacy/v1/' || id")
            scheme = str(SCHEME_VERSION)
        elif has_documents():
            # The column predates this build, so its values follow scheme 1
            # unless the catalog says otherwise.
            scheme = str(SCHEME_UNRECORDED)
        else:
            scheme = str(SCHEME_VERSION)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_source_identity "
            "ON documents(source_identity) WHERE source_identity IS NOT NULL"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS source_roots (root_key TEXT PRIMARY KEY, label TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS catalog_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Recorded once for a catalog holding identities: a later release with
        # another scheme must migrate it explicitly instead of recomputing on
        # add. An empty catalog is brought to the current scheme.
        conn.execute(
            "INSERT OR IGNORE INTO catalog_settings (key, value) VALUES (?, ?)",
            (SCHEME_KEY, scheme),
        )
        if not has_documents():
            conn.execute(
                "UPDATE catalog_settings SET value = ? WHERE key = ?",
                (str(SCHEME_VERSION), SCHEME_KEY),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def db_connection(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = open_connection(db_path)
    try:
        _migrate_source_identity(conn)
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
