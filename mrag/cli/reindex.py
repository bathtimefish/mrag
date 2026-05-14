from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import cleanup_profile_index, run_index, write_index_log
from mrag.cli.index import _default_log_path, _load_skip_ids

console = Console()


def reindex(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name (default: project default_profile)"),
    output_log: Optional[str] = typer.Option(None, "--output-log", help="Log output path (default: logs/YYYYMMDDHHmmss-reindex.json)"),
    skip_list_json: Optional[str] = typer.Option(None, "--skip-list-json", help="Skip documents listed in a previous index log JSON"),
) -> None:
    """Force-rebuild the retrieval index for a profile (drops and recreates all chunks)."""
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
            skip_document_ids=skip_ids or None,
            console=console,
        )
    except (FileNotFoundError, ConnectionError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        f"[green]✓[/green] Indexed: {result.indexed}  "
        f"Up-to-date: {result.skipped}  "
        f"List-skipped: {result.skipped_by_list}"
    )

    for doc_id, msg in result.errors:
        console.print(f"[red]Error[/red] ({doc_id}): {msg}")

    log_path = Path(output_log) if output_log else _default_log_path(project_dir, "reindex")
    write_index_log(result, log_path, command="reindex", profile_name=profile_name)
    console.print(f"[dim]Log: {log_path}[/dim]")

    if result.errors:
        raise typer.Exit(1)
