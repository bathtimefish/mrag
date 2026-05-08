from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index

console = Console()


def index(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name (default: project default_profile)"),
    document_id: Optional[str] = typer.Option(None, "--document-id", help="Index a specific document only"),
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

    console.print(f"Indexing profile [cyan]{profile_name}[/cyan] ...")

    try:
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name=profile_name,
            document_ids=doc_ids,
        )
    except (FileNotFoundError, ConnectionError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Indexed: {result.indexed}  Skipped: {result.skipped}")

    for doc_id, msg in result.errors:
        console.print(f"[red]Error[/red] ({doc_id}): {msg}")

    if result.errors:
        raise typer.Exit(1)
