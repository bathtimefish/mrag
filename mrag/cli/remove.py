import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mrag.config.project import load_project_config
from mrag.db.connection import find_db, fts_db_connection, open_connection
from mrag.db.qdrant import make_client

console = Console()


def remove(
    document_id: str = typer.Argument(..., help="Document ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Actually delete (no dry-run)"),
) -> None:
    """Remove a document from the knowledge base."""
    project_dir = Path.cwd()

    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        db_path = find_db(project_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    conn = open_connection(db_path)
    doc = conn.execute(
        "SELECT id, filename, original_path FROM documents WHERE id = ?", (document_id,)
    ).fetchone()

    if doc is None:
        conn.close()
        console.print(f"[red]Error:[/red] Document '{document_id}' not found.")
        raise typer.Exit(1)

    # Collect Qdrant points grouped by collection
    variant_rows = conn.execute(
        "SELECT qdrant_point_id, qdrant_collection FROM chunk_variants "
        "WHERE document_id = ? AND qdrant_point_id IS NOT NULL",
        (document_id,),
    ).fetchall()
    qdrant_by_col: dict[str, list[str]] = {}
    for vr in variant_rows:
        col = vr["qdrant_collection"]
        if col:
            qdrant_by_col.setdefault(col, []).append(vr["qdrant_point_id"])

    chunk_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
    ).fetchone()[0]

    conn.close()

    doc_dir = project_dir / "data" / "documents" / document_id

    if not force:
        console.print(f"[yellow]Dry run — pass --force to actually delete.[/yellow]")
        console.print(f"\nDocument: [bold]{doc['filename']}[/bold]  (id={document_id})")
        console.print(f"  Chunks to delete : {chunk_count}")
        for col, ids in qdrant_by_col.items():
            console.print(f"  Qdrant collection: {col}  ({len(ids)} points)")
        console.print(f"  Directory        : {doc_dir}")
        console.print(
            "\n[dim]Hint: export first with [bold]mrag export-extracted[/bold] to back up extracted text.[/dim]"
        )
        return

    # --force: hard delete
    # FTS delete uses tokenizer-aware connection (vaporetto needs ApswConnection)
    try:
        with fts_db_connection(db_path, config.fts_tokenizer) as fts_conn:
            fts_conn.execute("DELETE FROM fts_chunks WHERE document_id = ?", (document_id,))
    except Exception as e:
        console.print(f"[red]Error:[/red] FTS deletion failed: {e}")
        raise typer.Exit(1)

    conn = open_connection(db_path)
    try:
        conn.execute("DELETE FROM chunk_variants WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM document_indexes WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        console.print(f"[red]Error:[/red] SQLite deletion failed: {e}")
        raise typer.Exit(1)
    conn.close()

    # Qdrant
    if qdrant_by_col:
        try:
            qdrant_client = make_client(
                mode=config.qdrant.mode,
                host=config.qdrant.host,
                port=config.qdrant.port,
                path=project_dir / "qdrant",
            )
            for col, point_ids in qdrant_by_col.items():
                from qdrant_client.models import PointIdsList
                qdrant_client.delete(
                    collection_name=col,
                    points_selector=PointIdsList(points=point_ids),
                )
        except (ConnectionError, Exception) as e:
            console.print(f"[yellow]Warning:[/yellow] Qdrant cleanup failed (rebuild with reindex): {e}")

    # File system
    if doc_dir.exists():
        shutil.rmtree(doc_dir)

    console.print(f"[green]Deleted:[/green] {doc['filename']}  (id={document_id})")
    console.print(f"  Removed {chunk_count} chunks, {sum(len(v) for v in qdrant_by_col.values())} Qdrant points.")
