"""`mrag inspect` subcommand group.

Subcommands:
  document <document_id>          Show extraction + per-profile chunk summary
  chunks   <document_id>          List chunks with metadata (paged, opt-in content/context)
  chunk    <chunk_id>             Show a single chunk (content + context always)
  sections <document_id>          Visualize heading hierarchy or parent/child layered view

All commands support `--json` for AI-agent consumption.
Stdout in --json mode contains only the JSON payload; warnings/errors go to stderr.

See: dev_docs/01_EXTENSION_STAGE_1/DESIGN_V18_INSPECT_REGISTRY.md §3.1
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from mrag.db.connection import find_db, open_connection
from mrag.db.inspect_queries import (
    ChunkRow,
    DocumentSummary,
    count_chunks,
    fetch_chunk_single,
    fetch_chunks,
    fetch_document,
    fetch_document_summary,
    fetch_sections,
    list_profiles_for_document,
)


console = Console()
err_console = Console(stderr=True)


def _pluralize(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or f"{singular}s")


def _opt(v: Any) -> str:
    """Render an optional value: '-' if None/empty, str(v) otherwise."""
    if v is None or v == "":
        return "-"
    return str(v)


inspect_app = typer.Typer(
    name="inspect",
    help="Inspect documents, chunks, and sections in the local mrag knowledge base.",
    no_args_is_help=True,
)


# ===========================================================================
# Shared helpers
# ===========================================================================


def _open_project_db() -> "sqlite3.Connection":  # noqa: F821
    """Open a read-only-ish connection to the current project's SQLite DB.

    Raises typer.Exit(1) with a user-facing error if cwd is not an mrag project.
    """
    try:
        db_path = find_db(Path.cwd())
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    return open_connection(db_path)


def _resolve_profile_or_exit(
    conn,
    document_id: str,
    requested: Optional[str],
) -> str:
    """Determine which profile to use for a per-profile inspect command.

    - if `requested` is given, return it
    - else if document has exactly 1 indexed profile, auto-select it
    - else exit 1 with a candidate list (DESIGN_V18 §3.1.2 / Q#4)
    """
    if requested is not None:
        return requested

    profiles = list_profiles_for_document(conn, document_id)
    if not profiles:
        err_console.print(
            f"[red]Error:[/red] document '{document_id}' has no indexed chunks."
        )
        raise typer.Exit(1)
    if len(profiles) == 1:
        return profiles[0]

    err_console.print(
        f"[red]Error:[/red] document '{document_id}' is indexed under multiple "
        f"profiles. Specify --profile to disambiguate. Available: "
        f"{', '.join(profiles)}"
    )
    raise typer.Exit(1)


# ===========================================================================
# inspect document
# ===========================================================================


def _build_document_payload(summary: DocumentSummary) -> dict[str, Any]:
    return {
        "document": {
            "id": summary.document.id,
            "filename": summary.document.filename,
            "source_type": summary.document.source_type,
            "extraction_provider": summary.document.extraction_provider,
            "status": summary.document.status,
            "extracted_hash": summary.document.extracted_hash,
            "created_at": summary.document.created_at,
            "updated_at": summary.document.updated_at,
        },
        "profiles": [
            {
                "name": p.profile_name,
                "status": p.status,
                "indexed_at": p.indexed_at,
                "chunk_counts": {
                    "chunk": p.chunk_counts.chunk,
                    "parent": p.chunk_counts.parent,
                    "child": p.chunk_counts.child,
                    "total": p.chunk_counts.total,
                },
                "augmentation": {
                    "succeeded": p.augmentation.succeeded,
                    "raw_fallback": p.augmentation.raw_fallback,
                },
                "embedding": {
                    "embedded": p.embedding.embedded,
                    "fallback_no_vector": p.embedding.fallback_no_vector,
                },
            }
            for p in summary.profiles
        ],
    }


def _render_document_human(summary: DocumentSummary) -> None:
    d = summary.document
    console.print(f"Document: [cyan]{d.id}[/cyan]")
    # Long values (Japanese filenames, SHA256 hashes) get soft_wrap so rich
    # doesn't break them mid-token at the console width.
    console.print(f"  filename             : {d.filename}", soft_wrap=True)
    console.print(f"  source_type          : {d.source_type}")
    console.print(f"  extraction_provider  : {_opt(d.extraction_provider)}")
    console.print(f"  status               : {d.status}")
    console.print(
        f"  extracted_hash       : {_opt(d.extracted_hash)}",
        soft_wrap=True,
    )
    console.print()
    if not summary.profiles:
        console.print("[yellow]No profiles have indexed this document.[/yellow]")
        return

    table = Table(title="Indexed Profiles", title_justify="left", show_edge=False)
    table.add_column("profile", style="bold")
    table.add_column("status")
    table.add_column("indexed_at")
    table.add_column("chunks", justify="right")
    for p in summary.profiles:
        counts = p.chunk_counts
        if counts.parent or counts.child:
            count_str = f"{counts.parent} parent + {counts.child} child"
        else:
            count_str = str(counts.chunk)
        table.add_row(
            p.profile_name,
            p.status,
            p.indexed_at or "-",
            count_str,
        )
    console.print(table)

    for p in summary.profiles:
        if p.augmentation.succeeded == 0 and p.augmentation.raw_fallback == 0:
            continue
        console.print()
        console.print(f"Augmentation Status ({p.profile_name}):")
        console.print(
            f"  contextualized successfully : {p.augmentation.succeeded}"
        )
        console.print(
            f"  raw_fallback                : {p.augmentation.raw_fallback}"
        )

    # v0.21.0: Embedding Status — only render when at least one chunk has a
    # fallback_no_vector entry. Successful-only profiles produce no section
    # (mirrors the Augmentation Status omission rule).
    for p in summary.profiles:
        if p.embedding.fallback_no_vector == 0:
            continue
        console.print()
        console.print(f"Embedding Status ({p.profile_name}):")
        console.print(
            f"  embedded            : {p.embedding.embedded}"
        )
        console.print(
            f"  fallback_no_vector  : {p.embedding.fallback_no_vector}"
        )


@inspect_app.command("document")
def inspect_document(
    document_id: str = typer.Argument(..., help="Document ID to inspect"),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Limit summary to this profile (default: all indexed profiles).",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a single JSON object to stdout (warnings/errors go to stderr).",
    ),
) -> None:
    """Show extraction info and per-profile chunk summary for a document."""
    conn = _open_project_db()
    try:
        summary = fetch_document_summary(conn, document_id, profile_name=profile)
    finally:
        conn.close()

    if summary is None:
        err_console.print(
            f"[red]Error:[/red] document_id '{document_id}' not found"
        )
        raise typer.Exit(1)

    if json_output:
        print(json.dumps(_build_document_payload(summary), indent=2, ensure_ascii=False))
    else:
        _render_document_human(summary)


# ===========================================================================
# inspect chunks (multiple)
# ===========================================================================


def _build_chunk_entry(
    row: ChunkRow,
    *,
    include_content: bool,
    include_context: bool,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "chunk_id": row.chunk_id,
        "chunk_type": row.chunk_type,
        "chunk_index": row.chunk_index,
        "parent_chunk_id": row.parent_chunk_id,
        "char_count": row.char_count,
        "token_count": row.token_count,
        "source_format": row.source_format,
        "metadata": row.metadata,
        "variant": {
            "type": row.variant.variant_type,
            "qdrant_collection": row.variant.qdrant_collection,
            "augmentation_status": row.variant.augmentation_status,
            "embedding_status": row.variant.embedding_status,
            "has_qdrant_point": row.variant.has_qdrant_point,
        },
    }
    if include_content:
        entry["content"] = row.content
    if include_context:
        entry["context_text"] = row.variant.context_text
    return entry


def _build_chunks_payload(
    document_id: str,
    profile_name: str,
    total: int,
    limit: Optional[int],
    offset: int,
    rows: list[ChunkRow],
    *,
    include_content: bool,
    include_context: bool,
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "profile": profile_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "chunks": [
            _build_chunk_entry(
                r,
                include_content=include_content,
                include_context=include_context,
            )
            for r in rows
        ],
    }


def _render_chunks_human(
    document_id: str,
    profile_name: str,
    total: int,
    limit: Optional[int],
    offset: int,
    rows: list[ChunkRow],
    *,
    include_content: bool,
    include_context: bool,
) -> None:
    if limit is None and offset == 0:
        range_str = f"showing all {total}"
    else:
        first = offset + 1 if rows else 0
        last = offset + len(rows)
        range_str = f"showing {first}-{last} of {total}"
    console.print(
        f"Document: [cyan]{document_id}[/cyan] / Profile: [cyan]{profile_name}[/cyan] "
        f"/ total chunks: {total} ({range_str})"
    )
    console.print()
    for i, r in enumerate(rows, start=offset + 1):
        meta = r.metadata or {}
        # markup=False so literal `[N]`, list reprs, etc. are not parsed as
        # rich style tags. soft_wrap=True keeps long lines from being broken
        # mid-token by the console width.
        console.print(
            f"[{i}] chunk_id={r.chunk_id}  type={r.chunk_type}  "
            f"chars={_opt(r.char_count)}  tokens={_opt(r.token_count)}",
            markup=False, soft_wrap=True,
        )
        if "heading_path" in meta and meta["heading_path"]:
            console.print(
                f"    heading_path: {' > '.join(str(s) for s in meta['heading_path'])}",
                soft_wrap=True,
            )
        if "block_types" in meta:
            types = ", ".join(str(t) for t in meta["block_types"])
            console.print(f"    block_types : {types}", markup=False, soft_wrap=True)
        if meta.get("contains_table"):
            cols = meta.get("table_columns")
            cnt = meta.get("table_count")
            if cols:
                col_str = ", ".join(str(c) for c in cols)
                extra = f" ({cnt} table(s), columns: {col_str})"
            else:
                extra = ""
            console.print(
                f"    contains_table: true{extra}",
                markup=False, soft_wrap=True,
            )
        if meta.get("contains_code"):
            lang = meta.get("language")
            extra = f" (language={lang})" if lang else ""
            console.print(
                f"    contains_code: true{extra}", markup=False, soft_wrap=True,
            )
        if r.variant.augmentation_status:
            console.print(
                f"    augmentation_status: {r.variant.augmentation_status}"
            )
        if r.variant.embedding_status:
            console.print(
                f"    embedding_status   : [yellow]{r.variant.embedding_status}[/yellow]"
            )
        if include_context:
            ctx = r.variant.context_text
            console.print(
                f"    context_text: {ctx if ctx else '(none)'}", soft_wrap=True,
            )
        if include_content and r.content is not None:
            console.print("    content:")
            for line in r.content.splitlines() or [r.content]:
                console.print(f"      {line}", soft_wrap=True)
        console.print()


@inspect_app.command("chunks")
def inspect_chunks(
    document_id: str = typer.Argument(..., help="Document ID to inspect"),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Profile name (required when multiple profiles index this document).",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit",
        help="Maximum number of chunks to return (default: all).",
    ),
    offset: int = typer.Option(
        0, "--offset",
        help="Number of chunks to skip from the start.",
    ),
    show_content: bool = typer.Option(
        False, "--show-content",
        help="Include full chunk body (chunks.content) in the output.",
    ),
    show_context: bool = typer.Option(
        False, "--show-context",
        help="Include LLM-generated context_text in the output.",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a single JSON object to stdout (warnings/errors go to stderr).",
    ),
) -> None:
    """List chunks for a document under a given profile."""
    conn = _open_project_db()
    try:
        # 1) Verify the document exists
        if fetch_document(conn, document_id) is None:
            err_console.print(
                f"[red]Error:[/red] document_id '{document_id}' not found"
            )
            raise typer.Exit(1)

        # 2) Resolve profile (auto-select or exit on ambiguity)
        profile_name = _resolve_profile_or_exit(conn, document_id, profile)

        # 3) Fetch
        rows = fetch_chunks(
            conn,
            document_id,
            profile_name,
            limit=limit,
            offset=offset,
            include_content=show_content,
            include_context=show_context,
        )
        total = count_chunks(conn, document_id, profile_name)
    finally:
        conn.close()

    if json_output:
        payload = _build_chunks_payload(
            document_id, profile_name, total, limit, offset, rows,
            include_content=show_content, include_context=show_context,
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _render_chunks_human(
            document_id, profile_name, total, limit, offset, rows,
            include_content=show_content, include_context=show_context,
        )


# ===========================================================================
# inspect chunk (single)
# ===========================================================================


def _build_chunk_single_payload(
    row: ChunkRow,
    document_filename: Optional[str],
) -> dict[str, Any]:
    return {
        "chunk_id": row.chunk_id,
        "document_id": row.document_id,
        "document_filename": document_filename,
        "profile": row.profile_name,
        "chunk_type": row.chunk_type,
        "chunk_index": row.chunk_index,
        "parent_chunk_id": row.parent_chunk_id,
        "char_count": row.char_count,
        "token_count": row.token_count,
        "source_format": row.source_format,
        "metadata": row.metadata,
        "variant": {
            "type": row.variant.variant_type,
            "qdrant_collection": row.variant.qdrant_collection,
            "augmentation_status": row.variant.augmentation_status,
            "embedding_status": row.variant.embedding_status,
            "embedding_error": row.variant.embedding_error,
            "has_qdrant_point": row.variant.has_qdrant_point,
        },
        "content": row.content,
        "context_text": row.variant.context_text,
    }


def _format_meta_value(v: Any) -> str:
    """Render a metadata value for human display.

    Lists become comma-separated, bools become lowercase true/false,
    everything else uses str().
    """
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _render_chunk_single_human(
    row: ChunkRow,
    document_filename: Optional[str],
) -> None:
    fn_suffix = f" ({document_filename})" if document_filename else ""
    console.print(f"Chunk: [cyan]{row.chunk_id}[/cyan]")
    console.print(f"  document_id  : {row.document_id}{fn_suffix}", soft_wrap=True)
    console.print(f"  profile      : {row.profile_name}")
    console.print(f"  chunk_type   : {row.chunk_type}")
    console.print(f"  chunk_index  : {row.chunk_index}")
    if row.parent_chunk_id:
        console.print(f"  parent_chunk_id: {row.parent_chunk_id}")
    console.print(f"  char_count   : {_opt(row.char_count)}")
    console.print(f"  token_count  : {_opt(row.token_count)}")
    if row.metadata:
        console.print("  metadata     :")
        for k, v in row.metadata.items():
            console.print(
                f"    {k}: {_format_meta_value(v)}",
                markup=False, soft_wrap=True,
            )
    console.print("  variant      :")
    console.print(f"    type              : {row.variant.variant_type or '-'}")
    console.print(
        f"    qdrant_collection : {row.variant.qdrant_collection or '-'}",
        soft_wrap=True,
    )
    console.print(
        f"    augmentation_status: {row.variant.augmentation_status or '-'}"
    )
    if row.variant.embedding_status:
        # v0.21.0: surface fallback prominently — the chunk is searchable
        # via FTS only, vector search will not return it.
        console.print(
            f"    embedding_status   : [yellow]{row.variant.embedding_status}[/yellow]"
        )
        if row.variant.embedding_error:
            console.print(
                f"    embedding_error    : {row.variant.embedding_error}",
                soft_wrap=True,
            )
        console.print()
        console.print(
            "[yellow]⚠ Embedding fallback:[/yellow] this chunk has no Qdrant vector. "
            "Vector search will not return it; keyword (FTS5) search still works."
        )
    console.print()
    console.print("Context (LLM-generated):")
    ctx = row.variant.context_text
    console.print(f"  {ctx if ctx else '(none)'}", soft_wrap=True)
    console.print()
    console.print("Content:")
    body = row.content if row.content is not None else "(none)"
    for line in body.splitlines() or [body]:
        console.print(f"  {line}", soft_wrap=True)


@inspect_app.command("chunk")
def inspect_chunk(
    chunk_id: str = typer.Argument(..., help="Chunk ID to inspect"),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a single JSON object to stdout (warnings/errors go to stderr).",
    ),
) -> None:
    """Show full information for a single chunk by chunk_id."""
    conn = _open_project_db()
    try:
        row = fetch_chunk_single(conn, chunk_id)
        if row is None:
            err_console.print(
                f"[red]Error:[/red] chunk_id '{chunk_id}' not found"
            )
            raise typer.Exit(1)
        doc = fetch_document(conn, row.document_id)
    finally:
        conn.close()

    filename = doc.filename if doc else None
    if json_output:
        payload = _build_chunk_single_payload(row, filename)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _render_chunk_single_human(row, filename)


# ===========================================================================
# inspect sections
# ===========================================================================


def _has_any_heading_path(rows: list[ChunkRow]) -> bool:
    for r in rows:
        if r.metadata.get("heading_path"):
            return True
    return False


def _is_parent_child_profile(rows: list[ChunkRow]) -> bool:
    return any(r.chunk_type in ("parent", "child") for r in rows)


def _build_heading_tree(rows: list[ChunkRow]) -> list[dict[str, Any]]:
    """Build a nested tree from chunks' heading_path metadata.

    Returns a list of root section dicts:
        {"title": str, "chunk_count": int, "char_count": int,
         "children": list[...]}
    """
    root: dict[str, Any] = {"_children": {}, "_chunks": [], "_chars": 0}

    for r in rows:
        path = r.metadata.get("heading_path") or []
        node = root
        for title in path:
            children = node["_children"]
            if title not in children:
                children[title] = {"_children": {}, "_chunks": [], "_chars": 0}
            node = children[title]
        node["_chunks"].append(r.chunk_id)
        node["_chars"] += r.char_count or 0

    def _to_section(name: str, node: dict[str, Any]) -> dict[str, Any]:
        # roll up descendants
        child_sections = [
            _to_section(n, c) for n, c in node["_children"].items()
        ]
        own_chunks = len(node["_chunks"])
        own_chars = node["_chars"]
        for cs in child_sections:
            own_chunks += cs["chunk_count"]
            own_chars += cs["char_count"]
        return {
            "title": name,
            "chunk_count": own_chunks,
            "char_count": own_chars,
            "children": child_sections,
        }

    return [_to_section(name, node) for name, node in root["_children"].items()]


def _build_parent_child_tree(rows: list[ChunkRow]) -> list[dict[str, Any]]:
    """Build a parent-child view from rows.

    Returns a list of parent dicts:
        {"parent_id", "chunk_index", "char_count", "metadata", "children": [...]}
    """
    parents = {r.chunk_id: r for r in rows if r.chunk_type == "parent"}
    children_by_parent: dict[str, list[ChunkRow]] = {}
    orphan_children: list[ChunkRow] = []
    for r in rows:
        if r.chunk_type == "child":
            pid = r.parent_chunk_id
            if pid and pid in parents:
                children_by_parent.setdefault(pid, []).append(r)
            else:
                orphan_children.append(r)

    result: list[dict[str, Any]] = []
    for pid in sorted(parents, key=lambda x: parents[x].chunk_index):
        p = parents[pid]
        result.append({
            "parent_id": p.chunk_id,
            "chunk_index": p.chunk_index,
            "char_count": p.char_count,
            "metadata": p.metadata,
            "children": [
                {
                    "chunk_id": c.chunk_id,
                    "chunk_index": c.chunk_index,
                    "char_count": c.char_count,
                }
                for c in sorted(children_by_parent.get(pid, []),
                                key=lambda x: x.chunk_index)
            ],
        })
    if orphan_children:
        result.append({
            "parent_id": None,
            "chunk_index": -1,
            "char_count": 0,
            "metadata": {},
            "children": [
                {
                    "chunk_id": c.chunk_id,
                    "chunk_index": c.chunk_index,
                    "char_count": c.char_count,
                }
                for c in sorted(orphan_children, key=lambda x: x.chunk_index)
            ],
        })
    return result


def _render_heading_tree_human(
    document_id: str, profile_name: str, sections: list[dict[str, Any]]
) -> None:
    console.print(
        f"Document: [cyan]{document_id}[/cyan] / "
        f"Profile: [cyan]{profile_name}[/cyan]"
    )
    console.print()

    def _walk(nodes: list[dict[str, Any]], depth: int) -> None:
        for n in nodes:
            indent = "  " * depth
            ck = n["chunk_count"]
            ch = n["char_count"]
            console.print(
                f"{indent}§ {n['title']} "
                f"({ck} {_pluralize(ck, 'chunk')}, {ch} chars)",
                soft_wrap=True,
            )
            _walk(n["children"], depth + 1)

    _walk(sections, 0)


def _render_parent_child_human(
    document_id: str, profile_name: str, groups: list[dict[str, Any]]
) -> None:
    console.print(
        f"Document: [cyan]{document_id}[/cyan] / "
        f"Profile: [cyan]{profile_name}[/cyan]"
    )
    console.print()
    for g in groups:
        if g["parent_id"]:
            heading = g["metadata"].get("heading_path") or []
            if heading:
                heading_str = " > ".join(str(s) for s in heading)
                line = f"§ {heading_str} [parent: {g['parent_id']}, {g['char_count']} chars]"
            else:
                # No heading metadata — drop the leading "§ " marker so the
                # output reads cleanly: "[parent: <id>, N chars]".
                line = f"[parent: {g['parent_id']}, {g['char_count']} chars]"
            console.print(line, markup=False, soft_wrap=True)
        else:
            console.print("[orphan children]", markup=False)
        for c in g["children"]:
            console.print(
                f"    ↳ child: {c['chunk_id']} ({c['char_count']} chars)"
            )


@inspect_app.command("sections")
def inspect_sections(
    document_id: str = typer.Argument(..., help="Document ID to inspect"),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Profile name (required when multiple profiles index this document).",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a single JSON object to stdout (warnings/errors go to stderr).",
    ),
) -> None:
    """Visualize the heading hierarchy or parent-child layered view."""
    conn = _open_project_db()
    try:
        if fetch_document(conn, document_id) is None:
            err_console.print(
                f"[red]Error:[/red] document_id '{document_id}' not found"
            )
            raise typer.Exit(1)
        profile_name = _resolve_profile_or_exit(conn, document_id, profile)
        rows = fetch_sections(conn, document_id, profile_name)
    finally:
        conn.close()

    is_pc = _is_parent_child_profile(rows)
    has_heading = _has_any_heading_path(rows)

    if not is_pc and not has_heading:
        err_console.print(
            f"[red]Error:[/red] profile '{profile_name}' has no section "
            f"structure (preserve_heading_path is disabled). "
            f"Use 'mrag inspect chunks {document_id} --profile {profile_name}' "
            f"for a flat chunk listing."
        )
        raise typer.Exit(1)

    if is_pc:
        groups = _build_parent_child_tree(rows)
        if json_output:
            payload = {
                "document_id": document_id,
                "profile": profile_name,
                "mode": "parent_child",
                "parents": groups,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _render_parent_child_human(document_id, profile_name, groups)
    else:
        sections = _build_heading_tree(rows)
        if json_output:
            payload = {
                "document_id": document_id,
                "profile": profile_name,
                "mode": "heading",
                "sections": sections,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _render_heading_tree_human(document_id, profile_name, sections)


__all__ = ["inspect_app"]
