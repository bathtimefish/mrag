from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import cleanup_profile_index, run_index

console = Console()


def reindex(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name (default: project default_profile)"),
) -> None:
    """Force-rebuild the retrieval index for a profile (drops and recreates all chunks)."""
    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_name = profile or config.default_profile
    console.print(f"Cleaning up index for profile [cyan]{profile_name}[/cyan] ...")

    try:
        cleanup_profile_index(project_dir=project_dir, config=config, profile_name=profile_name)
    except (FileNotFoundError, ConnectionError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print("Re-indexing ...")

    try:
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name=profile_name,
        )
    except (FileNotFoundError, ConnectionError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Indexed: {result.indexed}  Skipped: {result.skipped}")

    for doc_id, msg in result.errors:
        console.print(f"[red]Error[/red] ({doc_id}): {msg}")

    if result.errors:
        raise typer.Exit(1)
