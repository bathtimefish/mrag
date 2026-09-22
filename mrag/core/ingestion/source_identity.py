"""Portable source identity shared by single-file and recursive ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path


SCHEME_VERSION = 1


def _relative(value: str) -> str:
    value = value.replace("\\", "/")
    parts = [part for part in value.split("/") if part != "."]
    if (not parts or value.startswith("/") or (len(value) >= 2 and value[1] == ":") or
        any(not part or part == ".." or any(ord(c) < 32 or ord(c) == 127 for c in part) for part in parts)):
        raise ValueError("Source identity must be a portable relative path")
    result = "/".join(parts)
    if len(result.encode("utf-8")) > 1024:
        raise ValueError("Source identity exceeds 1024 bytes")
    return result


def root_key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:32]


def source_identity(source: Path, project_dir: Path, registered_roots: set[str], preferred_root: Path | None = None) -> tuple[str, tuple[str, str] | None]:
    """Return (identity, optional new root key/label) without writing state."""
    canonical = source.resolve(strict=True)
    project = project_dir.resolve(strict=True)
    if canonical.is_relative_to(project):
        return _relative(canonical.relative_to(project).as_posix()), None

    parent = canonical.parent
    for ancestor in (parent, *parent.parents):
        key = root_key(ancestor)
        if key in registered_roots:
            relative = _relative(canonical.relative_to(ancestor).as_posix())
            return f"external/{key}/{relative}", None

    root = preferred_root.resolve(strict=True) if preferred_root is not None else parent
    if not canonical.is_relative_to(root):
        # A followed symlink can point outside the scanned tree. Bind the
        # canonical target to its own directory instead of losing that source.
        root = parent
    key = root_key(root)
    relative = _relative(canonical.relative_to(root).as_posix())
    return f"external/{key}/{relative}", (key, root.name or "external")


def binding_status(identity: str) -> str:
    if identity.startswith("legacy/v1/"):
        return "legacy_unbound"
    if identity.startswith("external/"):
        return "external_root"
    return "project_relative"


def display_name(identity: str, root_labels: dict[str, str]) -> str:
    if binding_status(identity) != "external_root":
        return identity
    _, key, relative = identity.split("/", 2)
    label = root_labels.get(key)
    return f"{label}/{relative}" if label else relative
