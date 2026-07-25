"""
Tokenizer abstraction for FTS5.

Supported tokenizers:
  trigram   — SQLite built-in, always available
  vaporetto — Japanese morphological tokenizer via sqlite-vaporetto + apsw

Auto-detection order: vaporetto (if lib found + apsw installed) → trigram.
The chosen tokenizer is stored in mrag.yaml at init time and used for all
subsequent FTS5 table creation and MATCH queries.
"""
from __future__ import annotations

import os
from pathlib import Path

TOKENIZER_TRIGRAM = "trigram"
TOKENIZER_VAPORETTO = "vaporetto"

_VAPORETTO_ENTRYPOINT = "sqlite3_vaporetto_init"
_VAPORETTO_LIB_NAMES = [
    "libsqlite_vaporetto.dylib",  # macOS
    "libsqlite_vaporetto.so",     # Linux
    "sqlite_vaporetto.dll",       # Windows
]
_DEFAULT_SEARCH_DIRS = [
    Path.home() / ".mrag" / "extensions",
    Path("/usr/local/lib"),
    Path("/usr/lib"),
]


class VaporettoLibraryAmbiguityError(RuntimeError):
    """Raised when automatic discovery cannot choose one library safely."""


def _find_candidates(base: Path) -> list[Path]:
    candidates = [
        base / name
        for name in _VAPORETTO_LIB_NAMES
        if (base / name).is_file()
    ]
    for sub in sorted(base.iterdir(), key=lambda path: path.name):
        if not sub.is_dir():
            continue
        candidates.extend(
            sub / name
            for name in _VAPORETTO_LIB_NAMES
            if (sub / name).is_file()
        )
    return sorted(set(candidates), key=str)


def find_vaporetto_lib() -> Path | None:
    """Search well-known locations for exactly one Vaporetto shared library.

    An explicit ``MRAG_VAPORETTO_LIB`` remains authoritative. Automatic
    discovery preserves standard-directory precedence, but refuses to select
    one artifact when the same directory contains multiple candidates.
    """
    env = os.environ.get("MRAG_VAPORETTO_LIB")
    if env:
        p = Path(env)
        if p.exists():
            return p

    for base in _DEFAULT_SEARCH_DIRS:
        if not base.is_dir():
            continue
        candidates = _find_candidates(base)
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            rendered = ", ".join(str(candidate) for candidate in candidates)
            raise VaporettoLibraryAmbiguityError(
                "multiple sqlite-vaporetto libraries were found under "
                f"{base}: {rendered}. Set MRAG_VAPORETTO_LIB to the intended "
                "library."
            )
    return None


def probe_vaporetto(lib_path: Path) -> bool:
    """Return True if vaporetto loads correctly and can tokenize Japanese."""
    try:
        import apsw
        conn = apsw.Connection(":memory:")
        conn.enableloadextension(True)
        conn.loadextension(str(lib_path), _VAPORETTO_ENTRYPOINT)
        conn.enableloadextension(False)
        conn.execute("CREATE VIRTUAL TABLE _t USING fts5(body, tokenize='vaporetto')")
        conn.execute("INSERT INTO _t VALUES ('熱電対の基本仕様')")
        rows = list(conn.execute("SELECT body FROM _t WHERE _t MATCH '熱電対'"))
        return len(rows) == 1
    except Exception:
        return False


def detect_best_tokenizer() -> tuple[str, Path | None]:
    """
    Auto-detect the best available tokenizer.
    Returns (tokenizer_name, lib_path_or_None).
    """
    lib = find_vaporetto_lib()
    if lib and probe_vaporetto(lib):
        return TOKENIZER_VAPORETTO, lib
    return TOKENIZER_TRIGRAM, None


def fts5_tokenize_clause(tokenizer: str) -> str:
    """Return the tokenize= value for CREATE VIRTUAL TABLE fts5."""
    if tokenizer == TOKENIZER_VAPORETTO:
        return "vaporetto"
    return "trigram"
