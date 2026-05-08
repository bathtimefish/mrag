"""Tests for Phase 4: embedding provider, cache, and Qdrant client."""
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.embedding.ollama import OllamaEmbeddingProvider, _normalize
from mrag.core.embedding.cache import EmbeddingCache, _cache_key
from mrag.db.qdrant import (
    collection_name,
    normalize_name,
    ensure_collection,
    upsert_points,
    search,
    delete_points,
)
from mrag.db.migrate import apply_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embed_response(texts: list[str], dim: int = 4) -> dict:
    return {"embeddings": [[float(i)] * dim for i in range(len(texts))]}


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "mrag.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    apply_schema(conn)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# OllamaEmbeddingProvider — unit tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestOllamaEmbeddingProvider:
    def _provider(self) -> OllamaEmbeddingProvider:
        return OllamaEmbeddingProvider(model="nomic-embed-text", endpoint="http://localhost:11434")

    def test_is_base_provider(self):
        assert isinstance(self._provider(), BaseEmbeddingProvider)

    def test_embed_returns_vectors(self):
        prov = self._provider()
        fake_resp = MagicMock()
        fake_resp.json.return_value = _fake_embed_response(["hello", "world"], dim=4)
        fake_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=fake_resp):
            result = prov.embed(["hello", "world"])

        assert len(result) == 2
        assert len(result[0]) == 4

    def test_embed_empty_returns_empty(self):
        prov = self._provider()
        assert prov.embed([]) == []

    def test_dimension_set_after_embed(self):
        prov = self._provider()
        fake_resp = MagicMock()
        fake_resp.json.return_value = _fake_embed_response(["hi"], dim=768)
        fake_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=fake_resp):
            prov.embed(["hi"])

        assert prov.get_dimension() == 768

    def test_get_dimension_raises_before_embed(self):
        prov = self._provider()
        with pytest.raises(RuntimeError, match="Dimension unknown"):
            prov.get_dimension()

    def test_get_model_id_format(self):
        prov = self._provider()
        fake_resp = MagicMock()
        fake_resp.json.return_value = _fake_embed_response(["x"], dim=768)
        fake_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=fake_resp):
            prov.embed(["x"])

        model_id = prov.get_model_id()
        assert model_id == "ollama:nomic-embed-text:768:v1"

    def test_get_normalized_name(self):
        prov = OllamaEmbeddingProvider(model="nomic-embed-text")
        assert prov.get_normalized_name() == "nomic_embed_text"

    def test_batch_splitting(self):
        """Texts exceeding batch_size must be sent in multiple POST requests."""
        prov = OllamaEmbeddingProvider(model="nomic-embed-text", batch_size=2)
        texts = ["a", "b", "c", "d", "e"]

        call_count = 0

        def fake_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            batch = kwargs["json"]["input"]
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = _fake_embed_response(batch, dim=4)
            return resp

        with patch("httpx.post", side_effect=fake_post):
            result = prov.embed(texts)

        assert call_count == 3  # ceil(5/2) = 3
        assert len(result) == 5

    def test_connect_error_raises_connection_error(self):
        import httpx as _httpx
        prov = self._provider()
        with patch("httpx.post", side_effect=_httpx.ConnectError("refused")):
            with pytest.raises(ConnectionError, match="Cannot connect to Ollama"):
                prov.embed(["text"])

    def test_http_status_error_raises_runtime_error(self):
        import httpx as _httpx
        prov = self._provider()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch(
            "httpx.post",
            side_effect=_httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp),
        ):
            with pytest.raises(RuntimeError, match="Ollama returned HTTP"):
                prov.embed(["text"])

    def test_ensure_model_registered(self, tmp_path: Path):
        db_path = _make_db(tmp_path)
        prov = self._provider()
        fake_resp = MagicMock()
        fake_resp.json.return_value = _fake_embed_response(["x"], dim=768)
        fake_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=fake_resp):
            prov.embed(["x"])

        prov.ensure_model_registered(db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM embedding_models").fetchone()
        conn.close()

        assert row is not None
        assert row["provider"] == "ollama"
        assert row["model_name"] == "nomic-embed-text"
        assert row["dimension"] == 768
        assert row["normalized_name"] == "nomic_embed_text"

    def test_ensure_model_registered_idempotent(self, tmp_path: Path):
        """Calling ensure_model_registered twice must not raise or duplicate rows."""
        db_path = _make_db(tmp_path)
        prov = self._provider()
        fake_resp = MagicMock()
        fake_resp.json.return_value = _fake_embed_response(["x"], dim=768)
        fake_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=fake_resp):
            prov.embed(["x"])

        prov.ensure_model_registered(db_path)
        prov.ensure_model_registered(db_path)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM embedding_models").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# _normalize helper
# ---------------------------------------------------------------------------

def test_normalize_name():
    assert _normalize("nomic-embed-text") == "nomic_embed_text"
    assert _normalize("My Model v2") == "my_model_v2"
    assert _normalize("---test---") == "test"


# ---------------------------------------------------------------------------
# EmbeddingCache — unit tests (no real HTTP needed)
# ---------------------------------------------------------------------------

class TestEmbeddingCache:
    def _cache(self, tmp_path: Path) -> tuple[EmbeddingCache, Path]:
        db_path = _make_db(tmp_path)
        # Register a fake embedding model so FK constraint passes
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO embedding_models (id, provider, model_name, dimension, created_at) "
            "VALUES (?,?,?,?,?)",
            ("ollama:nomic-embed-text:4:v1", "ollama", "nomic-embed-text", 4, "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()
        cache = EmbeddingCache(cache_dir=tmp_path / "cache" / "embeddings", db_path=db_path)
        return cache, db_path

    def test_miss_returns_none(self, tmp_path: Path):
        cache, _ = self._cache(tmp_path)
        assert cache.get("nonexistent_key") is None

    def test_put_and_get(self, tmp_path: Path):
        cache, _ = self._cache(tmp_path)
        model_id = "ollama:nomic-embed-text:4:v1"
        key = cache.key_for(model_id, "hello")
        vector = [0.1, 0.2, 0.3, 0.4]

        cache.put(key, model_id, vector)
        result = cache.get(key)

        assert result is not None
        assert len(result) == 4
        for a, b in zip(result, vector):
            assert abs(a - b) < 1e-5

    def test_cache_key_is_deterministic(self):
        k1 = _cache_key("model_id", "content")
        k2 = _cache_key("model_id", "content")
        assert k1 == k2

    def test_cache_key_differs_by_content(self):
        assert _cache_key("m", "a") != _cache_key("m", "b")

    def test_cache_key_differs_by_model(self):
        assert _cache_key("m1", "text") != _cache_key("m2", "text")

    def test_npy_file_created(self, tmp_path: Path):
        cache, _ = self._cache(tmp_path)
        model_id = "ollama:nomic-embed-text:4:v1"
        key = cache.key_for(model_id, "hello")
        cache.put(key, model_id, [1.0, 2.0, 3.0, 4.0])
        npy_path = tmp_path / "cache" / "embeddings" / f"{key}.npy"
        assert npy_path.exists()

    def test_db_record_created(self, tmp_path: Path):
        cache, db_path = self._cache(tmp_path)
        model_id = "ollama:nomic-embed-text:4:v1"
        key = cache.key_for(model_id, "hello")
        cache.put(key, model_id, [1.0, 2.0, 3.0, 4.0])

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT * FROM embedding_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        conn.close()
        assert row is not None

    def test_get_or_embed_uses_cache_on_hit(self, tmp_path: Path):
        cache, _ = self._cache(tmp_path)
        model_id = "ollama:nomic-embed-text:4:v1"
        vector = [0.1, 0.2, 0.3, 0.4]
        key = cache.key_for(model_id, "hello")
        cache.put(key, model_id, vector)

        called = []
        def embed_fn(texts):
            called.extend(texts)
            return [[0.9] * 4 for _ in texts]

        result = cache.get_or_embed(["hello"], model_id, embed_fn)
        assert called == []  # embed_fn must NOT be called on cache hit
        for a, b in zip(result[0], vector):
            assert abs(a - b) < 1e-5

    def test_get_or_embed_calls_embed_on_miss(self, tmp_path: Path):
        cache, _ = self._cache(tmp_path)
        model_id = "ollama:nomic-embed-text:4:v1"

        called = []
        def embed_fn(texts):
            called.extend(texts)
            return [[0.5] * 4 for _ in texts]

        result = cache.get_or_embed(["new text"], model_id, embed_fn)
        assert "new text" in called
        assert len(result) == 1

    def test_get_or_embed_mixed_hit_and_miss(self, tmp_path: Path):
        cache, _ = self._cache(tmp_path)
        model_id = "ollama:nomic-embed-text:4:v1"

        # Pre-populate "cached" but not "fresh"
        cached_key = cache.key_for(model_id, "cached")
        cache.put(cached_key, model_id, [1.0, 1.0, 1.0, 1.0])

        called = []
        def embed_fn(texts):
            called.extend(texts)
            return [[2.0] * 4 for _ in texts]

        result = cache.get_or_embed(["cached", "fresh"], model_id, embed_fn)
        assert called == ["fresh"]
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Qdrant helpers — unit tests (mocked QdrantClient)
# ---------------------------------------------------------------------------

class TestQdrantHelpers:
    def test_normalize_name_lower(self):
        assert normalize_name("MyKB") == "mykb"

    def test_normalize_name_replaces_special(self):
        assert normalize_name("kb-001") == "kb_001"
        assert normalize_name("nomic-embed-text") == "nomic_embed_text"

    def test_collection_name_format(self):
        name = collection_name("my_kb", "default", "nomic_embed_text")
        assert name == "mrag_my_kb_default_nomic_embed_text"

    def test_collection_name_normalizes_inputs(self):
        name = collection_name("My-KB", "Default", "nomic-embed-text")
        assert name == "mrag_my_kb_default_nomic_embed_text"

    def test_ensure_collection_creates_when_absent(self):
        client = MagicMock()
        client.get_collections.return_value.collections = []
        ensure_collection(client, "mrag_test", dimension=768)
        client.create_collection.assert_called_once()

    def test_ensure_collection_skips_if_exists(self):
        existing = MagicMock()
        existing.name = "mrag_test"
        client = MagicMock()
        client.get_collections.return_value.collections = [existing]
        ensure_collection(client, "mrag_test", dimension=768)
        client.create_collection.assert_not_called()

    def test_upsert_points_calls_client(self):
        client = MagicMock()
        points = [
            {"id": str(uuid.uuid4()), "vector": [0.1, 0.2], "payload": {"chunk_id": "c1"}},
            {"id": str(uuid.uuid4()), "vector": [0.3, 0.4], "payload": {"chunk_id": "c2"}},
        ]
        upsert_points(client, "mrag_test", points)
        client.upsert.assert_called_once()
        call_args = client.upsert.call_args
        assert call_args.kwargs["collection_name"] == "mrag_test"
        assert len(call_args.kwargs["points"]) == 2

    def test_search_returns_list_of_dicts(self):
        hit = MagicMock()
        hit.id = str(uuid.uuid4())
        hit.score = 0.95
        hit.payload = {"chunk_id": "c1"}
        client = MagicMock()
        client.query_points.return_value.points = [hit]

        result = search(client, "mrag_test", vector=[0.1, 0.2], top_k=5)
        assert len(result) == 1
        assert result[0]["score"] == 0.95
        assert result[0]["payload"]["chunk_id"] == "c1"
        client.query_points.assert_called_once_with(
            collection_name="mrag_test",
            query=[0.1, 0.2],
            limit=5,
            with_payload=True,
        )

    def test_delete_points_calls_client(self):
        client = MagicMock()
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        delete_points(client, "mrag_test", ids)
        client.delete.assert_called_once()


# ---------------------------------------------------------------------------
# Integration tests — skipped when services are unavailable
# ---------------------------------------------------------------------------

def _ollama_model_available(model: str = "nomic-embed-text") -> bool:
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if resp.status_code != 200:
            return False
        models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        return model in models
    except Exception:
        return False


def _qdrant_available() -> bool:
    try:
        from qdrant_client import QdrantClient
        QdrantClient(host="localhost", port=6333).get_collections()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_model_available(), reason="Ollama nomic-embed-text not available")
def test_ollama_integration_embed():
    prov = OllamaEmbeddingProvider(model="nomic-embed-text")
    vectors = prov.embed(["Hello, Ollama!"])
    assert len(vectors) == 1
    assert len(vectors[0]) > 0
    assert prov.get_dimension() == len(vectors[0])


@pytest.mark.skipif(not _qdrant_available(), reason="Qdrant not running")
def test_qdrant_integration_upsert_search_delete():
    from mrag.db.qdrant import make_client

    client = make_client()
    col = f"mrag_test_integration_{uuid.uuid4().hex[:8]}"
    dim = 4

    try:
        ensure_collection(client, col, dim)

        pts = [
            {"id": str(uuid.uuid4()), "vector": [1.0, 0.0, 0.0, 0.0], "payload": {"chunk_id": "c1"}},
            {"id": str(uuid.uuid4()), "vector": [0.0, 1.0, 0.0, 0.0], "payload": {"chunk_id": "c2"}},
        ]
        upsert_points(client, col, pts)

        results = search(client, col, vector=[1.0, 0.0, 0.0, 0.0], top_k=2)
        assert len(results) > 0
        assert results[0]["score"] > 0.9

        delete_points(client, col, [pts[0]["id"]])
        results_after = search(client, col, vector=[1.0, 0.0, 0.0, 0.0], top_k=2)
        deleted_ids = {r["id"] for r in results_after}
        assert pts[0]["id"] not in deleted_ids
    finally:
        client.delete_collection(col)
