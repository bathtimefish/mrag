"""Tests for Phase 6: vector, keyword, fusion, hybrid retrieval, mrag search command."""
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.core.retrieval.base import RetrievalResult, fetch_chunks
from mrag.core.retrieval.fusion import rrf_fusion, weighted_fusion
from mrag.core.retrieval.hybrid import hybrid_search
from mrag.core.retrieval.keyword import keyword_search
from mrag.core.retrieval.parent_child import resolve_to_parent
from mrag.core.retrieval.vector import vector_search
from mrag.db.connection import find_db, open_connection
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture: fully-indexed project
# ---------------------------------------------------------------------------

@pytest.fixture
def indexed_project(tmp_path: Path, sample_txt: Path, monkeypatch):
    """Init, add sample_txt, run index pipeline. Returns project info namespace."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "test-kb", "--non-interactive"], catch_exceptions=False)
    project_dir = tmp_path / "test-kb"
    monkeypatch.chdir(project_dir)
    runner.invoke(app, ["add", str(sample_txt)], catch_exceptions=False)

    from mrag.config.project import load_project_config
    from mrag.core.indexing.pipeline import run_index

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
        tmp_path=project_dir,
        db_path=db_path,
        config=config,
        provider=provider,
        qdrant=qdrant,
    )


# ---------------------------------------------------------------------------
# fetch_chunks helper
# ---------------------------------------------------------------------------

def test_fetch_chunks_empty(indexed_project):
    assert fetch_chunks(indexed_project.db_path, []) == {}


def test_fetch_chunks_returns_rows(indexed_project):
    conn = open_connection(indexed_project.db_path)
    row = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()
    conn.close()
    chunk_id = row["id"]

    result = fetch_chunks(indexed_project.db_path, [chunk_id])
    assert chunk_id in result
    assert "content" in result[chunk_id]


def test_fetch_chunks_unknown_id(indexed_project):
    result = fetch_chunks(indexed_project.db_path, ["nonexistent-id"])
    assert result == {}


# ---------------------------------------------------------------------------
# 6-2: Keyword Retrieval
# ---------------------------------------------------------------------------

def test_keyword_search_returns_results(indexed_project):
    results = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        top_k=5,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert len(results) >= 1
    assert all(isinstance(r, RetrievalResult) for r in results)
    assert all(r.score > 0 for r in results)


def test_keyword_search_content_contains_query(indexed_project):
    results = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert any("Hello" in r.content for r in results)


def test_keyword_search_no_match(indexed_project):
    results = keyword_search(
        query_text="xyznonexistentterm",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert results == []


def test_keyword_search_wrong_profile(indexed_project):
    """Searching with a non-existent profile must return empty."""
    results = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="nonexistent-profile",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert results == []


def test_keyword_search_result_has_document_id(indexed_project):
    results = keyword_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert results[0].document_id != ""


# ---------------------------------------------------------------------------
# 6-1: Vector Retrieval (mocked Qdrant)
# ---------------------------------------------------------------------------

def _qdrant_hit(chunk_id: str, document_id: str, score: float = 0.95) -> MagicMock:
    hit = MagicMock()
    hit.id = str(uuid.uuid4())
    hit.score = score
    hit.payload = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "profile_name": "default",
    }
    return hit


def test_vector_search_returns_results(indexed_project):
    conn = open_connection(indexed_project.db_path)
    row = conn.execute("SELECT id, document_id FROM chunks LIMIT 1").fetchone()
    conn.close()

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = [
        _qdrant_hit(row["id"], row["document_id"], score=0.92)
    ]

    results = vector_search(
        query_text="test query",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=indexed_project.provider,
        qdrant_client=mock_qdrant,
        col_name="mrag_test_col",
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].chunk_id == row["id"]
    assert results[0].score == pytest.approx(0.92)
    assert results[0].content != ""


def test_vector_search_empty_when_qdrant_returns_nothing(indexed_project):
    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = []

    results = vector_search(
        query_text="test",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=indexed_project.provider,
        qdrant_client=mock_qdrant,
        col_name="mrag_test_col",
        top_k=5,
    )
    assert results == []


def test_vector_search_unknown_chunk_id_filtered(indexed_project):
    """If Qdrant returns a chunk_id not in DB, it must be excluded from results."""
    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = [
        _qdrant_hit("unknown-chunk-id", "doc1", score=0.99)
    ]

    results = vector_search(
        query_text="test",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=indexed_project.provider,
        qdrant_client=mock_qdrant,
        col_name="mrag_test_col",
    )
    assert results == []


# ---------------------------------------------------------------------------
# 6-3: Fusion
# ---------------------------------------------------------------------------

def _r(chunk_id: str, score: float, content: str = "c") -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, document_id="d", content=content, score=score)


class TestRrfFusion:
    def test_empty_lists(self):
        assert rrf_fusion([]) == []
        assert rrf_fusion([[]]) == []

    def test_single_list(self):
        results = rrf_fusion([[_r("c1", 0.9), _r("c2", 0.5)]])
        # c1 is rank-0 → higher RRF score
        assert results[0].chunk_id == "c1"
        assert results[1].chunk_id == "c2"

    def test_higher_ranked_wins(self):
        list_a = [_r("c1", 0.9), _r("c2", 0.5)]
        list_b = [_r("c2", 0.8), _r("c3", 0.7)]
        fused = rrf_fusion([list_a, list_b])
        chunk_ids = [r.chunk_id for r in fused]
        # c2 appears in both lists → should score well
        assert "c2" in chunk_ids

    def test_chunk_in_both_lists_outranks_chunk_in_one(self):
        # c_both appears rank-0 in both lists; c_solo only in list_a at rank-0
        list_a = [_r("c_both", 0.9), _r("c_solo", 0.8)]
        list_b = [_r("c_both", 0.7), _r("c_other", 0.6)]
        fused = rrf_fusion([list_a, list_b])
        assert fused[0].chunk_id == "c_both"

    def test_rrf_scores_are_positive(self):
        fused = rrf_fusion([[_r("c1", 0.9)], [_r("c2", 0.5)]])
        assert all(r.score > 0 for r in fused)

    def test_rrf_preserves_content(self):
        fused = rrf_fusion([[_r("c1", 0.9, content="hello")]])
        assert fused[0].content == "hello"

    def test_rrf_deduplicates_chunk_ids(self):
        list_a = [_r("c1", 0.9)]
        list_b = [_r("c1", 0.8)]
        fused = rrf_fusion([list_a, list_b])
        ids = [r.chunk_id for r in fused]
        assert ids.count("c1") == 1


class TestWeightedFusion:
    def test_empty_lists(self):
        assert weighted_fusion([]) == []

    def test_single_list(self):
        results = weighted_fusion([[_r("c1", 0.9), _r("c2", 0.1)]])
        assert results[0].chunk_id == "c1"

    def test_equal_weights(self):
        list_a = [_r("c1", 1.0), _r("c2", 0.0)]
        list_b = [_r("c2", 1.0), _r("c1", 0.0)]
        fused = weighted_fusion([list_a, list_b], weights=[0.5, 0.5])
        # Both c1 and c2 share equal normalized total
        scores = {r.chunk_id: r.score for r in fused}
        assert abs(scores["c1"] - scores["c2"]) < 1e-9

    def test_weighted_respects_weights(self):
        # list_a heavily weighted; c1 is top in list_a
        list_a = [_r("c1", 1.0), _r("c2", 0.0)]
        list_b = [_r("c2", 1.0), _r("c1", 0.0)]
        fused = weighted_fusion([list_a, list_b], weights=[0.9, 0.1])
        assert fused[0].chunk_id == "c1"


# ---------------------------------------------------------------------------
# 6-4: Hybrid Retrieval
# ---------------------------------------------------------------------------

def test_hybrid_search_combines_results(indexed_project):
    conn = open_connection(indexed_project.db_path)
    row = conn.execute("SELECT id, document_id FROM chunks LIMIT 1").fetchone()
    conn.close()

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = [
        _qdrant_hit(row["id"], row["document_id"], score=0.9)
    ]

    results = hybrid_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=indexed_project.provider,
        qdrant_client=mock_qdrant,
        col_name="mrag_test_col",
        dense_top_k=10,
        keyword_top_k=10,
        top_k=5,
        fusion="rrf",
        tokenizer=indexed_project.config.fts_tokenizer,
    )

    assert len(results) >= 1
    assert all(r.score > 0 for r in results)


def test_hybrid_search_top_k_respected(indexed_project):
    conn = open_connection(indexed_project.db_path)
    rows = conn.execute("SELECT id, document_id FROM chunks").fetchall()
    conn.close()

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = [
        _qdrant_hit(r["id"], r["document_id"], score=0.9 - i * 0.01)
        for i, r in enumerate(rows)
    ]

    results = hybrid_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=indexed_project.provider,
        qdrant_client=mock_qdrant,
        col_name="mrag_test_col",
        top_k=1,
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert len(results) <= 1


def test_hybrid_weighted_fusion(indexed_project):
    conn = open_connection(indexed_project.db_path)
    row = conn.execute("SELECT id, document_id FROM chunks LIMIT 1").fetchone()
    conn.close()

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = [
        _qdrant_hit(row["id"], row["document_id"], score=0.9)
    ]

    results = hybrid_search(
        query_text="Hello",
        knowledge_id=indexed_project.config.knowledge_id,
        profile_name="default",
        db_path=indexed_project.db_path,
        embedding_provider=indexed_project.provider,
        qdrant_client=mock_qdrant,
        col_name="mrag_test_col",
        fusion="weighted",
        tokenizer=indexed_project.config.fts_tokenizer,
    )
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# 6-5: Parent-Child resolution
# ---------------------------------------------------------------------------

def test_resolve_to_parent_no_op_for_regular_chunks(indexed_project):
    """MVP: all chunks have parent_chunk_id=NULL → resolve_to_parent is a no-op."""
    conn = open_connection(indexed_project.db_path)
    rows = conn.execute("SELECT id, document_id, content FROM chunks").fetchall()
    conn.close()

    results = [
        RetrievalResult(chunk_id=r["id"], document_id=r["document_id"],
                        content=r["content"], score=0.9)
        for r in rows
    ]
    resolved = resolve_to_parent(results, indexed_project.db_path)
    assert len(resolved) == len(results)
    for orig, res in zip(results, resolved):
        assert res.chunk_id == orig.chunk_id
        assert res.content == orig.content


def test_resolve_to_parent_replaces_content(indexed_project):
    """Manually create a parent-child pair and verify resolution."""
    db_path = indexed_project.db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")

    # Get an existing document_id
    doc_id = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()[0]
    now = "2026-01-01T00:00:00+00:00"
    kb = indexed_project.config.knowledge_id

    parent_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())

    conn.execute(
        "INSERT INTO chunks (id, knowledge_id, document_id, profile_name, chunk_type, "
        "chunk_index, content, source_format, char_count, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (parent_id, kb, doc_id, "default", "parent", 0, "parent content", "text", 14, now, now),
    )
    conn.execute(
        "INSERT INTO chunks (id, knowledge_id, document_id, profile_name, parent_chunk_id, "
        "chunk_type, chunk_index, content, source_format, char_count, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (child_id, kb, doc_id, "default", parent_id, "child", 0, "child content", "text", 13, now, now),
    )
    conn.commit()
    conn.close()

    child_result = RetrievalResult(chunk_id=child_id, document_id=doc_id,
                                   content="child content", score=0.9)
    resolved = resolve_to_parent([child_result], db_path)

    assert len(resolved) == 1
    assert resolved[0].chunk_id == parent_id
    assert resolved[0].content == "parent content"


def test_resolve_to_parent_deduplicates(indexed_project):
    """Two children of the same parent should collapse into one result."""
    db_path = indexed_project.db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")

    doc_id = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()[0]
    now = "2026-01-01T00:00:00+00:00"
    kb = indexed_project.config.knowledge_id

    parent_id = str(uuid.uuid4())
    child1_id = str(uuid.uuid4())
    child2_id = str(uuid.uuid4())

    conn.execute(
        "INSERT INTO chunks (id, knowledge_id, document_id, profile_name, chunk_type, "
        "chunk_index, content, source_format, char_count, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (parent_id, kb, doc_id, "default", "parent", 0, "parent body", "text", 11, now, now),
    )
    for i, cid in enumerate([child1_id, child2_id]):
        conn.execute(
            "INSERT INTO chunks (id, knowledge_id, document_id, profile_name, parent_chunk_id, "
            "chunk_type, chunk_index, content, source_format, char_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, kb, doc_id, "default", parent_id, "child", i, f"child {i}", "text", 7, now, now),
        )
    conn.commit()
    conn.close()

    results = [
        RetrievalResult(chunk_id=child1_id, document_id=doc_id, content="child 0", score=0.95),
        RetrievalResult(chunk_id=child2_id, document_id=doc_id, content="child 1", score=0.90),
    ]
    resolved = resolve_to_parent(results, db_path)
    assert len(resolved) == 1
    assert resolved[0].chunk_id == parent_id


def test_resolve_to_parent_sets_retrieved_child_id(indexed_project):
    """resolved result must carry retrieved_child_id in metadata."""
    db_path = indexed_project.db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")

    doc_id = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()[0]
    now = "2026-01-01T00:00:00+00:00"
    kb = indexed_project.config.knowledge_id

    parent_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())

    conn.execute(
        "INSERT INTO chunks (id, knowledge_id, document_id, profile_name, chunk_type, "
        "chunk_index, content, source_format, char_count, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (parent_id, kb, doc_id, "default", "parent", 0, "parent body", "text", 11, now, now),
    )
    conn.execute(
        "INSERT INTO chunks (id, knowledge_id, document_id, profile_name, parent_chunk_id, "
        "chunk_type, chunk_index, content, source_format, char_count, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (child_id, kb, doc_id, "default", parent_id, "child", 1, "child body", "text", 10, now, now),
    )
    conn.commit()
    conn.close()

    results = [RetrievalResult(chunk_id=child_id, document_id=doc_id, content="child body", score=0.9)]
    resolved = resolve_to_parent(results, db_path)

    assert resolved[0].metadata.get("retrieved_child_id") == child_id
    assert resolved[0].metadata.get("retrieval_mode") == "parent_child"


def test_resolve_to_parent_empty_input(indexed_project):
    resolved = resolve_to_parent([], indexed_project.db_path)
    assert resolved == []


# ---------------------------------------------------------------------------
# 6-6: mrag search CLI
# ---------------------------------------------------------------------------

def test_search_command_keyword(indexed_project):
    result = runner.invoke(
        app, ["search", "Hello", "--strategy", "keyword", "--top-k", "5"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Should find something from sample_txt ("Hello, world!")
    assert result.output.strip() != ""
    assert "No results found" not in result.output


def test_search_command_keyword_no_results(indexed_project):
    result = runner.invoke(
        app, ["search", "xyznonexistentterm", "--strategy", "keyword"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "No results found" in result.output


def test_search_command_hybrid_with_mock(indexed_project, monkeypatch):
    """Hybrid search with mocked Ollama and Qdrant."""
    from unittest.mock import patch

    conn = open_connection(indexed_project.db_path)
    row = conn.execute("SELECT id, document_id FROM chunks LIMIT 1").fetchone()
    conn.close()

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value.points = [
        _qdrant_hit(row["id"], row["document_id"], score=0.88)
    ]

    fake_provider = FakeEmbeddingProvider()

    with patch("mrag.cli.search.OllamaEmbeddingProvider", return_value=fake_provider), \
         patch("mrag.cli.search.make_client", return_value=mock_qdrant):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "hybrid", "--top-k", "3"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "No results found" not in result.output


def test_search_command_without_init_exits_nonzero(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["search", "query"])
    assert result.exit_code != 0
