"""`mrag registry` subcommand group.

Subcommands:
  generate <root_dir>           Build knowledge_registry.yaml from <root>/*/kb_information.yaml
  validate <registry_path>      Verify a knowledge_registry.yaml is internally consistent

See: dev_docs/01_EXTENSION_STAGE_1/DESIGN_V18_INSPECT_REGISTRY.md §3.2 / §4.4 / §4.5
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console

from mrag.config.kb_info import KB_INFORMATION_FILENAME, load_kb_info
from mrag.config.registry import (
    REGISTRY_FILENAME,
    KnowledgeRegistry,
    RegistryKnowledgeBase,
    dump_registry,
    from_kb_information,
    now_utc_iso,
    registry_path,
    to_relative_posix_path,
)


console = Console()
err_console = Console(stderr=True)


registry_app = typer.Typer(
    name="registry",
    help="Generate and validate a multi-KB knowledge_registry.yaml.",
    no_args_is_help=True,
)


# ===========================================================================
# Stable issue keys (validator JSON output — agents branch on these)
# ===========================================================================

ISSUE_PATH_NOT_FOUND = "path_not_found"
ISSUE_MRAG_YAML_NOT_FOUND = "mrag_yaml_not_found"
ISSUE_KB_INFORMATION_YAML_NOT_FOUND = "kb_information_yaml_not_found"
ISSUE_PREFERRED_PROFILE_NOT_FOUND = "preferred_profile_not_found"
ISSUE_DUPLICATE_ID = "duplicate_id"


# ===========================================================================
# generate
# ===========================================================================


def _list_subdirs(root: Path) -> list[Path]:
    """Return immediate subdirectories of `root`, sorted."""
    return sorted(p for p in root.iterdir() if p.is_dir())


@registry_app.command("generate")
def registry_generate(
    root_dir: Path = typer.Argument(
        ...,
        help="Root directory containing mrag KB project subdirectories.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help=f"Output path (default: <root_dir>/{REGISTRY_FILENAME}).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the generated registry to stdout instead of writing to disk.",
    ),
) -> None:
    """Build knowledge_registry.yaml by aggregating <root>/*/kb_information.yaml files."""
    root_dir = root_dir.resolve()
    if not root_dir.exists() or not root_dir.is_dir():
        err_console.print(
            f"[red]Error:[/red] {root_dir} does not exist or is not a directory"
        )
        raise typer.Exit(1)

    output_path = (output.resolve() if output else registry_path(root_dir))
    registry_dir = output_path.parent

    entries: list[RegistryKnowledgeBase] = []
    entries_source: list[str] = []  # parallel list of source dir names
    skipped: list[tuple[str, str]] = []  # (dir_name, reason)

    for subdir in _list_subdirs(root_dir):
        kb_info_path = subdir / KB_INFORMATION_FILENAME
        if not kb_info_path.exists():
            skipped.append((subdir.name, "no kb_information.yaml"))
            err_console.print(
                f"[yellow]Warning:[/yellow] skipping {subdir.name} "
                f"(no {KB_INFORMATION_FILENAME})"
            )
            continue

        mrag_yaml = subdir / "mrag.yaml"
        if not mrag_yaml.exists():
            skipped.append((subdir.name, "no mrag.yaml"))
            err_console.print(
                f"[yellow]Warning:[/yellow] skipping {subdir.name} "
                f"(no mrag.yaml — not an mrag project)"
            )
            continue

        try:
            kb_info = load_kb_info(subdir)
        except ValidationError as e:
            skipped.append((subdir.name, f"invalid kb_information.yaml"))
            err_console.print(
                f"[yellow]Warning:[/yellow] skipping {subdir.name} "
                f"(kb_information.yaml invalid: {e.errors()[0]['msg']})"
            )
            continue
        except Exception as e:  # noqa: BLE001
            skipped.append((subdir.name, "kb_information.yaml read error"))
            err_console.print(
                f"[yellow]Warning:[/yellow] skipping {subdir.name} "
                f"(cannot read kb_information.yaml: {e})"
            )
            continue

        rel_path = to_relative_posix_path(subdir, registry_dir)
        entries.append(from_kb_information(kb_info, rel_path))
        entries_source.append(subdir.name)

    if not entries:
        err_console.print(
            f"[red]Error:[/red] no {KB_INFORMATION_FILENAME} found under "
            f"{root_dir} (searched 1 level deep)"
        )
        err_console.print(
            f"       - 0 subdirectories contained {KB_INFORMATION_FILENAME}"
        )
        if skipped:
            err_console.print(
                f"       - {len(skipped)} subdirectories were skipped: "
                f"{', '.join(name for name, _ in skipped)}"
            )
        err_console.print(
            "Tip: run 'mrag init <root>/<kb-name>' to create a KB project first."
        )
        raise typer.Exit(1)

    # ID uniqueness — emit ALL collision pairs at once
    seen: dict[str, str] = {}  # id -> first dir_name
    collisions: list[tuple[str, str, str]] = []  # (id, first_dir, conflict_dir)
    for entry, cur_dir in zip(entries, entries_source):
        prior = seen.get(entry.id)
        if prior is None:
            seen[entry.id] = cur_dir
        else:
            collisions.append((entry.id, prior, cur_dir))
    if collisions:
        for kb_id, d1, d2 in collisions:
            err_console.print(
                f"[red]Error:[/red] duplicate knowledge_base.id '{kb_id}' "
                f"in {d1}, {d2}"
            )
        raise typer.Exit(1)

    registry = KnowledgeRegistry(
        generated_at=now_utc_iso(),
        knowledge_bases=entries,
    )

    if dry_run:
        # Emit YAML to stdout (no file written). Status messages already went
        # to stderr via err_console above.
        yaml.dump(
            registry.model_dump(),
            sys.stdout,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
    else:
        try:
            dump_registry(registry, output_path)
        except OSError as e:
            err_console.print(
                f"[red]Error:[/red] failed to write {output_path}: {e}"
            )
            raise typer.Exit(1)
        console.print(
            f"[green]✓[/green] Wrote {len(entries)} knowledge_base(s) to {output_path}"
        )


# ===========================================================================
# validate
# ===========================================================================


def _check_kb_entry(
    kb: RegistryKnowledgeBase, index: int, registry_dir: Path
) -> list[dict[str, Any]]:
    """Return a list of issue dicts for one knowledge_bases entry."""
    issues: list[dict[str, Any]] = []
    kb_dir = (registry_dir / kb.path).resolve()

    if not kb_dir.exists() or not kb_dir.is_dir():
        issues.append({
            "knowledge_base_index": index,
            "knowledge_base_id": kb.id,
            "issue": ISSUE_PATH_NOT_FOUND,
            "detail": f"path '{kb.path}' does not exist",
        })
        # without a valid dir we can't check the other things
        return issues

    if not (kb_dir / "mrag.yaml").exists():
        issues.append({
            "knowledge_base_index": index,
            "knowledge_base_id": kb.id,
            "issue": ISSUE_MRAG_YAML_NOT_FOUND,
            "detail": f"mrag.yaml not found at {kb.path}/mrag.yaml",
        })

    if not (kb_dir / KB_INFORMATION_FILENAME).exists():
        issues.append({
            "knowledge_base_index": index,
            "knowledge_base_id": kb.id,
            "issue": ISSUE_KB_INFORMATION_YAML_NOT_FOUND,
            "detail": (
                f"{KB_INFORMATION_FILENAME} not found at "
                f"{kb.path}/{KB_INFORMATION_FILENAME}"
            ),
        })

    profiles_dir = kb_dir / "profiles"
    for pname in kb.preferred_profiles:
        if not (profiles_dir / f"{pname}.yaml").exists():
            issues.append({
                "knowledge_base_index": index,
                "knowledge_base_id": kb.id,
                "issue": ISSUE_PREFERRED_PROFILE_NOT_FOUND,
                "detail": (
                    f"preferred_profile '{pname}' not found at "
                    f"{kb.path}/profiles/{pname}.yaml"
                ),
            })

    return issues


def _check_id_uniqueness(
    knowledge_bases: list[RegistryKnowledgeBase],
) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    issues: list[dict[str, Any]] = []
    for i, kb in enumerate(knowledge_bases):
        prior = seen.get(kb.id)
        if prior is None:
            seen[kb.id] = i
        else:
            issues.append({
                "knowledge_base_index": i,
                "knowledge_base_id": kb.id,
                "issue": ISSUE_DUPLICATE_ID,
                "detail": (
                    f"id '{kb.id}' is duplicated "
                    f"(also at knowledge_bases[{prior}])"
                ),
            })
    return issues


def _render_validate_human(
    registry_path_str: str,
    schema_valid: bool,
    ids_unique: bool,
    issues: list[dict[str, Any]],
    kb_count: int,
) -> None:
    console.print(f"Validating {registry_path_str} ...")
    console.print()
    if schema_valid:
        console.print("[green]✓[/green] registry schema valid")
    if ids_unique:
        console.print(
            f"[green]✓[/green] {kb_count} knowledge_bases, all ids unique"
        )

    # Group issues by knowledge_base_index for readability
    by_kb: dict[int, list[dict[str, Any]]] = {}
    for iss in issues:
        by_kb.setdefault(iss["knowledge_base_index"], []).append(iss)

    if by_kb:
        console.print()
    for idx in sorted(by_kb):
        kb_id = by_kb[idx][0]["knowledge_base_id"]
        console.print(
            f"[red]✗[/red] knowledge_bases[{idx}] (id={kb_id}):"
        )
        for iss in by_kb[idx]:
            console.print(f"    - {iss['detail']}")
        console.print()

    if issues:
        kbs_with_issues = len(by_kb)
        kb_word = "knowledge_base" if kbs_with_issues == 1 else "knowledge_bases"
        has_verb = "has" if kbs_with_issues == 1 else "have"
        issue_word = "issue" if len(issues) == 1 else "issues"
        console.print(
            f"{kbs_with_issues} {kb_word} {has_verb} issues "
            f"({len(issues)} {issue_word} total)."
        )


@registry_app.command("validate")
def registry_validate(
    registry_path_arg: Path = typer.Argument(
        ..., help="Path to knowledge_registry.yaml to validate.",
        metavar="REGISTRY_PATH",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a single JSON object to stdout (warnings/errors go to stderr).",
    ),
) -> None:
    """Validate a knowledge_registry.yaml for schema + filesystem consistency."""
    registry_file = registry_path_arg.resolve()

    # 1) Read raw YAML (fatal error: file missing or parse fail)
    if not registry_file.exists():
        err_console.print(f"[red]Error:[/red] {registry_file} does not exist")
        raise typer.Exit(1)
    try:
        raw = yaml.safe_load(registry_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        err_console.print(
            f"[red]Error:[/red] failed to parse {registry_file}: {e}"
        )
        raise typer.Exit(1)

    # 2) Schema validation (fatal error)
    try:
        registry = KnowledgeRegistry(**raw)
    except ValidationError as e:
        err_console.print(
            f"[red]Error:[/red] {registry_file} schema validation failed:"
        )
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            err_console.print(f"  - {loc}: {err['msg']}")
        raise typer.Exit(1)

    # 3) Aggregate non-fatal issues
    registry_dir = registry_file.parent
    issues: list[dict[str, Any]] = []
    issues.extend(_check_id_uniqueness(registry.knowledge_bases))
    ids_unique = not any(i["issue"] == ISSUE_DUPLICATE_ID for i in issues)

    for i, kb in enumerate(registry.knowledge_bases):
        issues.extend(_check_kb_entry(kb, i, registry_dir))

    # 4) Output
    if json_output:
        payload = {
            "registry_path": str(registry_file),
            "schema_valid": True,
            "ids_unique": ids_unique,
            "issues": issues,
            "issue_count": len(issues),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _render_validate_human(
            str(registry_file),
            schema_valid=True,
            ids_unique=ids_unique,
            issues=issues,
            kb_count=len(registry.knowledge_bases),
        )

    if issues:
        raise typer.Exit(1)


__all__ = [
    "ISSUE_DUPLICATE_ID",
    "ISSUE_KB_INFORMATION_YAML_NOT_FOUND",
    "ISSUE_MRAG_YAML_NOT_FOUND",
    "ISSUE_PATH_NOT_FOUND",
    "ISSUE_PREFERRED_PROFILE_NOT_FOUND",
    "registry_app",
]
