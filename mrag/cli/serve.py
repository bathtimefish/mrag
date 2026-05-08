from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.profile import load_profile
from mrag.config.project import load_project_config
from mrag.db.connection import find_db
from mrag.db.qdrant import make_client

console = Console()


def serve(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", help="Bind port"),
) -> None:
    """Start the MRAG API server."""
    import uvicorn

    from mrag.api.app import create_app

    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_name = profile or config.default_profile

    try:
        load_profile(profile_name, project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        find_db(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        make_client(host=config.qdrant.host, port=config.qdrant.port)
    except ConnectionError as e:
        console.print(f"[red]Error:[/red] Qdrant not reachable: {e}")
        raise typer.Exit(1)

    app = create_app(
        project_dir=project_dir,
        profile_name=profile_name,
        config=config,
    )

    console.print(
        f"[green]Starting MRAG API server[/green] on [cyan]http://{host}:{port}[/cyan]"
    )
    console.print(f"Profile: [bold]{profile_name}[/bold]  |  Docs: http://{host}:{port}/docs")
    console.print(f"Knowledge ID: [bold]{config.knowledge_id}[/bold]")

    uvicorn.run(app, host=host, port=port)
