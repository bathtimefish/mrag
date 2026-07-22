"""Deterministic and policy-aware recursive document discovery."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

_MAX_IGNORE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class DirectoryCandidate:
    source_path: Path
    relative_path: str


@dataclass(frozen=True)
class DirectoryScanIssue:
    relative_path: str
    code: str
    message: str


@dataclass
class DirectoryScan:
    candidates: list[DirectoryCandidate] = field(default_factory=list)
    issues: list[DirectoryScanIssue] = field(default_factory=list)


class _GlobRule:
    def __init__(self, pattern: str, field_name: str) -> None:
        if not pattern or "\0" in pattern:
            raise ValueError(f"Invalid {field_name} glob")
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        directory = normalized.endswith("/")
        normalized = normalized.rstrip("/")
        if not normalized:
            raise ValueError(f"Invalid {field_name} glob")
        body = _glob_regex_body(normalized)
        if "/" in normalized:
            expression = rf"^{body}(?:/.*)?$" if directory else rf"^{body}$"
        else:
            expression = rf"(?:^|/){body}(?:$|/.*)"
        try:
            self._regex = re.compile(expression)
        except re.error as error:
            raise ValueError(f"Invalid {field_name} glob") from error

    def matches(self, path: str) -> bool:
        return self._regex.search(path) is not None


@dataclass(frozen=True)
class _IgnoreRule:
    glob: _GlobRule
    negated: bool


class _PathFilter:
    def __init__(self, root: Path, include: list[str], exclude: list[str]) -> None:
        self._include = [_GlobRule(pattern, "include") for pattern in include]
        self._exclude = [_GlobRule(pattern, "exclude") for pattern in exclude]
        self._ignore = _load_ignore(root)

    def accepts(self, path: str) -> bool:
        if self._include and not any(rule.matches(path) for rule in self._include):
            return False
        if any(rule.matches(path) for rule in self._exclude):
            return False
        ignored = False
        for rule in self._ignore:
            if rule.glob.matches(path):
                ignored = not rule.negated
        return not ignored


def scan_directory(
    project_dir: Path,
    source_root: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    hidden: bool = False,
    follow_symlinks: bool = False,
) -> DirectoryScan:
    """Discover each selected regular file exactly once in POSIX path order."""
    try:
        root_lstat = source_root.lstat()
    except OSError as error:
        raise ValueError(f"Directory source cannot be read: {source_root}") from error
    if stat.S_ISLNK(root_lstat.st_mode) and not follow_symlinks:
        raise ValueError("A symlink directory requires --follow-symlinks")
    try:
        canonical_root = source_root.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Directory source cannot be resolved: {source_root}") from error
    if not canonical_root.is_dir():
        raise ValueError("Recursive add requires a directory source")

    project_data = (project_dir / "data").resolve(strict=False)
    if _within(canonical_root, project_data):
        raise ValueError("The project data directory cannot be added recursively")

    path_filter = _PathFilter(source_root, include or [], exclude or [])
    scan = DirectoryScan()
    seen_files: set[Path] = set()
    visited_directories: set[tuple[int, int]] = set()
    root_stat = canonical_root.stat()
    root_identity = (root_stat.st_dev, root_stat.st_ino)
    visited_directories.add(root_identity)

    _walk(
        source_root,
        "",
        path_filter,
        hidden,
        follow_symlinks,
        project_data,
        {root_identity},
        visited_directories,
        seen_files,
        scan,
    )
    scan.candidates.sort(key=lambda candidate: candidate.relative_path)
    scan.issues.sort(key=lambda issue: (issue.relative_path, issue.code))
    return scan


def _walk(
    directory: Path,
    prefix: str,
    path_filter: _PathFilter,
    hidden: bool,
    follow_symlinks: bool,
    project_data: Path,
    ancestors: set[tuple[int, int]],
    visited_directories: set[tuple[int, int]],
    seen_files: set[Path],
    scan: DirectoryScan,
) -> None:
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError:
        scan.issues.append(
            DirectoryScanIssue(prefix or ".", "directory_walk_failed", "Directory cannot be enumerated")
        )
        return

    for entry in entries:
        relative = f"{prefix}/{entry.name}" if prefix else entry.name
        if not hidden and _has_hidden_component(relative):
            continue
        if entry.is_symlink() and not follow_symlinks:
            continue
        try:
            metadata = entry.stat(follow_symlinks=True)
        except OSError:
            scan.issues.append(
                DirectoryScanIssue(relative, "directory_entry_metadata_failed", "Entry metadata cannot be read")
            )
            continue
        if stat.S_ISDIR(metadata.st_mode):
            canonical = Path(entry.path).resolve(strict=False)
            if _within(canonical, project_data):
                continue
            identity = (metadata.st_dev, metadata.st_ino)
            if identity in ancestors:
                scan.issues.append(
                    DirectoryScanIssue(relative, "directory_symlink_cycle", "Symbolic-link cycle detected")
                )
                continue
            if identity in visited_directories:
                continue
            visited_directories.add(identity)
            _walk(
                Path(entry.path),
                relative,
                path_filter,
                hidden,
                follow_symlinks,
                project_data,
                ancestors | {identity},
                visited_directories,
                seen_files,
                scan,
            )
            continue
        if not stat.S_ISREG(metadata.st_mode) or relative == ".mragignore":
            continue
        if not path_filter.accepts(relative):
            continue
        try:
            canonical = Path(entry.path).resolve(strict=True)
        except OSError:
            scan.issues.append(
                DirectoryScanIssue(relative, "directory_entry_resolution_failed", "Entry cannot be resolved")
            )
            continue
        if _within(canonical, project_data) or canonical in seen_files:
            continue
        seen_files.add(canonical)
        scan.candidates.append(DirectoryCandidate(canonical, relative))


def _load_ignore(root: Path) -> list[_IgnoreRule]:
    path = root / ".mragignore"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ValueError(".mragignore metadata cannot be read") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_IGNORE_BYTES:
        raise ValueError(".mragignore must be a regular non-symlink file no larger than one megabyte")
    try:
        contents = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        raise ValueError(".mragignore must be readable UTF-8") from error
    rules: list[_IgnoreRule] = []
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        rules.append(_IgnoreRule(_GlobRule(pattern, ".mragignore"), negated))
    return rules


def _glob_regex_body(pattern: str) -> str:
    regex: list[str] = []
    index = 0
    while index < len(pattern):
        value = pattern[index]
        if value == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            index += 2
            while index < len(pattern) and pattern[index] == "*":
                index += 1
            if index < len(pattern) and pattern[index] == "/":
                regex.append("(?:.*/)?")
                index += 1
            else:
                regex.append(".*")
        elif value == "*":
            regex.append("[^/]*")
            index += 1
        elif value == "?":
            regex.append("[^/]")
            index += 1
        elif value == "[" and "]" in pattern[index + 1 :]:
            end = pattern.index("]", index + 1)
            character_class = pattern[index + 1 : end]
            if not character_class:
                regex.append(r"\[")
                index += 1
            else:
                if character_class.startswith("!"):
                    character_class = "^" + character_class[1:]
                regex.append("[" + character_class.replace("\\", r"\\") + "]")
                index = end + 1
        else:
            regex.append(re.escape(value))
            index += 1
    return "".join(regex)


def _has_hidden_component(relative: str) -> bool:
    return any(component.startswith(".") and component not in (".", "..") for component in relative.split("/"))


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)
