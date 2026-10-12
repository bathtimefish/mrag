"""Project-relative artifact paths as the catalog stores them.

They are stored with ``/`` on every platform. Before 1.5.1 they were stored
with the platform's separator, so a catalog written on Windows holds
``data\\documents\\<id>\\original.md``. Read through these helpers, such a
catalog works on any platform and is reported with ``/``. A backslash never
belongs to these paths otherwise: every one is ``data/documents/<id>/<name>``
with a name mrag chose.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def to_stored(path: Path, project_dir: Path) -> str:
    """The catalog spelling of ``path``, which lies under ``project_dir``."""
    return path.relative_to(project_dir).as_posix()


def normalize_stored(stored: str | None) -> str | None:
    """A stored path with ``/`` separators, whichever platform wrote it."""
    return None if stored is None else stored.replace("\\", "/")


def resolve_stored(project_dir: Path, stored: str) -> Path:
    """The file a stored path names under ``project_dir``."""
    return project_dir.joinpath(*PurePosixPath(stored.replace("\\", "/")).parts)
