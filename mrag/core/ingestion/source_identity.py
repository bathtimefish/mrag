"""Portable source identity shared by single-file and recursive ingestion.

Scheme 2 spells every identity that is not a project path under the reserved
``identities/`` namespace: ``identities/external/<root-key>/<path>`` for a
source outside the project and ``identities/legacy/v1/<document-id>`` for a
row whose original path cannot be recovered. Scheme 1 spelled them
``external/...`` and ``legacy/...`` beside bare project paths, so a project
directory of either name produced identities nothing could tell apart.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


SCHEME_VERSION = 2
# The scheme a catalog without a record holds: the record arrived with scheme 1,
# the only scheme that existed then.
SCHEME_UNRECORDED = 1
# The catalog_settings key recording the scheme a catalog's identities follow.
SCHEME_KEY = "source_identity_scheme"

RESERVED_NAMESPACE = "identities"
_EXTERNAL_PREFIX = "identities/external/"
_LEGACY_PREFIX = "identities/legacy/v1/"
_SCHEME_ONE_EXTERNAL_PREFIX = "external/"
_SCHEME_ONE_LEGACY_PREFIX = "legacy/v1/"
_ROOT_KEY = re.compile(r"^[0-9a-f]{32}$")
_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

MIGRATE_COMMAND = "mrag catalog migrate-identities"

# What identities/README.md says. The same words as MRAG Plus's notice, so a
# user who meets the directory in either product reads one explanation.
IDENTITIES_NOTICE = """\
# identities/ — reserved by mrag

This directory is reserved. mrag names documents whose source is outside this
project, or whose original path is unknown, under `identities/...`, so no file
in this directory can be added as a source: `mrag add` refuses it, and a
recursive add passes this directory by.

mrag reads nothing here. Deleting this file or the directory is harmless; if the
directory is missing, `mrag add` recreates it with this notice. Keep your own
documents elsewhere in the project.

# identities/ — mrag の予約ディレクトリ

このディレクトリは予約済みです。mrag はプロジェクト外にあるソースや元のパスが
分からない文書を `identities/...` という名前で管理するため、このディレクトリ内の
ファイルはソースとして追加できません。`mrag add` は拒否し、再帰的な追加では
このディレクトリを読み飛ばします。

mrag はここにあるものを読みません。このファイルやディレクトリを削除しても問題は
なく、ディレクトリが無ければ `mrag add` がこの告知とともに作り直します。文書は
プロジェクト内の別の場所に置いてください。
"""


class SchemeUnsupportedError(ValueError):
    """The catalog's identities follow a scheme this build does not derive."""


class ReservedPathError(ValueError):
    """A source inside the project's reserved identities/ directory."""

    def __init__(self) -> None:
        super().__init__(
            "The project's identities/ directory is reserved and cannot hold a source; "
            "move the file elsewhere in the project."
        )


def require_scheme(recorded: str | None) -> None:
    """Refuse to derive identities under a scheme the catalog was not built with."""
    if recorded != str(SCHEME_VERSION):
        hint = (
            f" Run `{MIGRATE_COMMAND}` to convert them."
            if recorded is not None and recorded.isdigit() and int(recorded) < SCHEME_VERSION
            else ""
        )
        raise SchemeUnsupportedError(
            f"This project's source identities follow scheme {recorded or 'unknown'}, "
            f"but this mrag derives scheme {SCHEME_VERSION}; the catalog needs an "
            f"explicit migration before sources can be added.{hint}"
        )


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


def is_reserved(canonical: str) -> bool:
    """Whether a canonical project path sits under the reserved namespace.

    ASCII case is ignored: on the case-insensitive file systems macOS and Windows
    use by default ``Identities/`` is the same directory, and a project must not
    accept a source on one OS that it refuses after moving to another.
    """
    first = canonical.split("/", 1)[0]
    return first.lower() == RESERVED_NAMESPACE


def project_identity(path: str) -> str:
    """Name a source inside the project by its project-relative path."""
    canonical = _relative(path)
    if is_reserved(canonical):
        raise ReservedPathError()
    return canonical


def external_identity(key: str, relative: str) -> str:
    """Name a source outside the project; the bound covers the whole identity."""
    return _relative(f"{_EXTERNAL_PREFIX}{key}/{_relative(relative)}")


def legacy_identity(document_id: str) -> str:
    return f"{_LEGACY_PREFIX}{document_id}"


def root_key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:32]


def source_identity(source: Path, project_dir: Path, registered_roots: set[str], preferred_root: Path | None = None) -> tuple[str, tuple[str, str] | None]:
    """Return (identity, optional new root key/label) without writing state."""
    canonical = source.resolve(strict=True)
    project = project_dir.resolve(strict=True)
    if canonical.is_relative_to(project):
        # The canonical path decides, so a link from anywhere that resolves
        # into the namespace is refused as surely as a direct add.
        return project_identity(canonical.relative_to(project).as_posix()), None

    parent = canonical.parent
    for ancestor in (parent, *parent.parents):
        key = root_key(ancestor)
        if key in registered_roots:
            return external_identity(key, canonical.relative_to(ancestor).as_posix()), None

    root = preferred_root.resolve(strict=True) if preferred_root is not None else parent
    if not canonical.is_relative_to(root):
        # A followed symlink can point outside the scanned tree. Bind the
        # canonical target to its own directory instead of losing that source.
        root = parent
    key = root_key(root)
    identity = external_identity(key, canonical.relative_to(root).as_posix())
    return identity, (key, root.name or "external")


def _shape(identity: str) -> tuple[str, str | None, str | None]:
    """Classify an identity exactly: (binding, root key, root-relative path).

    Matching on the prefix alone would call a malformed value external and then
    fail to find its key — the crash scheme 1 had on ``external/notes.md``.
    """
    if identity.startswith(_EXTERNAL_PREFIX):
        key, _, relative = identity[len(_EXTERNAL_PREFIX):].partition("/")
        if _ROOT_KEY.match(key) and relative:
            return "external_root", key, relative
    elif identity.startswith(_LEGACY_PREFIX):
        if _DOCUMENT_ID.match(identity[len(_LEGACY_PREFIX):]):
            return "legacy_unbound", None, None
    return "project_relative", None, None


def binding_status(identity: str) -> str:
    return _shape(identity)[0]


def display_name(identity: str, root_labels: dict[str, str]) -> str:
    binding, key, relative = _shape(identity)
    if binding != "external_root":
        # Project-relative and legacy-unbound both display as themselves.
        return identity
    label = root_labels.get(key)
    return f"{label}/{relative}" if label else relative


@dataclass(frozen=True)
class SchemeOneReading:
    """What a scheme-1 identity is under scheme 2.

    ``reading`` is ``unchanged`` (spelled the same), ``respelled`` or
    ``reserved`` (a project path scheme 2 has no spelling for; ``identity`` is
    then ``None``).
    """

    reading: str
    identity: str | None
    binding: str


def read_scheme_one(stored: str, document_id: str, registered_roots: set[str]) -> SchemeOneReading:
    """Read an identity stored under scheme 1, as scheme 2 names it.

    The one classification rule: the explicit migration applies it, and a
    listing of a catalog nobody has migrated applies it in memory, so the two
    never disagree about what a stored value means. Raises ValueError for a value
    no scheme-1 build could have written.
    """
    if _relative(stored) != stored:
        raise ValueError("Source identity must already be in canonical form")
    if stored.startswith(_SCHEME_ONE_EXTERNAL_PREFIX):
        key, _, relative = stored[len(_SCHEME_ONE_EXTERNAL_PREFIX):].partition("/")
        if _ROOT_KEY.match(key) and key in registered_roots and relative:
            return SchemeOneReading("respelled", external_identity(key, relative), "external_root")
    if stored == f"{_SCHEME_ONE_LEGACY_PREFIX}{document_id}":
        return SchemeOneReading("respelled", legacy_identity(document_id), "legacy_unbound")
    if is_reserved(stored):
        return SchemeOneReading("reserved", None, "project_relative")
    return SchemeOneReading("unchanged", stored, "project_relative")


def restore_identities_notice(project_dir: Path) -> bool:
    """Recreate identities/ with its notice when the directory is missing.

    A missing directory is normal — a project created before scheme 2 never had
    one — so it is restored rather than treated as damage. A directory that
    exists without the notice is left alone: someone removed the file on purpose.
    """
    directory = project_dir / RESERVED_NAMESPACE
    if directory.exists():
        return False
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with open(directory / "README.md", "x", encoding="utf-8", newline="\n") as notice:
            notice.write(IDENTITIES_NOTICE)
    except FileExistsError:
        return False
    return True
