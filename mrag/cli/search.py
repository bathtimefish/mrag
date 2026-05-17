import json
import statistics
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
from mrag.cli.eval import print_score_stats_and_distribution
from mrag.db.connection import find_db, open_connection
from mrag.db.qdrant import collection_name, make_client, normalize_name

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
        prof = load_profile(profile_name, project_dir)
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    db_path = find_db(project_dir)
    retrieval_strategy = strategy or prof.retrieval.strategy
    tokenizer = config.fts_tokenizer

    use_rerank = prof.rerank.enabled and not no_rerank
    retrieval_top_k = prof.rerank.top_n if use_rerank else top_k

    if use_rerank and retrieval_strategy == "parent_child":
        out.print(
            "[yellow]WARN[/yellow]  rerank.enabled=true with strategy: parent_child. "
            "Reranking scores parent chunks (~3000 chars) after parent resolution. "
            "BERT-based rerankers truncate at 512 tokens, discarding most parent content. "
            "Consider disabling rerank for parent_child profiles."
        )

    try:
        if retrieval_strategy == "keyword":
            results = keyword_search(
                query_text=query,
                knowledge_id=config.knowledge_id,
                profile_name=profile_name,
                db_path=db_path,
                top_k=retrieval_top_k,
                tokenizer=tokenizer,
            )
        else:
            provider = OllamaEmbeddingProvider(
                model=prof.embedding.model,
                endpoint=prof.embedding.endpoint,
                max_attempts=prof.embedding.retry.max_attempts,
                initial_delay=prof.embedding.retry.initial_delay_seconds,
                backoff_multiplier=prof.embedding.retry.backoff_multiplier,
                max_delay=prof.embedding.retry.max_delay_seconds,
            )
            try:
                qdrant_client = make_client(
                    mode=config.qdrant.mode,
                    host=config.qdrant.host,
                    port=config.qdrant.port,
                    path=project_dir / "qdrant",
                )
            except ConnectionError as e:
                err_console.print(f"[red]Error:[/red] {e}")
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
                    top_k=retrieval_top_k,
                )
            elif retrieval_strategy == "parent_child":
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
                    top_k=retrieval_top_k * 3,  # parent 解決後に絞るため多めに取得
                    fusion=prof.retrieval.fusion,
                    weights=prof.retrieval.weights,
                    tokenizer=tokenizer,
                )
                from mrag.core.retrieval.parent_child import resolve_to_parent
                results = resolve_to_parent(results, db_path)
                results = results[:retrieval_top_k]
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
                    top_k=retrieval_top_k,
                    fusion=prof.retrieval.fusion,
                    weights=prof.retrieval.weights,
                    tokenizer=tokenizer,
                )
    except (ConnectionError, RuntimeError) as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    reranked = False
    if use_rerank and results:
        try:
            from mrag.core.reranking import get_reranker
            reranker = get_reranker(prof.rerank)
            results = reranker.rerank(query, results)[:top_k]
            reranked = True
        except ImportError as e:
            err_console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # Look up filenames for display / JSON
    filename_map: dict = {}
    if results:
        conn = open_connection(db_path)
        doc_rows = conn.execute(
            "SELECT id, filename FROM documents WHERE id IN (%s)"
            % ",".join("?" * len({r.document_id for r in results})),
            list({r.document_id for r in results}),
        ).fetchall()
        conn.close()
        filename_map = {r["id"]: r["filename"] for r in doc_rows}

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
