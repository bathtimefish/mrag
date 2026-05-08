"""Tests for reranking: BaseReranker, SentenceTransformersReranker, get_reranker, CLI, API."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mrag.cli import app as cli_app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from mrag.core.retrieval.base import RetrievalResult
from mrag.db.connection import find_db
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _r(chunk_id: str, score: float, content: str = "sample content") -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, document_id="doc1", content=content, score=score)


def _fake_cross_encoder(scores: list[float]):
    """Return a mock CrossEncoder whose predict() returns the given scores."""
    mock = MagicMock()
    mock.predict.return_value = scores
    return mock


# ---------------------------------------------------------------------------
# SentenceTransformersReranker unit tests (CrossEncoder mocked)
# ---------------------------------------------------------------------------

class TestSentenceTransformersReranker:
    def _make_reranker(self, scores: list[float]):
        import mrag.core.reranking.sentence_transformers as mod
        with patch.object(mod, "CrossEncoder", return_value=_fake_cross_encoder(scores)):
            from mrag.core.reranking.sentence_transformers import SentenceTransformersReranker
            return SentenceTransformersReranker(model="dummy-model")

    def test_rerank_sorts_by_score_descending(self):
        reranker = self._make_reranker([0.2, 0.9, 0.5])
        results = [_r("c1", 0.8), _r("c2", 0.6), _r("c3", 0.7)]
        reranked = reranker.rerank("query", results)
        scores = [r.score for r in reranked]
        assert scores == sorted(scores, reverse=True)
        assert reranked[0].chunk_id == "c2"  # highest reranker score 0.9

    def test_rerank_preserves_content(self):
        reranker = self._make_reranker([0.7, 0.3])
        results = [_r("c1", 0.8, content="hello"), _r("c2", 0.6, content="world")]
        reranked = reranker.rerank("query", results)
        contents = {r.chunk_id: r.content for r in reranked}
        assert contents["c1"] == "hello"
        assert contents["c2"] == "world"

    def test_rerank_stores_retrieval_score_in_metadata(self):
        reranker = self._make_reranker([0.9])
        results = [_r("c1", 0.75)]
        reranked = reranker.rerank("query", results)
        assert reranked[0].metadata["retrieval_score"] == pytest.approx(0.75)

    def test_rerank_empty_returns_empty(self):
        reranker = self._make_reranker([])
        assert reranker.rerank("query", []) == []

    def test_rerank_score_is_cross_encoder_score(self):
        reranker = self._make_reranker([1.23])
        results = [_r("c1", 0.5)]
        reranked = reranker.rerank("query", results)
        assert reranked[0].score == pytest.approx(1.23)

    def test_import_error_on_missing_sentence_transformers(self):
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            import importlib
            import mrag.core.reranking.sentence_transformers as mod
            importlib.reload(mod)
            with pytest.raises(ImportError, match="sentence-transformers"):
                mod.SentenceTransformersReranker(model="dummy")


# ---------------------------------------------------------------------------
# get_reranker factory
# ---------------------------------------------------------------------------

class TestGetReranker:
    def test_returns_sentence_transformers_reranker(self):
        import mrag.core.reranking.sentence_transformers as mod
        from mrag.config.profile import RerankConfig
        from mrag.core.reranking.base import BaseReranker
        from mrag.core.reranking import get_reranker

        config = RerankConfig(enabled=True, provider="sentence-transformers", model="dummy")
        with patch.object(mod, "CrossEncoder", return_value=_fake_cross_encoder([0.5])):
            reranker = get_reranker(config)
        assert isinstance(reranker, BaseReranker)

    def test_raises_on_unknown_provider(self):
        from mrag.config.profile import RerankConfig
        from mrag.core.reranking import get_reranker

        config = RerankConfig(enabled=True, provider="unknown-provider", model="m")
        with pytest.raises(ValueError, match="Unsupported reranker provider"):
            get_reranker(config)


# ---------------------------------------------------------------------------
# Fixture: indexed project
# ---------------------------------------------------------------------------

@pytest.fixture
def indexed_project(tmp_path: Path, sample_txt: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli_app, ["init", "--name", "test-kb", "--yes"], catch_exceptions=False)
    project_dir = tmp_path / "test-kb"
    monkeypatch.chdir(project_dir)
    runner.invoke(cli_app, ["add", str(sample_txt)], catch_exceptions=False)

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
# CLI: --no-rerank
# ---------------------------------------------------------------------------

class TestSearchNoRerank:
    def test_no_rerank_flag_accepted(self, indexed_project):
        result = runner.invoke(
            cli_app,
            ["search", "Hello", "--strategy", "keyword", "--no-rerank"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_rerank_skipped_when_no_rerank_flag(self, indexed_project, monkeypatch):
        """With --no-rerank, get_reranker should never be called."""
        monkeypatch.chdir(indexed_project.project_dir)

        with patch("mrag.core.reranking.get_reranker") as mock_get_reranker:
            runner.invoke(
                cli_app,
                ["search", "Hello", "--strategy", "keyword", "--no-rerank"],
                catch_exceptions=False,
            )
        mock_get_reranker.assert_not_called()


# ---------------------------------------------------------------------------
# API: reranker in app.state
# ---------------------------------------------------------------------------

def _make_api_client(indexed_project, reranker=None):
    from mrag.api.app import create_app
    fastapi_app = create_app(
        project_dir=indexed_project.project_dir,
        profile_name="default",
        config=indexed_project.config,
        _embedding_provider=indexed_project.provider,
        _qdrant_client=indexed_project.qdrant,
        reranker=reranker,
    )
    return TestClient(fastapi_app)


class TestAPIReranking:
    def test_retrieve_reranked_false_when_no_reranker(self, indexed_project):
        client = _make_api_client(indexed_project, reranker=None)
        with client:
            resp = client.post(
                "/api/v1/retrieve",
                json={"query": "Hello", "strategy": "keyword", "top_k": 3},
            )
        assert resp.status_code == 200
        assert resp.json()["reranked"] is False

    def test_retrieve_reranked_true_when_reranker_present(self, indexed_project):
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = lambda query, results: results

        client = _make_api_client(indexed_project, reranker=mock_reranker)
        with client:
            resp = client.post(
                "/api/v1/retrieve",
                json={"query": "Hello", "strategy": "keyword", "top_k": 3},
            )
        assert resp.status_code == 200
        assert resp.json()["reranked"] is True
        mock_reranker.rerank.assert_called_once()

    def test_dify_retrieve_applies_reranker(self, indexed_project):
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = lambda query, results: results

        client = _make_api_client(indexed_project, reranker=mock_reranker)
        with client:
            resp = client.post(
                "/retrieval",
                json={
                    "knowledge_id": indexed_project.config.knowledge_id,
                    "query": "Hello",
                    "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
                },
            )
        assert resp.status_code == 200
        mock_reranker.rerank.assert_called_once()

    def test_dify_retrieve_skips_reranker_when_none(self, indexed_project):
        client = _make_api_client(indexed_project, reranker=None)
        with client:
            resp = client.post(
                "/retrieval",
                json={
                    "knowledge_id": indexed_project.config.knowledge_id,
                    "query": "Hello",
                    "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
                },
            )
        assert resp.status_code == 200
