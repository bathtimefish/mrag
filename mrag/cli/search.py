import json
import statistics
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.profile import load_profile
from mrag.config.project import load_project_config
from mrag.core.retrieval.runner import fetch_filename_map, run_retrieval
from mrag.cli.eval import print_score_stats_and_distribution
from mrag.db.connection import find_db

console = Console()
err_console = Console(stderr=True)


def _build_json_payload(
    *,
    query: str,
    profile_name: str,
    strategy: str,
    reranked: bool,
    results: list,
    filename_map: dict,
) -> dict:
    """Build the structured JSON payload emitted by `mrag search --json`."""
    result_entries = []
    for i, r in enumerate(results, 1):
        entry = {
            "rank": i,
            "chunk_id": r.chunk_id,
            "document_id": r.document_id,
            "filename": filename_map.get(r.document_id, ""),
            "score": float(r.score),
            "content": r.content,
            "metadata": dict(r.metadata) if r.metadata else {},
        }
        # retrieval_score is preserved by the reranker in metadata
        if "retrieval_score" in entry["metadata"]:
            entry["retrieval_score"] = float(entry["metadata"]["retrieval_score"])
        result_entries.append(entry)

    score_stats = None
    doc_distribution: dict = {}
    if results:
        scores = [float(r.score) for r in results]
        score_stats = {
            "min": min(scores),
            "max": max(scores),
            "mean": statistics.mean(scores),
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        }
        for r in results:
            name = filename_map.get(r.document_id, r.document_id[:8])
            doc_distribution[name] = doc_distribution.get(name, 0) + 1

    return {
        "query": query,
        "profile": profile_name,
        "strategy": strategy,
        "reranked": reranked,
        "result_count": len(results),
        "results": result_entries,
        "score_stats": score_stats,
        "document_distribution": doc_distribution,
    }


def search(
    query: str = typer.Argument(..., help="Search query"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
    strategy: Optional[str] = typer.Option(
        None, "--strategy", "-s", help="hybrid | vector | keyword (default: profile setting)"
    ),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="Disable reranking even if enabled in profile"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a single JSON object to stdout (status/warning messages go to stderr).",
    ),
) -> None:
    """Search the knowledge base."""
    # In --json mode, status messages and warnings must go to stderr so stdout
    # contains only the JSON payload.
    out = err_console if json_output else console

    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_name = profile or config.default_profile

    try:
        load_profile(profile_name, project_dir)
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    db_path = find_db(project_dir)
    try:
        run = run_retrieval(
            query=query,
            project_dir=project_dir,
            config=config,
            profile_name=profile_name,
            strategy=strategy,
            top_k=top_k,
            no_rerank=no_rerank,
            load_reranker=True,
            warn=lambda msg: out.print(f"[yellow]WARN[/yellow]  {msg}"),
        )
    except (ConnectionError, RuntimeError, ImportError) as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    retrieval_strategy = run.strategy
    results = run.results
    reranked = run.reranked

    # Look up filenames for display / JSON
    filename_map = fetch_filename_map(db_path, results)

    # -----------------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------------
    if json_output:
        payload = _build_json_payload(
            query=query,
            profile_name=profile_name,
            strategy=retrieval_strategy,
            reranked=reranked,
            results=results,
            filename_map=filename_map,
        )
        # Emit JSON to stdout (plain print, no rich formatting)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # Human-readable output (existing behavior)
    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    for i, r in enumerate(results, 1):
        filename = filename_map.get(r.document_id, r.document_id[:8])
        preview = r.content[:200].replace("\n", " ")
        section_text = r.metadata.get("heading_path_text", "")
        retrieval_mode = r.metadata.get("retrieval_mode", "")
        if retrieval_mode == "parent_child":
            child_id = r.metadata.get("retrieved_child_id", "")[:8]
            console.print(
                f"[bold]\\[{i}][/bold] score=[cyan]{r.score:.4f}[/cyan]  "
                f"doc=[green]{filename}[/green]  "
                f"[dim]child={child_id}...  →  parent={r.chunk_id[:8]}...[/dim]"
            )
        else:
            console.print(
                f"[bold]\\[{i}][/bold] score=[cyan]{r.score:.4f}[/cyan]  "
                f"doc=[green]{filename}[/green]  chunk=[dim]{r.chunk_id[:8]}...[/dim]"
            )
        if section_text:
            console.print(f"    [dim]section:[/dim] {section_text}")
        console.print(f"    {preview}")
        console.print()

    print_score_stats_and_distribution(results, filename_map, console)
