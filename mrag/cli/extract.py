from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.extractors import detect_source_type, get_extractor

console = Console()


def extract(
    file: Path = typer.Argument(..., help="File to extract (dry-run, no DB writes)"),
    extractor: Optional[str] = typer.Option(None, "--extractor", "-e"),
    out: Optional[Path] = typer.Option(None, "--out", help="Save output to file"),
    format: str = typer.Option("text", "--format", help="Output format: text | markdown"),
) -> None:
    """Extract text from a file (dry-run — nothing written to project)."""
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    try:
        source_type = detect_source_type(file)
        extractor_obj = get_extractor(source_type, extractor)
        result = extractor_obj.extract(file.resolve())
    except (ValueError, ImportError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    for w in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {w}", err=True)

    content = result.markdown if format == "markdown" else result.text

    if out:
        out.write_text(content, encoding="utf-8")
        console.print(f"[green]✓[/green] Saved to {out}")
    else:
        typer.echo(content)
