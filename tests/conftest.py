import sqlite3
from pathlib import Path
from textwrap import dedent

import pytest

from mrag.db.migrate import apply_schema


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Empty mrag project directory structure (no mrag.yaml or mrag.db yet)."""
    (tmp_path / "data" / "documents").mkdir(parents=True)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "cache" / "embeddings").mkdir(parents=True)
    (tmp_path / "qdrant").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    """A simple plain-text file for extraction tests."""
    p = tmp_path / "sample.txt"
    p.write_text("Hello, world!\nThis is a test document.", encoding="utf-8")
    return p


@pytest.fixture
def sample_md(tmp_path: Path) -> Path:
    """A simple Markdown file for extraction tests."""
    p = tmp_path / "sample.md"
    p.write_text(
        dedent("""\
            # Test Document

            This is a **markdown** document.

            ## Section 2

            Some more content here.
        """),
        encoding="utf-8",
    )
    return p
