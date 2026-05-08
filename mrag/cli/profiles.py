from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mrag.config.profile import load_profile
from mrag.config.project import load_project_config
from mrag.db.connection import find_db, open_connection

console = Console()

profiles_app = typer.Typer(name="profiles", help="Manage retrieval profiles.", no_args_is_help=True)


@profiles_app.command("list")
def profiles_list() -> None:
    """List registered profiles."""
    project_dir = Path.cwd()

    try:
        load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        db_path = find_db(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    conn = open_connection(db_path)
    rows = conn.execute(
        "SELECT name, knowledge_id, profile_hash, updated_at FROM profiles ORDER BY name"
    ).fetchall()
    conn.close()

    if not rows:
        console.print("[yellow]No profiles registered. Run 'mrag index' first.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Hash (short)")
    table.add_column("Updated At")

    for r in rows:
        table.add_row(r["name"], r["profile_hash"][:12] + "...", r["updated_at"])

    console.print(table)


@profiles_app.command("show")
def profiles_show(
    name: str = typer.Argument(..., help="Profile name"),
) -> None:
    """Show profile configuration details."""
    project_dir = Path.cwd()

    try:
        prof = load_profile(name, project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[bold]Profile:[/bold] {prof.name}")
    console.print()

    console.print("[bold]Chunking[/bold]")
    console.print(f"  strategy   : {prof.chunking.strategy}")
    console.print(f"  chunk_size : {prof.chunking.chunk_size}")
    console.print(f"  overlap    : {prof.chunking.overlap}")

    console.print()
    console.print("[bold]Embedding[/bold]")
    console.print(f"  provider   : {prof.embedding.provider}")
    console.print(f"  model      : {prof.embedding.model}")
    console.print(f"  endpoint   : {prof.embedding.endpoint}")
    console.print(f"  cache      : {prof.embedding.cache.enabled}")

    console.print()
    console.print("[bold]Retrieval[/bold]")
    console.print(f"  strategy      : {prof.retrieval.strategy}")
    console.print(f"  top_k         : {prof.retrieval.top_k}")
    console.print(f"  dense_top_k   : {prof.retrieval.dense_top_k}")
    console.print(f"  keyword_top_k : {prof.retrieval.keyword_top_k}")
    console.print(f"  fusion        : {prof.retrieval.fusion}")

    console.print()
    console.print("[bold]Keyword[/bold]")
    console.print(f"  provider  : {prof.keyword.provider}")
    console.print(f"  tokenizer : {prof.keyword.tokenizer}")
