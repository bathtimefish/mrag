"""Tests for Phase 8: FastAPI server, native router endpoints."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mrag.api.app import create_app
from mrag.cli import app as cli_app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from mrag.db.connection import find_db, open_connection
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture: fully-indexed project with FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(tmp_path: Path, sample_txt: Path, monkeypatch):
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
        yield SimpleNamespace(client=client, tmp_path=project_dir, config=config)


# ---------------------------------------------------------------------------
# POST /api/v1/retrieve (and /search alias)
# ---------------------------------------------------------------------------

def test_retrieve_keyword(api_client):
    resp = api_client.client.post(
        "/api/v1/retrieve",
        json={"query": "Hello world", "strategy": "keyword", "top_k": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "keyword"
    assert isinstance(body["results"], list)
    assert "query" in body
    assert "profile" in body


def test_retrieve_vector(api_client):
    resp = api_client.client.post(
        "/api/v1/retrieve",
        json={"query": "Hello world", "strategy": "vector", "top_k": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "vector"


def test_retrieve_hybrid_default(api_client):
    resp = api_client.client.post(
        "/api/v1/retrieve",
        json={"query": "Hello world", "top_k": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"] == "default"


def test_search_alias(api_client):
    resp = api_client.client.post(
        "/api/v1/search",
        json={"query": "Hello world", "strategy": "keyword"},
    )
    assert resp.status_code == 200


def test_retrieve_result_fields(api_client):
    resp = api_client.client.post(
        "/api/v1/retrieve",
        json={"query": "Hello", "strategy": "keyword", "top_k": 1},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    if results:
        r = results[0]
        assert "chunk_id" in r
        assert "document_id" in r
        assert "filename" in r
        assert "score" in r
        assert "content" in r


def test_retrieve_unknown_profile(api_client):
    resp = api_client.client.post(
        "/api/v1/retrieve",
        json={"query": "test", "profile": "nonexistent"},
    )
    assert resp.status_code == 404


def test_retrieve_alternate_profile_does_not_reuse_startup_provider(
    api_client,
    monkeypatch,
):
    """A request profile must resolve its own embedding and rerank providers."""
    import yaml
    import mrag.api.routers.native as native_router

    default_path = api_client.tmp_path / "profiles" / "default.yaml"
    alternate_path = api_client.tmp_path / "profiles" / "alternate.yaml"
    profile_data = yaml.safe_load(default_path.read_text(encoding="utf-8"))
    profile_data["name"] = "alternate"
    profile_data["embedding"]["model"] = "alternate-model"
    alternate_path.write_text(
        yaml.safe_dump(profile_data, sort_keys=False),
        encoding="utf-8",
    )

    captured = {}

    def fake_run_retrieval(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            results=[],
            profile_name="alternate",
            strategy="hybrid",
            reranked=False,
        )

    monkeypatch.setattr(native_router, "run_retrieval", fake_run_retrieval)

    response = api_client.client.post(
        "/api/v1/retrieve",
        json={"query": "Hello", "profile": "alternate"},
    )

    assert response.status_code == 200
    assert captured["profile_name"] == "alternate"
    assert captured["embedding_provider"] is None
    assert captured["reranker"] is None
    assert captured["load_reranker"] is True


# ---------------------------------------------------------------------------
# GET /api/v1/documents
# ---------------------------------------------------------------------------

def test_list_documents(api_client):
    resp = api_client.client.get("/api/v1/documents")
    assert resp.status_code == 200
    docs = resp.json()
    assert isinstance(docs, list)
    assert len(docs) >= 1
    doc = docs[0]
    assert "id" in doc
    assert "filename" in doc
    assert "status" in doc
    assert "created_at" in doc


def test_get_document(api_client):
    docs_resp = api_client.client.get("/api/v1/documents")
    doc_id = docs_resp.json()[0]["id"]

    resp = api_client.client.get(f"/api/v1/documents/{doc_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == doc_id
    assert "chunk_count" in body
    assert body["chunk_count"] >= 0


def test_get_document_not_found(api_client):
    resp = api_client.client.get("/api/v1/documents/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/profiles
# ---------------------------------------------------------------------------

def test_list_profiles(api_client):
    resp = api_client.client.get("/api/v1/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    assert isinstance(profiles, list)
    # "default" profile was used for indexing and should be registered
    names = [p["name"] for p in profiles]
    assert "default" in names


def test_get_profile(api_client):
    resp = api_client.client.get("/api/v1/profiles/default")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "default"
    assert "strategy" in body
    assert "embedding_model" in body
    assert "chunking_strategy" in body
    assert "chunk_size" in body


def test_get_profile_not_found(api_client):
    resp = api_client.client.get("/api/v1/profiles/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authentication middleware
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_client(tmp_path: Path, sample_txt: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MRAG_API_KEY", "secret-test-key")

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
        yield client


def test_auth_no_key_rejected(auth_client, monkeypatch):
    monkeypatch.setenv("MRAG_API_KEY", "secret-test-key")
    resp = auth_client.get("/api/v1/documents")
    assert resp.status_code == 401


def test_auth_wrong_key_rejected(auth_client, monkeypatch):
    monkeypatch.setenv("MRAG_API_KEY", "secret-test-key")
    resp = auth_client.get(
        "/api/v1/documents", headers={"Authorization": "Bearer wrong-key"}
    )
    assert resp.status_code == 401


def test_auth_correct_key_accepted(auth_client, monkeypatch):
    monkeypatch.setenv("MRAG_API_KEY", "secret-test-key")
    resp = auth_client.get(
        "/api/v1/documents", headers={"Authorization": "Bearer secret-test-key"}
    )
    assert resp.status_code == 200
