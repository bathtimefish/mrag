"""`mrag documents` — keep the catalog in step with the files documents came from.

``sync <DIR>`` compares what the catalog says lives under a directory with what
the directory holds now and reports the difference as a plan; ``--apply``
carries it out, each item as its own transaction. ``rebind <ID> <FILE>`` binds
one document to one file by hand when the sync's one-to-one evidence is not
there. Both decide through pure planners (``sync_plan``, ``rebind_plan``) that
the shared decision table in ``tests/fixtures`` checks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from mrag.config.profile import load_profile
from mrag.config.project import ProjectConfig, load_project_config
from mrag.core.indexing.pipeline import run_index, write_index_log
from mrag.core.ingestion.directory import scan_directory
from mrag.core.ingestion.document import (
    DuplicateDocumentError,
    hash_document,
    persist_prepared_document,
    prepare_document,
    record_root_ancestors,
)
from mrag.core.ingestion.rebind_plan import RebindRefused, plan_rebind
from mrag.core.ingestion.source_identity import (
    SCHEME_KEY,
    ReservedPathError,
    SchemeUnsupportedError,
    binding_status,
    is_reserved,
    require_scheme,
    resolve_root,
    root_key,
    source_identity,
)
from mrag.core.ingestion.sync_plan import (
    ExternalDirectory,
    ProjectDirectory,
    ScannedSource,
    SourceRootFacts,
    SyncContent,
    SyncDocument,
    SyncExclusion,
    SyncPlan,
    SyncPlanItem,
    plan_sync,
    resolve_sync_scope,
)
from mrag.core.interrupt import CANCELLED_EXIT, Interrupt
from mrag.db.connection import db_connection, find_db
from mrag.db.exclusions import active_exclusions_by_document, create_exclusion, revoke_exclusions
from mrag.extractors import detect_source_type

console = Console()
err_console = Console(stderr=True)
documents_app = typer.Typer(
    name="documents",
    help="Keep the catalog's documents in step with the files they came from.",
    no_args_is_help=True,
)

SYNC_COMMAND = "documents sync"
REBIND_COMMAND = "documents rebind"
SYNC_EXCLUSION_REASON = "source file missing at documents sync"
DEGRADED_EXIT = 3
USAGE_EXIT = 2


class CommandError(Exception):
    """A failure with a stable code and the exit code the contract gives it."""

    def __init__(self, code: str, message: str, exit_code: int = 1, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.context = context


# ---------------------------------------------------------------------------
# Where the directory is, and what the catalog holds
# ---------------------------------------------------------------------------


@dataclass
class DirectoryFacts:
    directory: ProjectDirectory | ExternalDirectory
    canonical: Path
    # Where every root the run could place is, for existence checks.
    root_paths: dict[str, Path]
    # The directory by name only, for the report.
    root: str
    binding: str


def _locate_directory(project_dir: Path, directory: Path, registered: set[str], follow_symlinks: bool) -> DirectoryFacts:
    try:
        canonical = directory.resolve(strict=True)
    except OSError as error:
        raise CommandError("sync_directory_not_found", "the directory to sync does not exist or cannot be read", USAGE_EXIT) from error
    if not canonical.is_dir():
        raise CommandError("sync_directory_not_a_directory", "documents sync reconciles a directory; to add one file, use mrag add", USAGE_EXIT)
    project = project_dir.resolve(strict=True)
    if canonical.is_relative_to(project):
        prefix = canonical.relative_to(project).as_posix() if canonical != project else ""
        first = prefix.split("/", 1)[0]
        if first == "data":
            raise CommandError("sync_directory_is_project_data", "the project's data/ directory holds mrag's own artifacts and cannot be synced", USAGE_EXIT)
        if first and is_reserved(prefix):
            raise CommandError("sync_directory_is_reserved", "the project's identities/ directory is reserved and cannot be synced", USAGE_EXIT)
        return DirectoryFacts(ProjectDirectory(prefix), canonical, {}, prefix or ".", "project_relative")
    key = root_key(canonical)
    ancestor_roots: dict[str, str] = {}
    root_paths: dict[str, Path] = {}
    for ancestor in (canonical, *canonical.parents):
        ancestor_key = root_key(ancestor)
        if ancestor_key in registered:
            ancestor_roots[ancestor_key] = canonical.relative_to(ancestor).as_posix() if ancestor != canonical else ""
            root_paths[ancestor_key] = ancestor
    found: set[str] = set()
    for current, directories, _files in os.walk(canonical, followlinks=follow_symlinks):
        for name in directories:
            path = Path(current) / name
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            found_key = root_key(resolved)
            if found_key in registered:
                found.add(found_key)
                root_paths.setdefault(found_key, resolved)
    label = canonical.name or "external"
    return DirectoryFacts(ExternalDirectory(key, ancestor_roots, frozenset(found)), canonical, root_paths, label, "external_root")


@dataclass
class CatalogFacts:
    documents: list[SyncDocument]
    roots: list[SourceRootFacts]
    labels: dict[str, str]

    @property
    def registered(self) -> set[str]:
        return {root.key for root in self.roots}


def _catalog_facts(conn) -> CatalogFacts:
    scheme = conn.execute("SELECT value FROM catalog_settings WHERE key = ?", (SCHEME_KEY,)).fetchone()
    require_scheme(scheme["value"] if scheme else None)
    labels = {row["root_key"]: row["label"] for row in conn.execute("SELECT root_key, label FROM source_roots")}
    ancestors: dict[str, set[str]] = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_root_ancestors'").fetchone():
        for row in conn.execute("SELECT root_key, ancestor_key FROM source_root_ancestors"):
            ancestors.setdefault(row["root_key"], set()).add(row["ancestor_key"])
    roots = [SourceRootFacts(key, frozenset(ancestors.get(key, set()))) for key in labels]
    exclusions = active_exclusions_by_document(conn)
    documents = []
    for row in conn.execute("SELECT id, source_identity, file_hash, status FROM documents ORDER BY id"):
        content = SyncContent(row["file_hash"]) if row["status"] == "extracted" else None
        rules = [SyncExclusion(rule.id, rule.profile_name, rule.origin) for rule in exclusions.get(row["id"], [])]
        documents.append(SyncDocument(row["id"], row["source_identity"], content, rules))
    return CatalogFacts(documents, roots, labels)


def _exists_as_spelled(base: Path, relative: str) -> bool:
    """Whether ``relative`` names an existing entry under ``base``, spelled exactly.

    ``Path.exists`` asks the file system, and a case-insensitive or
    normalization-insensitive one says yes to another spelling of a name. A
    file renamed only in case would then still "exist" under its old spelling
    and never move, so each component is compared with the directory's own
    entries byte for byte.
    """
    current = base
    for component in (part for part in relative.split("/") if part):
        try:
            entries = os.listdir(current)
        except OSError:
            return False
        wanted = os.fsencode(component)
        if not any(os.fsencode(entry) == wanted for entry in entries):
            return False
        current = current / component
    return current.exists()


def _source_location(identity: str, project_dir: Path, root_paths: dict[str, Path]) -> tuple[Path, str] | None:
    binding = binding_status(identity)
    if binding == "project_relative":
        return project_dir, identity
    if binding == "external_root":
        key, _, relative = identity[len("identities/external/"):].partition("/")
        base = root_paths.get(key)
        return (base, relative) if base is not None else None
    return None


# ---------------------------------------------------------------------------
# Preparing the plan
# ---------------------------------------------------------------------------


@dataclass
class ScannedFile:
    identity: str
    path: Path
    relative_path: str


@dataclass
class Prepared:
    plan: SyncPlan
    files: dict[str, ScannedFile]
    issues: list[dict[str, str]]
    unsupported: list[str]
    facts: DirectoryFacts
    catalog: CatalogFacts


def _prepare(project_dir: Path, directory: Path, options: dict[str, Any], conn) -> Prepared:
    catalog = _catalog_facts(conn)
    registered = catalog.registered
    facts = _locate_directory(project_dir, directory, registered, options["follow_symlinks"])
    scope = resolve_sync_scope(facts.directory, catalog.documents, catalog.roots)
    try:
        scan = scan_directory(
            project_dir,
            directory,
            include=options["include"],
            exclude=options["exclude"],
            hidden=options["hidden"],
            follow_symlinks=options["follow_symlinks"],
        )
    except ValueError as error:
        raise CommandError("sync_scan_failed", str(error), USAGE_EXIT) from error
    preferred = facts.canonical if isinstance(facts.directory, ExternalDirectory) else None
    issues = [{"path": issue.relative_path, "code": issue.code, "message": issue.message} for issue in scan.issues]
    files: dict[str, ScannedFile] = {}
    scanned: list[ScannedSource] = []
    present: set[str] = set()
    unsupported: list[str] = []
    for candidate in scan.candidates:
        try:
            identity, _new_root = source_identity(candidate.source_path, project_dir, registered, preferred)
        except (ReservedPathError, OSError, ValueError) as error:
            issues.append({"path": candidate.relative_path, "code": "source_identity_failed", "message": str(error)})
            continue
        try:
            detect_source_type(candidate.source_path)
        except ValueError:
            # A format mrag does not ingest is reported, not failed: a corpus
            # directory holds files that are not documents.
            unsupported.append(candidate.relative_path)
            present.add(identity)
            continue
        try:
            content_hash = hash_document(candidate.source_path)
        except OSError as error:
            issues.append({"path": candidate.relative_path, "code": "native_source_read_failed", "message": str(error)})
            continue
        present.add(identity)
        files[identity] = ScannedFile(identity, candidate.source_path, candidate.relative_path)
        scanned.append(ScannedSource(identity, SyncContent(content_hash), True))
    # A scoped document the scan did not find is missing only if its file is
    # not there. Everything else that exists is out of scope.
    present_out_of_scope = []
    for document in catalog.documents:
        if document.document_id not in scope.documents or document.identity in files:
            continue
        if document.identity in present:
            present_out_of_scope.append(document.identity)
            continue
        location = _source_location(document.identity, project_dir, facts.root_paths)
        if location is not None and _exists_as_spelled(location[0], location[1]):
            present_out_of_scope.append(document.identity)
    plan = plan_sync(catalog.documents, scope, scanned, present_out_of_scope)
    return Prepared(plan, files, issues, unsupported, facts, catalog)


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


def _item_report(item: SyncPlanItem, status: str) -> dict[str, Any]:
    return {
        "action": item.action,
        "status": status,
        "document_id": item.document_id,
        "source_identity": item.source_identity,
        "previous_identity": item.previous_identity,
        "content_hash": item.content.content_hash if item.content else None,
        "reason": item.reason,
    }


def _failed_item(item: SyncPlanItem, code: str, message: str, reason: str | None = None) -> dict[str, Any]:
    report = _item_report(item, "failed")
    if reason is not None:
        report["reason"] = reason
    report["error"] = {"code": code, "message": message}
    return report


def _register_root_for(conn, path: Path, project_dir: Path, registered: set[str], preferred: Path | None, labels: dict[str, str]) -> None:
    """Register the root a moved or added file now lives under, with its ancestors."""
    located = resolve_root(path, project_dir, registered, preferred)
    if located is None:
        return
    key, root = located
    if key not in registered:
        conn.execute("INSERT OR IGNORE INTO source_roots (root_key, label) VALUES (?, ?)", (key, root.name or "external"))
        registered.add(key)
        labels[key] = root.name or "external"
    record_root_ancestors(conn, key, root)


def _apply(prepared: Prepared, project_dir: Path, config: ProjectConfig, db_path: Path, interrupt: Interrupt) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    cancelled = False
    external = isinstance(prepared.facts.directory, ExternalDirectory)
    preferred = prepared.facts.canonical if external else None
    registered = prepared.catalog.registered
    # Roots found by the walk that recorded no ancestors gain them now, so a
    # later sync can place them once their directory is gone.
    with db_connection(db_path) as conn:
        for key, path in prepared.facts.root_paths.items():
            record_root_ancestors(conn, key, path)
    for item in prepared.plan.items:
        if not item.mutates:
            items.append(_item_report(item, "reported"))
            continue
        if cancelled or interrupt.is_cancelled():
            cancelled = True
            items.append(_item_report(item, "cancelled"))
            continue
        try:
            items.append(_apply_one(item, prepared, project_dir, config, db_path, preferred, registered))
        except CommandError as error:
            items.append(_failed_item(item, error.code, str(error), error.context.get("reason")))
        except (OSError, ValueError) as error:
            items.append(_failed_item(item, "documents_sync_item_failed", str(error)))
    return items, cancelled


def _apply_one(item: SyncPlanItem, prepared: Prepared, project_dir: Path, config: ProjectConfig, db_path: Path, preferred: Path | None, registered: set[str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if item.action in ("add", "update"):
        scanned = prepared.files[item.source_identity]
        current = hash_document(scanned.path)
        if item.content is None or current != item.content.content_hash:
            # The one failure the sync itself defines: the bytes moved between
            # the plan and the write, and the next run picks them up.
            raise CommandError("ingestion_source_changed", "the file changed between the plan and the write", reason="changed_during_sync")
        try:
            document_id, _warnings = persist_prepared_document(
                prepare_document(scanned.path, file_hash=current),
                project_dir,
                config,
                force=item.action == "update",
                source_root=preferred,
            )
        except DuplicateDocumentError as error:
            raise CommandError("documents_sync_duplicate_appeared", "another document gained this content between the plan and the write", held_by=error.document_id) from error
        if item.revoke_exclusions:
            with db_connection(db_path) as conn:
                revoke_exclusions(conn, item.revoke_exclusions)
        report = _item_report(item, "applied")
        report["document_id"] = document_id
        return report
    if item.action == "move":
        scanned = prepared.files[item.source_identity]
        with db_connection(db_path) as conn:
            conn.execute(
                "UPDATE documents SET source_identity = ?, filename = ?, updated_at = ? WHERE id = ?",
                (item.source_identity, scanned.path.name, now, item.document_id),
            )
            _register_root_for(conn, scanned.path, project_dir, registered, preferred, prepared.catalog.labels)
            revoke_exclusions(conn, item.revoke_exclusions)
        return _item_report(item, "applied")
    if item.action == "exclude":
        create_exclusion(db_path, item.document_id, None, SYNC_EXCLUSION_REASON, origin="sync")
        return _item_report(item, "applied")
    if item.action == "restore":
        with db_connection(db_path) as conn:
            revoke_exclusions(conn, item.revoke_exclusions)
        return _item_report(item, "applied")
    raise CommandError("documents_sync_action_unknown", f"the plan holds an action the sync cannot apply: {item.action}")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _summary(prepared: Prepared, items: list[dict[str, Any]]) -> dict[str, Any]:
    def count(action: str) -> int:
        return sum(1 for item in items if item["action"] == action)
    failed = sum(1 for item in items if item["status"] == "failed") + len(prepared.issues)
    return {
        "add": count("add"), "update": count("update"), "move": count("move"),
        "exclude": count("exclude"), "restore": count("restore"), "noop": count("noop"),
        "duplicate": count("duplicate"), "adopt_candidate": count("adopt_candidate"),
        "blocked": count("blocked"), "out_of_scope": prepared.plan.out_of_scope,
        "unsupported": len(prepared.unsupported), "failed": failed,
        "unresolvable_roots": prepared.plan.unresolvable_roots,
    }


def _report(prepared: Prepared, items: list[dict[str, Any]], applied: bool, cancelled: bool) -> dict[str, Any]:
    summary = _summary(prepared, items)
    status = "cancelled" if cancelled else ("partial" if summary["failed"] or summary["blocked"] else "success")
    return {
        "schema_version": 1,
        "command": SYNC_COMMAND,
        "status": status,
        "applied": applied,
        "root": prepared.facts.root,
        "directory_binding": prepared.facts.binding,
        "summary": summary,
        "items": items,
        "scan_issues": prepared.issues,
        "audit_log": None,
    }


def _exit_code(report: dict[str, Any]) -> int:
    if report["status"] == "cancelled":
        return CANCELLED_EXIT
    index = report.get("index") or {}
    if report["status"] == "degraded" or index.get("status") == "failed" or report["summary"]["failed"] or report["summary"]["blocked"]:
        return DEGRADED_EXIT
    return 0


def _write_audit_log(project_dir: Path, name: str, report: dict[str, Any], started: datetime) -> str:
    stamp = started.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    relative = f"logs/{stamp}-{name}.json"
    path = project_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**report, "audit_log": relative}, ensure_ascii=False, indent=2), encoding="utf-8")
    return relative


def _emit(report: dict[str, Any], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return
    verb = {
        "add": ("Would add", "Added"), "update": ("Would update", "Updated"), "move": ("Would move", "Moved"),
        "exclude": ("Would exclude", "Excluded"), "restore": ("Would restore", "Restored"),
    }
    for item in report["items"]:
        if item["status"] == "failed":
            err_console.print(f"[red]Failed[/red] {item['source_identity']}: {item['error']['message']}")
            continue
        if item["status"] == "cancelled":
            err_console.print(f"[yellow]Cancelled[/yellow] {item['source_identity']}")
            continue
        if item["action"] in verb:
            planned, done = verb[item["action"]]
            where = f" (from {item['previous_identity']})" if item["previous_identity"] else ""
            console.print(f"{done if report['applied'] else planned} {item['source_identity']}{where}")
        elif item["action"] in ("duplicate", "adopt_candidate", "blocked"):
            console.print(f"{item['action'].replace('_', ' ').capitalize()} {item['source_identity']} ({item['reason']})")
    for issue in report["scan_issues"]:
        err_console.print(f"[red]Failed[/red] {issue['path']}: {issue['message']}")
    summary = report["summary"]
    if summary["out_of_scope"]:
        console.print(f"Out of scope: {summary['out_of_scope']} document(s) whose files exist outside the scan's filters")
    if summary["unsupported"]:
        console.print(f"Unsupported: {summary['unsupported']} file(s) in formats mrag does not ingest")
    if summary["unresolvable_roots"]:
        console.print(f"Unresolvable roots: {summary['unresolvable_roots']} registered before ancestors were recorded and not found under this directory")
    console.print(
        f"Summary: {summary['add']} add, {summary['update']} update, {summary['move']} move, {summary['exclude']} exclude, "
        f"{summary['restore']} restore, {summary['noop']} noop, {summary['duplicate']} duplicate, "
        f"{summary['adopt_candidate']} adopt candidate, {summary['failed']} failed"
    )
    if not report["applied"] and any(item["action"] in verb for item in report["items"]):
        console.print("Run again with --apply to make these changes.")
    index = report.get("index")
    if index:
        if index["status"] == "skipped":
            why = {"no_changes": "the sync changed nothing", "no_indexed_profile": "no profile has been indexed yet; name one with --profile", "cancelled": "the sync was interrupted"}
            console.print(f"Index: skipped, {why.get(index.get('reason'), index.get('reason'))}")
        else:
            for profile in index["profiles"]:
                line = f"Index {profile['profile']}: {profile['status']}"
                if profile.get("error"):
                    line += f" — {profile['error']['message']}"
                console.print(line)
            if index.get("next_action"):
                console.print(f"Next: {index['next_action']}")
    if report.get("audit_log"):
        console.print(f"Audit log: {report['audit_log']}")
    if report["status"] == "cancelled":
        err_console.print("Interrupted; items already applied were kept.")
    if report.get("warning"):
        err_console.print(f"[yellow]Warning:[/yellow] {report['warning']}")


def _fatal(command: str, error: CommandError, json_output: bool) -> None:
    if json_output:
        body: dict[str, Any] = {"code": error.code, "message": str(error)}
        body.update({key: value for key, value in error.context.items() if value is not None})
        typer.echo(json.dumps({"schema_version": 1, "command": command, "status": "error", "error": body}, ensure_ascii=False, separators=(",", ":")))
    else:
        err_console.print(f"[red]Error:[/red] {error}")
    raise typer.Exit(error.exit_code)


# ---------------------------------------------------------------------------
# Indexing after the sync
# ---------------------------------------------------------------------------


def _index_targets(project_dir: Path, db_path: Path, requested: list[str]) -> list[str]:
    if requested:
        for name in requested:
            try:
                load_profile(name, project_dir)
            except (FileNotFoundError, ValueError) as error:
                raise CommandError("profile_not_found", f"no profile named {name} is stored in the project: {error}", USAGE_EXIT) from error
        return list(dict.fromkeys(requested))
    with db_connection(db_path) as conn:
        return [row["name"] for row in conn.execute("SELECT name FROM profiles ORDER BY name")]


def _index_after_apply(project_dir: Path, config: ProjectConfig, targets: list[str], changed: bool, cancelled: bool) -> dict[str, Any]:
    if cancelled:
        return {"status": "skipped", "reason": "cancelled", "profiles": [], "next_action": None}
    if not changed:
        return {"status": "skipped", "reason": "no_changes", "profiles": [], "next_action": None}
    if not targets:
        return {"status": "skipped", "reason": "no_indexed_profile", "profiles": [], "next_action": None}
    profiles = []
    unfinished = []
    for name in targets:
        started = datetime.now()
        try:
            result = run_index(project_dir=project_dir, config=config, profile_name=name)
        except (FileNotFoundError, ConnectionError, ValueError) as error:
            profiles.append({"profile": name, "status": "failed", "error": {"code": "index_failed", "message": str(error)}})
            unfinished.append(f"mrag index --profile {name}")
            continue
        log_path = project_dir / "logs" / f"{started.strftime('%Y%m%d%H%M%S')}-documents-sync-{name}.json"
        write_index_log(result, log_path, command="documents-sync", profile_name=name)
        if result.errors:
            profiles.append({"profile": name, "status": "failed", "error": {"code": "index_document_failed", "message": f"{len(result.errors)} document(s) failed"}})
            unfinished.append(f"mrag index --profile {name}")
        else:
            profiles.append({"profile": name, "status": "indexed", "error": None})
    return {
        "status": "failed" if unfinished else "indexed",
        "reason": None,
        "profiles": profiles,
        "next_action": "; ".join(unfinished) if unfinished else None,
    }


# ---------------------------------------------------------------------------
# mrag documents sync
# ---------------------------------------------------------------------------


@documents_app.command("sync")
def sync(
    directory: Path = typer.Argument(..., metavar="DIR", help="Directory to reconcile with the catalog, inside or outside the project."),
    apply: bool = typer.Option(False, "--apply", help="Carry the plan out. Without it the plan is shown and nothing is touched."),
    json_output: bool = typer.Option(False, "--json", help="Emit one machine-readable JSON object on stdout."),
    include: Optional[list[str]] = typer.Option(None, "--include", metavar="GLOB", help="Include paths matching this repeatable root-relative glob."),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", metavar="GLOB", help="Exclude paths matching this repeatable root-relative glob."),
    hidden: bool = typer.Option(False, "--hidden", help="Include dot-prefixed files and directories."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Follow symbolic links while detecting cycles and duplicate targets."),
    index: bool = typer.Option(False, "--index", help="After applying, index every profile the project has indexed before."),
    profile: Optional[list[str]] = typer.Option(None, "--profile", metavar="NAME", help="Index this profile instead. Repeat the option to name several."),
) -> None:
    """Reconcile a directory with the catalog: add, update, move, exclude, restore.

    Shows the plan unless --apply is given. A file that is gone is excluded
    from every profile (the sync's own rule, lifted when the file returns); a
    file whose content moved to a new path keeps its document ID. Files in
    formats mrag does not ingest are reported as unsupported.
    """
    options = {"include": include or [], "exclude": exclude or [], "hidden": hidden, "follow_symlinks": follow_symlinks}
    if profile and not index:
        _fatal(SYNC_COMMAND, CommandError("profile_requires_index", "--profile names the profiles --index indexes; pass --index", USAGE_EXIT), json_output)
    project_dir = Path.cwd()
    try:
        db_path = find_db(project_dir)
        config = load_project_config(project_dir)
    except FileNotFoundError as error:
        _fatal(SYNC_COMMAND, CommandError("project_not_initialized", str(error), 1), json_output)
        return
    try:
        targets = _index_targets(project_dir, db_path, profile or []) if index else None
        with db_connection(db_path) as conn:
            prepared = _prepare(project_dir, directory, options, conn)
    except SchemeUnsupportedError as error:
        _fatal(SYNC_COMMAND, CommandError("source_identity_scheme_unsupported", str(error), USAGE_EXIT), json_output)
        return
    except CommandError as error:
        _fatal(SYNC_COMMAND, error, json_output)
        return

    if not apply:
        items = [_item_report(item, "planned" if item.mutates else "reported") for item in prepared.plan.items]
        report = _report(prepared, items, False, False)
        if targets is not None:
            has_changes = any(item["status"] == "planned" for item in items)
            if not targets:
                report["index"] = {"status": "skipped", "reason": "no_indexed_profile", "profiles": [], "next_action": None}
            elif not has_changes:
                report["index"] = {"status": "skipped", "reason": "no_changes", "profiles": [], "next_action": None}
            else:
                report["index"] = {"status": "planned", "reason": None, "profiles": [{"profile": name, "status": "planned", "error": None} for name in targets], "next_action": None}
        _emit(report, json_output)
        raise typer.Exit(_exit_code(report))

    # Installed before anything is written, after the plan: a plan-only run has
    # nothing to leave half-done. Honoured between items (and before the first).
    interrupt = Interrupt().install()
    started = datetime.now(timezone.utc)
    try:
        # Gathered again now: the plan above was read before this run's writes.
        with db_connection(db_path) as conn:
            prepared = _prepare(project_dir, directory, options, conn)
        items, cancelled = _apply(prepared, project_dir, config, db_path, interrupt)
    except SchemeUnsupportedError as error:
        _fatal(SYNC_COMMAND, CommandError("source_identity_scheme_unsupported", str(error), USAGE_EXIT), json_output)
        return
    except CommandError as error:
        _fatal(SYNC_COMMAND, error, json_output)
        return
    finally:
        interrupt.restore()
    report = _report(prepared, items, True, cancelled)
    if targets is not None:
        changed = any(item["status"] == "applied" for item in items)
        report["index"] = _index_after_apply(project_dir, config, targets, changed, cancelled)
        if report["index"]["status"] == "failed" and report["status"] == "success":
            report["status"] = "partial"
    try:
        report["audit_log"] = _write_audit_log(project_dir, "documents-sync", report, started)
    except OSError as error:
        report["status"] = "degraded"
        report["warning"] = f"the sync was applied, but its audit log could not be written: {error}"
    _emit(report, json_output)
    raise typer.Exit(_exit_code(report))


# ---------------------------------------------------------------------------
# mrag documents rebind
# ---------------------------------------------------------------------------


def _rebind_report(plan, applied: bool, status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command": REBIND_COMMAND,
        "status": status,
        "applied": applied,
        "action": plan.action,
        "document_id": plan.document_id,
        "previous_identity": plan.previous_identity,
        "source_identity": plan.source_identity,
        "content_hash": plan.content.content_hash,
        "new_revision": plan.new_revision,
        "moves_identity": plan.moves_identity,
        "revoke_exclusions": len(plan.revoke_exclusions),
        "audit_log": None,
    }


def _emit_rebind(report: dict[str, Any], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return
    verb = {"rebind": ("Would rebind", "Rebound"), "update": ("Would update", "Updated"), "restore": ("Would restore", "Restored"), "noop": ("Nothing to do for", "Nothing to do for")}
    planned, done = verb[report["action"]]
    where = f" (from {report['previous_identity']})" if report["moves_identity"] else ""
    console.print(f"{done if report['applied'] else planned} {report['document_id']} → {report['source_identity']}{where}")
    if report.get("error"):
        err_console.print(f"[red]Failed[/red] {report['error']['message']}")
    if not report["applied"] and report["action"] != "noop":
        console.print("Run again with --apply to make this change.")
    if report.get("audit_log"):
        console.print(f"Audit log: {report['audit_log']}")
    if report.get("warning"):
        err_console.print(f"[yellow]Warning:[/yellow] {report['warning']}")


@documents_app.command("rebind")
def rebind(
    document_id: str = typer.Argument(..., metavar="DOCUMENT_ID", help="Document to bind, keeping its ID."),
    file: Path = typer.Argument(..., metavar="FILE", help="File the document is bound to from now on."),
    apply: bool = typer.Option(False, "--apply", help="Carry the plan out. Without it the plan is shown and nothing is touched."),
    json_output: bool = typer.Option(False, "--json", help="Emit one machine-readable JSON object on stdout."),
) -> None:
    """Bind a document to a file you name, keeping its ID. Shows the plan unless --apply is given.

    A file holding different content becomes the document's new content, with
    no --force: you named both ends. The sync's own exclusions on the document
    are lifted, since the file it found missing is now named; a person's stay.
    """
    project_dir = Path.cwd()
    try:
        db_path = find_db(project_dir)
        config = load_project_config(project_dir)
    except FileNotFoundError as error:
        _fatal(REBIND_COMMAND, CommandError("project_not_initialized", str(error), 1), json_output)
        return
    try:
        if not file.exists():
            raise CommandError("rebind_file_not_found", "the file to bind does not exist", USAGE_EXIT)
        if file.is_dir():
            raise CommandError("rebind_source_not_a_file", "documents rebind binds a document to one file; to reconcile a directory, use documents sync", USAGE_EXIT)
        if not file.is_file():
            raise CommandError("rebind_source_not_a_file", "the file to bind is not a regular file", USAGE_EXIT)
        canonical = file.resolve(strict=True)
        project_data = (project_dir / "data").resolve()
        if canonical.is_relative_to(project_data):
            raise CommandError("rebind_file_is_project_data", "the project's data/ directory holds mrag's own artifacts; a document cannot be bound to a file there", USAGE_EXIT)
        try:
            detect_source_type(canonical)
        except ValueError as error:
            raise CommandError("rebind_source_unsupported", str(error), USAGE_EXIT) from error
        with db_connection(db_path) as conn:
            catalog = _catalog_facts(conn)
        try:
            target, _new_root = source_identity(canonical, project_dir, catalog.registered)
        except ReservedPathError as error:
            raise CommandError("source_identity_reserved_path", str(error), USAGE_EXIT) from error
        content = SyncContent(hash_document(canonical))
        plan = plan_rebind(catalog.documents, document_id, target, content)
    except SchemeUnsupportedError as error:
        _fatal(REBIND_COMMAND, CommandError("source_identity_scheme_unsupported", str(error), USAGE_EXIT), json_output)
        return
    except RebindRefused as error:
        _fatal(REBIND_COMMAND, CommandError(error.code, str(error), 1, document_id=error.document_id, held_by=error.held_by), json_output)
        return
    except CommandError as error:
        _fatal(REBIND_COMMAND, error, json_output)
        return

    if not apply:
        report = _rebind_report(plan, False, "planned")
        _emit_rebind(report, json_output)
        raise typer.Exit(0)

    started = datetime.now(timezone.utc)
    report = _rebind_report(plan, True, "applied")
    now = started.isoformat(timespec="seconds")
    try:
        if plan.action == "noop":
            report["status"] = "unchanged"
        else:
            with db_connection(db_path) as conn:
                # The path moves first and in one transaction with the rules; if
                # the new content cannot be read afterwards, the document is left
                # at its new path with its previous content and the command says
                # `partial`; running it again finishes the job.
                if plan.moves_identity:
                    conn.execute(
                        "UPDATE documents SET source_identity = ?, filename = ?, updated_at = ? WHERE id = ?",
                        (plan.source_identity, canonical.name, now, plan.document_id),
                    )
                    _register_root_for(conn, canonical, project_dir, catalog.registered, None, catalog.labels)
                revoke_exclusions(conn, plan.revoke_exclusions)
            if plan.new_revision:
                try:
                    persist_prepared_document(prepare_document(canonical, file_hash=content.content_hash), project_dir, config, force=True)
                except (OSError, ValueError, DuplicateDocumentError) as error:
                    report["status"] = "partial"
                    report["error"] = {"code": "rebind_content_not_ingested", "message": str(error)}
    except (OSError, ValueError) as error:
        report["status"] = "failed"
        report["error"] = {"code": "rebind_failed", "message": str(error)}
    try:
        report["audit_log"] = _write_audit_log(project_dir, "documents-rebind", report, started)
    except OSError as error:
        if report["status"] in ("applied", "unchanged"):
            report["status"] = "degraded"
        report["warning"] = f"the rebind was applied, but its audit log could not be written: {error}"
    _emit_rebind(report, json_output)
    raise typer.Exit({"applied": 0, "unchanged": 0, "partial": DEGRADED_EXIT, "degraded": DEGRADED_EXIT}.get(report["status"], 1))
