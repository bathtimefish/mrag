import re
import statistics
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from mrag.config.profile import load_profile, validate_effective_tokenizer
from mrag.config.project import load_project_config
from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from mrag.core.retrieval.base import RetrievalResult
from mrag.core.retrieval.hybrid import hybrid_search
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import find_db, open_connection
from mrag.db.qdrant import make_client, normalize_name
from mrag.db.qdrant_migrate import resolve_collection

console = Console()


def _run_search(
    query: str,
    profile_name: str,
    project_dir: Path,
    config,
    top_k: int,
    strategy: Optional[str],
    no_rerank: bool,
) -> tuple[list[RetrievalResult], str, bool]:
    prof = load_profile(profile_name, project_dir)
    db_path = find_db(project_dir)
    retrieval_strategy = strategy or prof.retrieval.strategy
    tokenizer = validate_effective_tokenizer(prof, config.fts_tokenizer)

    use_rerank = prof.rerank.enabled and not no_rerank
    retrieval_top_k = prof.rerank.top_n if use_rerank else top_k

    if use_rerank and retrieval_strategy == "parent_child":
        console.print(
            "[yellow]WARN[/yellow]  rerank.enabled=true with strategy: parent_child. "
            "Reranking scores parent chunks (~3000 chars) after parent resolution. "
            "BERT-based rerankers truncate at 512 tokens, discarding most parent content. "
            "Consider disabling rerank for parent_child profiles."
        )

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
        qdrant_client = make_client(
            mode=config.qdrant.mode,
            host=config.qdrant.host,
            port=config.qdrant.port,
            path=project_dir / "qdrant",
        )
        col = resolve_collection(
            qdrant_client,
            db_path=db_path,
            config=config,
            profile_name=profile_name,
            model_normalized=normalize_name(prof.embedding.model),
            notify=lambda msg: console.print(f"[yellow]WARN[/yellow]  {msg}"),
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
                top_k=retrieval_top_k * 3,
                fusion=prof.retrieval.fusion,
                weights=prof.retrieval.weights,
                tokenizer=tokenizer,
            )
            from mrag.core.retrieval.parent_child import resolve_to_parent
            results = resolve_to_parent(results, db_path)
            results = results[:retrieval_top_k]
        else:
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

    reranked = False
    if use_rerank and results:
        from mrag.core.reranking import get_reranker
        reranker = get_reranker(prof.rerank)
        results = reranker.rerank(query, results)[:top_k]
        reranked = True
    else:
        results = results[:top_k]

    return results, retrieval_strategy, reranked


def _fetch_filenames(db_path: Path, results: list[RetrievalResult]) -> dict[str, str]:
    if not results:
        return {}
    doc_ids = list({r.document_id for r in results})
    conn = open_connection(db_path)
    rows = conn.execute(
        "SELECT id, filename FROM documents WHERE id IN (%s)"
        % ",".join("?" * len(doc_ids)),
        doc_ids,
    ).fetchall()
    conn.close()
    return {r["id"]: r["filename"] for r in rows}


# Duplicate detection. Corpora that re-publish the same text every year (annual
# policy surveys, report series) return the same paragraph from several
# documents, and those copies rarely match byte for byte: chunk boundaries
# shift with the surrounding document, heading levels differ, whitespace and
# full-width punctuation vary. Exact matching missed every one of them, so
# results are compared on normalized character shingles instead.
#
# The overlap coefficient (|A ∩ B| / min(|A|, |B|)) is used rather than
# Jaccard because it stays high when one chunk is the other plus a few extra
# sentences — the shifted-boundary case — where Jaccard drops with the length
# difference. Measured on real annual-series chunks: true copies score
# 0.89–1.00, unrelated chunks 0.00–0.02, so the threshold has a wide margin.
_DEDUP_STRIP = re.compile(
    r"[\s#*_`>|\-–—•·・:：、。,.()（）「」『』\[\]!?！？\"'’“”]+"
)
_SHINGLE_SIZE = 5
NEAR_DUPLICATE_THRESHOLD = 0.85
# Below this many normalized characters there are too few shingles for the
# overlap to mean anything; such results are only ever flagged on exact match.
_MIN_NEAR_DUPLICATE_CHARS = 40


@dataclass(frozen=True)
class DuplicateMatch:
    """A result that repeats an earlier, higher-ranked result."""

    rank: int
    """1-based rank of the earlier result."""
    chunk_id: str
    """chunk_id of the earlier result."""
    similarity: float
    exact: bool


def _dedup_key(text: str) -> str:
    return _DEDUP_STRIP.sub("", unicodedata.normalize("NFKC", text)).lower()


def _shingles(key: str) -> set[str]:
    if len(key) < _SHINGLE_SIZE:
        return {key} if key else set()
    return {key[i:i + _SHINGLE_SIZE] for i in range(len(key) - _SHINGLE_SIZE + 1)}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def find_duplicates(
    results: list[RetrievalResult],
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> dict[str, DuplicateMatch]:
    """Map each result that repeats an earlier one to that earlier result.

    Exact matches are compared after normalization (Unicode NFKC, case,
    whitespace and punctuation removed); near matches need an overlap of at
    least ``threshold`` between the two results' character shingles and both
    texts to be long enough for that to be meaningful. Each result is matched
    against earlier results in rank order, so the reported rank is the first
    occurrence.
    """
    keys = [_dedup_key(r.content) for r in results]
    shingles = [_shingles(k) for k in keys]
    matches: dict[str, DuplicateMatch] = {}
    for j, later in enumerate(results):
        for i in range(j):
            earlier = results[i]
            if keys[i] == keys[j]:
                matches[later.chunk_id] = DuplicateMatch(i + 1, earlier.chunk_id, 1.0, True)
                break
            if min(len(keys[i]), len(keys[j])) < _MIN_NEAR_DUPLICATE_CHARS:
                continue
            similarity = _overlap(shingles[i], shingles[j])
            if similarity >= threshold:
                matches[later.chunk_id] = DuplicateMatch(i + 1, earlier.chunk_id, similarity, False)
                break
    return matches


def detect_duplicates(
    results: list[RetrievalResult],
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> set[str]:
    """Return the chunk_ids on either side of a duplicate or near-duplicate pair."""
    dupes: set[str] = set()
    for chunk_id, match in find_duplicates(results, threshold).items():
        dupes.add(chunk_id)
        dupes.add(match.chunk_id)
    return dupes


def print_score_stats_and_distribution(
    results: list[RetrievalResult],
    filename_map: dict[str, str],
    con: Console,
) -> None:
    """Print score statistics and document distribution for a result set."""
    scores = [r.score for r in results]
    stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0
    con.print(
        f"[bold]Score stats:[/bold]  "
        f"min=[cyan]{min(scores):.4f}[/cyan]  "
        f"max=[cyan]{max(scores):.4f}[/cyan]  "
        f"mean=[cyan]{statistics.mean(scores):.4f}[/cyan]  "
        f"σ=[cyan]{stdev:.4f}[/cyan]"
    )
    con.print()

    doc_counts: dict[str, int] = {}
    for r in results:
        name = filename_map.get(r.document_id, r.document_id[:8])
        doc_counts[name] = doc_counts.get(name, 0) + 1

    bar_max = max(doc_counts.values())
    con.print("[bold]Document distribution:[/bold]")
    for name, count in sorted(doc_counts.items(), key=lambda x: -x[1]):
        bar = "█" * max(1, int(count / bar_max * 20))
        con.print(f"  [green]{name:<40}[/green] {bar} {count}")


def _print_single_profile(
    query: str,
    profile_name: str,
    results: list[RetrievalResult],
    strategy: str,
    reranked: bool,
    filename_map: dict[str, str],
) -> None:
    duplicates = find_duplicates(results)
    duplicated_by: dict[int, list[int]] = {}
    for rank, r in enumerate(results, 1):
        match = duplicates.get(r.chunk_id)
        if match is not None:
            duplicated_by.setdefault(match.rank, []).append(rank)

    console.print()
    console.print(f"[bold]Query:[/bold] {query}")
    console.print(
        f"[bold]Profile:[/bold] {profile_name}  "
        f"[bold]Strategy:[/bold] {strategy}  "
        f"[bold]Reranked:[/bold] {'yes' if reranked else 'no'}"
    )
    console.rule()

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    for i, r in enumerate(results, 1):
        filename = filename_map.get(r.document_id, r.document_id[:8])
        retrieval_score = r.metadata.get("retrieval_score")
        score_str = f"score=[cyan]{r.score:.4f}[/cyan]"
        if retrieval_score is not None:
            score_str += f"  (retrieval=[dim]{retrieval_score:.4f}[/dim])"
        dupe_flag = ""
        match = duplicates.get(r.chunk_id)
        if match is not None:
            kind = "duplicate" if match.exact else f"near-duplicate ({match.similarity:.2f})"
            dupe_flag = f"  [yellow]⚠ {kind} of {escape(f'[{match.rank}]')}[/yellow]"
        elif i in duplicated_by:
            later = ", ".join(f"[{rank}]" for rank in duplicated_by[i])
            dupe_flag = f"  [yellow]⚠ duplicated by {escape(later)}[/yellow]"

        console.print(f"[bold]\\[{i}][/bold] {score_str}{dupe_flag}")
        console.print(
            f"    doc=[green]{filename}[/green]  "
            f"chunk=[dim]{r.chunk_id[:8]}...[/dim]"
        )
        console.print(f"    {r.content[:200].replace(chr(10), ' ')}")
        console.print()

    print_score_stats_and_distribution(results, filename_map, console)


def _print_profile_diff(
    profile_results: dict[str, tuple[list[RetrievalResult], str, bool]],
) -> None:
    profile_names = list(profile_results.keys())
    chunk_lists = {
        name: [r.chunk_id for r in results]
        for name, (results, _, _) in profile_results.items()
    }
    max_k = max(len(lst) for lst in chunk_lists.values())

    table = Table(
        title="Profile Diff: " + " vs ".join(profile_names),
        box=box.SIMPLE_HEAD,
        show_lines=False,
    )
    table.add_column("Rank", style="bold", width=4, justify="right")
    for name in profile_names:
        _, strategy, reranked = profile_results[name]
        suffix = "  reranked" if reranked else ""
        table.add_column(f"{name}\n({strategy}{suffix})", min_width=26)

    for rank in range(max_k):
        ids_at_rank = [
            chunk_lists[name][rank] if rank < len(chunk_lists[name]) else None
            for name in profile_names
        ]
        non_none = [cid for cid in ids_at_rank if cid is not None]
        all_same = len(set(non_none)) == 1 and len(non_none) == len(profile_names)

        cells = [str(rank + 1)]
        for name, chunk_id in zip(profile_names, ids_at_rank):
            if chunk_id is None:
                cells.append("[dim]—[/dim]")
                continue
            results, _, _ = profile_results[name]
            r = results[rank]
            indicator = " [green]✓[/green]" if all_same else ""
            cells.append(
                f"{chunk_id[:8]}...{indicator}\n[dim]score={r.score:.4f}[/dim]"
            )
        table.add_row(*cells)

    console.print()
    console.print(table)


def eval_cmd(
    query: str = typer.Argument(..., help="Search query to evaluate"),
    profile: List[str] = typer.Option([], "--profile", "-p", help="Profile name (repeatable for diff)"),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Number of results per profile"),
    strategy: Optional[str] = typer.Option(
        None, "--strategy", "-s", help="hybrid | vector | keyword (default: profile setting)"
    ),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="Disable reranking even if enabled in profile"),
) -> None:
    """Evaluate retrieval quality: scores, duplicates, document distribution, multi-profile diff."""
    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    profile_names = list(profile) if profile else [config.default_profile]

    for p in profile_names:
        try:
            load_profile(p, project_dir)
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    if len(profile_names) > 1:
        models = {p: load_profile(p, project_dir).embedding.model for p in profile_names}
        if len(set(models.values())) > 1:
            console.print(
                "[yellow]Warning:[/yellow] Profiles use different embedding models — "
                "scores are not directly comparable."
            )
            for p, m in models.items():
                console.print(f"  {p}: {m}")

    db_path = find_db(project_dir)
    profile_results: dict[str, tuple[list[RetrievalResult], str, bool]] = {}

    for p in profile_names:
        try:
            results, used_strategy, reranked = _run_search(
                query=query,
                profile_name=p,
                project_dir=project_dir,
                config=config,
                top_k=top_k,
                strategy=strategy,
                no_rerank=no_rerank,
            )
        except (ConnectionError, RuntimeError, ImportError, ValueError) as e:
            console.print(f"[red]Error[/red] (profile={p}): {e}")
            raise typer.Exit(1)
        profile_results[p] = (results, used_strategy, reranked)

    if len(profile_names) == 1:
        p = profile_names[0]
        results, used_strategy, reranked = profile_results[p]
        filename_map = _fetch_filenames(db_path, results)
        _print_single_profile(
            query=query,
            profile_name=p,
            results=results,
            strategy=used_strategy,
            reranked=reranked,
            filename_map=filename_map,
        )
    else:
        for p in profile_names:
            results, used_strategy, reranked = profile_results[p]
            filename_map = _fetch_filenames(db_path, results)
            _print_single_profile(
                query=query,
                profile_name=p,
                results=results,
                strategy=used_strategy,
                reranked=reranked,
                filename_map=filename_map,
            )
        _print_profile_diff(profile_results)
