import typer

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
from mrag.cli.doctor import doctor
from mrag.cli.eval import eval_cmd

app = typer.Typer(
    name="mrag",
    help="Micro RAG — A lightweight, local-first retrieval runtime.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
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
