from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

import typer
from rich.console import Console

from mrag.config.project import ProjectConfig, load_project_config
from mrag.core.ingestion.directory import scan_directory
from mrag.core.ingestion.document import (
    DuplicateDocumentError,
    PreparedDocument,
    add_document,
    find_duplicate,
    hash_document,
    persist_prepared_document,
    prepare_document,
)
from mrag.db.connection import find_db
from mrag.extractors import detect_source_type, get_extractor

console = Console()


def add(
    path: Path = typer.Argument(..., help="File or directory to add (txt, md)"),
    force: bool = typer.Option(False, "--force", help="Re-add if already registered."),
    recursive: bool = typer.Option(False, "--recursive", help="Recursively add a directory."),
    include: Optional[list[str]] = typer.Option(None, "--include", help="Repeatable relative-path glob."),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", help="Repeatable relative-path glob."),
    hidden: bool = typer.Option(False, "--hidden", help="Include dot-prefixed paths."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Follow links with cycle protection."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without modifying the project."),
    strict: bool = typer.Option(False, "--strict", help="Fail when any recursive item fails."),
    json_output: bool = typer.Option(False, "--json", help="Emit one machine-readable JSON object."),
) -> None:
    """Add one document or recursively ingest a directory without indexing."""
    if not path.exists():
        _fatal(f"File not found: {path}", json_output, "source_not_found")

    is_directory = path.is_dir()
    if is_directory and not recursive:
        _fatal("A directory source requires --recursive", json_output, "directory_requires_recursive", 2)
    if recursive and not is_directory:
        _fatal("--recursive requires a directory source", json_output, "recursive_source_not_directory", 2)
    if not recursive and (include or exclude or hidden or follow_symlinks or dry_run or strict):
        _fatal("Directory options require --recursive", json_output, "recursive_option_requires_recursive", 2)

    project_dir = Path.cwd()
    try:
        config = load_project_config(project_dir)
    except FileNotFoundError as error:
        _fatal(str(error), json_output, "project_not_initialized")

    if recursive:
        report = _add_directory(
            path,
            project_dir,
            config,
            force,
            include or [],
            exclude or [],
            hidden,
            follow_symlinks,
            dry_run,
        )
        _render_report(report, json_output, dry_run)
        if report["summary"]["failed"]:
            successful = len(report["items"]) - report["summary"]["failed"]
            raise typer.Exit(1 if strict or successful == 0 else 3)
        return

    _add_single(path, project_dir, config, force, json_output)


def _add_single(
    path: Path,
    project_dir: Path,
    config: ProjectConfig,
    force: bool,
    json_output: bool,
) -> None:
    try:
        document_id, warnings = add_document(
            file_path=path.resolve(),
            project_dir=project_dir,
            config=config,
            force=force,
        )
    except DuplicateDocumentError as error:
        item = _item(str(path), "skipped_duplicate", document_id=error.document_id)
        _render_report(_report([item], False, False), json_output, False)
        return
    except (OSError, ValueError) as error:
        _fatal(str(error), json_output, "add_failed")

    item = _item(str(path), "added", document_id=document_id, warnings=warnings)
    _render_report(_report([item], False, False), json_output, False)


def _add_directory(
    source_root: Path,
    project_dir: Path,
    config: ProjectConfig,
    force: bool,
    include: list[str],
    exclude: list[str],
    hidden: bool,
    follow_symlinks: bool,
    dry_run: bool,
) -> dict[str, Any]:
    try:
        scan = scan_directory(
            project_dir,
            source_root,
            include=include,
            exclude=exclude,
            hidden=hidden,
            follow_symlinks=follow_symlinks,
        )
    except ValueError as error:
        return _report([_failed(str(source_root), "directory_scan_failed", str(error))], True, dry_run)

    items: dict[str, dict[str, Any]] = {
        issue.relative_path: _failed(issue.relative_path, issue.code, issue.message)
        for issue in scan.issues
    }
    if dry_run:
        for candidate in scan.candidates:
            try:
                get_extractor(detect_source_type(candidate.source_path))
                items[candidate.relative_path] = _item(candidate.relative_path, "planned")
            except ValueError as error:
                items[candidate.relative_path] = _failed(candidate.relative_path, "unsupported_source", str(error))
        return _report(_ordered_items(items), True, True)

    db_path = find_db(project_dir)
    prepared: dict[str, PreparedDocument] = {}
    existing_ids: dict[str, str | None] = {}
    for candidate in scan.candidates:
        try:
            file_hash = hash_document(candidate.source_path)
            existing_id = find_duplicate(file_hash, db_path)
            if existing_id and not force:
                items[candidate.relative_path] = _item(
                    candidate.relative_path,
                    "skipped_duplicate",
                    document_id=existing_id,
                    original_sha256=file_hash,
                )
                continue
            existing_ids[candidate.relative_path] = existing_id
            prepared[candidate.relative_path] = prepare_document(
                candidate.source_path,
                file_hash=file_hash,
            )
        except (OSError, ValueError) as error:
            items[candidate.relative_path] = _failed(candidate.relative_path, "prepare_failed", str(error))

    for candidate in scan.candidates:
        if candidate.relative_path in items:
            continue
        document = prepared.get(candidate.relative_path)
        if document is None:
            items[candidate.relative_path] = _failed(candidate.relative_path, "prepare_failed", "Source was not prepared")
            continue
        try:
            document_id, warnings = persist_prepared_document(
                document,
                project_dir,
                config,
                existing_id=existing_ids.get(candidate.relative_path),
                force=force,
            )
            items[candidate.relative_path] = _item(
                candidate.relative_path,
                "added",
                document_id=document_id,
                original_sha256=document.file_hash,
                warnings=warnings,
            )
        except DuplicateDocumentError as error:
            items[candidate.relative_path] = _item(
                candidate.relative_path,
                "skipped_duplicate",
                document_id=error.document_id,
                original_sha256=document.file_hash,
            )
        except (OSError, ValueError) as error:
            items[candidate.relative_path] = _failed(candidate.relative_path, "persist_failed", str(error))
    return _report(_ordered_items(items), True, False)


def _item(
    source: str,
    status: str,
    *,
    document_id: str | None = None,
    original_sha256: str | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "source": source,
        "status": status,
        "document_id": document_id,
        "original_sha256": original_sha256,
        "warnings": list(warnings),
        "error": None,
    }


def _failed(source: str, code: str, message: str) -> dict[str, Any]:
    item = _item(source, "failed")
    item["error"] = {"code": code, "message": message}
    return item


def _ordered_items(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [items[source] for source in sorted(items)]


def _report(items: list[dict[str, Any]], recursive: bool, dry_run: bool) -> dict[str, Any]:
    added = sum(item["status"] == "added" for item in items)
    skipped = sum(item["status"] == "skipped_duplicate" for item in items)
    failed = sum(item["status"] == "failed" for item in items)
    status = "success" if failed == 0 else ("error" if failed == len(items) else "partial")
    return {
        "schema_version": 1,
        "command": "add",
        "status": status,
        "summary": {"added": added, "skipped": skipped, "failed": failed},
        "items": items,
        "index_started": False,
        "recursive": recursive,
        "dry_run": dry_run,
    }


def _render_report(report: dict[str, Any], json_output: bool, dry_run: bool) -> None:
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return
    for item in report["items"]:
        if item["status"] == "added":
            console.print(f"[green]✓[/green] Added [cyan]{item['source']}[/cyan]")
        elif item["status"] == "skipped_duplicate":
            console.print(f"[yellow]Skipped:[/yellow] {item['source']} already exists")
        elif item["status"] == "planned":
            console.print(f"Would add [cyan]{item['source']}[/cyan]")
        else:
            console.print(f"[red]Failed:[/red] {item['source']}: {item['error']['message']}")
        if item["document_id"]:
            console.print(f"  document_id: {item['document_id']}")
        for warning in item["warnings"]:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
    summary = report["summary"]
    console.print(f"Summary: {summary['added']} added, {summary['skipped']} skipped, {summary['failed']} failed")
    if summary["added"] and not dry_run:
        console.print("  Run [bold]mrag index[/bold] to build the retrieval index.")


def _fatal(message: str, json_output: bool, code: str, exit_code: int = 1) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                {"schema_version": 1, "command": "add", "status": "error", "error": {"code": code, "message": message}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(exit_code)
