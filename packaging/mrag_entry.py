"""PyInstaller entry point for the mrag CLI.

This mirrors the `mrag = "mrag.cli:app"` console_script declared in
pyproject.toml. PyInstaller freezes an executable around this module rather
than around the entry-point metadata, so it must call the Typer app directly.
"""
from mrag.cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
