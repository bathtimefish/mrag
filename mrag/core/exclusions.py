"""Document exclusion planning and derived-index cleanup."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mrag.config.project import ProjectConfig
from mrag.db import fts as fts_db
from mrag.db.connection import db_connection, fts_db_connection, open_connection
from mrag.db.qdrant import delete_points, make_client


@dataclass(frozen=True)
class ExclusionCleanupPlan:
    document_id: str
    filename: str
    profile_name: str | None
    chunk_count: int
    variant_count: int
    fts_count: int
    qdrant_points: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def qdrant_point_count(self) -> int:
        return sum(len(point_ids) for point_ids in self.qdrant_points.values())


@dataclass(frozen=True)
class ExclusionCleanupResult:
    plan: ExclusionCleanupPlan
    warnings: tuple[str, ...] = ()


def plan_document_cleanup(
    db_path: Path,
    document_id: str,
    profile_name: str | None,
) -> ExclusionCleanupPlan:
    conn = open_connection(db_path)
    try:
        document = conn.execute(
            "SELECT filename FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if document is None:
            raise KeyError(document_id)

        profile_clause = "" if profile_name is None else " AND profile_name = ?"
        params: tuple[str, ...] = (
            (document_id,) if profile_name is None else (document_id, profile_name)
        )
        chunk_count = conn.execute(
            f"SELECT COUNT(*) FROM chunks WHERE document_id = ?{profile_clause}",
            params,
        ).fetchone()[0]
        variants = conn.execute(
            f"""SELECT qdrant_point_id, qdrant_collection
                FROM chunk_variants
                WHERE document_id = ?{profile_clause}""",
            params,
        ).fetchall()
        fts_count = conn.execute(
            f"SELECT COUNT(*) FROM fts_chunks WHERE document_id = ?{profile_clause}",
            params,
        ).fetchone()[0]
    finally:
        conn.close()

    qdrant: dict[str, list[str]] = {}
    for row in variants:
        point_id = row["qdrant_point_id"]
        collection = row["qdrant_collection"]
        if point_id and collection:
            qdrant.setdefault(collection, []).append(point_id)

    return ExclusionCleanupPlan(
        document_id=document_id,
        filename=document["filename"],
        profile_name=profile_name,
        chunk_count=int(chunk_count),
        variant_count=len(variants),
        fts_count=int(fts_count),
        qdrant_points={
            collection: tuple(point_ids)
            for collection, point_ids in sorted(qdrant.items())
        },
    )


def purge_document_index(
    *,
    project_dir: Path,
    db_path: Path,
    config: ProjectConfig,
    document_id: str,
    profile_name: str | None,
    qdrant_client=None,
) -> ExclusionCleanupResult:
    """Remove every derived retrieval artifact for the selected scope.

    SQLite/FTS failures propagate because restore must not revoke the policy
    while searchable rows remain. On Qdrant failure, chunk metadata is retained
    so a repeated forced add/restore can retry point deletion safely; the policy
    and retrieval filters still prevent any result from escaping.
    """
    plan = plan_document_cleanup(db_path, document_id, profile_name)

    with fts_db_connection(db_path, config.fts_tokenizer) as fts_conn:
        if profile_name is None:
            fts_db.delete_document_all_profiles(
                fts_conn, config.knowledge_id, document_id
            )
        else:
            fts_db.delete_by_document(
                fts_conn, config.knowledge_id, profile_name, document_id
            )

    warnings: list[str] = []
    if plan.qdrant_points:
        try:
            client = qdrant_client or make_client(
                mode=config.qdrant.mode,
                host=config.qdrant.host,
                port=config.qdrant.port,
                path=project_dir / "qdrant",
            )
            for collection, point_ids in plan.qdrant_points.items():
                delete_points(client, collection, list(point_ids))
        except Exception as error:
            warnings.append(
                "Qdrant physical cleanup is pending; chunk metadata was retained "
                "for a safe retry while policy filters remain authoritative "
                f"({error})"
            )
            return ExclusionCleanupResult(plan=plan, warnings=tuple(warnings))

    profile_clause = "" if profile_name is None else " AND profile_name = ?"
    params: tuple[str, ...] = (
        (document_id,) if profile_name is None else (document_id, profile_name)
    )
    with db_connection(db_path) as conn:
        conn.execute(
            f"DELETE FROM chunk_variants WHERE document_id = ?{profile_clause}",
            params,
        )
        conn.execute(
            f"DELETE FROM chunks WHERE document_id = ?{profile_clause}",
            params,
        )
        conn.execute(
            f"DELETE FROM document_indexes WHERE document_id = ?{profile_clause}",
            params,
        )

    return ExclusionCleanupResult(plan=plan, warnings=tuple(warnings))


__all__ = [
    "ExclusionCleanupPlan",
    "ExclusionCleanupResult",
    "plan_document_cleanup",
    "purge_document_index",
]
