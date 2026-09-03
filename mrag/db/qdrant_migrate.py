"""One-time migration of Qdrant collections named under the pre-0.24.0 scheme.

0.24.0 appended an identity fingerprint to every collection name (see
``mrag.db.qdrant.collection_name``) and its changelog claimed only
``qdrant.mode: server`` was affected. The name is derived the same way in
``local`` mode, so a project indexed before 0.24.0 kept its vectors under the
old name while every vector / hybrid search after upgrading looked for the new
one and failed with ``Collection ... not found``.

Instead of asking every user to re-embed their corpus, the retrieval and
indexing paths call ``resolve_collection`` before touching Qdrant. When a
legacy-named collection exists, the points that ``chunk_variants`` still
attributes to it are copied into the fingerprinted collection, those rows are
repointed, the copied points are deleted from the legacy collection, and the
legacy collection itself is dropped once no row references it. Only points
the database names are moved: a collection shared by look-alike knowledge
bases on a Qdrant server (the collision 0.24.0 fixed) is never emptied of
another project's data, and a legacy collection that ``mrag reindex`` already
rebuilt under the new name is recognised as an orphan rather than merged back.

The migration is idempotent and safe to interrupt. Copies are keyed by point
ID, rows are repointed only after their points have been copied, and points are
deleted from the legacy collection only after that — so re-running after a
crash re-copies whatever is still attributed to the legacy collection and
continues from there.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from qdrant_client.models import PointIdsList, PointStruct, VectorParams

from mrag.config.project import ProjectConfig
from mrag.db.connection import db_connection, open_connection
from mrag.db.qdrant import collection_name, ensure_collection, legacy_collection_name

_BATCH_SIZE = 1000


@dataclass(frozen=True)
class LegacyMigration:
    """What ``migrate_legacy_collection`` did for one profile."""

    legacy: str
    target: str
    moved: int
    """Points copied into ``target`` and deleted from ``legacy``."""
    orphaned: int
    """Points left in ``legacy`` afterwards: none of this profile's rows named them."""
    dropped: bool
    """Whether ``legacy`` was deleted. Only happens when no row of any profile references it."""

    def describe(self) -> str:
        if self.moved:
            msg = (
                f"Migrated {self.moved} vector point(s) from pre-0.24.0 Qdrant collection "
                f"'{self.legacy}' to '{self.target}'"
            )
            if not self.dropped:
                return (
                    f"{msg}; {self.orphaned} point(s) this profile does not reference "
                    f"remain in '{self.legacy}'"
                )
            if self.orphaned:
                return f"{msg}; removed the legacy collection ({self.orphaned} unreferenced point(s) discarded)"
            return f"{msg}; removed the empty legacy collection"
        return (
            f"Removed pre-0.24.0 Qdrant collection '{self.legacy}': no chunk row referenced "
            f"its {self.orphaned} point(s) (the index already lives in '{self.target}')"
        )


def _batched(ids: list[str], size: int = _BATCH_SIZE) -> Iterator[list[str]]:
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def _rows_attributed_to(
    db_path: Path, legacy: str, kb_id: str, profile_name: str
) -> tuple[int, set[str]]:
    """Rows of this profile still pointing at ``legacy``, and the point IDs they name.

    Rows whose ``qdrant_point_id`` is NULL (embedding fallback variants) count as
    attributed — they must be repointed too — but name no point to move.
    """
    conn = open_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT qdrant_point_id FROM chunk_variants "
            "WHERE qdrant_collection=? AND knowledge_id=? AND profile_name=?",
            (legacy, kb_id, profile_name),
        ).fetchall()
    finally:
        conn.close()
    return len(rows), {row[0] for row in rows if row[0]}


def _is_referenced(db_path: Path, collection: str) -> bool:
    conn = open_connection(db_path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM chunk_variants WHERE qdrant_collection=? LIMIT 1",
                (collection,),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def migrate_legacy_collection(
    client: Any,
    db_path: Path,
    kb_id: str,
    profile_name: str,
    model_normalized: str,
    *,
    drop_unreferenced: bool = False,
) -> LegacyMigration | None:
    """Move this profile's vectors out of a pre-0.24.0 collection, if one exists.

    Returns ``None`` when there is nothing to do: no legacy collection exists,
    or it exists but holds nothing this profile references and was left alone.

    ``drop_unreferenced`` deletes a legacy collection that no ``chunk_variants``
    row of any profile references even when it still holds points. That is
    right for ``local`` mode, where the collection can only ever have belonged
    to this project (typically: ``mrag reindex`` already rebuilt the index under
    the new name and the old collection is dead weight on disk). It is wrong for
    a shared Qdrant server, where the leftover points may belong to a
    knowledge base whose ID normalizes to the same slug — leave it False there.
    """
    legacy = legacy_collection_name(kb_id, profile_name, model_normalized)
    target = collection_name(kb_id, profile_name, model_normalized)
    existing = {c.name for c in client.get_collections().collections}
    if legacy not in existing:
        return None

    attributed_rows, live_ids = _rows_attributed_to(db_path, legacy, kb_id, profile_name)

    moved = 0
    if live_ids:
        vectors = client.get_collection(collection_name=legacy).config.params.vectors
        if not isinstance(vectors, VectorParams):
            raise ValueError(
                f"Qdrant collection '{legacy}' uses named vectors, which mrag never "
                "writes; refusing to migrate it. Remove it intentionally, or rebuild "
                "the profile with `mrag reindex`."
            )
        # Creates the target, or verifies an existing one (e.g. one that a
        # post-0.24.0 `mrag index` created for newly added documents) has the
        # same vector schema — mixing schemas would corrupt search.
        ensure_collection(client, target, vectors.size, vectors.distance)
        for batch in _batched(sorted(live_ids)):
            records = client.retrieve(
                collection_name=legacy, ids=batch, with_payload=True, with_vectors=True
            )
            if not records:
                continue
            client.upsert(
                collection_name=target,
                points=[
                    PointStruct(id=str(r.id), vector=r.vector, payload=r.payload or {})
                    for r in records
                ],
            )
            moved += len(records)

    if attributed_rows:
        with db_connection(db_path) as conn:
            conn.execute(
                "UPDATE chunk_variants SET qdrant_collection=? "
                "WHERE qdrant_collection=? AND knowledge_id=? AND profile_name=?",
                (target, legacy, kb_id, profile_name),
            )

    if live_ids:
        for batch in _batched(sorted(live_ids)):
            client.delete(collection_name=legacy, points_selector=PointIdsList(points=batch))

    orphaned = client.count(collection_name=legacy, exact=True).count
    dropped = False
    if not _is_referenced(db_path, legacy) and (orphaned == 0 or drop_unreferenced):
        client.delete_collection(collection_name=legacy)
        dropped = True

    if not moved and not dropped:
        return None
    return LegacyMigration(
        legacy=legacy, target=target, moved=moved, orphaned=orphaned, dropped=dropped
    )


def resolve_collection(
    client: Any,
    *,
    db_path: Path,
    config: ProjectConfig,
    profile_name: str,
    model_normalized: str,
    notify: Callable[[str], None] | None = None,
) -> str:
    """Return the collection name for this profile, migrating a legacy one first.

    Every path that reads or writes a profile's vectors goes through here so a
    project indexed before 0.24.0 keeps working after an upgrade without a
    reindex. ``notify`` receives one line describing a migration that ran.
    """
    report = migrate_legacy_collection(
        client,
        db_path,
        config.knowledge_id,
        profile_name,
        model_normalized,
        drop_unreferenced=config.qdrant.mode == "local",
    )
    if report is not None and notify is not None:
        notify(report.describe())
    return collection_name(config.knowledge_id, profile_name, model_normalized)


__all__ = ["LegacyMigration", "migrate_legacy_collection", "resolve_collection"]
