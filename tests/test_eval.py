"""Tests for mrag eval command."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from mrag.core.retrieval.base import RetrievalResult
from mrag.cli.eval import detect_duplicates, _fetch_filenames
from mrag.db.connection import find_db
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _r(chunk_id: str, score: float, content: str = "content", doc_id: str = "doc1") -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, document_id=doc_id, content=content, score=score)


# ---------------------------------------------------------------------------
# detect_duplicates unit tests
# ---------------------------------------------------------------------------

class TestDetectDuplicates:
    def test_no_duplicates(self):
        results = [_r("c1", 0.9, "aaa"), _r("c2", 0.8, "bbb"), _r("c3", 0.7, "ccc")]
        assert detect_duplicates(results) == set()

    def test_detects_exact_duplicate(self):
        results = [_r("c1", 0.9, "same content"), _r("c2", 0.8, "same content")]
        dupes = detect_duplicates(results)
        assert dupes == {"c1", "c2"}

    def test_ignores_leading_trailing_whitespace(self):
        results = [_r("c1", 0.9, "  content  "), _r("c2", 0.8, "content")]
        dupes = detect_duplicates(results)
        assert dupes == {"c1", "c2"}

    def test_three_results_two_duplicates(self):
        results = [
            _r("c1", 0.9, "unique"),
            _r("c2", 0.8, "dup"),
            _r("c3", 0.7, "dup"),
        ]
        dupes = detect_duplicates(results)
        assert "c1" not in dupes
        assert {"c2", "c3"} == dupes

    def test_empty_results(self):
        assert detect_duplicates([]) == set()

    def test_single_result(self):
        assert detect_duplicates([_r("c1", 0.9, "x")]) == set()


# ---------------------------------------------------------------------------
# Fixture: indexed project
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
    return SimpleNamespace(
        project_dir=project_dir,
        db_path=db_path,
        config=config,
        provider=provider,
        qdrant=qdrant,
    )


# ---------------------------------------------------------------------------
# _fetch_filenames unit test
# ---------------------------------------------------------------------------

def test_fetch_filenames(indexed_project):
    from mrag.db.connection import open_connection
    conn = open_connection(indexed_project.db_path)
    row = conn.execute("SELECT id, filename FROM documents LIMIT 1").fetchone()
    conn.close()

    results = [_r("c1", 0.9, doc_id=row["id"])]
    filename_map = _fetch_filenames(indexed_project.db_path, results)
    assert row["id"] in filename_map
    assert filename_map[row["id"]] == row["filename"]


def test_fetch_filenames_empty(indexed_project):
    assert _fetch_filenames(indexed_project.db_path, []) == {}


# ---------------------------------------------------------------------------
# mrag eval CLI — single profile
# ---------------------------------------------------------------------------

class TestEvalSingleProfile:
    def test_eval_keyword_exits_zero(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "Hello", "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_eval_shows_query(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "Hello", "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert "Hello" in result.output

    def test_eval_shows_score_stats(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "Hello", "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert "Score stats" in result.output

    def test_eval_shows_document_distribution(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "Hello", "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert "Document distribution" in result.output

    def test_eval_no_results(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "xyznonexistentterm", "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_eval_no_rerank_flag_accepted(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "Hello", "--strategy", "keyword", "--no-rerank"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_eval_unknown_profile_exits_nonzero(self, indexed_project):
        result = runner.invoke(
            app, ["eval", "Hello", "--profile", "nonexistent"],
        )
        assert result.exit_code != 0

    def test_eval_without_init_exits_nonzero(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["eval", "query"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# mrag eval CLI — multi-profile diff
# ---------------------------------------------------------------------------

class TestEvalMultiProfile:
    @pytest.fixture
    def two_profile_project(self, indexed_project):
        """Add a second profile (same embedding model, different chunking)."""
        import yaml
        second_profile = {
            "name": "second",
            "extraction": {"pdf": {"provider": "pymupdf", "output_format": "text"}},
            "chunking": {"strategy": "recursive", "source_format": "text",
                         "chunk_size": 400, "overlap": 60},
            "retrieval": {"strategy": "hybrid", "top_k": 8,
                          "dense_top_k": 20, "keyword_top_k": 20, "fusion": "rrf"},
            "embedding": {"provider": "ollama", "model": "bge-m3",
                          "endpoint": "http://localhost:11434",
                          "cache": {"enabled": False}},
            "contextual": {"enabled": False},
            "keyword": {"provider": "sqlite_fts5", "tokenizer": "trigram",
                        "fallback_tokenizer": "trigram"},
            "rerank": {"enabled": False, "provider": "sentence-transformers",
                       "model": "hotchpotch/japanese-reranker-cross-encoder-small-v1",
                       "top_n": 30, "top_k": 8},
        }
        profile_path = indexed_project.project_dir / "profiles" / "second.yaml"
        profile_path.write_text(yaml.dump(second_profile), encoding="utf-8")

        run_index(
            project_dir=indexed_project.project_dir,
            config=indexed_project.config,
            profile_name="second",
            embedding_provider=indexed_project.provider,
            qdrant_client=indexed_project.qdrant,
        )
        return indexed_project

    def test_diff_shows_profile_names(self, two_profile_project):
        result = runner.invoke(
            app,
            ["eval", "Hello",
             "--profile", "default", "--profile", "second",
             "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "default" in result.output
        assert "second" in result.output

    def test_diff_shows_profile_diff_section(self, two_profile_project):
        result = runner.invoke(
            app,
            ["eval", "Hello",
             "--profile", "default", "--profile", "second",
             "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Profile Diff" in result.output

    def test_diff_warns_on_different_models(self, two_profile_project):
        """Swap the second profile's embedding model and verify warning."""
        import yaml
        profile_path = two_profile_project.project_dir / "profiles" / "second.yaml"
        data = yaml.safe_load(profile_path.read_text())
        data["embedding"]["model"] = "other-model"
        profile_path.write_text(yaml.dump(data), encoding="utf-8")

        result = runner.invoke(
            app,
            ["eval", "Hello",
             "--profile", "default", "--profile", "second",
             "--strategy", "keyword"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "embedding models" in result.output
