"""Tests for Phase 8: FastAPI server, native router endpoints."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mrag import __version__
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

def test_openapi_reports_package_version(api_client):
    response = api_client.client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == __version__


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


def test_retrieve_without_top_k_defers_to_profile(api_client, monkeypatch):
    """An omitted top_k must reach run_retrieval as None, not as a request-model default."""
    import mrag.api.routers.native as native_router

    captured = {}

    def fake_run_retrieval(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(results=[], profile_name="default", strategy="keyword", reranked=False)

    monkeypatch.setattr(native_router, "run_retrieval", fake_run_retrieval)

    resp = api_client.client.post("/api/v1/retrieve", json={"query": "Hello"})
    assert resp.status_code == 200
    assert captured["top_k"] is None


def test_retrieve_explicit_top_k_is_passed_through(api_client, monkeypatch):
    import mrag.api.routers.native as native_router

    captured = {}

    def fake_run_retrieval(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(results=[], profile_name="default", strategy="keyword", reranked=False)

    monkeypatch.setattr(native_router, "run_retrieval", fake_run_retrieval)

    resp = api_client.client.post("/api/v1/retrieve", json={"query": "Hello", "top_k": 3})
    assert resp.status_code == 200
    assert captured["top_k"] == 3


def test_retrieve_top_k_bounds_still_enforced(api_client):
    """Making top_k optional must not drop its 1..100 validation."""
    assert api_client.client.post(
        "/api/v1/retrieve", json={"query": "Hello", "top_k": 0}
    ).status_code == 422
    assert api_client.client.post(
        "/api/v1/retrieve", json={"query": "Hello", "top_k": 101}
    ).status_code == 422


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
    body = resp.json()
    # The listing envelope.
    assert (body["schema_version"], body["status"], body["profile"]) == (1, "ok", "default")
    assert body["filter"] == {"all": False, "statuses": []}
    assert (body["total"], body["returned"]) == (1, 1)
    assert body["page"] == {"limit": 100, "offset": 0, "count": 1, "next_offset": None}
    [doc] = body["documents"]
    assert list(doc)[:16] == [
        "document_id", "display_name", "source_identity", "source_binding_status", "content_hash",
        "status", "aggregate_status", "source_status", "index_status", "retrieval_status", "profile",
        "exclusion_id", "created_at", "updated_at", "ingest_ms", "id",
    ]
    assert (doc["status"], doc["aggregate_status"], doc["index_status"]) == ("extracted", "indexed", "indexed")
    assert doc["ingest_ms"] is None


def test_native_and_mcp_document_lists_share_contract(api_client):
    from mrag.config.mcp import load_mcp_config, resolve_mcp_config
    from mrag.mcp.tools import McpToolContext, list_documents_tool

    project = api_client.tmp_path
    cfg = load_mcp_config(env={"MRAG_PROJECT_DIR": str(project)})
    ctx = McpToolContext(resolve_mcp_config(cfg, env={}))
    for query, arguments in [
        ("", {}),
        ("?all=true&status=indexed&limit=1", {"all": True, "status": ["indexed"], "limit": 1}),
    ]:
        native = api_client.client.get(f"/api/v1/documents{query}").json()
        assert native == list_documents_tool(ctx, **arguments)
    assert native["documents"][0]["source_binding_status"] == "external_root"
    assert native["documents"][0]["document_id"] == native["documents"][0]["id"]


@pytest.mark.parametrize(
    ("query", "status", "code"),
    [
        ("?statuses=ready", 400, "documents_query_unknown_parameter"),
        ("?status=missing", 400, "document_status_unknown"),
        ("?all=yes", 400, "documents_query_invalid"),
        ("?limit=0", 400, "documents_page_invalid"),
        ("?limit=501", 400, "documents_page_invalid"),
        ("?offset=-1", 400, "documents_page_invalid"),
        ("?profile=nope", 404, "profile_not_found"),
    ],
)
def test_the_document_list_refuses_what_it_would_otherwise_ignore(api_client, query, status, code):
    resp = api_client.client.get(f"/api/v1/documents{query}")
    assert resp.status_code == status
    body = resp.json()
    assert (body["status"], body["error"]["code"]) == ("error", code)


def test_the_document_list_hides_unready_documents_until_all_is_asked(api_client):
    import sqlite3

    with sqlite3.connect(api_client.tmp_path / "mrag.db") as conn:
        conn.execute(
            "INSERT INTO documents (id, knowledge_id, source_identity, filename, original_path, file_hash, "
            "source_type, status, created_at, updated_at) VALUES "
            "('doc-err', 'k', 'broken.md', 'broken.md', 'x', 'h', 'md', 'error', 't', 't')"
        )
    default = api_client.client.get("/api/v1/documents").json()
    assert [d["document_id"] for d in default["documents"]] != ["doc-err"]
    assert default["total"] == 1
    everything = api_client.client.get("/api/v1/documents?all=true").json()
    broken = next(d for d in everything["documents"] if d["document_id"] == "doc-err")
    assert (everything["total"], broken["aggregate_status"], broken["content_hash"]) == (2, "error", None)
    only_errors = api_client.client.get("/api/v1/documents?all=true&status=error").json()
    assert (only_errors["total"], only_errors["returned"]) == (2, 1)
    assert only_errors["filter"] == {"all": True, "statuses": ["error"]}


def test_a_profile_change_makes_an_indexed_document_stale(api_client):
    profile = api_client.tmp_path / "profiles" / "default.yaml"
    profile.write_text(profile.read_text(encoding="utf-8").replace("chunk_size: 800", "chunk_size: 700"),
                       encoding="utf-8")
    [doc] = api_client.client.get("/api/v1/documents").json()["documents"]
    assert (doc["index_status"], doc["aggregate_status"]) == ("stale", "stale")


def test_an_exclusion_scoped_to_the_profile_is_the_one_reported(api_client):
    import sqlite3

    [doc] = api_client.client.get("/api/v1/documents").json()["documents"]
    with sqlite3.connect(api_client.tmp_path / "mrag.db") as conn:
        conn.execute("INSERT INTO document_exclusions (id, document_id, profile_name, created_at) "
                     "VALUES ('global', ?, NULL, 't1')", (doc["document_id"],))
        conn.execute("INSERT INTO document_exclusions (id, document_id, profile_name, created_at) "
                     "VALUES ('scoped', ?, 'default', 't2')", (doc["document_id"],))
        conn.execute("INSERT INTO document_exclusions (id, document_id, profile_name, created_at) "
                     "VALUES ('other', ?, 'other', 't3')", (doc["document_id"],))
    [row] = api_client.client.get("/api/v1/documents").json()["documents"]
    assert (row["retrieval_status"], row["exclusion_id"], row["aggregate_status"]) == (
        "excluded", "scoped", "excluded"
    )


def test_get_document(api_client):
    docs_resp = api_client.client.get("/api/v1/documents")
    doc_id = docs_resp.json()["documents"][0]["id"]

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
