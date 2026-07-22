import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index, write_index_log

console = Console()


def _default_log_path(project_dir: Path, command: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return project_dir / "logs" / f"{ts}-{command}.json"


def _load_skip_ids(skip_list_json: str) -> set[str]:
    path = Path(skip_list_json)
    if not path.exists():
        console.print(f"[red]Error:[/red] skip-list-json not found: {path}")
        raise typer.Exit(1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to parse skip-list-json: {e}")
        raise typer.Exit(1)
    ids = {d["document_id"] for d in data.get("failed_documents", []) if d.get("document_id")}
    if not ids:
        console.print("[yellow]WARN[/yellow]  skip-list-json contains no document IDs to skip.")
    return ids


def index(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name (default: project default_profile)"),
    document_id: Optional[str] = typer.Option(None, "--document-id", help="Index a specific document only"),
    output_log: Optional[str] = typer.Option(None, "--output-log", help="Log output path (default: logs/YYYYMMDDHHmmss-index.json)"),
    skip_list_json: Optional[str] = typer.Option(None, "--skip-list-json", help="Skip documents listed in a previous index log JSON"),
) -> None:
    """Build the retrieval index for all documents (differential)."""
    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_name = profile or config.default_profile
    doc_ids = [document_id] if document_id else None

    skip_ids: set[str] = set()
    if skip_list_json:
        skip_ids = _load_skip_ids(skip_list_json)
        console.print(f"[dim]Skip list: {len(skip_ids)} document(s) will be skipped.[/dim]")

    console.print(f"Indexing profile [cyan]{profile_name}[/cyan] ...")

    try:
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name=profile_name,
            document_ids=doc_ids,
            skip_document_ids=skip_ids or None,
            console=console,
        )
    except (FileNotFoundError, ConnectionError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

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

    log_path = Path(output_log) if output_log else _default_log_path(project_dir, "index")
    write_index_log(result, log_path, command="index", profile_name=profile_name)
    console.print(f"[dim]Log: {log_path}[/dim]")

    if result.errors:
        raise typer.Exit(1)
