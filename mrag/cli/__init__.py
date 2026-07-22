import sys
from typing import Optional

# Force UTF-8 stdio so Rich and plain print() don't choke on non-mappable
# characters when the console uses a legacy code page (e.g. cp932 on Japanese
# Windows). Effectively a no-op on Linux/macOS where stdio is already UTF-8.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

import typer

from mrag import __version__

from mrag.cli.init import init
from mrag.cli.add import add
from mrag.cli.extract import extract
from mrag.cli.show import show_extracted, export_extracted
from mrag.cli.index import index
from mrag.cli.reindex import reindex
from mrag.cli.search import search
from mrag.cli.serve import serve
from mrag.cli.remove import remove
from mrag.cli.profiles import profiles_app
from mrag.cli.kb_info import kb_info_app
from mrag.cli.inspect import inspect_app
from mrag.cli.registry import registry_app
from mrag.cli.mcp import mcp_app
from mrag.cli.doctor import doctor
from mrag.cli.eval import eval_cmd
from mrag.cli.exclusions import exclusions_app

app = typer.Typer(
    name="mrag",
    help="Micro RAG — A lightweight, local-first retrieval runtime.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Micro RAG — A lightweight, local-first retrieval runtime."""


app.command("init")(init)
app.command("add")(add)
app.command("extract")(extract)
app.command("show-extracted")(show_extracted)
app.command("export-extracted")(export_extracted)
app.command("index")(index)
app.command("reindex")(reindex)
app.command("search")(search)
app.command("serve")(serve)
app.command("remove")(remove)
app.command("doctor")(doctor)
app.command("eval")(eval_cmd)
app.add_typer(profiles_app, name="profiles")
app.add_typer(kb_info_app, name="kb-info")
app.add_typer(inspect_app, name="inspect")
app.add_typer(registry_app, name="registry")
app.add_typer(mcp_app, name="mcp")
app.add_typer(exclusions_app, name="exclusions")
