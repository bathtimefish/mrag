from dataclasses import dataclass
from pathlib import Path


@dataclass
class IndexDecision:
    document_id: str
    needs_index: bool
    reason: str  # "new" | "file_changed" | "extracted_changed" | "profile_changed" | "error_retry" | "skip"


def plan_indexing(
    documents: list[dict],
    profile_name: str,
    profile_hash: str,
    db_path: Path,
) -> list[IndexDecision]:
    """Compare each document against document_indexes and decide which need (re)indexing."""
    from mrag.db.connection import open_connection

    conn = open_connection(db_path)
    try:
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
            else:
                decisions.append(IndexDecision(doc_id, False, "skip"))

        return decisions
    finally:
        conn.close()
