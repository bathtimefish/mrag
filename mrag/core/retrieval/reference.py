"""Where a search result's text came from, in a form a citation can use.

Every surface that returns search results — `mrag search --json`, the native
API and the MCP ``search`` tool — attaches the same ``reference`` object to
each result, built here once:

* ``display_name`` and ``source_binding_status`` — the document's name and how
  its source is bound, read exactly as the document list reads them;
* ``source_path`` — the source file, project-relative, when it lives in the
  project; ``null`` for an external root (the catalog does not record where
  one is on disk) and for a legacy row;
* ``original`` and ``extracted_markdown`` — the stored original and the
  Markdown extraction, project-relative;
* ``resource`` — the MCP resource that serves the stored original;
* ``revision_id`` — always ``null``: a document keeps one extraction here, with
  no revisions to tell apart;
* ``location`` — which extraction the chunk was cut from (``source_format``),
  and its character range in it, which chunks do not record (``null``).

Every field is present on every result, and a value that does not apply is
``null``, so a caller can tell "this cannot be named" from a field it forgot
to read. Paths are project-relative; no absolute path is returned.

The extraction named is the document's current one. After a file is updated
and before `mrag index` runs again, it may no longer contain the result's
text; the document list reports that document as ``stale`` meanwhile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mrag.core.ingestion.inventory import read_identity, recorded_scheme, root_labels
from mrag.core.retrieval.base import RetrievalResult


def original_resource(document_id: str) -> str:
    """The MCP resource that serves a document's stored original."""
    return f"mrag://documents/{document_id}/original"


def fetch_references(db_path: Path, results: list[RetrievalResult]) -> dict[str, dict[str, Any]]:
    """Return ``{chunk_id: reference}`` for every result, in three queries."""
    if not results:
        return {}
    from mrag.db.connection import open_connection

    chunk_ids = list({r.chunk_id for r in results})
    document_ids = list({r.document_id for r in results})
    conn = open_connection(db_path)
    try:
        formats = {
            row["id"]: row["source_format"]
            for row in conn.execute(
                "SELECT id, source_format FROM chunks WHERE id IN (%s)" % ",".join("?" * len(chunk_ids)),
                chunk_ids,
            )
        }
        documents = {
            row["id"]: row
            for row in conn.execute(
                "SELECT * FROM documents WHERE id IN (%s)" % ",".join("?" * len(document_ids)),
                document_ids,
            )
        }
        scheme = recorded_scheme(conn)
        labels = root_labels(conn)
    finally:
        conn.close()

    references: dict[str, dict[str, Any]] = {}
    for result in results:
        row = documents.get(result.document_id)
        references[result.chunk_id] = _reference(result, row, formats.get(result.chunk_id), scheme, labels)
    return references


def _reference(
    result: RetrievalResult,
    row: Any,
    source_format: str | None,
    scheme: int,
    labels: dict[str, str],
) -> dict[str, Any]:
    if row is None:
        # A document row that is gone leaves nothing to name; shown as such,
        # with no binding claimed for it.
        name, binding, path, original, extracted = "", "project_relative", None, None, None
    else:
        stored = row["source_identity"] if "source_identity" in row.keys() else None
        reading = read_identity(scheme, stored, result.document_id, labels)
        name, binding, path = reading.name, reading.binding, reading.path
        original = row["original_path"]
        extracted = row["extracted_markdown_path"]
    return {
        "display_name": name,
        "source_binding_status": binding,
        "source_path": path,
        "original": original,
        "extracted_markdown": extracted,
        "resource": original_resource(result.document_id),
        "revision_id": None,
        "location": {
            "source_format": source_format,
            "source_start": None,
            "source_end": None,
        },
    }


__all__ = ["fetch_references", "original_resource"]
