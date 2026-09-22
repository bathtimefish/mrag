"""One document-list contract for the native API and MCP."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from mrag.core.ingestion.source_identity import binding_status, display_name


def list_document_rows(conn: sqlite3.Connection) -> list[dict]:
    has_roots = conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_roots'").fetchone()
    labels = ({r["root_key"]: r["label"] for r in conn.execute("SELECT root_key, label FROM source_roots")}
              if has_roots else {})
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
        identity = (row["source_identity"] if "source_identity" in row.keys() else None) or f"legacy/v1/{document_id}"
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
            "display_name": display_name(identity, labels),
            "source_identity": identity,
            "source_binding_status": binding_status(identity),
            "content_hash": row["file_hash"],
            "status": "error" if source_status == "error" or index_status == "error" else "ready" if source_status == "ready" else "building",
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
