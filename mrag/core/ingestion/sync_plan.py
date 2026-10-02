"""Source-directory sync planning.

``mrag documents sync <DIR>`` compares two things that can each be read without
touching the other: what the catalog says lives under a directory, and what the
file system holds there now. This module is the comparison and nothing else —
no catalog, no file system, no clock — so the decision table in
``tests/fixtures/sync_transitions_golden.json`` can be checked against it
directly.

Two questions, asked in order:

1. :func:`resolve_sync_scope` — which documents are *under* the directory. For
   a directory inside the project this is a prefix test on the identity. For one
   outside it is the union of three sets: documents under a registered root
   that is the directory or one of its ancestors, documents under a root whose
   recorded ancestors include the directory, and documents under a root the
   caller found by walking the directory. The last set is what lets a root
   registered before ancestors were recorded still be resolved while its
   directory exists; only a root that recorded no ancestors *and* was not found
   is reported as unresolvable.
2. :func:`plan_sync` — what to do about each document and each scanned file,
   given which files exist.

"Missing" means exactly that the file does not exist: a file that exists but
fell outside the scan's filters is reported as out of scope and left alone, so
narrowing ``--exclude`` never excludes documents.

A move is only ever one-to-one. Content decides it: a document whose file is
gone and a scanned file with no document, holding the same bytes read the same
way. When several documents or several files share that content, any pairing
is a guess about which file used to be which, so such a key is not paired at
all and the items fall back to the add, duplicate and exclude rules with
``ambiguous_move`` on each.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from mrag.core.ingestion.source_identity import binding_status

ACTIONS = ("add", "update", "move", "exclude", "restore", "noop", "duplicate", "adopt_candidate", "blocked")
"""Every action a plan item can carry, in the order the shared table lists them."""

MUTATING_ACTIONS = frozenset({"add", "update", "move", "exclude", "restore"})

REASONS = (
    "content_changed",
    "never_ready",
    "file_missing",
    "matches_document",
    "matches_legacy_document",
    "conversion_not_requested",
    "ambiguous_move",
    "already_excluded_by_sync",
    "excluded_by_user",
)

ORIGIN_USER = "user"
ORIGIN_SYNC = "sync"


class SyncPlanError(ValueError):
    """A contradiction in the planner's input; carries a stable ``code``."""

    def __init__(self, code: str, message: str, **context: str) -> None:
        super().__init__(message)
        self.code = code
        self.context = context


@dataclass(frozen=True)
class SyncContent:
    """What decides whether two files are the same document content.

    mrag stores a source as the bytes it was given, so the converter is always
    ``native``; the field exists so the shared decision table, which also
    describes converted sources, is read whole.
    """

    content_hash: str
    converter: str = "native"
    converter_options: str = "{}"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.content_hash, self.converter, self.converter_options)


@dataclass(frozen=True)
class SyncExclusion:
    id: str
    profile_name: str | None
    origin: str


@dataclass
class SyncDocument:
    document_id: str
    identity: str
    content: SyncContent | None
    exclusions: list[SyncExclusion] = field(default_factory=list)

    def sync_exclusions(self) -> list[str]:
        return [rule.id for rule in self.exclusions if rule.origin == ORIGIN_SYNC]

    def has_user_exclusion_for_every_profile(self) -> bool:
        return any(rule.origin == ORIGIN_USER and rule.profile_name is None for rule in self.exclusions)


@dataclass(frozen=True)
class ScannedSource:
    identity: str
    content: SyncContent
    conversion_permitted: bool = True


@dataclass(frozen=True)
class SourceRootFacts:
    """One registered root as the planner needs it: its key and recorded ancestors."""

    key: str
    ancestor_keys: frozenset[str]


@dataclass(frozen=True)
class ProjectDirectory:
    """A directory inside the project; ``prefix`` is empty for the project root."""

    prefix: str


@dataclass(frozen=True)
class ExternalDirectory:
    """A directory outside the project.

    ``ancestor_roots`` maps the key of each registered root that is the
    directory or one of its ancestors to the directory's path relative to that
    root (empty when the root is the directory itself). ``roots_found_under``
    holds the keys of registered roots the caller found by walking the
    directory.
    """

    key: str
    ancestor_roots: dict[str, str]
    roots_found_under: frozenset[str]


@dataclass(frozen=True)
class SyncScope:
    documents: frozenset[str]
    unresolvable_roots: int


def _under_prefix(path: str, prefix: str) -> bool:
    if prefix == "":
        return True
    return path.startswith(prefix) and path[len(prefix):].startswith("/")


def _external_parts(identity: str) -> tuple[str, str] | None:
    if binding_status(identity) != "external_root":
        return None
    rest = identity[len("identities/external/"):]
    key, _, relative = rest.partition("/")
    return key, relative


def resolve_sync_scope(
    directory: ProjectDirectory | ExternalDirectory,
    documents: list[SyncDocument],
    roots: list[SourceRootFacts],
) -> SyncScope:
    """Decide which documents are under the directory."""
    if isinstance(directory, ProjectDirectory):
        return SyncScope(
            documents=frozenset(
                document.document_id
                for document in documents
                if binding_status(document.identity) == "project_relative"
                and _under_prefix(document.identity, directory.prefix)
            ),
            unresolvable_roots=0,
        )
    descendants = {root.key for root in roots if directory.key in root.ancestor_keys} | set(directory.roots_found_under)
    in_scope = set()
    for document in documents:
        parts = _external_parts(document.identity)
        if parts is None:
            continue
        root_key, relative = parts
        if root_key in descendants:
            in_scope.add(document.document_id)
            continue
        prefix = directory.ancestor_roots.get(root_key)
        if prefix is not None and _under_prefix(relative, prefix):
            in_scope.add(document.document_id)
    unresolvable = sum(
        1
        for root in roots
        if not root.ancestor_keys
        and root.key != directory.key
        and root.key not in directory.ancestor_roots
        and root.key not in directory.roots_found_under
    )
    return SyncScope(documents=frozenset(in_scope), unresolvable_roots=unresolvable)


@dataclass
class SyncPlanItem:
    action: str
    document_id: str | None
    source_identity: str
    previous_identity: str | None
    content: SyncContent | None
    reason: str | None
    revoke_exclusions: list[str] = field(default_factory=list)

    @property
    def mutates(self) -> bool:
        return self.action in MUTATING_ACTIONS


@dataclass
class SyncPlan:
    items: list[SyncPlanItem]
    out_of_scope: int
    unresolvable_roots: int

    def count(self, action: str) -> int:
        return sum(1 for item in self.items if item.action == action)

    def has_work(self) -> bool:
        return any(item.mutates for item in self.items)


def plan_sync(
    documents: list[SyncDocument],
    scope: SyncScope,
    scanned: list[ScannedSource],
    present_out_of_scope: list[str],
) -> SyncPlan:
    """Decide what to do about each document under the directory and each scanned file."""
    by_identity = {document.identity: document for document in documents}
    by_id = {document.document_id: document for document in documents}
    scanned_by_identity: dict[str, ScannedSource] = {}
    for source in scanned:
        if source.identity in scanned_by_identity:
            raise SyncPlanError(
                "sync_scan_identity_repeated",
                "the scan reported one identity twice",
                source_identity=source.identity,
            )
        scanned_by_identity[source.identity] = source
    out_of_scope = set(present_out_of_scope)
    for identity in out_of_scope:
        if identity in scanned_by_identity:
            raise SyncPlanError(
                "sync_scan_identity_both_scanned_and_out_of_scope",
                "the scan reported one identity as both scanned and out of scope",
                source_identity=identity,
            )
    in_scope = []
    for document_id in sorted(scope.documents):
        document = by_id.get(document_id)
        if document is None:
            raise SyncPlanError(
                "sync_scope_document_unknown",
                "the scope names a document the catalog does not hold",
                document_id=document_id,
            )
        in_scope.append(document)

    out_of_scope_count = sum(1 for document in in_scope if document.identity in out_of_scope)
    missing = [
        document
        for document in in_scope
        if document.identity not in scanned_by_identity and document.identity not in out_of_scope
    ]
    appeared = [source for source in scanned_by_identity.values() if source.identity not in by_identity]

    missing_by_content: dict[tuple, list[SyncDocument]] = defaultdict(list)
    for document in missing:
        if document.content is not None:
            missing_by_content[document.content.key].append(document)
    appeared_by_content: dict[tuple, list[ScannedSource]] = defaultdict(list)
    for source in appeared:
        appeared_by_content[source.content.key].append(source)

    items: list[SyncPlanItem] = []
    for key, sources in appeared_by_content.items():
        gone = missing_by_content.get(key)
        if gone is None or len(sources) != 1 or len(gone) != 1:
            continue
        source, document = sources[0], gone[0]
        items.append(SyncPlanItem(
            action="move",
            document_id=document.document_id,
            source_identity=source.identity,
            previous_identity=document.identity,
            content=source.content,
            reason=None,
            revoke_exclusions=document.sync_exclusions(),
        ))
    moved_sources = {item.source_identity for item in items}
    moved_documents = {item.document_id for item in items}

    held_by_content: dict[tuple, list[SyncDocument]] = defaultdict(list)
    for document in documents:
        if document.content is not None:
            held_by_content[document.content.key].append(document)
    for holders in held_by_content.values():
        holders.sort(key=lambda document: document.document_id)

    for source in appeared:
        if source.identity in moved_sources:
            continue
        key = source.content.key
        items.append(_decide_appeared(source, held_by_content.get(key, []), key in missing_by_content))

    for source in scanned_by_identity.values():
        document = by_identity.get(source.identity)
        if document is not None:
            items.append(_decide_found(source, document))

    for document in missing:
        if document.document_id in moved_documents:
            continue
        ambiguous = document.content is not None and document.content.key in appeared_by_content
        items.append(_decide_missing(document, ambiguous))

    items.sort(key=lambda item: (item.source_identity, item.document_id or ""))
    return SyncPlan(items=items, out_of_scope=out_of_scope_count, unresolvable_roots=scope.unresolvable_roots)


def _decide_appeared(source: ScannedSource, holders: list[SyncDocument], ambiguous: bool) -> SyncPlanItem:
    ambiguous_reason = "ambiguous_move" if ambiguous else None
    bound = next((d for d in holders if binding_status(d.identity) != "legacy_unbound"), None)
    legacy = next((d for d in holders if binding_status(d.identity) == "legacy_unbound"), None)
    if bound is not None:
        action, document_id, reason = "duplicate", bound.document_id, ambiguous_reason or "matches_document"
    elif legacy is not None:
        action, document_id, reason = "adopt_candidate", legacy.document_id, "matches_legacy_document"
    elif source.conversion_permitted:
        action, document_id, reason = "add", None, ambiguous_reason
    else:
        action, document_id, reason = "blocked", None, "conversion_not_requested"
    return SyncPlanItem(
        action=action,
        document_id=document_id,
        source_identity=source.identity,
        previous_identity=None,
        content=source.content,
        reason=reason,
    )


def _decide_found(source: ScannedSource, document: SyncDocument) -> SyncPlanItem:
    sync_exclusions = document.sync_exclusions()
    if document.content is not None and document.content == source.content:
        if sync_exclusions:
            action, reason, revoke = "restore", None, sync_exclusions
        else:
            action, reason, revoke = "noop", None, []
    else:
        changed = "content_changed" if document.content is not None else "never_ready"
        if source.conversion_permitted:
            action, reason, revoke = "update", changed, sync_exclusions
        else:
            action, reason, revoke = "blocked", "conversion_not_requested", []
    return SyncPlanItem(
        action=action,
        document_id=document.document_id,
        source_identity=source.identity,
        previous_identity=None,
        content=source.content,
        reason=reason,
        revoke_exclusions=revoke,
    )


def _decide_missing(document: SyncDocument, ambiguous: bool) -> SyncPlanItem:
    if document.sync_exclusions():
        action, reason = "noop", "already_excluded_by_sync"
    elif document.has_user_exclusion_for_every_profile():
        action, reason = "noop", "excluded_by_user"
    else:
        action, reason = "exclude", ("ambiguous_move" if ambiguous else "file_missing")
    return SyncPlanItem(
        action=action,
        document_id=document.document_id,
        source_identity=document.identity,
        previous_identity=None,
        content=document.content,
        reason=reason,
    )
