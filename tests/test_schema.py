"""SQLite schema tests — table existence, CHECK constraints, indexes."""
import sqlite3


EXPECTED_TABLES = {
    "documents",
    "embedding_models",
    "profiles",
    "chunks",
    "chunk_variants",
    "embedding_cache",
    "document_indexes",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_all_tables_created(db_conn: sqlite3.Connection) -> None:
    assert EXPECTED_TABLES.issubset(_table_names(db_conn))


def test_fts_chunks_virtual_table(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fts_chunks'"
    ).fetchall()
    assert rows, "fts_chunks virtual table should exist"


def test_indexes_created(db_conn: sqlite3.Connection) -> None:
    indexes = _index_names(db_conn)
    assert "idx_chunks_document_profile" in indexes
    assert "idx_chunk_variants_chunk_id" in indexes
    assert "idx_document_indexes_lookup" in indexes


def _insert_document(conn: sqlite3.Connection, doc_id: str, status: str) -> None:
    conn.execute(
        """INSERT INTO documents
           (id, knowledge_id, filename, original_path, file_hash, source_type,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (doc_id, "kb1", "f.pdf", "data/f.pdf", f"hash_{doc_id}", "pdf",
         status, "2026-01-01", "2026-01-01"),
    )


def _insert_profile(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO profiles VALUES (?,?,?,?,?,?)",
        ("default", "kb1", "{}", "abc123", "2026-01-01", "2026-01-01"),
    )


def test_documents_status_check(db_conn: sqlite3.Connection) -> None:
    with db_conn:
        _insert_document(db_conn, "d1", "pending")

    with db_conn:
        try:
            _insert_document(db_conn, "d2", "invalid_status")
            db_conn.commit()
            assert False, "CHECK constraint should have rejected invalid status"
        except sqlite3.IntegrityError:
            db_conn.rollback()


def test_chunks_chunk_type_check(db_conn: sqlite3.Connection) -> None:
    with db_conn:
        _insert_document(db_conn, "d1", "extracted")
        _insert_profile(db_conn)
        db_conn.execute(
            """INSERT INTO chunks
               (id, knowledge_id, document_id, profile_name, chunk_type,
                chunk_index, content, source_format, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("c1", "kb1", "d1", "default", "chunk", 0, "text", "text",
             "2026-01-01", "2026-01-01"),
        )

    with db_conn:
        try:
            db_conn.execute(
                """INSERT INTO chunks
                   (id, knowledge_id, document_id, profile_name, chunk_type,
                    chunk_index, content, source_format, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("c2", "kb1", "d1", "default", "bad_type", 1, "text", "text",
                 "2026-01-01", "2026-01-01"),
            )
            db_conn.commit()
            assert False, "CHECK constraint should have rejected invalid chunk_type"
        except sqlite3.IntegrityError:
            db_conn.rollback()


def test_chunk_variants_variant_type_check(db_conn: sqlite3.Connection) -> None:
    with db_conn:
        _insert_document(db_conn, "d1", "extracted")
        _insert_profile(db_conn)
        db_conn.execute(
            """INSERT INTO chunks
               (id, knowledge_id, document_id, profile_name, chunk_type,
                chunk_index, content, source_format, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("c1", "kb1", "d1", "default", "chunk", 0, "text", "text",
             "2026-01-01", "2026-01-01"),
        )

    with db_conn:
        try:
            db_conn.execute(
                """INSERT INTO chunk_variants
                   (id, knowledge_id, document_id, chunk_id, profile_name,
                    variant_type, content_for_embedding, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("v1", "kb1", "d1", "c1", "default",
                 "bad_variant", "content", "2026-01-01"),
            )
            db_conn.commit()
            assert False, "CHECK constraint should have rejected invalid variant_type"
        except sqlite3.IntegrityError:
            db_conn.rollback()


def test_document_indexes_status_check(db_conn: sqlite3.Connection) -> None:
    with db_conn:
        _insert_document(db_conn, "d1", "extracted")
        _insert_profile(db_conn)

    with db_conn:
        try:
            db_conn.execute(
                """INSERT INTO document_indexes
                   (id, knowledge_id, document_id, profile_name,
                    document_file_hash, extracted_hash, profile_hash, status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("i1", "kb1", "d1", "default",
                 "h1", "h2", "h3", "bad_status"),
            )
            db_conn.commit()
            assert False, "CHECK constraint should have rejected invalid status"
        except sqlite3.IntegrityError:
            db_conn.rollback()
