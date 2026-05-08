import importlib.util
import sqlite3
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console

console = Console()

_OK = "[green]OK   [/green]"
_WARN = "[yellow]WARN [/yellow]"
_ERR = "[red]ERROR[/red]"


def _check(label: str, fn: Callable[[], tuple[bool, str]]) -> bool:
    try:
        ok, msg = fn()
        badge = _OK if ok else _ERR
        console.print(f"  {badge}  {label}: {msg}")
        return ok
    except Exception as e:
        console.print(f"  {_ERR}  {label}: {e}")
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
    spec = importlib.util.find_spec("sqlite_vaporetto")
    if spec:
        return True, "sqlite-vaporetto found"
    return False, "not installed (optional — used for Japanese tokenization)"


def _check_qdrant(host: str, port: int) -> tuple[bool, str]:
    from mrag.db.qdrant import make_client
    try:
        make_client(host=host, port=port)
        return True, f"connected to {host}:{port}"
    except ConnectionError as e:
        return False, str(e)


def _check_ollama(endpoint: str) -> tuple[bool, str]:
    import urllib.request
    import urllib.error
    import json
    try:
        with urllib.request.urlopen(f"{endpoint}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        if models:
            return True, f"connected — {len(models)} model(s): {', '.join(models[:3])}" + (
                " ..." if len(models) > 3 else ""
            )
        return True, "connected (no models installed)"
    except Exception as e:
        return False, f"not reachable at {endpoint}: {e}"


def _check_marker() -> tuple[bool, str]:
    spec = importlib.util.find_spec("marker")
    if spec:
        return True, "marker-pdf installed"
    return False, "not installed (optional — needed for --provider marker)"


def _check_mrag_yaml(project_dir: Path) -> tuple[bool, str]:
    from mrag.config.project import load_project_config
    try:
        cfg = load_project_config(project_dir)
        return True, f"valid (project={cfg.project.name}, kb={cfg.knowledge_id})"
    except FileNotFoundError:
        return False, "mrag.yaml not found — run 'mrag init' first"
    except Exception as e:
        return False, f"invalid: {e}"


def doctor() -> None:
    """Check environment and project configuration."""
    project_dir = Path.cwd()

    console.print("[bold]MRAG Environment Check[/bold]\n")

    console.print("[bold]SQLite[/bold]")
    _check("version (3.35.0+)", _check_sqlite_version)
    _check("FTS5 trigram tokenizer", _check_fts5_trigram)
    _check("sqlite-vaporetto (optional)", _check_vaporetto)

    console.print()
    console.print("[bold]Project[/bold]")
    ok, msg = (False, "")
    try:
        from mrag.config.project import load_project_config
        cfg = load_project_config(project_dir)
        ok = True
        msg = f"valid (project={cfg.project.name}, kb={cfg.knowledge_id})"
        console.print(f"  {_OK}  mrag.yaml: {msg}")
    except FileNotFoundError:
        console.print(f"  {_WARN}  mrag.yaml: not found — run 'mrag init' first")
        cfg = None
    except Exception as e:
        console.print(f"  {_ERR}  mrag.yaml: {e}")
        cfg = None

    console.print()
    console.print("[bold]Qdrant[/bold]")
    if cfg:
        _check(f"server ({cfg.qdrant.host}:{cfg.qdrant.port})", lambda: _check_qdrant(cfg.qdrant.host, cfg.qdrant.port))
    else:
        _check("server (localhost:6333)", lambda: _check_qdrant("localhost", 6333))

    console.print()
    console.print("[bold]Ollama[/bold]")
    ollama_endpoint = "http://localhost:11434"
    if cfg:
        # Read from default profile if available
        try:
            from mrag.config.profile import load_profile
            prof = load_profile(cfg.default_profile, project_dir)
            ollama_endpoint = prof.embedding.endpoint
        except FileNotFoundError:
            pass
    _check(f"endpoint ({ollama_endpoint})", lambda ep=ollama_endpoint: _check_ollama(ep))

    console.print()
    console.print("[bold]Optional packages[/bold]")
    _check("marker-pdf", _check_marker)
