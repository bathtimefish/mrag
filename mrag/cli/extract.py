from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.extractors import detect_source_type, get_extractor

console = Console()
# Warnings go to stderr, so stdout stays the extracted text alone.
err_console = Console(stderr=True)


def extract(
    file: Path = typer.Argument(..., help="File to extract (dry-run, no DB writes)"),
    out: Optional[Path] = typer.Option(None, "--out", help="Save output to file"),
    format: str = typer.Option("text", "--format", help="Output format: text | markdown"),
) -> None:
    """Extract text from a file (dry-run — nothing written to project)."""
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    try:
        extractor_obj = get_extractor(detect_source_type(file))
        result = extractor_obj.extract(file.resolve())
    except (ValueError, ImportError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    for w in result.warnings:
        err_console.print("[yellow]Warning:[/yellow]", end=" ")
        err_console.print(w, markup=False, highlight=False)

    content = result.markdown if format == "markdown" else result.text

    if out:
        out.write_text(content, encoding="utf-8")
        console.print(f"[green]✓[/green] Saved to {out}")
    else:
        typer.echo(content)
