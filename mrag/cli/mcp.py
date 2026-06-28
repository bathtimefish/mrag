from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from rich.console import Console

from mrag.config.mcp import (
    default_mcp_config_yaml,
    dump_effective_config,
    load_mcp_config,
    mcp_json_schema,
    resolve_mcp_config,
)
from mrag.db.connection import find_db
from mrag.db.qdrant import make_client


err_console = Console(stderr=True)
mcp_app = typer.Typer(
    name="mcp",
    help="Expose the current mrag project as a Model Context Protocol server.",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _load_effective_or_exit(config_path: Optional[Path]):
    try:
        cfg = load_mcp_config(config_path)
        effective = resolve_mcp_config(cfg)
        find_db(effective.project_dir)
    except (FileNotFoundError, ValueError, ValidationError) as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    return effective


@mcp_app.callback(invoke_without_command=True)
def mcp_main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", help="Path to mrag MCP YAML config."
    ),
    print_effective_config: bool = typer.Option(
        False,
        "--print-effective-config",
        help="Print resolved MCP config and exit.",
    ),
) -> None:
    """Start the mrag MCP server."""
    if ctx.invoked_subcommand is not None:
        return

    effective = _load_effective_or_exit(config)

    if print_effective_config:
        print(dump_effective_config(effective), end="")
        raise typer.Exit()

    if (
        effective.raw.transport == "streamable-http"
        and effective.raw.http.host == "0.0.0.0"
        and not effective.auth_token
    ):
        err_console.print(
            "[yellow]WARN[/yellow]  mrag mcp is bound to 0.0.0.0 without bearer auth. "
            "Set MRAG_MCP_API_KEY or bind to 127.0.0.1."
        )

    if (
        effective.project_config.qdrant.mode != "local"
        and effective.retrieval_strategy != "keyword"
    ):
        try:
            make_client(
                mode=effective.project_config.qdrant.mode,
                host=effective.project_config.qdrant.host,
                port=effective.project_config.qdrant.port,
            )
        except ConnectionError as e:
            err_console.print(f"[red]Error:[/red] Qdrant not reachable: {e}")
            raise typer.Exit(1)

    try:
        from mrag.mcp.server import run_mcp_server

        run_mcp_server(effective)
    except RuntimeError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@mcp_app.command("validate")
def validate(
    config: Path = typer.Option(..., "--config", help="Path to mrag MCP YAML config."),
) -> None:
    """Validate an MCP config file without starting the server."""
    effective = _load_effective_or_exit(config)
    if (
        effective.raw.transport == "streamable-http"
        and effective.raw.http.host == "0.0.0.0"
        and not effective.auth_token
    ):
        err_console.print(
            "[yellow]WARN[/yellow]  host is 0.0.0.0 and bearer auth is not configured."
        )
    err_console.print("[green]OK[/green] MCP config is valid.")


@mcp_app.command("schema")
def schema() -> None:
    """Print the MCP config JSON Schema."""
    print(json.dumps(mcp_json_schema(), indent=2, ensure_ascii=False))


@mcp_app.command("init-config")
def init_config() -> None:
    """Print a starter mrag-mcp.yaml to stdout."""
    print(default_mcp_config_yaml(), end="")


__all__ = ["mcp_app"]
