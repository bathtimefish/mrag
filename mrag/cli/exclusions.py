"""Manage persistent document-level retrieval exclusions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from mrag.config.profile import load_profile
from mrag.config.project import load_project_config
from mrag.core.exclusions import (
    ExclusionCleanupPlan,
    plan_document_cleanup,
    purge_document_index,
)
from mrag.db.connection import db_connection, find_db, open_connection
from mrag.db.exclusions import (
    DocumentExclusion,
    active_scoped_exclusions,
    create_exclusion,
    ensure_exclusions_schema,
    find_covering_exclusion,
    find_exclusion,
    list_exclusions,
    revoke_exclusion,
)


console = Console()
err_console = Console(stderr=True)

exclusions_app = typer.Typer(
    name="exclusions",
    help="Exclude retained documents from every retrieval path.",
    no_args_is_help=True,
)


def _scope(profile_name: str | None) -> str:
    return profile_name or "all profiles"


def _exclusion_payload(exclusion: DocumentExclusion) -> dict[str, Any]:
    return {
        "id": exclusion.id,
        "document_id": exclusion.document_id,
        "profile": exclusion.profile_name,
        "scope": _scope(exclusion.profile_name),
        "reason": exclusion.reason,
        "created_at": exclusion.created_at,
        "revoked_at": exclusion.revoked_at,
        "active": exclusion.active,
    }


def _plan_payload(plan: ExclusionCleanupPlan) -> dict[str, Any]:
    return {
        "document_id": plan.document_id,
        "filename": plan.filename,
        "profile": plan.profile_name,
        "scope": _scope(plan.profile_name),
        "chunks": plan.chunk_count,
        "variants": plan.variant_count,
        "fts_rows": plan.fts_count,
        "qdrant_points": plan.qdrant_point_count,
        "qdrant_collections": len(plan.qdrant_points),
    }


def _emit_json(command: str, status: str, **values: Any) -> None:
    print(
        json.dumps(
            {"schema_version": 1, "command": command, "status": status, **values},
            ensure_ascii=False,
        )
    )


def _fatal(message: str, json_output: bool, code: str, exit_code: int = 1) -> None:
    if json_output:
        _emit_json(
            "exclusions",
            "error",
            error={"code": code, "message": message},
        )
    else:
        err_console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(exit_code)


def _project(json_output: bool):
    project_dir = Path.cwd()
    try:
        config = load_project_config(project_dir)
        db_path = find_db(project_dir)
    except FileNotFoundError as error:
        _fatal(str(error), json_output, "project_not_initialized")
    return project_dir, config, db_path


def _document(db_path: Path, document_id: str, json_output: bool):
    conn = open_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, filename, status FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        _fatal(
            f"Document '{document_id}' not found.",
            json_output,
            "document_not_found",
        )
    return row


def _validate_profile(project_dir: Path, profile: str | None, json_output: bool) -> None:
    if profile is None:
        return
    try:
        load_profile(profile, project_dir)
    except (FileNotFoundError, ValueError) as error:
        _fatal(str(error), json_output, "profile_invalid", 2)


def _render_plan(plan: ExclusionCleanupPlan, *, restore: bool = False) -> None:
    action = "restore" if restore else "exclude"
    console.print("[yellow]Dry run — pass --force to apply.[/yellow]")
    console.print()
    console.print(f"Document: {plan.filename}  (id={plan.document_id})", markup=False)
    console.print(f"  Scope          : {_scope(plan.profile_name)}", markup=False)
    console.print(f"  Chunks         : {plan.chunk_count}")
    console.print(f"  Variants       : {plan.variant_count}")
    console.print(f"  FTS rows       : {plan.fts_count}")
    console.print(f"  Qdrant points  : {plan.qdrant_point_count}")
    if not restore:
        console.print("  Source files   : retained")
        console.print("  Future reindex : excluded")
    console.print()
    console.print(f"Run again with --force to {action} this document.", markup=False)


@exclusions_app.command("add")
def exclusions_add(
    document_id: str = typer.Option(
        ...,
        "--document-id",
        help="Stable document ID to exclude.",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        "-p",
        help="Limit the exclusion to one profile (default: every profile).",
    ),
    reason: Optional[str] = typer.Option(
        None,
        "--reason",
        help="Optional audit reason (maximum 1000 characters).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Apply the exclusion."),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON object."),
) -> None:
    """Immediately exclude a retained document from retrieval."""
    project_dir, config, db_path = _project(json_output)
    document = _document(db_path, document_id, json_output)
    _validate_profile(project_dir, profile, json_output)
    if reason is not None and len(reason.strip()) > 1000:
        _fatal(
            "exclusion reason must be at most 1000 characters",
            json_output,
            "exclusion_reason_too_long",
            2,
        )

    with db_connection(db_path) as conn:
        ensure_exclusions_schema(conn)

    existing = find_covering_exclusion(db_path, document_id, profile)
    if existing is not None and not force:
        if json_output:
            _emit_json(
                "exclusions.add",
                "already_active",
                exclusion=_exclusion_payload(existing),
            )
        else:
            console.print(
                f"[green]Already excluded:[/green] {document['filename']} "
                f"(exclusion_id={existing.id}, scope={_scope(existing.profile_name)})"
            )
        return

    if profile is None:
        scoped = active_scoped_exclusions(db_path, document_id)
        if scoped:
            ids = ", ".join(exclusion.id for exclusion in scoped)
            _fatal(
                "Profile-scoped exclusions already exist for this document; "
                f"restore them before creating an all-profile exclusion: {ids}",
                json_output,
                "exclusion_scope_conflict",
                2,
            )

    try:
        plan = plan_document_cleanup(db_path, document_id, profile)
    except KeyError:
        _fatal(f"Document '{document_id}' not found.", json_output, "document_not_found")

    if not force:
        if json_output:
            _emit_json("exclusions.add", "dry_run", cleanup=_plan_payload(plan))
        else:
            _render_plan(plan)
        return

    if existing is None:
        try:
            exclusion = create_exclusion(db_path, document_id, profile, reason)
        except Exception as error:
            _fatal(str(error), json_output, "exclusion_create_failed")
    else:
        exclusion = existing

    try:
        cleanup = purge_document_index(
            project_dir=project_dir,
            db_path=db_path,
            config=config,
            document_id=document_id,
            profile_name=profile,
        )
    except Exception as error:
        message = (
            "The exclusion policy is active, but physical index cleanup failed; "
            f"retrieval filtering remains enforced ({error})"
        )
        if json_output:
            _emit_json(
                "exclusions.add",
                "degraded",
                exclusion=_exclusion_payload(exclusion),
                cleanup=_plan_payload(plan),
                warnings=[message],
            )
        else:
            console.print(
                f"[yellow]Excluded with pending cleanup:[/yellow] {document['filename']}"
            )
            err_console.print(f"[yellow]Warning:[/yellow] {message}")
        raise typer.Exit(3)

    status = "degraded" if cleanup.warnings else "applied"
    if json_output:
        _emit_json(
            "exclusions.add",
            status,
            exclusion=_exclusion_payload(exclusion),
            cleanup=_plan_payload(cleanup.plan),
            warnings=list(cleanup.warnings),
        )
    else:
        label = "Exclusion reconciled" if existing is not None else "Excluded"
        console.print(
            f"[green]{label}:[/green] {document['filename']}  "
            f"(id={document_id}, scope={_scope(profile)})"
        )
        if not cleanup.warnings:
            console.print(
                f"  Removed {cleanup.plan.fts_count} FTS rows, "
                f"{cleanup.plan.chunk_count} chunks, and "
                f"{cleanup.plan.qdrant_point_count} Qdrant points."
            )
        console.print("  Original and extracted source artifacts were retained.")
        for warning in cleanup.warnings:
            err_console.print(f"[yellow]Warning:[/yellow] {warning}")
    if cleanup.warnings:
        raise typer.Exit(3)


@exclusions_app.command("list")
def exclusions_list(
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Include restored/revoked exclusions.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON object."),
) -> None:
    """List active document exclusions and their audit identifiers."""
    _, _, db_path = _project(json_output)
    exclusions = list_exclusions(db_path, include_revoked=show_all)

    conn = open_connection(db_path)
    try:
        filenames = {
            row["id"]: row["filename"]
            for row in conn.execute("SELECT id, filename FROM documents").fetchall()
        }
    finally:
        conn.close()

    if json_output:
        _emit_json(
            "exclusions.list",
            "success",
            count=len(exclusions),
            exclusions=[
                {**_exclusion_payload(exclusion), "filename": filenames.get(exclusion.document_id)}
                for exclusion in exclusions
            ],
        )
        return

    if not exclusions:
        console.print("No document exclusions.")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Exclusion ID")
    table.add_column("Document")
    table.add_column("Scope")
    table.add_column("State")
    table.add_column("Reason")
    for exclusion in exclusions:
        filename = filenames.get(exclusion.document_id, "(document removed)")
        table.add_row(
            exclusion.id,
            f"{filename}\n{exclusion.document_id}",
            _scope(exclusion.profile_name),
            "active" if exclusion.active else "restored",
            exclusion.reason or "-",
        )
    console.print(table)


@exclusions_app.command("restore")
def exclusions_restore(
    exclusion_id: str = typer.Argument(..., help="Exclusion ID returned by list/add."),
    force: bool = typer.Option(False, "--force", "-f", help="Restore index eligibility."),
    json_output: bool = typer.Option(False, "--json", help="Emit one JSON object."),
) -> None:
    """Revoke an exclusion; a later index rebuilds the retained document."""
    project_dir, config, db_path = _project(json_output)
    exclusion = find_exclusion(db_path, exclusion_id)
    if exclusion is None:
        _fatal(
            f"Exclusion '{exclusion_id}' not found.",
            json_output,
            "exclusion_not_found",
        )
    if not exclusion.active:
        if json_output:
            _emit_json(
                "exclusions.restore",
                "already_restored",
                exclusion=_exclusion_payload(exclusion),
            )
        else:
            console.print(f"[green]Already restored:[/green] {exclusion.id}")
        return

    document = _document(db_path, exclusion.document_id, json_output)
    plan = plan_document_cleanup(
        db_path,
        exclusion.document_id,
        exclusion.profile_name,
    )
    if not force:
        if json_output:
            _emit_json(
                "exclusions.restore",
                "dry_run",
                exclusion=_exclusion_payload(exclusion),
                cleanup=_plan_payload(plan),
            )
        else:
            _render_plan(plan, restore=True)
        return

    try:
        cleanup = purge_document_index(
            project_dir=project_dir,
            db_path=db_path,
            config=config,
            document_id=exclusion.document_id,
            profile_name=exclusion.profile_name,
        )
    except Exception as error:
        _fatal(
            "The exclusion remains active because residual index cleanup failed: "
            f"{error}",
            json_output,
            "exclusion_restore_cleanup_failed",
        )

    if cleanup.warnings:
        message = (
            "The exclusion remains active until Qdrant cleanup can be retried."
        )
        if json_output:
            _emit_json(
                "exclusions.restore",
                "degraded",
                exclusion=_exclusion_payload(exclusion),
                warnings=[*cleanup.warnings, message],
            )
        else:
            err_console.print(f"[yellow]Warning:[/yellow] {cleanup.warnings[0]}")
            err_console.print(f"[yellow]Warning:[/yellow] {message}")
        raise typer.Exit(3)

    restored = revoke_exclusion(db_path, exclusion_id)
    next_command = f"mrag index --document-id {exclusion.document_id}"
    if exclusion.profile_name:
        next_command += f" --profile {exclusion.profile_name}"

    if json_output:
        _emit_json(
            "exclusions.restore",
            "restored",
            exclusion=_exclusion_payload(restored),
            next_command=next_command,
            warnings=[],
        )
    else:
        console.print(
            f"[green]Restored index eligibility:[/green] {document['filename']} "
            f"(exclusion_id={exclusion_id})"
        )
        console.print(f"Next: {next_command}", markup=False)


__all__ = ["exclusions_app"]
