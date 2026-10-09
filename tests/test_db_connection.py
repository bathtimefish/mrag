import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrag.db.connection as connection
from mrag.db import apsw_compat
from mrag.db.tokenizer import TOKENIZER_TRIGRAM, TOKENIZER_VAPORETTO


def test_vaporetto_connection_fails_before_opening_db_when_library_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "mrag.db"
    monkeypatch.setattr(connection, "find_vaporetto_lib", lambda: None)

    with pytest.raises(
        connection.VaporettoDependencyError,
        match="cannot fall back to trigram",
    ):
        connection.open_fts_connection(db_path, TOKENIZER_VAPORETTO)

    assert not db_path.exists()


def test_vaporetto_connection_wraps_ambiguous_library_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ambiguous() -> Path:
        raise connection.VaporettoLibraryAmbiguityError("ambiguous fixture")

    monkeypatch.setattr(connection, "find_vaporetto_lib", ambiguous)

    with pytest.raises(connection.VaporettoDependencyError, match="ambiguous fixture"):
        connection.open_fts_connection(tmp_path / "mrag.db", "vaporetto")

    assert not (tmp_path / "mrag.db").exists()


def test_vaporetto_connection_reports_missing_apsw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib_path = tmp_path / "libsqlite_vaporetto.so"
    monkeypatch.setattr(connection, "find_vaporetto_lib", lambda: lib_path)

    def missing_apsw(*_args: object) -> None:
        raise ModuleNotFoundError("No module named 'apsw'", name="apsw")

    monkeypatch.setattr(apsw_compat, "ApswConnection", missing_apsw)

    with pytest.raises(
        connection.VaporettoDependencyError,
        match="APSW is not installed",
    ):
        connection.open_fts_connection(
            tmp_path / "mrag.db", TOKENIZER_VAPORETTO
        )


def test_trigram_connection_does_not_probe_for_vaporetto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_probe() -> None:
        raise AssertionError("trigram must not probe for sqlite-vaporetto")

    monkeypatch.setattr(connection, "find_vaporetto_lib", unexpected_probe)

    conn = connection.open_fts_connection(
        tmp_path / "mrag.db", TOKENIZER_TRIGRAM
    )
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_apsw_loader_disables_extension_loading_after_load_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_calls: list[bool] = []

    class FailingConnection:
        def __init__(self, _db_path: str) -> None:
            pass

        def setbusytimeout(self, _milliseconds: int) -> None:
            pass

        def enableloadextension(self, enabled: bool) -> None:
            enable_calls.append(enabled)

        def loadextension(self, _lib_path: str, _entrypoint: str) -> None:
            raise RuntimeError("simulated extension load failure")

    monkeypatch.setitem(
        sys.modules,
        "apsw",
        SimpleNamespace(Connection=FailingConnection),
    )

    with pytest.raises(RuntimeError, match="simulated extension load failure"):
        apsw_compat.ApswConnection(
            tmp_path / "mrag.db",
            tmp_path / "libsqlite_vaporetto.so",
            "sqlite3_vaporetto_init",
        )

    assert enable_calls == [True, False]


def _schema_objects(conn) -> dict[str, set[str]]:
    rows = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    objects: dict[str, set[str]] = {"table": set(), "index": set()}
    for row in rows:
        objects[row[0]].add(row[1])
    return objects


def _sqlite3_schema(db_path: Path) -> dict[str, set[str]]:
    from mrag.db.migrate import apply_schema

    conn = sqlite3.connect(str(db_path))
    try:
        apply_schema(conn, tokenizer=TOKENIZER_TRIGRAM)
        conn.commit()
        return _schema_objects(conn)
    finally:
        conn.close()


def _bare_apsw_connection(db_path: Path) -> "apsw_compat.ApswConnection":
    """An ApswConnection without the vaporetto extension, which the schema's
    trigram FTS table does not need."""
    apsw = pytest.importorskip("apsw")
    conn = apsw_compat.ApswConnection.__new__(apsw_compat.ApswConnection)
    conn._conn = apsw.Connection(str(db_path))
    conn._conn.execute("PRAGMA journal_mode=WAL")
    return conn


def test_the_apsw_path_creates_every_table_and_index_the_schema_defines(
    tmp_path: Path,
) -> None:
    # Before 1.4.2 the apsw path split the schema on every semicolon,
    # comments included, and ignored the errors: a project initialized with
    # vaporetto had no `embedding_cache` and no `document_indexes`, and its
    # first `mrag index` failed.
    from mrag.db.migrate import apply_schema

    conn = _bare_apsw_connection(tmp_path / "apsw.db")
    try:
        apply_schema(conn, tokenizer=TOKENIZER_TRIGRAM)
        created = _schema_objects(conn)
    finally:
        conn.close()

    expected = _sqlite3_schema(tmp_path / "sqlite3.db")
    assert {"embedding_cache", "document_indexes"} <= created["table"]
    assert created == expected


def test_a_failing_statement_in_an_apsw_script_is_raised_not_skipped(
    tmp_path: Path,
) -> None:
    apsw = pytest.importorskip("apsw")
    conn = _bare_apsw_connection(tmp_path / "apsw.db")
    try:
        with pytest.raises(apsw.SQLError):
            conn.executescript(
                "CREATE TABLE first (a); -- a comment; with a semicolon\n"
                "CREATE TABLE broken (;\n"
                "CREATE TABLE after (b);"
            )
        tables = _schema_objects(conn)["table"]
    finally:
        conn.close()
    assert tables == set(), "the script runs in one transaction, so nothing of it stays"


def test_schema_statements_end_where_sqlite_ends_them() -> None:
    from mrag.db.migrate import schema_statements

    statements = schema_statements()
    for statement in statements:
        assert sqlite3.complete_statement(statement), statement
        assert not statement.startswith("--"), statement
    tables = [s for s in statements if s.upper().startswith("CREATE TABLE")]
    # A comment holding a semicolon used to cut these two in half.
    assert any("embedding_cache" in s.split("(")[0] for s in tables)
    assert any("document_indexes" in s.split("(")[0] for s in tables)


def test_opening_a_catalog_restores_the_tables_it_lacks(tmp_path: Path) -> None:
    db_path = tmp_path / "mrag.db"
    expected = _sqlite3_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
        "source_type, status, created_at, updated_at, source_identity) "
        "VALUES ('doc-1', 'kb', 'a.md', 'data/a.md', 'h', 'md', 'extracted', "
        "'2026-10-09', '2026-10-09', 'a.md')"
    )
    # The shape a vaporetto project initialized before 1.4.2 has.
    conn.execute("DROP TABLE embedding_cache")
    conn.execute("DROP TABLE document_indexes")
    conn.commit()
    conn.close()

    with connection.db_connection(db_path) as opened:
        restored = _schema_objects(opened)
        columns = {row[1] for row in opened.execute("PRAGMA table_info(document_indexes)")}
        documents = opened.execute("SELECT id FROM documents").fetchall()

    assert restored == expected
    assert "indexed_display_name" in columns
    assert [row[0] for row in documents] == ["doc-1"]
