"""Tests for Dify External Knowledge API compatibility (POST /retrieval)."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mrag.api.app import create_app
from mrag.api.routers.dify import _normalize_score
from mrag.cli import app as cli_app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture: fully-indexed project with FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def dify_client(tmp_path: Path, sample_txt: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli_app, ["init", "--name", "test-kb", "--non-interactive"], catch_exceptions=False)
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

    fastapi_app = create_app(
        project_dir=project_dir,
        profile_name="default",
        config=config,
        _embedding_provider=provider,
        _qdrant_client=qdrant,
    )

    with TestClient(fastapi_app) as client:
        yield SimpleNamespace(client=client, config=config)


# ---------------------------------------------------------------------------
# Score normalization unit tests
# ---------------------------------------------------------------------------

def test_normalize_score_keyword():
    # BM25 score of 0 → 0.0
    assert _normalize_score(0.0, "keyword") == 0.0
    # BM25 score → (0, 1)
    s = _normalize_score(5.0, "keyword")
    assert 0.0 < s < 1.0
    # Monotonically increasing
    assert _normalize_score(10.0, "keyword") > _normalize_score(5.0, "keyword")


def test_normalize_score_vector():
    assert _normalize_score(0.9, "vector") == 0.9
    assert _normalize_score(0.0, "vector") == 0.0
    assert _normalize_score(1.0, "vector") == 1.0


def test_normalize_score_clamp():
    # Out-of-range values clamped for non-keyword strategies
    assert _normalize_score(1.5, "vector") == 1.0
    assert _normalize_score(-0.1, "hybrid") == 0.0


def test_normalize_score_reranked_passes_through_whatever_the_strategy():
    # A reranked score is the CrossEncoder's probability, not BM25, so the
    # keyword compression must not apply to it.
    for strategy in ("keyword", "vector", "hybrid", "parent_child"):
        assert _normalize_score(0.737, strategy, reranked=True) == 0.737
        assert _normalize_score(1.5, strategy, reranked=True) == 1.0
        assert _normalize_score(-0.1, strategy, reranked=True) == 0.0
    # Not reranked, keyword is still compressed.
    assert _normalize_score(0.737, "keyword") < 0.5


def test_dify_reranked_keyword_records_carry_the_reranker_score(dify_client, monkeypatch):
    # Under the keyword strategy with reranking on, a record carries the
    # reranker's score as it is, not score/(1+score) of it.
    import mrag.api.routers.dify as router
    results = [
        SimpleNamespace(document_id="d1", chunk_id="c1", content="relevant", score=0.737014, metadata={}),
        SimpleNamespace(document_id="d2", chunk_id="c2", content="unrelated", score=0.500044, metadata={}),
    ]
    monkeypatch.setattr(router, "load_profile", lambda *_: SimpleNamespace(retrieval=SimpleNamespace(strategy="keyword")))
    monkeypatch.setattr(router, "run_retrieval", lambda **_: SimpleNamespace(results=results, reranked=True))
    monkeypatch.setattr(router, "fetch_filename_map", lambda *_: {"d1": "one.md", "d2": "two.md"})

    resp = dify_client.client.post("/retrieval", json={
        "knowledge_id": dify_client.config.knowledge_id,
        "query": "Hello",
        "retrieval_setting": {"top_k": 10, "score_threshold": 0.6},
    })
    assert resp.status_code == 200
    assert [(r["content"], r["score"]) for r in resp.json()["records"]] == [("relevant", 0.737014)]


# ---------------------------------------------------------------------------
# POST /retrieval — success cases
# ---------------------------------------------------------------------------

def test_dify_retrieve_returns_records(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "records" in body
    assert isinstance(body["records"], list)


def test_dify_retrieve_record_fields(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 200
    records = resp.json()["records"]
    if records:
        r = records[0]
        assert "content" in r
        assert "score" in r
        assert "title" in r
        assert "metadata" in r
        assert isinstance(r["metadata"], dict)


def test_dify_retrieve_score_normalized(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 5, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 200
    for r in resp.json()["records"]:
        assert 0.0 <= r["score"] <= 1.0


def test_dify_retrieve_top_k_respected(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 2, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["records"]) <= 2


def test_dify_retrieve_score_threshold_filters(dify_client):
    # A very high threshold should filter out all results
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 10, "score_threshold": 0.9999},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []


def test_dify_threshold_is_compared_with_the_score_the_record_shows(dify_client, monkeypatch):
    # A record that shows 0.5 is kept by a threshold of 0.5, though its score
    # before rounding is a little lower; one that shows 0.499999 is dropped.
    import mrag.api.routers.dify as router
    results = [
        SimpleNamespace(document_id="d1", chunk_id="c1", content="shows 0.5", score=0.4999996, metadata={}),
        SimpleNamespace(document_id="d2", chunk_id="c2", content="shows 0.499999", score=0.4999994, metadata={}),
    ]
    monkeypatch.setattr(router, "run_retrieval", lambda **_: SimpleNamespace(results=results, reranked=False))
    monkeypatch.setattr(router, "fetch_filename_map", lambda *_: {"d1": "one.md", "d2": "two.md"})
    monkeypatch.setattr(router, "_normalize_score", lambda score, strategy, reranked: score)

    def retrieve(threshold):
        resp = dify_client.client.post("/retrieval", json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 10, "score_threshold": threshold},
        })
        assert resp.status_code == 200
        return [(r["content"], r["score"]) for r in resp.json()["records"]]

    assert retrieve(0.5) == [("shows 0.5", 0.5)]
    assert retrieve(0.499999) == [("shows 0.5", 0.5), ("shows 0.499999", 0.499999)]
    assert retrieve(0.500001) == []


def test_dify_retrieve_metadata_condition_accepted(dify_client):
    # An unmatched condition must be applied, not silently ignored.
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
            "metadata_condition": {
                "logical_operator": "and",
                "conditions": [{"name": "source", "comparison_operator": "is", "value": "test"}],
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []


def test_dify_filter_overfetches_and_returns_fixed_metadata(dify_client, monkeypatch):
    import mrag.api.routers.dify as router
    seen = {}
    results = [
        SimpleNamespace(document_id="d1", chunk_id="c1", content="first", score=0.9, metadata={"heading_path": ["Other"]}),
        SimpleNamespace(document_id="d2", chunk_id="c2", content="second", score=0.8, metadata={"heading_path": ["Manual", "Match"], "private": "hidden"}),
    ]

    def fake_retrieve(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(results=results, reranked=False)

    monkeypatch.setattr(router, "run_retrieval", fake_retrieve)
    monkeypatch.setattr(router, "fetch_filename_map", lambda *_: {"d1": "one.md", "d2": "two.md"})
    resp = dify_client.client.post("/retrieval", json={
        "knowledge_id": dify_client.config.knowledge_id,
        "query": "match", "retrieval_setting": {"top_k": 1},
        "metadata_condition": {"logical_operator": "and", "conditions": [
            {"name": "heading_path", "comparison_operator": "contains", "value": "Match"}
        ]},
    })
    assert resp.status_code == 200
    assert seen["top_k"] == 4
    assert [r["content"] for r in resp.json()["records"]] == ["second"]
    assert resp.json()["records"][0]["metadata"] == {
        "document_id": "d2", "source": "two.md", "chunk_id": "c2", "heading_path": "Manual > Match",
    }


def test_dify_rejects_unsupported_filter(dify_client):
    resp = dify_client.client.post("/retrieval", json={
        "knowledge_id": dify_client.config.knowledge_id,
        "query": "Hello", "retrieval_setting": {"top_k": 1},
        "metadata_condition": {"logical_operator": "and", "conditions": [
            {"name": "source", "comparison_operator": ">", "value": "a"}
        ]},
    })
    assert resp.status_code == 400
    assert resp.json()["error_code"] == 4001


# ---------------------------------------------------------------------------
# POST /retrieval — error cases
# ---------------------------------------------------------------------------

def test_dify_retrieve_wrong_knowledge_id(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": "nonexistent-kb-id",
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error_code"] == 2001
    assert "error_msg" in body


def test_dify_retrieve_missing_knowledge_id(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={"query": "Hello", "retrieval_setting": {"top_k": 3, "score_threshold": 0.0}},
    )
    assert resp.status_code == 422


def test_dify_retrieve_missing_query(dify_client):
    resp = dify_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_client.config.knowledge_id,
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Authentication on /retrieval (Dify error codes)
# ---------------------------------------------------------------------------

@pytest.fixture
def dify_auth_client(tmp_path: Path, sample_txt: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MRAG_API_KEY", "dify-test-key")
    runner.invoke(cli_app, ["init", "--name", "auth-kb", "--non-interactive"], catch_exceptions=False)
    project_dir = tmp_path / "auth-kb"
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
    fastapi_app = create_app(
        project_dir=project_dir,
        profile_name="default",
        config=config,
        _embedding_provider=provider,
        _qdrant_client=qdrant,
    )
    with TestClient(fastapi_app) as client:
        yield SimpleNamespace(client=client, config=config)


def test_dify_auth_no_header_returns_1001(dify_auth_client, monkeypatch):
    monkeypatch.setenv("MRAG_API_KEY", "dify-test-key")
    resp = dify_auth_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_auth_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error_code"] == 1001
    assert "error_msg" in body


def test_dify_auth_wrong_key_returns_1002(dify_auth_client, monkeypatch):
    monkeypatch.setenv("MRAG_API_KEY", "dify-test-key")
    resp = dify_auth_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_auth_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error_code"] == 1002
    assert "error_msg" in body


def test_dify_auth_correct_key_succeeds(dify_auth_client, monkeypatch):
    monkeypatch.setenv("MRAG_API_KEY", "dify-test-key")
    resp = dify_auth_client.client.post(
        "/retrieval",
        json={
            "knowledge_id": dify_auth_client.config.knowledge_id,
            "query": "Hello",
            "retrieval_setting": {"top_k": 3, "score_threshold": 0.0},
        },
        headers={"Authorization": "Bearer dify-test-key"},
    )
    assert resp.status_code == 200
    assert "records" in resp.json()
