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
    runner.invoke(app, ["init", "--name", "test-kb", "--non-interactive"], catch_exceptions=False)
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


def test_an_invalid_profile_is_an_error_line_not_a_traceback(indexed_project):
    """`profiles show` and `search` caught only a missing profile, so one that
    failed validation ended them with a traceback; `index` and `serve` already
    printed the reason and exited 1 (1.5.6)."""
    profile = Path("profiles") / "default.yaml"
    text = profile.read_text(encoding="utf-8")
    assert text.count("  provider: ollama") == 1
    profile.write_text(text.replace("  provider: ollama", "  provider: lm_studio"), encoding="utf-8")

    for args in (["profiles", "show", "default"], ["search", "hello"], ["index"]):
        result = runner.invoke(app, args, catch_exceptions=False)

        assert result.exit_code == 1, (args, result.output)
        assert "Error:" in result.output, (args, result.output)
        assert "embedding.provider" in result.output, (args, result.output)


# ---------------------------------------------------------------------------
# mrag doctor
# ---------------------------------------------------------------------------

def test_doctor_runs_without_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "SQLite" in result.output
    assert "Ollama" in result.output


def test_doctor_shows_sqlite_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert "version" in result.output


def test_doctor_is_project_independent(indexed_project):
    """Doctor output must not depend on whether the cwd is an mrag project."""
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0
    # Project section was removed — mrag.yaml status is not part of doctor.
    assert "mrag.yaml" not in result.output
    # Qdrant check was removed entirely.
    assert "Qdrant" not in result.output
    assert "qdrant" not in result.output


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


def _doctor_module():
    # `mrag.cli` re-exports the `doctor` command under the module's own name, so
    # a dotted monkeypatch target would resolve to the function, not the module.
    import importlib
    return importlib.import_module("mrag.cli.doctor")


def test_doctor_reports_missing_apsw_on_its_own_line(tmp_path, monkeypatch):
    # A vaporetto project fails at search time with "APSW is not installed";
    # doctor has to say so even when the library check stops early.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_doctor_module(), "_apsw_version", lambda: None)
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0
    line = next(l for l in result.output.splitlines() if "apsw (optional" in l)
    assert "WARN" in line and "not installed" in line


def test_doctor_reports_installed_apsw_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_doctor_module(), "_apsw_version", lambda: "9.9.9.0")
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    line = next(l for l in result.output.splitlines() if "apsw (optional" in l)
    assert "OK" in line and "9.9.9.0" in line


def test_doctor_names_apsw_when_library_is_found_but_cannot_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_doctor_module(), "_apsw_version", lambda: None)
    monkeypatch.setattr(
        "mrag.db.tokenizer.find_vaporetto_lib",
        lambda: tmp_path / "libsqlite_vaporetto.dylib",
    )
    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert "apsw is not installed" in result.output.replace("\n", "")


@pytest.mark.parametrize(
    ("version", "badge", "expected"),
    [
        ("0.40.7", "OK", "0.40.7"),
        ("0.40.4", "OK", "0.40.4"),
        ("0.40.3", "WARN", "EmbeddingGemma 2, the embedding model mrag init writes, needs 0.40.4+"),
        ("0.9.10", "WARN", "needs 0.40.4+"),
        ("dev", "WARN", "cannot tell whether it serves EmbeddingGemma 2"),
    ],
)
def test_doctor_judges_the_ollama_version_against_embeddinggemma_2(tmp_path, monkeypatch, version, badge, expected):
    from unittest.mock import MagicMock, patch

    monkeypatch.chdir(tmp_path)
    response = MagicMock()
    response.json.return_value = {"version": version}
    with patch("httpx.get", return_value=response):
        result = runner.invoke(app, ["doctor"], catch_exceptions=False)

    [line] = [line for line in result.output.splitlines() if "version (0.40.4+" in line]
    assert line.split()[0] == badge
    assert expected in " ".join(result.output.split())
    assert result.exit_code == 0


def test_doctor_skips_the_version_when_ollama_is_unreachable(tmp_path, monkeypatch):
    from unittest.mock import patch

    import httpx

    monkeypatch.chdir(tmp_path)
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = runner.invoke(app, ["doctor"], catch_exceptions=False)

    assert "not reachable" in result.output
    assert "0.40.4" not in result.output
