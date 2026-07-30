from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import (
    reconcile_profile_vectors,
    run_index,
    write_index_log,
)
from mrag.db.connection import find_db
from mrag.db.qdrant import make_client
from mrag.cli.index import _default_log_path, _load_skip_ids

console = Console()


def reindex(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name (default: project default_profile)"),
    output_log: Optional[str] = typer.Option(None, "--output-log", help="Log output path (default: logs/YYYYMMDDHHmmss-reindex.json)"),
    skip_list_json: Optional[str] = typer.Option(None, "--skip-list-json", help="Skip documents listed in a previous index log JSON"),
) -> None:
    """Force-rebuild the retrieval index for a profile (recreates every chunk).

    Rebuilds document by document rather than emptying the profile first. Each
    document's previous chunks, FTS rows and vector points are deleted only once
    its replacements have been chunked, augmented and embedded successfully, so a
    provider outage mid-run leaves the existing index searchable instead of
    destroying it.
    """
    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_name = profile or config.default_profile

    skip_ids: set[str] = set()
    if skip_list_json:
        skip_ids = _load_skip_ids(skip_list_json)
        console.print(f"[dim]Skip list: {len(skip_ids)} document(s) will be skipped.[/dim]")

    # Built here rather than inside run_index so an unreachable Qdrant fails
    # before any document is touched, and so the same client can reconcile the
    # collection afterwards. Local mode takes an exclusive lock on ./qdrant, so
    # there must only ever be one client per run.
    try:
        qdrant_client = make_client(
            mode=config.qdrant.mode,
            host=config.qdrant.host,
            port=config.qdrant.port,
            path=project_dir / "qdrant",
        )
    except (ConnectionError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"Rebuilding index for profile [cyan]{profile_name}[/cyan] ...")

    try:
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name=profile_name,
            skip_document_ids=skip_ids or None,
            force=True,
            qdrant_client=qdrant_client,
            console=console,
        )
    except (FileNotFoundError, ConnectionError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("[dim]The existing index was left in place.[/dim]")
        raise typer.Exit(1)

    # Reclaims points that earlier reindex runs orphaned. Reindex is the only
    # command that rewrites a whole profile, which makes it the natural place to
    # reconcile; it deletes nothing that a live chunk row still names.
    try:
        reclaimed = reconcile_profile_vectors(
            db_path=find_db(project_dir),
            profile_name=profile_name,
            qdrant_client=qdrant_client,
        )
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] orphaned vector cleanup was skipped ({e})")
    else:
        if reclaimed:
            console.print(f"[dim]Reclaimed {reclaimed} orphaned vector point(s).[/dim]")

    # Combine fallback counters in processing order (DESIGN_V21 Resolved Decision #10)
    fb_notes: list[str] = []
    if result.raw_fallback_chunks:
        fb_notes.append(f"{result.raw_fallback_chunks} augmentation fallback")
    if result.embedding_fallback_chunks:
        fb_notes.append(f"{result.embedding_fallback_chunks} embedding fallback")
    fb_suffix = f"  ({', '.join(fb_notes)})" if fb_notes else ""
    console.print(
        f"[green]✓[/green] Indexed: {result.indexed}  "
        f"Up-to-date: {result.skipped}  "
        f"List-skipped: {result.skipped_by_list}  "
        f"Excluded: {result.excluded}"
        f"{fb_suffix}"
    )

    for doc_id, msg in result.errors:
        console.print(f"[red]Error[/red] ({doc_id}): {msg}")

    log_path = Path(output_log) if output_log else _default_log_path(project_dir, "reindex")
    write_index_log(result, log_path, command="reindex", profile_name=profile_name)
    console.print(f"[dim]Log: {log_path}[/dim]")

    if result.errors:
        raise typer.Exit(1)
