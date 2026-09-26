"""`mrag catalog` — maintenance of the project catalog itself.

``migrate-identities`` is the explicit source-identity scheme migration: the only
command that rewrites a stored identity. The plan is shown before anything is
written, every document that cannot be converted is listed — not only the first
— and a blocked plan changes nothing. MRAG Plus has the same command under the
same name, with the same report.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from mrag.core.ingestion.identity_migration import (
    MigrationPlan,
    SchemeNotMigratableError,
    apply_migration,
    plan_migration,
)
from mrag.core.ingestion.source_identity import restore_identities_notice
from mrag.db.connection import db_connection, find_db, open_connection

console = Console()
catalog_app = typer.Typer(help="Maintain the project catalog itself.", no_args_is_help=True)

COMMAND = "catalog migrate-identities"


@catalog_app.command("migrate-identities")
def migrate_identities(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change without changing anything."),
    json_output: bool = typer.Option(False, "--json", help="Emit one machine-readable JSON object."),
) -> None:
    """Convert stored source identities to the scheme this mrag produces.

    Scheme 2 names sources outside the project identities/external/... and
    unrecoverable ones identities/legacy/..., so no project directory can be
    mistaken for either. Documents keep their IDs; nothing is re-indexed.
    """
    project_dir = Path.cwd()
    try:
        db_path = find_db(project_dir)
    except FileNotFoundError as error:
        _fatal(str(error), json_output, "project_not_initialized")
        return

    try:
        if dry_run:
            # Read-only: a question about the catalog must not migrate it.
            conn = open_connection(db_path)
            try:
                plan = plan_migration(conn)
            finally:
                conn.close()
            action = "refused" if plan.is_blocked else ("unchanged" if plan.is_current else "planned")
            _finish(_report(plan, action, True, None, False), json_output)
            return

        started = datetime.now(timezone.utc)
        with db_connection(db_path) as conn:
            plan = apply_migration(conn)
    except SchemeNotMigratableError as error:
        _fatal(str(error), json_output, "source_identity_scheme_unsupported", 2)
        return

    if plan.is_blocked:
        _finish(_report(plan, "refused", False, None, False), json_output)
        return
    restored = restore_identities_notice(project_dir)
    if plan.is_current:
        _finish(_report(plan, "unchanged", False, None, restored), json_output)
        return

    report = _report(plan, "migrated", False, None, restored)
    try:
        report["audit_log"] = _write_audit_log(project_dir, report, started)
    except OSError as error:
        # The catalog is migrated and committed; only the record of it is
        # missing. A completed run with a degraded outcome, not a failure that
        # would read as "nothing happened".
        report["status"] = "degraded"
        report["warning"] = f"the catalog was migrated, but its audit log could not be written: {error}"
    _finish(report, json_output)


def _report(
    plan: MigrationPlan, action: str, dry_run: bool, audit_log: str | None, notice_restored: bool
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command": COMMAND,
        "status": "error" if plan.is_blocked else "success",
        "action": action,
        "dry_run": dry_run,
        "recorded_scheme": plan.recorded_scheme,
        "target_scheme": plan.target_scheme,
        "summary": {
            "respelled": len(plan.respellings),
            "unchanged": plan.unchanged,
            "blocked": len(plan.blockers),
        },
        "respellings": [
            {
                "document_id": change.document_id,
                "before": change.before,
                "after": change.after,
                "source_binding_status": change.binding,
            }
            for change in plan.respellings
        ],
        "blockers": [
            {
                key: value
                for key, value in {
                    "document_id": blocker.document_id,
                    "source_identity": blocker.source_identity,
                    "reason": blocker.reason,
                    "other_document_id": blocker.other_document_id,
                    "detail": blocker.detail,
                }.items()
                if value is not None or key in ("document_id", "source_identity", "reason")
            }
            for blocker in plan.blockers
        ],
        "audit_log": audit_log,
        "notice_restored": notice_restored,
    }


def _write_audit_log(project_dir: Path, report: dict[str, Any], started: datetime) -> str:
    """Keep the applied report under logs/, beside the index logs.

    The report on stdout is gone when the terminal is; which identity every
    document carried before is the one thing a later question about the
    migration needs, so it is kept on purpose.
    """
    stamp = started.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    relative = f"logs/{stamp}-catalog-migrate-identities.json"
    path = project_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**report, "audit_log": relative}, ensure_ascii=False, indent=2), encoding="utf-8")
    return relative


def _finish(report: dict[str, Any], json_output: bool) -> None:
    _render(report, json_output)
    if report["status"] == "error":
        raise typer.Exit(1)
    if report["status"] == "degraded":
        raise typer.Exit(3)


def _render(report: dict[str, Any], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(f"Source identities: scheme {report['recorded_scheme']} -> {report['target_scheme']}")
    for change in report["respellings"]:
        typer.echo(f"  {change['document_id']}")
        typer.echo(f"    {change['before']}")
        typer.echo(f"    -> {change['after']}")
    if report["blockers"]:
        typer.echo("Cannot be converted:")
        for blocker in report["blockers"]:
            typer.echo(f"  {blocker['document_id']}  {blocker['source_identity']}  ({blocker['reason']})")
    summary = report["summary"]
    typer.echo(
        f"Summary: {summary['respelled']} respelled, {summary['unchanged']} unchanged, {summary['blocked']} blocked",
        markup=False,
    )
    closing = {
        "planned": "Dry run: nothing was changed.",
        "unchanged": "The catalog already follows this scheme; nothing was changed.",
        "refused": (
            'Nothing was changed. Remove each document under "Cannot be converted" '
            "(`mrag remove <ID> --force`), move a file that sits under identities/ elsewhere "
            "in the project, add it again, and rerun this command."
        ),
    }.get(report["action"])
    if closing is None:
        closing = f"Migrated. Audit log: {report['audit_log']}" if report["audit_log"] else "Migrated."
    typer.echo(closing)
    if report["notice_restored"]:
        typer.echo("Restored identities/README.md.")
    if report.get("warning"):
        typer.echo(f"Warning: {report['warning']}")


def _fatal(message: str, json_output: bool, code: str, exit_code: int = 1) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                {"schema_version": 1, "command": "catalog", "status": "error", "error": {"code": code, "message": message}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(exit_code)
