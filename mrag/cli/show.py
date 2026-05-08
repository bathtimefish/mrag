from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.core.ingestion.document import get_document
from mrag.db.connection import find_db

console = Console()


def show_extracted(
    document_id: str = typer.Argument(..., help="Document ID"),
    format: str = typer.Option("text", "--format", help="text | markdown"),
) -> None:
    """Print the extracted content of a document."""
    project_dir = Path.cwd()
    content = _read_extracted(document_id, project_dir, format)
    typer.echo(content)


def export_extracted(
    document_id: str = typer.Argument(..., help="Document ID"),
    out: Path = typer.Option(..., "--out", help="Output file path"),
    format: str = typer.Option("text", "--format", help="text | markdown"),
) -> None:
    """Export extracted content to a file."""
    project_dir = Path.cwd()
    content = _read_extracted(document_id, project_dir, format)
    out.write_text(content, encoding="utf-8")
    console.print(f"[green]✓[/green] Exported to {out}")


def _read_extracted(document_id: str, project_dir: Path, format: str) -> str:
    try:
        db_path = find_db(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    doc = get_document(document_id, db_path)
    if doc is None:
        console.print(f"[red]Error:[/red] Document not found: {document_id}")
        raise typer.Exit(1)

    path_key = "extracted_markdown_path" if format == "markdown" else "extracted_text_path"
    rel_path = doc.get(path_key)
    if not rel_path:
        console.print(f"[red]Error:[/red] Extracted file path not recorded for document {document_id}")
        raise typer.Exit(1)

    full_path = project_dir / rel_path
    if not full_path.exists():
        console.print(f"[red]Error:[/red] Extracted file not found: {full_path}")
        raise typer.Exit(1)

    return full_path.read_text(encoding="utf-8")
