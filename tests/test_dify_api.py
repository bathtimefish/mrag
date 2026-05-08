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


def test_dify_retrieve_metadata_condition_accepted(dify_client):
    # metadata_condition is optional; must be accepted without error even if not applied
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
    runner.invoke(cli_app, ["init", "--name", "auth-kb", "--yes"], catch_exceptions=False)
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
