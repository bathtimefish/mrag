import json
import re
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from mrag.config.kb_info import (
    KbInformationConfig,
    KbInformationInput,
    build_minimal_kb_info,
    dump_kb_info,
    kb_info_json_schema,
)
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


def _default_kb_id_from_name(name: str) -> str:
    """Derive a slug-safe kb_id from a project name."""
    return "kb_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _default_kb_display_name(name: str) -> str:
    """Derive a human-readable knowledge-base display name from project name."""
    return name.replace("-", " ").replace("_", " ").title() + " Knowledge Base"


def _load_kb_info_json(path: Path) -> KbInformationInput:
    """Read and validate a --kb-info-json file. Exits 1 on any error."""
    if not path.exists():
        console.print(f"[red]Error:[/red] kb-info-json file not found: {path}")
        raise typer.Exit(1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        console.print(f"[red]Error:[/red] invalid JSON in {path}: {e}")
        raise typer.Exit(1)
    try:
        return KbInformationInput(**data)
    except ValidationError as e:
        console.print(f"[red]Error:[/red] kb_information validation failed:")
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            console.print(f"  - [yellow]{loc}[/yellow]: {err['msg']}")
        raise typer.Exit(1)


def init(
    project_dir_arg: Optional[Path] = typer.Argument(
        None,
        metavar="[PROJECT_DIR]",
        help="Project directory path. When given, the project is created at this path. "
             "When omitted, the project is created as a subdirectory of cwd named after --name.",
    ),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name"),
    kb_id: Optional[str] = typer.Option(None, "--kb-id", help="Knowledge base ID"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not ask prompts; use defaults for all fields not provided on the CLI.",
    ),
    kb_info_json: Optional[Path] = typer.Option(
        None,
        "--kb-info-json",
        help="Read KB information from a JSON file and generate a fully populated kb_information.yaml.",
    ),
    print_kb_info_schema: bool = typer.Option(
        False,
        "--print-kb-info-schema",
        help="Print the JSON Schema for --kb-info-json input and exit. Other arguments are ignored.",
    ),
    force: bool = typer.Option(False, "--force", help="Reinitialize existing project"),
) -> None:
    """Initialize a new MRAG project."""
    # --print-kb-info-schema short-circuits everything else (no file writes).
    if print_kb_info_schema:
        print(json.dumps(kb_info_json_schema(), indent=2, ensure_ascii=False))
        raise typer.Exit(0)

    # -----------------------------------------------------------------------
    # Phase 1: Resolve project location + identity
    # -----------------------------------------------------------------------
    kb_info_input: Optional[KbInformationInput] = None
    if kb_info_json is not None:
        kb_info_input = _load_kb_info_json(kb_info_json)

    # Source of truth precedence for `name`:
    #   1. --name (explicit override)
    #   2. kb_info_input.project.name (when --kb-info-json given)
    #   3. PROJECT_DIR basename (when positional given)
    #   4. interactive prompt (default mode)
    #   5. cwd basename (non-interactive default)
    cwd = Path.cwd()
    if project_dir_arg is not None:
        project_dir = project_dir_arg.expanduser().resolve()
        derived_name = project_dir.name
    else:
        project_dir = None
        derived_name = cwd.name

    if name is None:
        if kb_info_input is not None:
            name = kb_info_input.project.name
        elif non_interactive:
            name = derived_name
        else:
            name = typer.prompt("Project name", default=derived_name)

    # If no positional dir given, the project lives at cwd/<slug>.
    if project_dir is None:
        project_dir = cwd / _slugify(name)

    if (project_dir / "mrag.yaml").exists() and not force:
        console.print(f"[red]Error:[/red] {project_dir}/mrag.yaml already exists.")
        console.print("Use [bold]--force[/bold] to reinitialize.")
        raise typer.Exit(1)

    # kb_id precedence:
    #   1. --kb-id
    #   2. kb_info_input.knowledge_base.id
    #   3. derived from name (non-interactive default)
    #   4. interactive prompt
    default_kb_id = _default_kb_id_from_name(name)
    if kb_id is None:
        if kb_info_input is not None:
            kb_id = kb_info_input.knowledge_base.id
        elif non_interactive:
            kb_id = default_kb_id
        else:
            kb_id = typer.prompt("Knowledge base ID", default=default_kb_id)

    # kb_name precedence:
    #   1. kb_info_input.knowledge_base.name
    #   2. derived from name
    if kb_info_input is not None:
        kb_name = kb_info_input.knowledge_base.name
    else:
        kb_name = _default_kb_display_name(name)

    # description: only prompted in interactive mode (not non-interactive, not json).
    description: str = ""
    if kb_info_input is not None:
        description = kb_info_input.knowledge_base.description
    elif not non_interactive:
        description = typer.prompt(
            "Knowledge base description (for AI agents; press Enter to skip)",
            default="",
            show_default=False,
        )

    # -----------------------------------------------------------------------
    # Phase 2: Build kb_information.yaml content
    # -----------------------------------------------------------------------
    if kb_info_input is not None:
        kb_info_config: KbInformationConfig = kb_info_input.to_kb_information()
    else:
        kb_info_config = build_minimal_kb_info(name=kb_name, kb_id=kb_id)
        if description:
            kb_info_config.knowledge_base.description = description

    # -----------------------------------------------------------------------
    # Phase 3: Detect tokenizer
    # -----------------------------------------------------------------------
    fts_tokenizer, lib_path = detect_best_tokenizer()
    if fts_tokenizer == TOKENIZER_VAPORETTO:
        console.print(f"[green]✓[/green] vaporetto tokenizer detected ({lib_path.name})")
    else:
        console.print("[dim]  trigram tokenizer (vaporetto not found)[/dim]")

    # -----------------------------------------------------------------------
    # Phase 4: Create directory structure and write files
    # -----------------------------------------------------------------------
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

    dump_kb_info(kb_info_config, project_dir)
    console.print("[green]✓[/green] Generated kb_information.yaml")

    # -----------------------------------------------------------------------
    # Phase 5: Initialize DB
    # -----------------------------------------------------------------------
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
            "Edit agent-facing metadata anytime:\n"
            "  [dim]kb_information.yaml[/dim]          KB description for Agentic RAG\n"
            "  [dim]profiles/context_prompt.txt[/dim]  Contextual augmentation prompt",
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
