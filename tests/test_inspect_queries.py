"""Tests for mrag/db/inspect_queries.py (Phase 2 — v0.18 inspect SQL helpers)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from mrag.db.inspect_queries import (
    AugmentationStatus,
    ChunkRow,
    ChunkVariantInfo,
    DocumentRow,
    DocumentSummary,
    ProfileChunkCounts,
    count_chunks,
    fetch_chunk_single,
    fetch_chunks,
    fetch_document,
    fetch_document_summary,
    fetch_sections,
    list_profiles_for_document,
)


# ---------------------------------------------------------------------------
# Helpers — seed the in-memory DB with deterministic data
# ---------------------------------------------------------------------------


def _insert_document(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    # Pre-1.0 values: PDF ingestion is gone, but existing catalogs still hold them.
    filename: str = "f.pdf",
    source_type: str = "pdf",
    status: str = "extracted",
) -> None:
    conn.execute(
        """INSERT INTO documents
           (id, knowledge_id, filename, original_path, file_hash, source_type,
            extraction_provider, extracted_hash, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            doc_id, "kb1", filename, f"data/{filename}",
            f"hash_{doc_id}", source_type,
            "pymupdf", f"ehash_{doc_id}", status,
            "2026-01-01", "2026-01-01",
        ),
    )


def _insert_profile(conn: sqlite3.Connection, name: str = "default") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO profiles VALUES (?,?,?,?,?,?)",
        (name, "kb1", "{}", f"hash_{name}", "2026-01-01", "2026-01-01"),
    )


def _insert_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    *,
    document_id: str = "d1",
    profile_name: str = "default",
    chunk_type: str = "chunk",
    chunk_index: int = 0,
    parent_chunk_id: str | None = None,
    content: str = "chunk content",
    source_format: str = "text",
    token_count: int = 10,
    char_count: int | None = None,
    metadata: dict[str, Any] | None = None,
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
            chunk_id, "kb1", document_id, profile_name, parent_chunk_id,
            chunk_type, chunk_index, content, source_format,
            token_count, char_count, metadata_json,
            "2026-01-01", "2026-01-01",
        ),
    )


def _insert_variant(
    conn: sqlite3.Connection,
    variant_id: str,
    *,
    chunk_id: str,
    document_id: str = "d1",
    profile_name: str = "default",
    variant_type: str = "raw",
    qdrant_collection: str = "kb1__default__nomic",
    context_text: str | None = None,
    augmentation_status: str | None = None,
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
            variant_id, "kb1", document_id, chunk_id, profile_name,
            variant_type, "embed text", context_text,
            qdrant_collection, metadata_json, "2026-01-01",
        ),
    )


def _insert_document_index(
    conn: sqlite3.Connection,
    index_id: str,
    *,
    document_id: str = "d1",
    profile_name: str = "default",
    status: str = "indexed",
    indexed_at: str | None = "2026-05-15T09:30:01Z",
) -> None:
    conn.execute(
        """INSERT INTO document_indexes
           (id, knowledge_id, document_id, profile_name,
            document_file_hash, extracted_hash, profile_hash,
            status, indexed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            index_id, "kb1", document_id, profile_name,
            "fh", "eh", "ph", status, indexed_at,
        ),
    )


@pytest.fixture
def seeded_db(db_conn: sqlite3.Connection) -> sqlite3.Connection:
    """A DB with 1 document indexed under 'default' profile with 3 chunks."""
    with db_conn:
        _insert_document(db_conn, "d1", filename="ble_guide.pdf")
        _insert_profile(db_conn, "default")
        _insert_document_index(db_conn, "i1", document_id="d1", profile_name="default")
        for i in range(3):
            _insert_chunk(
                db_conn,
                f"c{i}",
                chunk_index=i,
                content=f"chunk {i} content",
                metadata={"heading_path": ["Ch1", f"Sec{i}"], "contains_table": i == 1},
            )
            _insert_variant(
                db_conn, f"v{i}", chunk_id=f"c{i}", variant_type="raw"
            )
    return db_conn


# ---------------------------------------------------------------------------
# fetch_document
# ---------------------------------------------------------------------------


class TestFetchDocument:
    def test_returns_row_when_found(self, seeded_db):
        doc = fetch_document(seeded_db, "d1")
        assert doc is not None
        assert isinstance(doc, DocumentRow)
        assert doc.id == "d1"
        assert doc.filename == "ble_guide.pdf"
        assert doc.source_type == "pdf"
        assert doc.extraction_provider == "pymupdf"
        assert doc.status == "extracted"
        assert doc.extracted_hash == "ehash_d1"

    def test_returns_none_when_missing(self, seeded_db):
        assert fetch_document(seeded_db, "no-such-id") is None


# ---------------------------------------------------------------------------
# list_profiles_for_document
# ---------------------------------------------------------------------------


class TestListProfiles:
    def test_single_profile(self, seeded_db):
        assert list_profiles_for_document(seeded_db, "d1") == ["default"]

    def test_multiple_profiles_sorted(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            for prof in ["zeta", "default", "alpha"]:
                _insert_profile(db_conn, prof)
                _insert_chunk(db_conn, f"c-{prof}", profile_name=prof, chunk_index=0)
        assert list_profiles_for_document(db_conn, "d1") == [
            "alpha", "default", "zeta"
        ]

    def test_no_chunks_returns_empty(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
        assert list_profiles_for_document(db_conn, "d1") == []


# ---------------------------------------------------------------------------
# fetch_document_summary
# ---------------------------------------------------------------------------


class TestFetchDocumentSummary:
    def test_basic_summary(self, seeded_db):
        summary = fetch_document_summary(seeded_db, "d1")
        assert summary is not None
        assert isinstance(summary, DocumentSummary)
        assert summary.document.id == "d1"
        assert len(summary.profiles) == 1
        p = summary.profiles[0]
        assert p.profile_name == "default"
        assert p.status == "indexed"
        assert p.indexed_at == "2026-05-15T09:30:01Z"
        assert p.chunk_counts.chunk == 3
        assert p.chunk_counts.total == 3
        # seeded_db uses raw variants without augmentation_status — augmentation
        # was never attempted for this profile, so neither counter increments.
        assert p.augmentation.succeeded == 0
        assert p.augmentation.raw_fallback == 0

    def test_returns_none_for_unknown_document(self, seeded_db):
        assert fetch_document_summary(seeded_db, "no-such-id") is None

    def test_with_multiple_profiles(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            for prof in ["default", "parent-child"]:
                _insert_profile(db_conn, prof)
                _insert_document_index(
                    db_conn, f"i-{prof}", profile_name=prof
                )
            for i in range(2):
                _insert_chunk(db_conn, f"c-d-{i}", profile_name="default", chunk_index=i)
            _insert_chunk(
                db_conn, "p1", profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
            )
            _insert_chunk(
                db_conn, "ch1", profile_name="parent-child",
                chunk_type="child", chunk_index=0, parent_chunk_id="p1",
            )
        summary = fetch_document_summary(db_conn, "d1")
        names = [p.profile_name for p in summary.profiles]
        assert names == ["default", "parent-child"]
        pc = next(p for p in summary.profiles if p.profile_name == "parent-child")
        assert pc.chunk_counts.parent == 1
        assert pc.chunk_counts.child == 1
        assert pc.chunk_counts.chunk == 0

    def test_profile_filter_returns_only_one(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            for prof in ["default", "alt"]:
                _insert_profile(db_conn, prof)
                _insert_chunk(db_conn, f"c-{prof}", profile_name=prof, chunk_index=0)
        summary = fetch_document_summary(db_conn, "d1", profile_name="default")
        assert [p.profile_name for p in summary.profiles] == ["default"]

    def test_augmentation_fallback_counted(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
            _insert_chunk(db_conn, "c1", chunk_index=1)
            # c0: contextual variant — counts as succeeded
            _insert_variant(
                db_conn, "v0", chunk_id="c0",
                variant_type="contextual",
                context_text="ctx",
            )
            # c1: raw fallback after augmentation failure
            _insert_variant(
                db_conn, "v1", chunk_id="c1",
                variant_type="raw",
                augmentation_status="fallback_raw",
            )
        summary = fetch_document_summary(db_conn, "d1")
        p = summary.profiles[0]
        assert p.augmentation.succeeded == 1
        assert p.augmentation.raw_fallback == 1

    def test_raw_variant_without_status_not_counted(self, db_conn):
        """Plain raw variants (no augmentation attempted) shouldn't inflate
        the success counter — verifies the corrected semantic."""
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "parent-child")
            _insert_chunk(db_conn, "c0", profile_name="parent-child",
                          chunk_index=0)
            _insert_variant(
                db_conn, "v0", chunk_id="c0",
                profile_name="parent-child",
                variant_type="raw",
                augmentation_status=None,
            )
        summary = fetch_document_summary(db_conn, "d1")
        p = summary.profiles[0]
        assert p.augmentation.succeeded == 0
        assert p.augmentation.raw_fallback == 0

    def test_unindexed_profile_status_unknown(self, db_conn):
        # chunks exist but no document_indexes row
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
        summary = fetch_document_summary(db_conn, "d1")
        assert summary.profiles[0].status == "unknown"
        assert summary.profiles[0].indexed_at is None


# ---------------------------------------------------------------------------
# count_chunks
# ---------------------------------------------------------------------------


class TestCountChunks:
    def test_count_matches_inserts(self, seeded_db):
        assert count_chunks(seeded_db, "d1", "default") == 3

    def test_count_zero_when_missing(self, seeded_db):
        assert count_chunks(seeded_db, "d1", "no-such-profile") == 0


# ---------------------------------------------------------------------------
# fetch_chunks
# ---------------------------------------------------------------------------


class TestFetchChunks:
    def test_default_returns_all_no_content_no_context(self, seeded_db):
        rows = fetch_chunks(seeded_db, "d1", "default")
        assert len(rows) == 3
        for r in rows:
            assert isinstance(r, ChunkRow)
            assert r.content is None
            assert r.variant.context_text is None
            # metadata is parsed dict
            assert "heading_path" in r.metadata
        # sorted by chunk_index
        assert [r.chunk_index for r in rows] == [0, 1, 2]

    def test_with_limit(self, seeded_db):
        rows = fetch_chunks(seeded_db, "d1", "default", limit=2)
        assert len(rows) == 2
        assert [r.chunk_index for r in rows] == [0, 1]

    def test_with_offset(self, seeded_db):
        rows = fetch_chunks(seeded_db, "d1", "default", offset=1)
        # no limit + offset=1: should return last 2
        assert [r.chunk_index for r in rows] == [1, 2]

    def test_with_limit_and_offset(self, seeded_db):
        rows = fetch_chunks(seeded_db, "d1", "default", limit=1, offset=1)
        assert len(rows) == 1
        assert rows[0].chunk_index == 1

    def test_include_content(self, seeded_db):
        rows = fetch_chunks(seeded_db, "d1", "default", include_content=True)
        assert rows[0].content == "chunk 0 content"
        # full text returned (not truncated)
        assert rows[1].content == "chunk 1 content"

    def test_include_context_returns_context_text(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
            _insert_variant(
                db_conn, "v0", chunk_id="c0",
                variant_type="contextual",
                context_text="ctx for chunk 0",
            )
        rows = fetch_chunks(db_conn, "d1", "default", include_context=True)
        assert rows[0].variant.context_text == "ctx for chunk 0"
        assert rows[0].variant.variant_type == "contextual"

    def test_include_context_false_returns_none(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
            _insert_variant(
                db_conn, "v0", chunk_id="c0",
                variant_type="contextual",
                context_text="ctx for chunk 0",
            )
        rows = fetch_chunks(db_conn, "d1", "default", include_context=False)
        assert rows[0].variant.context_text is None

    def test_parent_chunk_with_no_variant(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "parent-child")
            _insert_chunk(
                db_conn, "p1", profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
            )
        rows = fetch_chunks(db_conn, "d1", "parent-child")
        assert len(rows) == 1
        assert rows[0].chunk_type == "parent"
        assert rows[0].variant.variant_type is None
        assert rows[0].variant.qdrant_collection is None

    def test_contextual_preferred_over_raw(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
            _insert_variant(
                db_conn, "v_raw", chunk_id="c0", variant_type="raw",
            )
            _insert_variant(
                db_conn, "v_ctx", chunk_id="c0",
                variant_type="contextual",
                context_text="prefer me",
            )
        rows = fetch_chunks(db_conn, "d1", "default", include_context=True)
        assert rows[0].variant.variant_type == "contextual"
        assert rows[0].variant.context_text == "prefer me"

    def test_augmentation_status_extracted(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
            _insert_variant(
                db_conn, "v0", chunk_id="c0", variant_type="raw",
                augmentation_status="fallback_raw",
            )
        rows = fetch_chunks(db_conn, "d1", "default")
        assert rows[0].variant.augmentation_status == "fallback_raw"

    def test_metadata_empty_when_none(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0, metadata=None)
        rows = fetch_chunks(db_conn, "d1", "default")
        assert rows[0].metadata == {}


# ---------------------------------------------------------------------------
# fetch_chunk_single
# ---------------------------------------------------------------------------


class TestFetchChunkSingle:
    def test_returns_full_content_and_context(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0, content="full body")
            _insert_variant(
                db_conn, "v0", chunk_id="c0",
                variant_type="contextual",
                context_text="my context",
            )
        row = fetch_chunk_single(db_conn, "c0")
        assert row is not None
        assert row.content == "full body"
        assert row.variant.context_text == "my context"
        assert row.variant.variant_type == "contextual"

    def test_returns_none_when_missing(self, seeded_db):
        assert fetch_chunk_single(seeded_db, "no-such-id") is None

    def test_raw_variant_context_text_is_null(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
            _insert_chunk(db_conn, "c0", chunk_index=0)
            _insert_variant(
                db_conn, "v0", chunk_id="c0", variant_type="raw",
                context_text=None,
            )
        row = fetch_chunk_single(db_conn, "c0")
        assert row.variant.variant_type == "raw"
        assert row.variant.context_text is None

    def test_no_variant_at_all(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "parent-child")
            _insert_chunk(
                db_conn, "p1", profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
            )
        row = fetch_chunk_single(db_conn, "p1")
        assert row is not None
        assert row.content == "chunk content"
        assert row.variant.variant_type is None
        assert row.variant.context_text is None


# ---------------------------------------------------------------------------
# fetch_sections
# ---------------------------------------------------------------------------


class TestFetchSections:
    def test_returns_all_chunks_without_content(self, seeded_db):
        rows = fetch_sections(seeded_db, "d1", "default")
        assert len(rows) == 3
        for r in rows:
            assert r.content is None
            assert r.variant.context_text is None
            assert "heading_path" in r.metadata

    def test_returns_empty_when_no_chunks(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "default")
        assert fetch_sections(db_conn, "d1", "default") == []

    def test_includes_parent_and_child_chunks(self, db_conn):
        with db_conn:
            _insert_document(db_conn, "d1")
            _insert_profile(db_conn, "parent-child")
            _insert_chunk(
                db_conn, "p1", profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
                metadata={"heading_path": ["Ch1"]},
            )
            _insert_chunk(
                db_conn, "ch1", profile_name="parent-child",
                chunk_type="child", chunk_index=0,
                parent_chunk_id="p1",
                metadata={"heading_path": ["Ch1"]},
            )
        rows = fetch_sections(db_conn, "d1", "parent-child")
        chunk_types = [r.chunk_type for r in rows]
        assert "parent" in chunk_types
        assert "child" in chunk_types
        # parent_chunk_id preserved for tree-building
        child = next(r for r in rows if r.chunk_type == "child")
        assert child.parent_chunk_id == "p1"
