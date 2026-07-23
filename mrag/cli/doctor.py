"""mrag doctor — runtime environment health check.

This command verifies that the system-level dependencies mrag needs to operate
are present and working. It is intentionally project-agnostic: project-level
configuration (mrag.yaml, profiles, Qdrant mode/host, embedding endpoint) is
the responsibility of the individual project commands at runtime.

Doctor checks:
  - SQLite version (>= 3.35.0)
  - SQLite FTS5 trigram tokenizer
  - sqlite-vaporetto native library (optional)
  - Ollama default endpoint (http://localhost:11434) reachability
"""
import sqlite3
from typing import Callable

from rich.console import Console

console = Console()

_OK = "[green]OK   [/green]"
_WARN = "[yellow]WARN [/yellow]"
_ERR = "[red]ERROR[/red]"

_OLLAMA_DEFAULT_ENDPOINT = "http://localhost:11434"


def _check(label: str, fn: Callable[[], tuple[bool, str]]) -> bool:
    try:
        ok, msg = fn()
        badge = _OK if ok else _ERR
        console.print(f"  {badge}  {label}: {msg}")
        return ok
    except Exception as e:
        console.print(f"  {_ERR}  {label}: {e}")
        return False


def _check_warn(label: str, fn: Callable[[], tuple[bool, str]]) -> bool:
    """Like _check but uses WARN instead of ERROR on failure (for optional components)."""
    try:
        ok, msg = fn()
        badge = _OK if ok else _WARN
        console.print(f"  {badge}  {label}: {msg}")
        return ok
    except Exception as e:
        console.print(f"  {_WARN}  {label}: {e}")
        return False


def _check_sqlite_version() -> tuple[bool, str]:
    ver_str = sqlite3.sqlite_version
    parts = [int(x) for x in ver_str.split(".")]
    ok = (parts[0], parts[1], parts[2]) >= (3, 35, 0)
    return ok, f"{ver_str}" + ("" if ok else " (need 3.35.0+)")


def _check_fts5_trigram() -> tuple[bool, str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')"
        )
        conn.execute("INSERT INTO t VALUES ('hello world')")
        rows = conn.execute("SELECT * FROM t WHERE t MATCH 'ello'").fetchall()
        ok = len(rows) == 1
        return ok, "trigram tokenizer works" if ok else "trigram returned no results"
    finally:
        conn.close()


def _check_vaporetto() -> tuple[bool, str]:
    from mrag.db.tokenizer import (
        VaporettoLibraryAmbiguityError,
        find_vaporetto_lib,
        probe_vaporetto,
    )
    try:
        lib = find_vaporetto_lib()
    except VaporettoLibraryAmbiguityError as exc:
        return False, str(exc)
    if lib is None:
        return False, "library not found (optional — place libsqlite_vaporetto in ~/.mrag/extensions/)"
    if not probe_vaporetto(lib):
        return False, f"found at {lib} but failed to load (check apsw installation)"
    return True, f"ready ({lib.name})"


def _check_ollama() -> tuple[bool, str]:
    """Simple alive check against the default Ollama endpoint."""
    import httpx
    try:
        resp = httpx.get(f"{_OLLAMA_DEFAULT_ENDPOINT}/api/version", timeout=3.0)
        resp.raise_for_status()
        return True, f"running at {_OLLAMA_DEFAULT_ENDPOINT}"
    except Exception as e:
        return False, f"not reachable at {_OLLAMA_DEFAULT_ENDPOINT}: {e}"


def doctor() -> None:
    """Check that the mrag runtime environment is healthy.

    This is a system-level check independent of any specific project. Project
    configuration (mrag.yaml, profiles) is validated by individual commands at
    runtime — `mrag doctor` only confirms that the underlying tools mrag needs
    are present and working.
    """
    console.print("[bold]MRAG Environment Check[/bold]\n")

    console.print("[bold]SQLite[/bold]")
    _check("version (3.35.0+)", _check_sqlite_version)
    _check("FTS5 trigram tokenizer", _check_fts5_trigram)
    _check_warn("sqlite-vaporetto (optional)", _check_vaporetto)

    console.print()
    console.print("[bold]Ollama[/bold]")
    _check(f"endpoint ({_OLLAMA_DEFAULT_ENDPOINT})", _check_ollama)
