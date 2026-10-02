"""One document-list contract for the native API and MCP.

The rows, their statuses, their visibility rules and their order are decided
here once for `GET /api/v1/documents` and the MCP `list_documents` tool, and
both surfaces answer through this module, so a document cannot be `indexed` on
one and `stale` on the other.

Every status is derived for one selected profile:

* ``source_status`` — does the catalog hold a complete extraction?
* ``index_status`` — what does that profile's index record say, compared with
  the document and the profile as they are now?
* ``retrieval_status`` — is an exclusion in force for that profile?

and folded into ``aggregate_status`` by one priority order. The row's
``status`` keeps the stored extraction value (``pending``/``extracted``/
``error``), as ``GET /documents/{id}`` and earlier releases report it; the
derived state is in the three statuses and ``aggregate_status``.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mrag.core.ingestion.source_identity import (
    SCHEME_KEY,
    SCHEME_UNRECORDED,
    SCHEME_VERSION,
    binding_status,
    display_name,
    legacy_identity,
    read_scheme_one,
)


def _recorded_scheme(conn: sqlite3.Connection) -> int:
    has_settings = conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_settings'").fetchone()
    row = (conn.execute("SELECT value FROM catalog_settings WHERE key = ?", (SCHEME_KEY,)).fetchone()
           if has_settings else None)
    return int(row[0]) if row else SCHEME_UNRECORDED


def _read_identity(scheme: int, stored: str | None, document_id: str, labels: dict[str, str]) -> tuple[str, str, str]:
    """Return (listed identity, binding, display name) for one stored value.

    A catalog nobody has migrated is read through the rule the explicit
    migration applies, so what a listing shows before `mrag catalog
    migrate-identities` is what the migration makes true. The listed identity is
    what is stored; the binding and the name are what it is read as.
    """
    if stored is None:
        # Written by a release from before source identities, after the
        # catalog was migrated: an unrecoverable path, named as one.
        identity = legacy_identity(document_id)
        return identity, "legacy_unbound", identity
    if scheme == SCHEME_VERSION:
        return stored, binding_status(stored), display_name(stored, labels)
    if scheme == SCHEME_UNRECORDED:
        reading = read_scheme_one(stored, document_id, set(labels))
        if reading.identity is None:
            return stored, reading.binding, stored
        return stored, reading.binding, display_name(reading.identity, labels)
    raise ValueError(
        f"This project's source identities follow scheme {scheme}, which this mrag "
        f"(scheme {SCHEME_VERSION}) cannot read; use the mrag release that created it."
    )


# Aggregate statuses in priority order: the first that applies wins, and a
# filter echoes its statuses in this order.
AGGREGATE_STATUSES = ("excluded", "error", "pending", "indexing", "stale", "fallback", "indexed", "ready")
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 500
_ACCEPTED_PARAMETERS = "profile, all, status, limit, offset"


class InventoryError(ValueError):
    """A listing that cannot be answered, with the code and HTTP status it maps to."""

    def __init__(self, code: str, message: str, *, kind: str = "validation", http_status: int = 400,
                 context: dict[str, str] | None = None) -> None:
        detail = "".join(f" ({key}={value})" for key, value in (context or {}).items())
        super().__init__(f"{code}: {message}{detail}")
        self.code = code
        self.kind = kind
        self.http_status = http_status
        self.public_message = message
        self.context = context or {}

    def envelope(self) -> dict:
        return {
            "schema_version": 1,
            "status": "error",
            "error": {"code": self.code, "kind": self.kind, "message": self.public_message, "retryable": False},
        }


@dataclass
class InventoryQuery:
    profile: str | None = None
    include_all: bool = False
    statuses: set[str] = field(default_factory=set)
    limit: int = DEFAULT_PAGE_LIMIT
    offset: int = 0


def _unknown_status() -> InventoryError:
    return InventoryError(
        "document_status_unknown", "no document status has this name",
        context={"accepted": ", ".join(AGGREGATE_STATUSES)},
    )


def _page_error(parameter: str, value: object) -> InventoryError:
    return InventoryError(
        "documents_page_invalid", "the requested page cannot be served",
        context={"parameter": parameter, "value": str(value), "limit_maximum": str(MAX_PAGE_LIMIT)},
    )


def parse_query(items: list[tuple[str, str]]) -> InventoryQuery:
    """Parse query parameters strictly: an ignored parameter would look like an answer."""
    query = InventoryQuery()
    for key, value in items:
        if key == "profile":
            query.profile = value
        elif key == "all":
            if value not in ("true", "false"):
                raise InventoryError(
                    "documents_query_invalid", "the `all` parameter accepts only `true` or `false`",
                    context={"all": value},
                )
            query.include_all = value == "true"
        elif key == "status":
            query.statuses |= parse_statuses([value])
        elif key == "limit":
            query.limit = validate_limit(value)
        elif key == "offset":
            query.offset = validate_offset(value)
        else:
            raise InventoryError(
                "documents_query_unknown_parameter", "this endpoint does not accept that query parameter",
                context={"parameter": key, "accepted": _ACCEPTED_PARAMETERS},
            )
    return query


def parse_statuses(values: list[str] | None) -> set[str]:
    statuses = set(values or [])
    if statuses - set(AGGREGATE_STATUSES):
        raise _unknown_status()
    return statuses


def validate_limit(value: object) -> int:
    try:
        limit = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _page_error("limit", value) from None
    if isinstance(value, bool) or limit < 1 or limit > MAX_PAGE_LIMIT or str(limit) != str(value).strip():
        raise _page_error("limit", value)
    return limit


def validate_offset(value: object) -> int:
    try:
        offset = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _page_error("offset", value) from None
    if isinstance(value, bool) or offset < 0 or str(offset) != str(value).strip():
        raise _page_error("offset", value)
    return offset


def _current_profile_hash(project_dir: Path, profile_name: str) -> str:
    """The index identity the selected profile would build today.

    Every `stale` answer is a comparison against this, so failing to compute it
    is an error rather than a guess: listing on regardless would report every
    indexed document as stale — not a missing value, a made-up answer.
    """
    from mrag.config.profile import load_profile, validate_effective_tokenizer
    from mrag.config.project import load_project_config
    from mrag.core.indexing.pipeline import _load_context_prompt
    from mrag.core.indexing.context_prompt_template import DEFAULT_CONTEXT_PROMPT_TEMPLATE

    config = load_project_config(project_dir)
    profile = load_profile(profile_name, project_dir)
    tokenizer = validate_effective_tokenizer(profile, config.fts_tokenizer)
    prompt = (_load_context_prompt(project_dir) if profile.augmentation.strategy == "contextual"
              else DEFAULT_CONTEXT_PROMPT_TEMPLATE)
    return profile.compute_hash(context_prompt=prompt, effective_tokenizer=tokenizer)


def resolve_profile(project_dir: Path, requested: str | None, default: str) -> str:
    """The profile a listing answers for; an unknown one names the ones that exist."""
    name = requested or default
    profiles_dir = project_dir / "profiles"
    if not (profiles_dir / f"{name}.yaml").is_file():
        candidates = sorted(path.stem for path in profiles_dir.glob("*.yaml")) if profiles_dir.is_dir() else []
        raise InventoryError(
            "profile_not_found", "the project does not store this profile",
            kind="config", http_status=404,
            context={"profile": name, "candidates": ", ".join(candidates)},
        )
    return name


def _aggregate(source: str, index: str, retrieval: str) -> str:
    if retrieval == "excluded":
        return "excluded"
    if source == "error" or index == "error":
        return "error"
    if source == "building" or index == "pending":
        return "pending"
    return {"indexing": "indexing", "stale": "stale", "fallback": "fallback", "indexed": "indexed"}.get(index, "ready")


def document_inventory(
    conn: sqlite3.Connection,
    project_dir: Path,
    profile_name: str,
    *,
    include_all: bool = False,
    statuses: set[str] | None = None,
) -> dict:
    """Derive every row for one profile: visibility first, then the status filter.

    ``total`` counts what the view could show and ``returned`` what the filter
    kept, so an empty project and a filter that matched nothing stay apart.
    """
    statuses = statuses or set()
    current_hash = _current_profile_hash(project_dir, profile_name)
    has_roots = conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_roots'").fetchone()
    labels = ({r["root_key"]: r["label"] for r in conn.execute("SELECT root_key, label FROM source_roots")}
              if has_roots else {})
    scheme = _recorded_scheme(conn)
    index_columns = {row[1] for row in conn.execute("PRAGMA table_info(document_indexes)")}
    name_column = "indexed_display_name" if "indexed_display_name" in index_columns else "NULL AS indexed_display_name"
    indexes = {
        r["document_id"]: r for r in conn.execute(
            "SELECT document_id, status, document_file_hash, extracted_hash, profile_hash, "
            f"{name_column} FROM document_indexes WHERE profile_name = ?",
            (profile_name,),
        )
    }
    fallback_documents = {
        r["document_id"] for r in conn.execute(
            "SELECT DISTINCT document_id FROM chunk_variants WHERE profile_name = ? "
            "AND json_valid(metadata_json) AND ("
            "json_extract(metadata_json, '$.augmentation_status') = 'fallback_raw' OR "
            "json_extract(metadata_json, '$.embedding_status') = 'fallback_no_vector')",
            (profile_name,),
        )
    }
    exclusions: dict[str, str] = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_exclusions'").fetchone():
        # A rule scoped to this profile is the most specific explanation, so it
        # is the one reported when an all-profile rule is also in force.
        for rule in conn.execute(
            "SELECT id, document_id, profile_name FROM document_exclusions "
            "WHERE revoked_at IS NULL AND (profile_name IS NULL OR profile_name = ?) "
            "ORDER BY (profile_name IS NULL) DESC, created_at, id",
            (profile_name,),
        ):
            exclusions[rule["document_id"]] = rule["id"]

    total = 0
    rows = []
    for row in conn.execute("SELECT * FROM documents").fetchall():
        ready = row["status"] == "extracted"
        if not include_all and not ready:
            continue
        total += 1
        document_id = row["id"]
        stored = row["source_identity"] if "source_identity" in row.keys() else None
        identity, binding, name = _read_identity(scheme, stored, document_id, labels)
        source_status = {"pending": "building", "extracted": "ready", "error": "error"}[row["status"]]
        record = indexes.get(document_id)
        if record is None:
            index_status = "not_indexed"
        elif record["status"] in ("pending", "indexing", "error"):
            index_status = record["status"]
        elif (record["document_file_hash"] != row["file_hash"]
              or record["extracted_hash"] != row["extracted_hash"]
              or record["profile_hash"] != current_hash
              # Every chunk carries the document's name, so a renamed file is
              # stale too; a row from before names were recorded is not judged.
              or (record["indexed_display_name"] is not None
                  and record["indexed_display_name"] != name)):
            # Decided by comparison only: asking whether a rebuild would change
            # anything must not become a rebuild.
            index_status = "stale"
        elif document_id in fallback_documents:
            index_status = "fallback"
        else:
            index_status = "indexed"
        exclusion_id = exclusions.get(document_id)
        retrieval_status = "excluded" if exclusion_id else "eligible"
        aggregate = _aggregate(source_status, index_status, retrieval_status)
        if statuses and aggregate not in statuses:
            continue
        rows.append({
            "document_id": document_id,
            "display_name": name,
            "source_identity": identity,
            "source_binding_status": binding,
            "content_hash": row["file_hash"] if ready else None,
            # The stored extraction status, as GET /documents/{id} and earlier
            # releases report it; the aggregate has its own field.
            "status": row["status"],
            "aggregate_status": aggregate,
            "source_status": source_status,
            "index_status": index_status,
            "retrieval_status": retrieval_status,
            "profile": profile_name,
            "exclusion_id": exclusion_id,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            # OSS records no ingestion timing; null is "not measured", not zero.
            "ingest_ms": None,
            # Additive compatibility with the pre-1.2 OSS list row.
            "id": document_id,
            "filename": row["filename"],
            "file_hash": row["file_hash"],
            "source_type": row["source_type"],
        })
    rows.sort(key=lambda item: (item["source_identity"], item["document_id"]))
    return {"profile": profile_name, "total": total, "rows": rows}


def current_display_name(conn: sqlite3.Connection, document_id: str) -> str | None:
    """The name a document displays under now, as the listing derives it.

    Recorded by ``mrag index`` beside each index row, so the listing can tell a
    renamed file from one indexed under its current name.
    """
    row = conn.execute("SELECT id, source_identity FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        return None
    has_roots = conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_roots'").fetchone()
    labels = ({r["root_key"]: r["label"] for r in conn.execute("SELECT root_key, label FROM source_roots")}
              if has_roots else {})
    stored = row["source_identity"] if "source_identity" in row.keys() else None
    _identity, _binding, name = _read_identity(_recorded_scheme(conn), stored, document_id, labels)
    return name


def list_envelope(inventory: dict, query: InventoryQuery) -> dict:
    """The listing envelope the native API and MCP answer with."""
    rows = inventory["rows"]
    start = min(query.offset, len(rows))
    page = rows[start:start + query.limit]
    following = start + len(page)
    return {
        "schema_version": 1,
        "status": "ok",
        "profile": inventory["profile"],
        "filter": {
            "all": query.include_all,
            "statuses": [status for status in AGGREGATE_STATUSES if status in query.statuses],
        },
        "total": inventory["total"],
        "returned": len(rows),
        # An offset past the end is an empty page, not an error: a listing that
        # shrank between two requests is ordinary.
        "page": {
            "limit": query.limit,
            "offset": query.offset,
            "count": len(page),
            "next_offset": following if following < len(rows) else None,
        },
        "documents": page,
    }
