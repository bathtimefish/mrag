from dataclasses import dataclass
from pathlib import Path


@dataclass
class IndexDecision:
    document_id: str
    needs_index: bool
    reason: str  # "new" | "file_changed" | "extracted_changed" | "profile_changed" | "name_changed" | "error_retry" | "skip"


def plan_indexing(
    documents: list[dict],
    profile_name: str,
    profile_hash: str,
    db_path: Path,
) -> list[IndexDecision]:
    """Compare each document against document_indexes and decide which need (re)indexing.

    The rules are the document list's (``mrag.core.ingestion.inventory``): a
    document the list calls stale is one this plan rebuilds, a renamed file
    included.
    """
    from mrag.core.ingestion.inventory import read_identity, recorded_scheme, root_labels
    from mrag.db.connection import open_connection

    conn = open_connection(db_path)
    try:
        scheme = recorded_scheme(conn)
        labels = root_labels(conn)
        decisions: list[IndexDecision] = []
        for doc in documents:
            doc_id = doc["id"]
            row = conn.execute(
                "SELECT * FROM document_indexes WHERE document_id = ? AND profile_name = ?",
                (doc_id, profile_name),
            ).fetchone()

            if row is None:
                decisions.append(IndexDecision(doc_id, True, "new"))
            elif row["status"] != "indexed":
                decisions.append(IndexDecision(doc_id, True, "error_retry"))
            elif row["document_file_hash"] != doc["file_hash"]:
                decisions.append(IndexDecision(doc_id, True, "file_changed"))
            elif row["extracted_hash"] != doc["extracted_hash"]:
                decisions.append(IndexDecision(doc_id, True, "extracted_changed"))
            elif row["profile_hash"] != profile_hash:
                decisions.append(IndexDecision(doc_id, True, "profile_changed"))
            elif _renamed(row, read_identity(scheme, doc.get("source_identity"), doc_id, labels).name):
                decisions.append(IndexDecision(doc_id, True, "name_changed"))
            else:
                decisions.append(IndexDecision(doc_id, False, "skip"))

        return decisions
    finally:
        conn.close()


def _renamed(row, name: str) -> bool:
    # A row from before names were recorded is not judged, as in the list.
    recorded = row["indexed_display_name"] if "indexed_display_name" in row.keys() else None
    return recorded is not None and recorded != name
