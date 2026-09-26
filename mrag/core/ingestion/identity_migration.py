"""The explicit source-identity scheme migration (scheme 1 -> 2).

Scheme 1 spelled an external source ``external/<key>/<path>`` and an
unrecoverable one ``legacy/v1/<id>``, beside bare project paths, so a project
directory called ``external/`` or ``legacy/`` produced values nothing could
tell apart. Scheme 2 moves both under the reserved ``identities/`` namespace.
Converting a catalog that already holds identities is this module's job and
nobody else's: an ordinary open or ``add`` never recomputes a stored identity.

Every value is read through ``read_scheme_one``, the rule a listing of an
unmigrated catalog also uses, so what the listing shows before the migration is
what the migration makes true. The same command exists in MRAG Plus under the
same name, with the same plan, blockers and report.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from mrag.core.ingestion.source_identity import (
    SCHEME_KEY,
    SCHEME_UNRECORDED,
    SCHEME_VERSION,
    read_scheme_one,
)


@dataclass(frozen=True)
class Respelling:
    document_id: str
    before: str | None  # None for a row written without an identity
    after: str
    binding: str


@dataclass(frozen=True)
class Blocker:
    document_id: str
    source_identity: str
    reason: str  # reserved_path | identity_collision | identity_unreadable
    other_document_id: str | None = None
    detail: str | None = None


@dataclass
class MigrationPlan:
    recorded_scheme: int
    target_scheme: int = SCHEME_VERSION
    respellings: list[Respelling] = field(default_factory=list)
    unchanged: int = 0
    blockers: list[Blocker] = field(default_factory=list)

    @property
    def is_current(self) -> bool:
        return self.recorded_scheme == self.target_scheme

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)


class SchemeNotMigratableError(ValueError):
    """The catalog records a scheme this build cannot migrate from."""


def _has_column(conn: sqlite3.Connection) -> bool:
    return "source_identity" in {row[1] for row in conn.execute("PRAGMA table_info(documents)")}


def _recorded(conn: sqlite3.Connection) -> int:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_settings'").fetchone():
        return SCHEME_UNRECORDED
    row = conn.execute("SELECT value FROM catalog_settings WHERE key = ?", (SCHEME_KEY,)).fetchone()
    return int(row[0]) if row else SCHEME_UNRECORDED


def plan_migration(conn: sqlite3.Connection) -> MigrationPlan:
    """Decide the migration from the catalog, writing nothing."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone():
        return MigrationPlan(recorded_scheme=SCHEME_VERSION)
    if not _has_column(conn):
        # A catalog from before source identities: the next write assigns
        # scheme-2 identities directly, so there is nothing for this to do.
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        return MigrationPlan(recorded_scheme=SCHEME_VERSION, unchanged=count)
    documents = conn.execute("SELECT id, source_identity FROM documents").fetchall()
    recorded = _recorded(conn)
    if recorded == SCHEME_VERSION or not documents:
        return MigrationPlan(recorded_scheme=SCHEME_VERSION, unchanged=len(documents))
    if recorded != SCHEME_UNRECORDED:
        raise SchemeNotMigratableError(
            f"This project's source identities follow scheme {recorded}, which this mrag "
            f"(scheme {SCHEME_VERSION}) cannot migrate from; use the mrag release that created it."
        )
    roots = {row[0] for row in conn.execute("SELECT root_key FROM source_roots")}
    return plan_from_scheme_one([(row[0], row[1]) for row in documents], roots)


def plan_from_scheme_one(documents: list[tuple[str, str | None]], roots: set[str]) -> MigrationPlan:
    """Decide a scheme-1 -> scheme-2 migration from raw catalog facts."""
    plan = MigrationPlan(recorded_scheme=SCHEME_UNRECORDED)
    claimed: dict[str, str] = {}
    # The order a person reads the report in, and the order collisions are
    # attributed in: the first document to claim an identity keeps it.
    for document_id, stored in sorted(documents, key=lambda row: (row[1] or "", row[0])):
        # A row without an identity was written by a release from before source
        # identities after this catalog was migrated; its path is unrecoverable,
        # so it reads as the legacy identity its ID gives it.
        stored_value = stored if stored is not None else f"legacy/v1/{document_id}"
        try:
            reading = read_scheme_one(stored_value, document_id, roots)
        except ValueError as error:
            plan.blockers.append(Blocker(document_id, stored_value, "identity_unreadable", detail=str(error)))
            continue
        if reading.reading == "reserved":
            plan.blockers.append(Blocker(document_id, stored_value, "reserved_path"))
            continue
        target = reading.identity
        if target in claimed:
            plan.blockers.append(
                Blocker(document_id, stored_value, "identity_collision", other_document_id=claimed[target])
            )
            continue
        claimed[target] = document_id
        if reading.reading == "respelled" or stored is None:
            plan.respellings.append(Respelling(document_id, stored, target, reading.binding))
        else:
            plan.unchanged += 1
    return plan


def apply_migration(conn: sqlite3.Connection) -> MigrationPlan:
    """Plan and apply in one immediate transaction; a blocked plan changes nothing.

    The plan is read inside the transaction it is applied in, so nothing can
    change between what was decided and what is written.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        plan = plan_migration(conn)
        if plan.is_blocked or plan.is_current:
            conn.rollback()
            return plan
        for change in plan.respellings:
            # Conditional on the value the plan read; `IS` also matches NULL.
            cursor = conn.execute(
                "UPDATE documents SET source_identity = ? WHERE id = ? AND source_identity IS ?",
                (change.after, change.document_id, change.before),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Document {change.document_id} changed during the migration")
        conn.execute(
            "INSERT INTO catalog_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEME_KEY, str(SCHEME_VERSION)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return plan
