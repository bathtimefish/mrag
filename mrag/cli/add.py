from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.ingestion.document import add_document

console = Console()


def add(
    file: Path = typer.Argument(..., help="File to add (pdf, txt, md)"),
    extractor: Optional[str] = typer.Option(
        None, "--extractor", "-e",
        help="Extractor override (pymupdf). Default: mrag.yaml default_extraction.",
    ),
    force: bool = typer.Option(False, "--force", help="Re-add if already registered."),
) -> None:
    """Add a document to the project (ingestion only — no indexing)."""
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    project_dir = Path.cwd()
    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        document_id, warnings = add_document(
            file_path=file.resolve(),
            project_dir=project_dir,
            config=config,
            extractor_override=extractor,
            force=force,
        )
    except FileExistsError as e:
        console.print(f"[yellow]Skipped:[/yellow] {e}")
        raise typer.Exit(0)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    for w in warnings:
        console.print(f"[yellow]Warning:[/yellow] {w}")

    console.print(f"[green]✓[/green] Added [cyan]{file.name}[/cyan]")
    console.print(f"  document_id: {document_id}")
    console.print()
    console.print(
        f"  Run [bold]mrag index[/bold] to build the retrieval index."
    )
