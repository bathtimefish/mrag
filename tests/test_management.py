"""Tests for Phase 9: mrag remove, mrag profiles, mrag doctor."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from mrag.db.connection import find_db, open_connection
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixture: indexed project
# ---------------------------------------------------------------------------

@pytest.fixture
def indexed_project(tmp_path: Path, sample_txt: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "test-kb", "--yes"], catch_exceptions=False)
    project_dir = tmp_path / "test-kb"
    monkeypatch.chdir(project_dir)
    runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)

    config = load_project_config(project_dir)
    provider = FakeEmbeddingProvider()
    qdrant = _fake_qdrant_client()
    run_index(
        project_dir=project_dir,
        config=config,
        profile_name="default",
        embedding_provider=provider,
        qdrant_client=qdrant,
    )

    db_path = find_db(project_dir)
    conn = open_connection(db_path)
    doc = conn.execute("SELECT id, filename FROM documents LIMIT 1").fetchone()
    conn.close()

    return SimpleNamespace(
        tmp_path=project_dir,
        db_path=db_path,
        config=config,
        document_id=doc["id"],
        filename=doc["filename"],
    )


# ---------------------------------------------------------------------------
# mrag remove
# ---------------------------------------------------------------------------

def test_remove_dry_run_shows_info(indexed_project):
    result = runner.invoke(app, ["remove", indexed_project.document_id], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert indexed_project.filename in result.output


def test_remove_dry_run_does_not_delete(indexed_project):
    runner.invoke(app, ["remove", indexed_project.document_id], catch_exceptions=False)
    conn = open_connection(indexed_project.db_path)
    doc = conn.execute(
        "SELECT id FROM documents WHERE id = ?", (indexed_project.document_id,)
    ).fetchone()
    conn.close()
    assert doc is not None


def test_remove_unknown_id_exits_nonzero(indexed_project):
    result = runner.invoke(app, ["remove", "nonexistent-id"])
    assert result.exit_code != 0


def test_remove_force_deletes_document(indexed_project):
    result = runner.invoke(
        app, ["remove", "--force", indexed_project.document_id], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert "Deleted" in result.output

    conn = open_connection(indexed_project.db_path)
    doc = conn.execute(
        "SELECT id FROM documents WHERE id = ?", (indexed_project.document_id,)
    ).fetchone()
    chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
        (indexed_project.document_id,),
    ).fetchone()[0]
    conn.close()

    assert doc is None
    assert chunks == 0


def test_remove_force_deletes_chunks_and_variants(indexed_project):
    runner.invoke(
        app, ["remove", "--force", indexed_project.document_id], catch_exceptions=False
    )
    conn = open_connection(indexed_project.db_path)
    variants = conn.execute(
        "SELECT COUNT(*) FROM chunk_variants WHERE document_id = ?",
        (indexed_project.document_id,),
    ).fetchone()[0]
    conn.close()
    assert variants == 0


def test_remove_without_init_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remove", "some-id"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# mrag profiles list / show
# ---------------------------------------------------------------------------

def test_profiles_list_after_index(indexed_project):
    result = runner.invoke(app, ["profiles", "list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "default" in result.output


def test_profiles_list_without_init_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["profiles", "list"])
    assert result.exit_code != 0


def test_profiles_show_default(indexed_project):
    result = runner.invoke(app, ["profiles", "show", "default"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "default" in result.output
    assert "strategy" in result.output
    assert "chunk_size" in result.output


def test_profiles_show_nonexistent(indexed_project):
    result = runner.invoke(app, ["profiles", "show", "nonexistent"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# mrag doctor
# ---------------------------------------------------------------------------

def test_doctor_runs_without_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "SQLite" in result.output
    assert "Qdrant" in result.output
    assert "Ollama" in result.output


def test_doctor_shows_sqlite_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert "version" in result.output


def test_doctor_shows_mrag_yaml_status(indexed_project):
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "mrag.yaml" in result.output
    assert "valid" in result.output


def test_doctor_shows_fts5_trigram(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert "trigram" in result.output


# ---------------------------------------------------------------------------
# Integration: remove → search returns no results (Phase 10)
# ---------------------------------------------------------------------------

def test_remove_then_keyword_search_returns_nothing(indexed_project):
    """After --force remove, keyword search must not return the deleted document."""
    from mrag.core.retrieval.keyword import keyword_search

    tokenizer = indexed_project.config.fts_tokenizer

    # Confirm the document is searchable before removal
    pre = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        top_k=10,
        tokenizer=tokenizer,
    )
    assert any(r.document_id == indexed_project.document_id for r in pre), \
        "document should be found before removal"

    # Remove
    runner.invoke(
        app, ["remove", "--force", indexed_project.document_id], catch_exceptions=False
    )

    # Search again — must return nothing from the deleted document
    post = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        top_k=10,
        tokenizer=tokenizer,
    )
    assert not any(r.document_id == indexed_project.document_id for r in post), \
        "deleted document must not appear in search results"


def test_remove_then_document_not_in_db(indexed_project):
    """After --force remove, the document row must be gone from SQLite."""
    runner.invoke(
        app, ["remove", "--force", indexed_project.document_id], catch_exceptions=False
    )
    conn = open_connection(indexed_project.db_path)
    doc = conn.execute(
        "SELECT id FROM documents WHERE id = ?", (indexed_project.document_id,)
    ).fetchone()
    fts_count = conn.execute(
        "SELECT COUNT(*) FROM fts_chunks WHERE document_id = ?",
        (indexed_project.document_id,),
    ).fetchone()[0]
    conn.close()
    assert doc is None
    assert fts_count == 0
