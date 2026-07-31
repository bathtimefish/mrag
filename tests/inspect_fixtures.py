"""Shared helpers for `mrag inspect` integration tests.

Not collected by pytest (no `test_` prefix). Provides:

- `init_inspect_project` — run `mrag init` in tmp_path, return project_dir + db_path
- `seed_document`, `seed_profile`, `seed_chunk`, `seed_variant`, `seed_index`
  — direct SQL INSERTs against the real on-disk mrag.db, bypassing the
  add/index pipeline (orders of magnitude faster than running the real
  pipeline against test fixtures).

All seeders take a sqlite3.Connection and use `with conn:` semantics.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from mrag.cli import app


runner = CliRunner()


def init_inspect_project(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Create a fresh mrag project via `mrag init --non-interactive`.

    Returns (project_dir, db_path). Also chdir's to project_dir so subsequent
    `mrag inspect` invocations resolve the DB from cwd.
    """
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["init", "--name", "kb-inspect", "--non-interactive"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    project_dir = tmp_path / "kb-inspect"
    monkeypatch.chdir(project_dir)
    return project_dir, project_dir / "mrag.db"


def open_seed_conn(db_path: Path) -> sqlite3.Connection:
    """Open a sqlite3 connection with Row factory for seeding."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Direct seeders
# ---------------------------------------------------------------------------


def seed_document(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    # Deliberately a pre-1.0 row: mrag no longer ingests PDF, but catalogs built
    # before PyMuPDF was removed still hold these values and must stay readable.
    filename: str = "doc.pdf",
    source_type: str = "pdf",
    extraction_provider: str | None = "pymupdf",
    status: str = "extracted",
    knowledge_id: str = "kb-inspect",
) -> None:
    conn.execute(
        """INSERT INTO documents
           (id, knowledge_id, filename, original_path, file_hash, source_type,
            extraction_provider, extracted_hash, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            doc_id, knowledge_id, filename, f"data/{filename}",
            f"fh_{doc_id}", source_type,
            extraction_provider, f"eh_{doc_id}", status,
            "2026-01-01", "2026-01-01",
        ),
    )


def seed_profile(
    conn: sqlite3.Connection,
    name: str = "default",
    *,
    knowledge_id: str = "kb-inspect",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO profiles VALUES (?,?,?,?,?,?)",
        (name, knowledge_id, "{}", f"ph_{name}", "2026-01-01", "2026-01-01"),
    )


def seed_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    *,
    document_id: str = "d1",
    profile_name: str = "default",
    chunk_type: str = "chunk",
    chunk_index: int = 0,
    parent_chunk_id: str | None = None,
    content: str = "chunk body",
    source_format: str = "text",
    token_count: int = 10,
    char_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    knowledge_id: str = "kb-inspect",
) -> None:
    char_count = char_count if char_count is not None else len(content)
    metadata_json = json.dumps(metadata) if metadata else None
    conn.execute(
        """INSERT INTO chunks
           (id, knowledge_id, document_id, profile_name, parent_chunk_id,
            chunk_type, chunk_index, content, source_format,
            token_count, char_count, metadata_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            chunk_id, knowledge_id, document_id, profile_name, parent_chunk_id,
            chunk_type, chunk_index, content, source_format,
            token_count, char_count, metadata_json,
            "2026-01-01", "2026-01-01",
        ),
    )


def seed_variant(
    conn: sqlite3.Connection,
    variant_id: str,
    *,
    chunk_id: str,
    document_id: str = "d1",
    profile_name: str = "default",
    variant_type: str = "raw",
    qdrant_collection: str = "kb_inspect__default__nomic",
    context_text: str | None = None,
    augmentation_status: str | None = None,
    knowledge_id: str = "kb-inspect",
) -> None:
    metadata_json = (
        json.dumps({"augmentation_status": augmentation_status})
        if augmentation_status
        else None
    )
    conn.execute(
        """INSERT INTO chunk_variants
           (id, knowledge_id, document_id, chunk_id, profile_name,
            variant_type, content_for_embedding, context_text,
            qdrant_collection, metadata_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            variant_id, knowledge_id, document_id, chunk_id, profile_name,
            variant_type, "embedded text", context_text,
            qdrant_collection, metadata_json, "2026-01-01",
        ),
    )


def seed_index(
    conn: sqlite3.Connection,
    index_id: str,
    *,
    document_id: str = "d1",
    profile_name: str = "default",
    status: str = "indexed",
    indexed_at: str | None = "2026-05-15T09:30:01Z",
    knowledge_id: str = "kb-inspect",
) -> None:
    conn.execute(
        """INSERT INTO document_indexes
           (id, knowledge_id, document_id, profile_name,
            document_file_hash, extracted_hash, profile_hash,
            status, indexed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            index_id, knowledge_id, document_id, profile_name,
            "fh", "eh", "ph", status, indexed_at,
        ),
    )


# ---------------------------------------------------------------------------
# Convenience: seed a minimal indexed document
# ---------------------------------------------------------------------------


def seed_basic_indexed_document(
    db_path: Path,
    *,
    doc_id: str = "d1",
    filename: str = "ble_guide.pdf",
    profile_name: str = "default",
    n_chunks: int = 3,
    include_heading_path: bool = True,
    contextual: bool = False,
) -> None:
    """Insert 1 doc + 1 profile + N chunks + variants + document_index row."""
    conn = open_seed_conn(db_path)
    try:
        with conn:
            seed_document(conn, doc_id, filename=filename)
            seed_profile(conn, profile_name)
            seed_index(
                conn, f"idx_{doc_id}_{profile_name}",
                document_id=doc_id, profile_name=profile_name,
            )
            for i in range(n_chunks):
                meta: dict[str, Any] = {}
                if include_heading_path:
                    meta = {
                        "heading_path": ["Ch1", f"Sec{i + 1}"],
                        "contains_table": i == 1,
                    }
                seed_chunk(
                    conn, f"c{i}",
                    document_id=doc_id, profile_name=profile_name,
                    chunk_index=i,
                    content=f"chunk {i} body text",
                    metadata=meta or None,
                )
                seed_variant(
                    conn, f"v{i}",
                    chunk_id=f"c{i}",
                    document_id=doc_id, profile_name=profile_name,
                    variant_type="contextual" if contextual else "raw",
                    context_text=f"context for chunk {i}" if contextual else None,
                )
    finally:
        conn.close()
