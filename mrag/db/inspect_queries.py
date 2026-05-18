"""Read-only SQL helpers for `mrag inspect` commands.

These helpers query the canonical mrag schema (documents, chunks,
chunk_variants, document_indexes) and return dataclasses that the CLI layer
formats as human-readable output or JSON.

All queries are read-only and never write to the DB.

See:
  - dev_docs/01_EXTENSION_STAGE_1/DESIGN_V18_INSPECT_REGISTRY.md §4.1-§4.3
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class DocumentRow:
    id: str
    filename: str
    source_type: str
    extraction_provider: str | None
    status: str
    extracted_hash: str | None
    created_at: str
    updated_at: str


@dataclass
class ProfileChunkCounts:
    chunk: int = 0
    parent: int = 0
    child: int = 0

    @property
    def total(self) -> int:
        return self.chunk + self.parent + self.child


@dataclass
class AugmentationStatus:
    succeeded: int = 0
    raw_fallback: int = 0


@dataclass
class DocumentProfileSummary:
    profile_name: str
    status: str
    indexed_at: str | None
    chunk_counts: ProfileChunkCounts
    augmentation: AugmentationStatus


@dataclass
class DocumentSummary:
    document: DocumentRow
    profiles: list[DocumentProfileSummary] = field(default_factory=list)


@dataclass
class ChunkVariantInfo:
    variant_type: str | None
    qdrant_collection: str | None
    augmentation_status: str | None
    context_text: str | None


@dataclass
class ChunkRow:
    chunk_id: str
    document_id: str
    profile_name: str
    chunk_type: str
    chunk_index: int
    parent_chunk_id: str | None
    char_count: int | None
    token_count: int | None
    source_format: str
    content: str | None
    metadata: dict[str, Any]
    variant: ChunkVariantInfo


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def fetch_document(conn: sqlite3.Connection, document_id: str) -> DocumentRow | None:
    """Look up a document by id; returns None if not found."""
    row = conn.execute(
        """
        SELECT id, filename, source_type, extraction_provider, status,
               extracted_hash, created_at, updated_at
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    return DocumentRow(
        id=row["id"],
        filename=row["filename"],
        source_type=row["source_type"],
        extraction_provider=row["extraction_provider"],
        status=row["status"],
        extracted_hash=row["extracted_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_profiles_for_document(
    conn: sqlite3.Connection, document_id: str
) -> list[str]:
    """Return all profile_names that have chunks for the given document."""
    rows = conn.execute(
        """
        SELECT DISTINCT profile_name FROM chunks
        WHERE document_id = ?
        ORDER BY profile_name
        """,
        (document_id,),
    ).fetchall()
    return [r["profile_name"] for r in rows]


def fetch_document_summary(
    conn: sqlite3.Connection,
    document_id: str,
    profile_name: str | None = None,
) -> DocumentSummary | None:
    """Build a per-profile summary for a document.

    When `profile_name` is given, only that profile is included.
    Returns None if the document does not exist.
    """
    document = fetch_document(conn, document_id)
    if document is None:
        return None

    if profile_name is not None:
        profile_clause = "AND profile_name = ?"
        params: tuple[Any, ...] = (document_id, profile_name)
    else:
        profile_clause = ""
        params = (document_id,)

    index_rows = conn.execute(
        f"""
        SELECT profile_name, status, indexed_at
        FROM document_indexes
        WHERE document_id = ? {profile_clause}
        ORDER BY profile_name
        """,
        params,
    ).fetchall()
    index_by_profile = {r["profile_name"]: r for r in index_rows}

    chunk_rows = conn.execute(
        f"""
        SELECT profile_name, chunk_type, COUNT(*) AS cnt
        FROM chunks
        WHERE document_id = ? {profile_clause}
        GROUP BY profile_name, chunk_type
        """,
        params,
    ).fetchall()
    counts_by_profile: dict[str, ProfileChunkCounts] = {}
    for r in chunk_rows:
        p = r["profile_name"]
        counts = counts_by_profile.setdefault(p, ProfileChunkCounts())
        setattr(counts, r["chunk_type"], r["cnt"])

    # Augmentation status: only count variants that were *targets* of
    # augmentation. A successful contextual variant or a fallback_raw row
    # both indicate the profile attempted augmentation. Plain `raw` rows
    # without an augmentation_status mean the profile never attempted
    # augmentation (e.g. parent_child without contextual) — those are not
    # "succeeded" in any meaningful sense.
    aug_rows = conn.execute(
        f"""
        SELECT profile_name, variant_type, metadata_json
        FROM chunk_variants
        WHERE document_id = ? {profile_clause}
        """,
        params,
    ).fetchall()
    aug_by_profile: dict[str, AugmentationStatus] = {}
    for r in aug_rows:
        meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
        status = meta.get("augmentation_status")
        is_contextual = r["variant_type"] == "contextual"
        is_fallback = status == "fallback_raw"
        if not (is_contextual or is_fallback):
            continue
        p = r["profile_name"]
        aug = aug_by_profile.setdefault(p, AugmentationStatus())
        if is_fallback:
            aug.raw_fallback += 1
        else:
            aug.succeeded += 1

    profile_keys = (
        set(index_by_profile) | set(counts_by_profile) | set(aug_by_profile)
    )
    if profile_name is not None:
        profile_keys &= {profile_name}

    profiles: list[DocumentProfileSummary] = []
    for p in sorted(profile_keys):
        idx = index_by_profile.get(p)
        profiles.append(
            DocumentProfileSummary(
                profile_name=p,
                status=idx["status"] if idx else "unknown",
                indexed_at=idx["indexed_at"] if idx else None,
                chunk_counts=counts_by_profile.get(p, ProfileChunkCounts()),
                augmentation=aug_by_profile.get(p, AugmentationStatus()),
            )
        )

    return DocumentSummary(document=document, profiles=profiles)


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


def _extract_augmentation_status(variant_row: sqlite3.Row | None) -> str | None:
    if variant_row is None:
        return None
    meta_json = variant_row["metadata_json"]
    if not meta_json:
        return None
    return json.loads(meta_json).get("augmentation_status")


def _fetch_variant_for_chunk(
    conn: sqlite3.Connection, chunk_id: str, profile_name: str
) -> sqlite3.Row | None:
    """Look up the variant row for a chunk; prefer contextual over raw."""
    return conn.execute(
        """
        SELECT variant_type, qdrant_collection, context_text, metadata_json
        FROM chunk_variants
        WHERE chunk_id = ? AND profile_name = ?
        ORDER BY CASE variant_type WHEN 'contextual' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (chunk_id, profile_name),
    ).fetchone()


def _build_chunk_row(
    row: sqlite3.Row,
    variant_row: sqlite3.Row | None,
    *,
    include_content: bool,
    include_context: bool,
) -> ChunkRow:
    metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    variant = ChunkVariantInfo(
        variant_type=variant_row["variant_type"] if variant_row else None,
        qdrant_collection=variant_row["qdrant_collection"] if variant_row else None,
        augmentation_status=_extract_augmentation_status(variant_row),
        context_text=(
            variant_row["context_text"] if (variant_row and include_context) else None
        ),
    )
    return ChunkRow(
        chunk_id=row["id"],
        document_id=row["document_id"],
        profile_name=row["profile_name"],
        chunk_type=row["chunk_type"],
        chunk_index=row["chunk_index"],
        parent_chunk_id=row["parent_chunk_id"],
        char_count=row["char_count"],
        token_count=row["token_count"],
        source_format=row["source_format"],
        content=row["content"] if include_content else None,
        metadata=metadata,
        variant=variant,
    )


def count_chunks(
    conn: sqlite3.Connection, document_id: str, profile_name: str
) -> int:
    """Return the total chunk count for (document_id, profile_name)."""
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM chunks WHERE document_id = ? AND profile_name = ?",
        (document_id, profile_name),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def fetch_chunks(
    conn: sqlite3.Connection,
    document_id: str,
    profile_name: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    include_content: bool = False,
    include_context: bool = False,
) -> list[ChunkRow]:
    """Return chunks for (document_id, profile_name) ordered by chunk_index.

    `limit=None` returns all chunks (agent-friendly default per DESIGN_V18 §3.1.2).
    `offset` is honored even when `limit` is None.
    """
    query = """
        SELECT id, document_id, profile_name, parent_chunk_id, chunk_type,
               chunk_index, content, source_format, token_count, char_count,
               metadata_json
        FROM chunks
        WHERE document_id = ? AND profile_name = ?
        ORDER BY chunk_index
    """
    params: list[Any] = [document_id, profile_name]
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    elif offset:
        # SQLite: LIMIT -1 means "no limit", required when only OFFSET is given.
        query += " LIMIT -1 OFFSET ?"
        params.append(offset)

    rows = conn.execute(query, params).fetchall()
    result: list[ChunkRow] = []
    for row in rows:
        variant_row = _fetch_variant_for_chunk(conn, row["id"], profile_name)
        result.append(
            _build_chunk_row(
                row,
                variant_row,
                include_content=include_content,
                include_context=include_context,
            )
        )
    return result


def fetch_chunk_single(conn: sqlite3.Connection, chunk_id: str) -> ChunkRow | None:
    """Fetch a single chunk by chunk_id; always includes content + context_text."""
    row = conn.execute(
        """
        SELECT id, document_id, profile_name, parent_chunk_id, chunk_type,
               chunk_index, content, source_format, token_count, char_count,
               metadata_json
        FROM chunks
        WHERE id = ?
        """,
        (chunk_id,),
    ).fetchone()
    if row is None:
        return None
    variant_row = _fetch_variant_for_chunk(conn, row["id"], row["profile_name"])
    return _build_chunk_row(
        row,
        variant_row,
        include_content=True,
        include_context=True,
    )


def fetch_sections(
    conn: sqlite3.Connection,
    document_id: str,
    profile_name: str,
) -> list[ChunkRow]:
    """Fetch chunks for sections-view rendering.

    Returns all chunks for the (document_id, profile_name) pair without
    content/context. The caller builds the heading hierarchy tree or the
    parent/child layered view from the returned metadata.
    """
    return fetch_chunks(
        conn,
        document_id,
        profile_name,
        limit=None,
        offset=0,
        include_content=False,
        include_context=False,
    )


__all__ = [
    "AugmentationStatus",
    "ChunkRow",
    "ChunkVariantInfo",
    "DocumentProfileSummary",
    "DocumentRow",
    "DocumentSummary",
    "ProfileChunkCounts",
    "count_chunks",
    "fetch_chunk_single",
    "fetch_chunks",
    "fetch_document",
    "fetch_document_summary",
    "fetch_sections",
    "list_profiles_for_document",
]
