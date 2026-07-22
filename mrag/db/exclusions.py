"""Persistent document-level retrieval exclusions.

The policy table is authoritative: physical FTS/Qdrant cleanup is an
optimization and must never be the only mechanism preventing retrieval.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mrag.db.connection import db_connection, open_connection


@dataclass(frozen=True)
class DocumentExclusion:
    id: str
    document_id: str
    profile_name: str | None
    reason: str | None
    created_at: str
    revoked_at: str | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_exclusions_schema(conn: sqlite3.Connection) -> None:
    """Install the additive v0.23 exclusion schema on a new or existing DB."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_exclusions (
          id           TEXT PRIMARY KEY,
          document_id  TEXT NOT NULL,
          profile_name TEXT,
          reason       TEXT CHECK(reason IS NULL OR length(reason) <= 1000),
          created_at   TEXT NOT NULL,
          revoked_at   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_document_exclusions_document
          ON document_exclusions(document_id);
        CREATE INDEX IF NOT EXISTS idx_document_exclusions_active_profile
          ON document_exclusions(profile_name, document_id)
          WHERE revoked_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_exclusions_active_global
          ON document_exclusions(document_id)
          WHERE profile_name IS NULL AND revoked_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_document_exclusions_active_profile
          ON document_exclusions(document_id, profile_name)
          WHERE profile_name IS NOT NULL AND revoked_at IS NULL;
        """
    )


def exclusions_schema_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_exclusions'"
    ).fetchone()
    return row is not None


def _from_row(row: sqlite3.Row) -> DocumentExclusion:
    return DocumentExclusion(
        id=row["id"],
        document_id=row["document_id"],
        profile_name=row["profile_name"],
        reason=row["reason"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def active_document_ids(db_path: Path, profile_name: str) -> set[str]:
    """Return documents excluded globally or for ``profile_name``."""
    conn = open_connection(db_path)
    try:
        if not exclusions_schema_exists(conn):
            return set()
        rows = conn.execute(
            """SELECT DISTINCT document_id
               FROM document_exclusions
               WHERE revoked_at IS NULL
                 AND (profile_name IS NULL OR profile_name = ?)""",
            (profile_name,),
        ).fetchall()
        return {row["document_id"] for row in rows}
    finally:
        conn.close()


def list_exclusions(
    db_path: Path,
    *,
    include_revoked: bool = False,
) -> list[DocumentExclusion]:
    conn = open_connection(db_path)
    try:
        if not exclusions_schema_exists(conn):
            return []
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        rows = conn.execute(
            f"""SELECT id, document_id, profile_name, reason, created_at, revoked_at
                FROM document_exclusions {where}
                ORDER BY created_at, id"""
        ).fetchall()
        return [_from_row(row) for row in rows]
    finally:
        conn.close()


def find_exclusion(db_path: Path, exclusion_id: str) -> DocumentExclusion | None:
    conn = open_connection(db_path)
    try:
        if not exclusions_schema_exists(conn):
            return None
        row = conn.execute(
            """SELECT id, document_id, profile_name, reason, created_at, revoked_at
               FROM document_exclusions WHERE id = ?""",
            (exclusion_id,),
        ).fetchone()
        return _from_row(row) if row else None
    finally:
        conn.close()


def find_covering_exclusion(
    db_path: Path,
    document_id: str,
    profile_name: str | None,
) -> DocumentExclusion | None:
    """Find an active rule that already covers the requested scope."""
    conn = open_connection(db_path)
    try:
        if not exclusions_schema_exists(conn):
            return None
        if profile_name is None:
            row = conn.execute(
                """SELECT id, document_id, profile_name, reason, created_at, revoked_at
                   FROM document_exclusions
                   WHERE document_id = ? AND profile_name IS NULL
                     AND revoked_at IS NULL""",
                (document_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id, document_id, profile_name, reason, created_at, revoked_at
                   FROM document_exclusions
                   WHERE document_id = ? AND revoked_at IS NULL
                     AND (profile_name IS NULL OR profile_name = ?)
                   ORDER BY profile_name IS NULL DESC
                   LIMIT 1""",
                (document_id, profile_name),
            ).fetchone()
        return _from_row(row) if row else None
    finally:
        conn.close()


def active_scoped_exclusions(
    db_path: Path,
    document_id: str,
) -> list[DocumentExclusion]:
    """Return active profile-scoped rules for conflict detection."""
    return [
        exclusion
        for exclusion in list_exclusions(db_path)
        if exclusion.document_id == document_id and exclusion.profile_name is not None
    ]


def create_exclusion(
    db_path: Path,
    document_id: str,
    profile_name: str | None,
    reason: str | None,
) -> DocumentExclusion:
    normalized_reason = reason.strip() if reason and reason.strip() else None
    if normalized_reason and len(normalized_reason) > 1000:
        raise ValueError("exclusion reason must be at most 1000 characters")
    exclusion = DocumentExclusion(
        id=str(uuid.uuid4()),
        document_id=document_id,
        profile_name=profile_name,
        reason=normalized_reason,
        created_at=_now_iso(),
        revoked_at=None,
    )
    with db_connection(db_path) as conn:
        ensure_exclusions_schema(conn)
        conn.execute(
            """INSERT INTO document_exclusions
               (id, document_id, profile_name, reason, created_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            (
                exclusion.id,
                exclusion.document_id,
                exclusion.profile_name,
                exclusion.reason,
                exclusion.created_at,
            ),
        )
    return exclusion


def revoke_exclusion(db_path: Path, exclusion_id: str) -> DocumentExclusion:
    revoked_at = _now_iso()
    with db_connection(db_path) as conn:
        ensure_exclusions_schema(conn)
        row = conn.execute(
            """SELECT id, document_id, profile_name, reason, created_at, revoked_at
               FROM document_exclusions WHERE id = ?""",
            (exclusion_id,),
        ).fetchone()
        if row is None:
            raise KeyError(exclusion_id)
        exclusion = _from_row(row)
        if exclusion.revoked_at is not None:
            return exclusion
        conn.execute(
            "UPDATE document_exclusions SET revoked_at = ? WHERE id = ?",
            (revoked_at, exclusion_id),
        )
    return DocumentExclusion(
        id=exclusion.id,
        document_id=exclusion.document_id,
        profile_name=exclusion.profile_name,
        reason=exclusion.reason,
        created_at=exclusion.created_at,
        revoked_at=revoked_at,
    )


__all__ = [
    "DocumentExclusion",
    "active_document_ids",
    "active_scoped_exclusions",
    "create_exclusion",
    "ensure_exclusions_schema",
    "exclusions_schema_exists",
    "find_covering_exclusion",
    "find_exclusion",
    "list_exclusions",
    "revoke_exclusion",
]
