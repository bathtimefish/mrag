import sqlite3


def insert_chunk(
    conn: sqlite3.Connection,
    content: str,
    knowledge_id: str,
    profile_name: str,
    chunk_id: str,
    document_id: str,
) -> None:
    conn.execute(
        "INSERT INTO fts_chunks(content, knowledge_id, profile_name, chunk_id, document_id) "
        "VALUES (?,?,?,?,?)",
        (content, knowledge_id, profile_name, chunk_id, document_id),
    )


def delete_by_document(
    conn: sqlite3.Connection,
    knowledge_id: str,
    profile_name: str,
    document_id: str,
) -> None:
    conn.execute(
        "DELETE FROM fts_chunks WHERE knowledge_id=? AND profile_name=? AND document_id=?",
        (knowledge_id, profile_name, document_id),
    )


def delete_document_all_profiles(
    conn: sqlite3.Connection,
    knowledge_id: str,
    document_id: str,
) -> None:
    conn.execute(
        "DELETE FROM fts_chunks WHERE knowledge_id=? AND document_id=?",
        (knowledge_id, document_id),
    )


def delete_by_profile(
    conn: sqlite3.Connection,
    knowledge_id: str,
    profile_name: str,
) -> None:
    conn.execute(
        "DELETE FROM fts_chunks WHERE knowledge_id=? AND profile_name=?",
        (knowledge_id, profile_name),
    )
