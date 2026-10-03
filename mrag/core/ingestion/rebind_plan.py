"""Binding one document to one file by hand: the decision behind ``mrag documents rebind``.

The sync pairs a document with a new path only when the evidence is
one-to-one; every other binding is the operator's to make, and this module
decides what that binding means for the catalog without touching it.
"""

from __future__ import annotations

from dataclasses import dataclass

from mrag.core.ingestion.sync_plan import ORIGIN_SYNC, SyncContent, SyncDocument

ACTIONS = ("rebind", "update", "restore", "noop", "blocked")
MUTATING_ACTIONS = frozenset({"rebind", "update", "restore"})


class RebindRefused(ValueError):
    """The binding would leave the catalog saying something false; carries a stable ``code``."""

    def __init__(self, code: str, message: str, *, document_id: str | None = None, held_by: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.document_id = document_id
        self.held_by = held_by


@dataclass
class RebindPlan:
    action: str
    document_id: str
    previous_identity: str
    source_identity: str
    content: SyncContent
    new_revision: bool
    revoke_exclusions: list[str]

    @property
    def moves_identity(self) -> bool:
        return self.previous_identity != self.source_identity

    @property
    def mutates(self) -> bool:
        return self.action in MUTATING_ACTIONS


def plan_rebind(
    documents: list[SyncDocument],
    document_id: str,
    target: str,
    content: SyncContent,
    conversion_permitted: bool = True,
) -> RebindPlan:
    """Decide how ``document_id`` becomes bound to the file at ``target`` holding ``content``."""
    document = next((d for d in documents if d.document_id == document_id), None)
    if document is None:
        raise RebindRefused("document_not_found", "the requested document does not exist", document_id=document_id)
    holder = next((d for d in documents if d.document_id != document_id and d.identity == target), None)
    if holder is not None:
        raise RebindRefused(
            "rebind_target_taken",
            "another document already answers for this file; remove or rebind it first",
            document_id=document_id,
            held_by=holder.document_id,
        )
    new_revision = document.content != content
    if new_revision:
        holder = next((d for d in documents if d.document_id != document_id and d.content == content), None)
        if holder is not None:
            raise RebindRefused(
                "rebind_content_held_by_other_document",
                "another document already holds this file's content; rebind that document instead",
                document_id=document_id,
                held_by=holder.document_id,
            )
    revoke = [rule.id for rule in document.exclusions if rule.origin == ORIGIN_SYNC]
    moves = document.identity != target
    if new_revision and not conversion_permitted:
        action = "blocked"
    elif moves:
        action = "rebind"
    elif new_revision:
        action = "update"
    elif revoke:
        action = "restore"
    else:
        action = "noop"
    return RebindPlan(
        action=action,
        document_id=document.document_id,
        previous_identity=document.identity,
        source_identity=target,
        content=content,
        new_revision=new_revision,
        revoke_exclusions=revoke,
    )
