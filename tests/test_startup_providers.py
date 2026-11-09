"""`mrag serve` reuses its startup providers only while the profile still says the same.

The server builds the startup profile's embedding provider and reranker once
and reads the profile from disk on every request. Before 1.7.1 an edit took
effect for the strategy and top_k but not for the providers: a profile pointed
at another endpoint, or with reranking turned off, was still served by the
startup ones, on the native route and on the Dify route alike.
"""
from unittest.mock import MagicMock, patch

import yaml

from mrag.core.embedding.ollama import OllamaEmbeddingProvider
from tests.test_reranking import _make_api_client, indexed_project  # noqa: F401  (fixture)


def _edit_profile(project_dir, edit) -> None:
    path = project_dir / "profiles" / "default.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _set(section: str, field: str, value):
    def edit(data):
        data[section][field] = value
    return edit


def _native_vector(client):
    return client.post("/api/v1/retrieve", json={"query": "Hello", "strategy": "vector", "top_k": 3})


def _native_keyword(client):
    return client.post("/api/v1/retrieve", json={"query": "Hello", "strategy": "keyword", "top_k": 3})


def _dify(client, knowledge_id):
    return client.post(
        "/retrieval",
        json={"knowledge_id": knowledge_id, "query": "Hello",
              "retrieval_setting": {"top_k": 3, "score_threshold": 0.0}},
    )


class _Capture:
    """Stands in for the vector and hybrid searches and keeps the provider each got."""

    def __init__(self):
        self.providers = []

    def __call__(self, *args, **kwargs):
        self.providers.append(kwargs["embedding_provider"])
        return []


def _searches(capture):
    return (patch("mrag.core.retrieval.runner.vector_search", side_effect=capture),
            patch("mrag.core.retrieval.runner.hybrid_search", side_effect=capture))


# ---------------------------------------------------------------------------
# Embedding provider
# ---------------------------------------------------------------------------

def test_an_unedited_profile_is_served_by_the_startup_embedding_provider(indexed_project):
    capture = _Capture()
    vector, hybrid = _searches(capture)
    client = _make_api_client(indexed_project)
    with client, vector, hybrid:
        assert _native_vector(client).status_code == 200
        assert _dify(client, indexed_project.config.knowledge_id).status_code == 200
    assert capture.providers == [indexed_project.provider, indexed_project.provider]


def test_an_edited_endpoint_is_served_by_a_provider_for_that_endpoint(indexed_project):
    capture = _Capture()
    vector, hybrid = _searches(capture)
    client = _make_api_client(indexed_project)
    with client, vector, hybrid:
        _edit_profile(indexed_project.project_dir, _set("embedding", "endpoint", "http://127.0.0.1:9"))
        assert _native_vector(client).status_code == 200
        assert _dify(client, indexed_project.config.knowledge_id).status_code == 200
    assert len(capture.providers) == 2
    for provider in capture.providers:
        assert provider is not indexed_project.provider
        assert isinstance(provider, OllamaEmbeddingProvider)
        assert provider.endpoint == "http://127.0.0.1:9"


def test_an_edited_model_is_not_served_by_the_startup_provider(indexed_project):
    capture = _Capture()
    vector, hybrid = _searches(capture)
    client = _make_api_client(indexed_project)
    with client, vector, hybrid:
        _edit_profile(indexed_project.project_dir, _set("embedding", "model", "bge-m3"))
        _native_vector(client)
    [provider] = capture.providers
    assert provider is not indexed_project.provider
    assert provider.model == "bge-m3"


def test_a_search_only_edit_keeps_the_startup_provider(indexed_project):
    """top_k and strategy are read per request anyway; they build nothing."""
    capture = _Capture()
    vector, hybrid = _searches(capture)
    client = _make_api_client(indexed_project)
    with client, vector, hybrid:
        _edit_profile(indexed_project.project_dir, _set("retrieval", "top_k", 5))
        _native_vector(client)
    assert capture.providers == [indexed_project.provider]


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------

def _reranking_project(indexed_project):
    """The served profile with reranking on and keyword search, as serve would load it."""
    _edit_profile(indexed_project.project_dir, _set("rerank", "enabled", True))
    _edit_profile(indexed_project.project_dir, _set("retrieval", "strategy", "keyword"))
    startup = MagicMock()
    startup.rerank.side_effect = lambda query, results: results
    return startup


def test_turning_reranking_off_stops_the_startup_reranker(indexed_project):
    startup = _reranking_project(indexed_project)
    client = _make_api_client(indexed_project, reranker=startup)
    with client:
        assert _native_keyword(client).json()["reranked"] is True
        _dify(client, indexed_project.config.knowledge_id)
        assert startup.rerank.call_count == 2
        _edit_profile(indexed_project.project_dir, _set("rerank", "enabled", False))
        with patch("mrag.core.reranking.get_reranker") as load:
            assert _native_keyword(client).json()["reranked"] is False
            assert _dify(client, indexed_project.config.knowledge_id).status_code == 200
        load.assert_not_called()
    assert startup.rerank.call_count == 2


def test_an_edited_rerank_model_is_served_by_that_model(indexed_project):
    startup = _reranking_project(indexed_project)
    edited = MagicMock()
    edited.rerank.side_effect = lambda query, results: results
    client = _make_api_client(indexed_project, reranker=startup)
    with client:
        _edit_profile(indexed_project.project_dir, _set("rerank", "model", "another/cross-encoder"))
        with patch("mrag.core.reranking.get_reranker", return_value=edited) as load:
            assert _native_keyword(client).json()["reranked"] is True
            _dify(client, indexed_project.config.knowledge_id)
    startup.rerank.assert_not_called()
    assert edited.rerank.call_count == 2
    assert {call.args[0].model for call in load.call_args_list} == {"another/cross-encoder"}


def test_the_dify_route_honours_no_rerank(indexed_project):
    _reranking_project(indexed_project)
    from fastapi.testclient import TestClient

    from mrag.api.app import create_app

    client = TestClient(create_app(
        project_dir=indexed_project.project_dir,
        profile_name="default",
        config=indexed_project.config,
        _embedding_provider=indexed_project.provider,
        _qdrant_client=indexed_project.qdrant,
        reranker=None,
        reranking_allowed=False,
    ))
    with client, patch("mrag.core.reranking.get_reranker") as load:
        assert _dify(client, indexed_project.config.knowledge_id).status_code == 200
        assert _native_keyword(client).json()["reranked"] is False
    load.assert_not_called()
