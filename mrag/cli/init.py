import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from mrag.core.indexing.context_prompt_template import DEFAULT_CONTEXT_PROMPT_TEMPLATE
from mrag.db.connection import db_connection
from mrag.db.migrate import apply_schema
from mrag.db.tokenizer import detect_best_tokenizer, TOKENIZER_VAPORETTO

console = Console()

_MRAG_YAML_TEMPLATE = """\
project:
  name: {name}

knowledge_base:
  id: {kb_id}
  name: {kb_name}

default_profile: default

fts_tokenizer: {fts_tokenizer}

default_extraction:
  pdf:
    provider: pymupdf
    output_format: markdown

qdrant:
  mode: local
"""

_DEFAULT_PROFILE_YAML_TEMPLATE = """\
name: default

extraction:
  pdf:
    provider: pymupdf
    output_format: markdown

chunking:
  strategy: recursive
  source_format: markdown
  chunk_size: 800
  overlap: 120
  preserve_heading_path: true
  preserve_tables: true
  preserve_code_blocks: true

retrieval:
  strategy: hybrid
  top_k: 8
  dense_top_k: 20
  keyword_top_k: 20
  fusion: rrf

embedding:
  provider: ollama
  model: bge-m3
  endpoint: http://localhost:11434
  cache:
    enabled: false

augmentation:
  strategy: none

keyword:
  provider: sqlite_fts5
  tokenizer: {fts_tokenizer}
  fallback_tokenizer: trigram

rerank:
  enabled: false
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1
  max_length: 512   # BERT-based rerankers: keep at 512. Raise only for long-context models.
  top_n: 30
"""


def _slugify(name: str) -> str:
    """Convert a project name to a safe directory name."""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "mrag-project"


def init(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name"),
    kb_id: Optional[str] = typer.Option(None, "--kb-id", help="Knowledge base ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Use defaults without prompting"),
    force: bool = typer.Option(False, "--force", help="Reinitialize existing project"),
) -> None:
    """Initialize a new MRAG project in a new subdirectory."""
    cwd = Path.cwd()

    default_name = cwd.name
    if name is None:
        name = default_name if yes else typer.prompt("Project name", default=default_name)

    project_dir = cwd / _slugify(name)

    if (project_dir / "mrag.yaml").exists() and not force:
        console.print(f"[red]Error:[/red] {project_dir}/mrag.yaml already exists.")
        console.print("Use [bold]--force[/bold] to reinitialize.")
        raise typer.Exit(1)

    default_kb_id = "kb_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if kb_id is None:
        kb_id = default_kb_id if yes else typer.prompt("Knowledge base ID", default=default_kb_id)

    kb_name = name.replace("-", " ").replace("_", " ").title() + " Knowledge Base"

    # Auto-detect best tokenizer
    fts_tokenizer, lib_path = detect_best_tokenizer()
    if fts_tokenizer == TOKENIZER_VAPORETTO:
        console.print(f"[green]✓[/green] vaporetto tokenizer detected ({lib_path.name})")
    else:
        console.print("[dim]  trigram tokenizer (vaporetto not found)[/dim]")

    _create_dirs(project_dir)
    console.print("[green]✓[/green] Created directory structure")

    (project_dir / "mrag.yaml").write_text(
        _MRAG_YAML_TEMPLATE.format(
            name=name, kb_id=kb_id, kb_name=kb_name, fts_tokenizer=fts_tokenizer
        ),
        encoding="utf-8",
    )
    console.print("[green]✓[/green] Generated mrag.yaml")

    (project_dir / "profiles" / "default.yaml").write_text(
        _DEFAULT_PROFILE_YAML_TEMPLATE.format(fts_tokenizer=fts_tokenizer),
        encoding="utf-8",
    )
    console.print("[green]✓[/green] Generated profiles/default.yaml")

    (project_dir / "profiles" / "context_prompt.txt").write_text(
        DEFAULT_CONTEXT_PROMPT_TEMPLATE,
        encoding="utf-8",
    )
    console.print("[green]✓[/green] Generated profiles/context_prompt.txt")

    # Initialize DB with the chosen FTS tokenizer
    if fts_tokenizer == TOKENIZER_VAPORETTO and lib_path:
        from mrag.db.apsw_compat import ApswConnection
        from mrag.db.tokenizer import _VAPORETTO_ENTRYPOINT
        conn = ApswConnection(project_dir / "mrag.db", lib_path, _VAPORETTO_ENTRYPOINT)
        apply_schema(conn, tokenizer=fts_tokenizer)
        conn.close()
    else:
        with db_connection(project_dir / "mrag.db") as conn:
            apply_schema(conn, tokenizer=fts_tokenizer)
    console.print("[green]✓[/green] Initialized mrag.db")

    console.print()
    console.print(
        Panel(
            f"[bold]Project [cyan]{name}[/cyan] initialized.[/bold]\n\n"
            f"  Directory : [dim]{project_dir}[/dim]\n"
            f"  Tokenizer : [dim]{fts_tokenizer}[/dim]\n\n"
            "Next steps:\n"
            f"  [dim]cd {project_dir.name}[/dim]\n"
            "  [dim]mrag add <file>[/dim]          Add documents\n"
            "  [dim]mrag index[/dim]               Build retrieval index\n"
            "  [dim]mrag search <query>[/dim]      Search documents\n"
            "  [dim]mrag doctor[/dim]              Check environment\n\n"
            "Contextual augmentation prompt:\n"
            "  [dim]profiles/context_prompt.txt[/dim]  Edit to tune per-project",
            title="mrag init",
            border_style="green",
        )
    )


def _create_dirs(project_dir: Path) -> None:
    for subdir in [
        "data/documents",
        "profiles",
        "qdrant",
        "logs",
        "docs",
        "cache/embeddings",
    ]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)
