from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.profile import load_profile
from mrag.config.project import load_project_config
from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from mrag.core.retrieval.hybrid import hybrid_search
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import find_db, open_connection
from mrag.db.qdrant import collection_name, make_client, normalize_name

console = Console()


def search(
    query: str = typer.Argument(..., help="Search query"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
    strategy: Optional[str] = typer.Option(
        None, "--strategy", "-s", help="hybrid | vector | keyword (default: profile setting)"
    ),
) -> None:
    """Search the knowledge base."""
    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_name = profile or config.default_profile

    try:
        prof = load_profile(profile_name, project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    db_path = find_db(project_dir)
    retrieval_strategy = strategy or prof.retrieval.strategy

    tokenizer = config.fts_tokenizer

    try:
        if retrieval_strategy == "keyword":
            results = keyword_search(
                query_text=query,
                knowledge_id=config.knowledge_id,
                profile_name=profile_name,
                db_path=db_path,
                top_k=top_k,
                tokenizer=tokenizer,
            )
        else:
            provider = OllamaEmbeddingProvider(
                model=prof.embedding.model,
                endpoint=prof.embedding.endpoint,
            )
            try:
                qdrant_client = make_client(
                    host=config.qdrant.host, port=config.qdrant.port
                )
            except ConnectionError as e:
                console.print(f"[red]Error:[/red] {e}")
                raise typer.Exit(1)

            col = collection_name(
                config.knowledge_id,
                profile_name,
                normalize_name(prof.embedding.model),
            )

            if retrieval_strategy == "vector":
                results = vector_search(
                    query_text=query,
                    knowledge_id=config.knowledge_id,
                    profile_name=profile_name,
                    db_path=db_path,
                    embedding_provider=provider,
                    qdrant_client=qdrant_client,
                    col_name=col,
                    top_k=top_k,
                )
            else:  # hybrid (default)
                results = hybrid_search(
                    query_text=query,
                    knowledge_id=config.knowledge_id,
                    profile_name=profile_name,
                    db_path=db_path,
                    embedding_provider=provider,
                    qdrant_client=qdrant_client,
                    col_name=col,
                    dense_top_k=prof.retrieval.dense_top_k,
                    keyword_top_k=prof.retrieval.keyword_top_k,
                    top_k=top_k,
                    fusion=prof.retrieval.fusion,
                    tokenizer=tokenizer,
                )
    except (ConnectionError, RuntimeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    # Look up filenames for display
    conn = open_connection(db_path)
    doc_rows = conn.execute(
        "SELECT id, filename FROM documents WHERE id IN (%s)"
        % ",".join("?" * len({r.document_id for r in results})),
        list({r.document_id for r in results}),
    ).fetchall()
    conn.close()
    filename_map = {r["id"]: r["filename"] for r in doc_rows}

    for i, r in enumerate(results, 1):
        filename = filename_map.get(r.document_id, r.document_id[:8])
        preview = r.content[:200].replace("\n", " ")
        console.print(
            f"[bold]\\[{i}][/bold] score=[cyan]{r.score:.4f}[/cyan]  "
            f"doc=[green]{filename}[/green]  chunk=[dim]{r.chunk_id[:8]}...[/dim]"
        )
        console.print(f"    {preview}")
        console.print()
