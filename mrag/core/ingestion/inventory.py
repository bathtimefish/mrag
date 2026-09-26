"""One document-list contract for the native API and MCP."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from mrag.core.ingestion.source_identity import (
    SCHEME_KEY,
    SCHEME_UNRECORDED,
    SCHEME_VERSION,
    binding_status,
    display_name,
    legacy_identity,
    read_scheme_one,
)


def _recorded_scheme(conn: sqlite3.Connection) -> int:
    has_settings = conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_settings'").fetchone()
    row = (conn.execute("SELECT value FROM catalog_settings WHERE key = ?", (SCHEME_KEY,)).fetchone()
           if has_settings else None)
    return int(row[0]) if row else SCHEME_UNRECORDED


def _read_identity(scheme: int, stored: str | None, document_id: str, labels: dict[str, str]) -> tuple[str, str, str]:
    """Return (listed identity, binding, display name) for one stored value.

    A catalog nobody has migrated is read through the rule the explicit
    migration applies, so what a listing shows before `mrag catalog
    migrate-identities` is what the migration makes true. The listed identity is
    what is stored; the binding and the name are what it is read as.
    """
    if stored is None:
        # Written by a release from before source identities, after the
        # catalog was migrated: an unrecoverable path, named as one.
        identity = legacy_identity(document_id)
        return identity, "legacy_unbound", identity
    if scheme == SCHEME_VERSION:
        return stored, binding_status(stored), display_name(stored, labels)
    if scheme == SCHEME_UNRECORDED:
        reading = read_scheme_one(stored, document_id, set(labels))
        if reading.identity is None:
            return stored, reading.binding, stored
        return stored, reading.binding, display_name(reading.identity, labels)
    raise ValueError(
        f"This project's source identities follow scheme {scheme}, which this mrag "
        f"(scheme {SCHEME_VERSION}) cannot read; use the mrag release that created it."
    )


def list_document_rows(conn: sqlite3.Connection) -> list[dict]:
    has_roots = conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_roots'").fetchone()
    labels = ({r["root_key"]: r["label"] for r in conn.execute("SELECT root_key, label FROM source_roots")}
              if has_roots else {})
    scheme = _recorded_scheme(conn)
    rows = conn.execute("SELECT * FROM documents").fetchall()
    indexes_by_document = defaultdict(list)
    for index in conn.execute("SELECT document_id, profile_name, status, document_file_hash FROM document_indexes ORDER BY profile_name"):
        indexes_by_document[index["document_id"]].append(index)
    exclusions_by_document = defaultdict(list)
    for exclusion in conn.execute("SELECT id, document_id, profile_name FROM document_exclusions WHERE revoked_at IS NULL ORDER BY created_at DESC, id"):
        exclusions_by_document[exclusion["document_id"]].append(exclusion)
    fallback_documents = {
        r["document_id"] for r in conn.execute(
            "SELECT DISTINCT document_id FROM chunk_variants WHERE metadata_json LIKE '%fallback%'"
        )
    }
    result = []
    for row in rows:
        document_id = row["id"]
        stored = row["source_identity"] if "source_identity" in row.keys() else None
        identity, binding, name = _read_identity(scheme, stored, document_id, labels)
        indexes = indexes_by_document[document_id]
        profiles = [r["profile_name"] for r in indexes]
        profile = profiles[0] if len(profiles) == 1 else None
        if not indexes:
            index_status = "not_indexed"
        elif any(r["document_file_hash"] != row["file_hash"] for r in indexes):
            index_status = "stale"
        elif any(r["status"] == "error" for r in indexes):
            index_status = "error"
        elif any(r["status"] == "indexing" for r in indexes):
            index_status = "indexing"
        elif any(r["status"] == "pending" for r in indexes):
            index_status = "pending"
        elif document_id in fallback_documents:
            index_status = "fallback"
        else:
            index_status = "indexed"
        exclusion = next((e for e in exclusions_by_document[document_id]
                          if e["profile_name"] is None or e["profile_name"] == profile), None)
        source_status = {"pending": "building", "extracted": "ready", "error": "error"}[row["status"]]
        result.append({
            "document_id": document_id,
            "display_name": name,
            "source_identity": identity,
            "source_binding_status": binding,
            "content_hash": row["file_hash"],
            # The stored extraction status, as GET /documents/{id} and earlier
            # releases report it; the derived states have fields of their own.
            "status": row["status"],
            "source_status": source_status,
            "index_status": index_status,
            "retrieval_status": "excluded" if exclusion else "eligible",
            "profile": profile,
            "exclusion_id": exclusion["id"] if exclusion else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # Additive compatibility with the existing OSS list response.
            "id": document_id,
            "filename": row["filename"],
            "file_hash": row["file_hash"],
            "source_type": row["source_type"],
        })
    return sorted(result, key=lambda item: (item["source_identity"], item["document_id"]))
