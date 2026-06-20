"""PyInstaller entry point for the mrag CLI.

This mirrors the `mrag = "mrag.cli:app"` console_script declared in
pyproject.toml. PyInstaller freezes an executable around this module rather
than around the entry-point metadata, so it must call the Typer app directly.

multiprocessing.freeze_support() must run first: the --with-reranker build
bundles torch / sentence-transformers, which spin up multiprocessing helpers
(e.g. the resource_tracker). In a frozen app those helpers re-exec the binary
itself; without freeze_support() the re-exec lands in the Typer CLI with
interpreter flags like `-B`, producing a "No such option '-B'" error. Calling
freeze_support() intercepts that re-exec before the CLI is reached. It is a
no-op for the LEAN build and for normal (non-frozen) execution.
"""
import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()
    from mrag.cli import app
    app()


if __name__ == "__main__":
    main()
