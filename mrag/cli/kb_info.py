"""`mrag kb-info` subcommand group.

Subcommands:
  show      Print the current project's kb_information.yaml
  validate  Validate the current project's kb_information.yaml
  schema    Print the JSON Schema for --kb-info-json input
"""
import json
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from mrag.config.kb_info import (
    KB_INFORMATION_FILENAME,
    kb_info_json_schema,
    kb_info_path,
    load_kb_info,
)

console = Console()

kb_info_app = typer.Typer(
    name="kb-info",
    help="Inspect and validate kb_information.yaml (agent-facing KB metadata).",
    no_args_is_help=True,
)


@kb_info_app.command("show")
def kb_info_show() -> None:
    """Print the current project's kb_information.yaml."""
    project_dir = Path.cwd()
    path = kb_info_path(project_dir)
    if not path.exists():
        console.print(
            f"[red]Error:[/red] {KB_INFORMATION_FILENAME} not found in {project_dir}. "
            "Run 'mrag init' first."
        )
        raise typer.Exit(1)
    # Print raw YAML so the user sees exactly what is on disk.
    console.print(path.read_text(encoding="utf-8"))


@kb_info_app.command("validate")
def kb_info_validate() -> None:
    """Validate the current project's kb_information.yaml against the v1 schema."""
    project_dir = Path.cwd()
    path = kb_info_path(project_dir)
    if not path.exists():
        console.print(
            f"[red]Error:[/red] {KB_INFORMATION_FILENAME} not found in {project_dir}."
        )
        raise typer.Exit(1)
    try:
        cfg = load_kb_info(project_dir)
    except ValidationError as e:
        console.print(f"[red]Error:[/red] {KB_INFORMATION_FILENAME} validation failed:")
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            console.print(f"  - [yellow]{loc}[/yellow]: {err['msg']}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] failed to read {path}: {e}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] {KB_INFORMATION_FILENAME} is valid")
    console.print(f"  knowledge_base.id   : [cyan]{cfg.knowledge_base.id}[/cyan]")
    console.print(f"  knowledge_base.name : [cyan]{cfg.knowledge_base.name}[/cyan]")
    console.print(f"  preferred_profiles  : [cyan]{', '.join(cfg.agent_usage.preferred_profiles)}[/cyan]")
    tag_count = len(cfg.agent_usage.tags)
    best_count = len(cfg.agent_usage.best_for)
    avoid_count = len(cfg.agent_usage.avoid_for)
    example_count = len(cfg.agent_usage.example_queries)
    console.print(
        f"  agent_usage         : "
        f"{tag_count} tag(s), {best_count} best_for, {avoid_count} avoid_for, "
        f"{example_count} example_queries"
    )


@kb_info_app.command("schema")
def kb_info_schema() -> None:
    """Print the JSON Schema for --kb-info-json input files.

    Equivalent to `mrag init --print-kb-info-schema`.
    """
    print(json.dumps(kb_info_json_schema(), indent=2, ensure_ascii=False))
