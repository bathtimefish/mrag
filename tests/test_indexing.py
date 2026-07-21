"""Tests for Phase 5: diff, FTS5, indexing pipeline, index/reindex commands."""
import sqlite3
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.indexing.diff import IndexDecision, plan_indexing
from mrag.core.indexing.pipeline import IndexResult, cleanup_profile_index, run_index
from mrag.db import fts as fts_db
from mrag.db.connection import find_db, open_connection
from mrag.db.migrate import apply_schema

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_FAKE_DIM = 4


class FakeEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic stub: returns [index * 0.1] * DIM vectors."""

    def __init__(self, dim: int = _FAKE_DIM) -> None:
        self._dim = dim
        self._call_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for i, _ in enumerate(texts):
            self._call_count += 1
            result.append([self._call_count * 0.1] * self._dim)
        return result

    def get_dimension(self) -> int:
        return self._dim

    def get_model_id(self) -> str:
        return f"fake:test-model:{self._dim}:v1"

    def get_normalized_name(self) -> str:
        return "test_model"

    def ensure_model_registered(self, db_path: Path) -> None:
        from datetime import datetime, timezone
        from mrag.db.connection import db_connection
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db_connection(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO embedding_models "
                "(id, provider, model_name, dimension, model_revision, normalized_name, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.get_model_id(), "fake", "test-model", self._dim, "v1", "test_model", now),
            )


def _make_db(path: Path) -> Path:
    db = path / "mrag.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    apply_schema(conn)
    conn.commit()
    conn.close()
    return db


def _fake_qdrant_client() -> MagicMock:
    client = MagicMock()
    client.get_collections.return_value.collections = []
    return client


def _init_project(parent_dir: Path, name: str = "test-kb") -> Path:
    """Run mrag init and return the project subdirectory."""
    result = runner.invoke(app, ["init", "--name", name, "--non-interactive"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return parent_dir / name


def _add_document(project_dir: Path, file_path: Path) -> str:
    result = runner.invoke(app, ["add", str(file_path)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    for line in result.output.splitlines():
        if "document_id:" in line:
            return line.split("document_id:")[-1].strip()
    raise AssertionError(f"document_id not found in:\n{result.output}")


def _get_doc(db_path: Path, doc_id: str) -> dict:
    conn = open_connection(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    return dict(row)


# ---------------------------------------------------------------------------
# 5-1: plan_indexing (diff)
# ---------------------------------------------------------------------------

def test_plan_indexing_new_document(tmp_path: Path):
    db = _make_db(tmp_path)
    doc = {"id": "doc1", "file_hash": "fh1", "extracted_hash": "eh1"}
    decisions = plan_indexing([doc], "default", "ph1", db)
    assert len(decisions) == 1
    assert decisions[0].needs_index is True
    assert decisions[0].reason == "new"


def test_plan_indexing_skip_unchanged(tmp_path: Path):
    db = _make_db(tmp_path)

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
        "source_type, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("doc1", "kb_test", "f.txt", "p/f.txt", "fh1", "txt", "extracted",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO document_indexes (id, knowledge_id, document_id, profile_name, "
        "document_file_hash, extracted_hash, profile_hash, status) VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kb_test", "doc1", "default", "fh1", "eh1", "ph1", "indexed"),
    )
    conn.commit()
    conn.close()

    doc = {"id": "doc1", "file_hash": "fh1", "extracted_hash": "eh1"}
    decisions = plan_indexing([doc], "default", "ph1", db)
    assert decisions[0].needs_index is False
    assert decisions[0].reason == "skip"


def test_plan_indexing_file_changed(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
        "source_type, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("doc1", "kb_test", "f.txt", "p/f.txt", "fh_NEW", "txt", "extracted",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO document_indexes (id, knowledge_id, document_id, profile_name, "
        "document_file_hash, extracted_hash, profile_hash, status) VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kb_test", "doc1", "default", "fh_OLD", "eh1", "ph1", "indexed"),
    )
    conn.commit()
    conn.close()

    doc = {"id": "doc1", "file_hash": "fh_NEW", "extracted_hash": "eh1"}
    decisions = plan_indexing([doc], "default", "ph1", db)
    assert decisions[0].needs_index is True
    assert decisions[0].reason == "file_changed"


def test_plan_indexing_profile_changed(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
        "source_type, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("doc1", "kb_test", "f.txt", "p/f.txt", "fh1", "txt", "extracted",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO document_indexes (id, knowledge_id, document_id, profile_name, "
        "document_file_hash, extracted_hash, profile_hash, status) VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kb_test", "doc1", "default", "fh1", "eh1", "ph_OLD", "indexed"),
    )
    conn.commit()
    conn.close()

    doc = {"id": "doc1", "file_hash": "fh1", "extracted_hash": "eh1"}
    decisions = plan_indexing([doc], "default", "ph_NEW", db)
    assert decisions[0].needs_index is True
    assert decisions[0].reason == "profile_changed"


def test_plan_indexing_error_retry(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
        "source_type, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("doc1", "kb_test", "f.txt", "p/f.txt", "fh1", "txt", "extracted",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO document_indexes (id, knowledge_id, document_id, profile_name, "
        "document_file_hash, extracted_hash, profile_hash, status) VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kb_test", "doc1", "default", "fh1", "eh1", "ph1", "error"),
    )
    conn.commit()
    conn.close()

    doc = {"id": "doc1", "file_hash": "fh1", "extracted_hash": "eh1"}
    decisions = plan_indexing([doc], "default", "ph1", db)
    assert decisions[0].needs_index is True
    assert decisions[0].reason == "error_retry"


# ---------------------------------------------------------------------------
# 5-2: FTS5 operations
# ---------------------------------------------------------------------------

def test_fts_insert_and_match(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    fts_db.insert_chunk(conn, "Hello micro RAG world", "kb1", "default", "c1", "d1")
    conn.commit()

    rows = conn.execute(
        "SELECT chunk_id FROM fts_chunks WHERE fts_chunks MATCH ? "
        "AND knowledge_id=? AND profile_name=?",
        ("micro", "kb1", "default"),
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "c1"


def test_fts_no_match(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    fts_db.insert_chunk(conn, "Hello micro RAG world", "kb1", "default", "c1", "d1")
    conn.commit()

    rows = conn.execute(
        "SELECT chunk_id FROM fts_chunks WHERE fts_chunks MATCH ? "
        "AND knowledge_id=? AND profile_name=?",
        ("nonexistent", "kb1", "default"),
    ).fetchall()
    conn.close()
    assert rows == []


def test_fts_delete_by_document(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    fts_db.insert_chunk(conn, "chunk one", "kb1", "default", "c1", "d1")
    fts_db.insert_chunk(conn, "chunk two", "kb1", "default", "c2", "d2")
    conn.commit()

    fts_db.delete_by_document(conn, "kb1", "default", "d1")
    conn.commit()

    rows = conn.execute("SELECT chunk_id FROM fts_chunks WHERE knowledge_id='kb1'").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["c2"]


def test_fts_delete_by_profile(tmp_path: Path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    fts_db.insert_chunk(conn, "chunk A", "kb1", "profile-a", "c1", "d1")
    fts_db.insert_chunk(conn, "chunk B", "kb1", "profile-b", "c2", "d1")
    conn.commit()

    fts_db.delete_by_profile(conn, "kb1", "profile-a")
    conn.commit()

    rows = conn.execute("SELECT chunk_id FROM fts_chunks WHERE knowledge_id='kb1'").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["c2"]


# ---------------------------------------------------------------------------
# 5-3: Indexing pipeline — full flow
# ---------------------------------------------------------------------------

def _run_pipeline(tmp_path: Path, sample_txt: Path) -> tuple[IndexResult, Path]:
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        from mrag.config.project import load_project_config
        config = load_project_config(project_dir)
        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=provider,
            qdrant_client=qdrant,
        )
        db_path = find_db(project_dir)
        return result, db_path


def test_pipeline_indexes_document(tmp_path: Path, sample_txt: Path):
    result, db = _run_pipeline(tmp_path, sample_txt)
    assert result.indexed == 1
    assert result.skipped == 0
    assert result.errors == []


def test_pipeline_creates_chunks(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    conn = open_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    assert count >= 1


def test_pipeline_creates_chunk_variants(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    conn = open_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM chunk_variants").fetchone()[0]
    variant_type = conn.execute("SELECT DISTINCT variant_type FROM chunk_variants").fetchone()[0]
    conn.close()
    assert count >= 1
    assert variant_type == "raw"


def test_pipeline_sets_indexed_status(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    conn = open_connection(db)
    row = conn.execute("SELECT status FROM document_indexes").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "indexed"


def test_pipeline_inserts_fts_rows(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()[0]
    conn.close()
    assert count >= 1


def test_pipeline_registers_embedding_model(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    conn = open_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM embedding_models").fetchone()[0]
    conn.close()
    assert count == 1


def test_pipeline_records_qdrant_point_ids(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    conn = open_connection(db)
    rows = conn.execute(
        "SELECT qdrant_point_id, qdrant_collection FROM chunk_variants"
    ).fetchall()
    conn.close()
    assert all(r[0] is not None for r in rows)
    assert all(r[1] is not None for r in rows)


def test_pipeline_idempotent_skips_second_run(tmp_path: Path, sample_txt: Path):
    """Second mrag index on unchanged doc/profile must skip."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        from mrag.config.project import load_project_config
        config = load_project_config(project_dir)
        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        run_index(project_dir=project_dir, config=config, profile_name="default",
                  embedding_provider=provider, qdrant_client=qdrant)
        result2 = run_index(project_dir=project_dir, config=config, profile_name="default",
                            embedding_provider=provider, qdrant_client=qdrant)

    assert result2.indexed == 0
    assert result2.skipped == 1
    assert result2.errors == []


def test_query_time_profile_change_does_not_reindex(tmp_path: Path, sample_txt: Path):
    """Candidate and fusion tuning must reuse the existing index artifacts."""
    import yaml

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        from mrag.config.project import load_project_config

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

        profile_path = project_dir / "profiles" / "default.yaml"
        profile_data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile_data["retrieval"].update(
            {
                "top_k": 12,
                "dense_top_k": 60,
                "keyword_top_k": 70,
                "fusion": "weighted",
                "weights": [0.8, 0.2],
            }
        )
        profile_path.write_text(
            yaml.safe_dump(profile_data, sort_keys=False),
            encoding="utf-8",
        )

        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=provider,
            qdrant_client=qdrant,
        )

    assert result.indexed == 0
    assert result.skipped == 1


def test_pipeline_rejects_project_profile_tokenizer_mismatch(
    tmp_path: Path,
    sample_txt: Path,
):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        from mrag.config.project import load_project_config

        config = load_project_config(project_dir)
        config.fts_tokenizer = (
            "trigram" if config.fts_tokenizer == "vaporetto" else "vaporetto"
        )

        with pytest.raises(ValueError, match="Tokenizer mismatch"):
            run_index(
                project_dir=project_dir,
                config=config,
                profile_name="default",
                embedding_provider=FakeEmbeddingProvider(),
                qdrant_client=_fake_qdrant_client(),
            )


def test_pipeline_no_documents_returns_empty(tmp_path: Path):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)

        from mrag.config.project import load_project_config
        config = load_project_config(project_dir)
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=FakeEmbeddingProvider(),
            qdrant_client=_fake_qdrant_client(),
        )

    assert result.indexed == 0
    assert result.skipped == 0


def test_pipeline_empty_document_completes_without_error(tmp_path: Path):
    """An empty document must not raise TypeError or be marked as errored.

    Regression test for the latent bug where _index_document returned None
    (implicit return) on empty chunks, causing `result.raw_fallback_chunks +=
    None` to raise TypeError, which was then caught and reported as a
    document-level error with a confusing message.
    """
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("", encoding="utf-8")

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, empty_file)

        from mrag.config.project import load_project_config
        config = load_project_config(project_dir)
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=FakeEmbeddingProvider(),
            qdrant_client=_fake_qdrant_client(),
        )

    # Empty document should be processed cleanly:
    # - indexed=1 (the document was processed, just with zero chunks)
    # - errors=[] (no TypeError leaked through)
    # - raw_fallback_chunks=0 (no fallback occurred)
    assert result.errors == [], f"unexpected errors: {result.errors}"
    assert result.indexed == 1
    assert result.raw_fallback_chunks == 0


def test_cleanup_profile_index_removes_data(tmp_path: Path, sample_txt: Path):
    _, db = _run_pipeline(tmp_path, sample_txt)
    project_dir = tmp_path / "test-kb"

    from mrag.config.project import load_project_config
    config = load_project_config(project_dir)
    cleanup_profile_index(
        project_dir=project_dir,
        config=config,
        profile_name="default",
        qdrant_client=_fake_qdrant_client(),
    )

    conn = open_connection(db)
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    variants = conn.execute("SELECT COUNT(*) FROM chunk_variants").fetchone()[0]
    indexes = conn.execute("SELECT COUNT(*) FROM document_indexes").fetchone()[0]
    fts_rows = conn.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()[0]
    conn.close()

    assert chunks == 0
    assert variants == 0
    assert indexes == 0
    assert fts_rows == 0


def test_cleanup_rejects_tokenizer_mismatch_before_deleting(
    tmp_path: Path,
    sample_txt: Path,
):
    _, db = _run_pipeline(tmp_path, sample_txt)
    project_dir = tmp_path / "test-kb"

    from mrag.config.project import load_project_config

    config = load_project_config(project_dir)
    config.fts_tokenizer = (
        "trigram" if config.fts_tokenizer == "vaporetto" else "vaporetto"
    )
    conn = open_connection(db)
    before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()

    with pytest.raises(ValueError, match="Tokenizer mismatch"):
        cleanup_profile_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
        )

    conn = open_connection(db)
    after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    assert after == before


# ---------------------------------------------------------------------------
# 5-4: mrag index CLI command
# ---------------------------------------------------------------------------

def test_index_command_success(tmp_path: Path, sample_txt: Path):
    from unittest.mock import patch

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        with patch("mrag.core.indexing.pipeline.OllamaEmbeddingProvider", return_value=provider), \
             patch("mrag.core.indexing.pipeline.make_client", return_value=qdrant):
            result = runner.invoke(app, ["index"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "Indexed: 1" in result.output


def test_index_command_second_run_skips(tmp_path: Path, sample_txt: Path):
    from unittest.mock import patch

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        with patch("mrag.core.indexing.pipeline.OllamaEmbeddingProvider", return_value=provider), \
             patch("mrag.core.indexing.pipeline.make_client", return_value=qdrant):
            runner.invoke(app, ["index"], catch_exceptions=False)
            result2 = runner.invoke(app, ["index"], catch_exceptions=False)

    assert result2.exit_code == 0
    assert "Up-to-date: 1" in result2.output


def test_index_command_without_init_exits_nonzero(tmp_path: Path, sample_txt: Path):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        result = runner.invoke(app, ["index"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 5-5: mrag reindex CLI command
# ---------------------------------------------------------------------------

def test_reindex_command_recreates_chunks(tmp_path: Path, sample_txt: Path):
    from unittest.mock import patch

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        project_dir = _init_project(tmp_path)
        mp.chdir(project_dir)
        _add_document(project_dir, sample_txt)

        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        with patch("mrag.core.indexing.pipeline.OllamaEmbeddingProvider", return_value=provider), \
             patch("mrag.core.indexing.pipeline.make_client", return_value=qdrant):
            runner.invoke(app, ["index"], catch_exceptions=False)

            db = find_db(project_dir)
            conn = open_connection(db)
            old_chunk_ids = {r[0] for r in conn.execute("SELECT id FROM chunks").fetchall()}
            conn.close()

            result = runner.invoke(app, ["reindex"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "Indexed: 1" in result.output

    conn = open_connection(db)
    new_chunk_ids = {r[0] for r in conn.execute("SELECT id FROM chunks").fetchall()}
    conn.close()
    assert old_chunk_ids.isdisjoint(new_chunk_ids)


# ---------------------------------------------------------------------------
# profile_hash idempotency (Phase 10)
# ---------------------------------------------------------------------------

def test_profile_hash_is_deterministic():
    """Same config always produces the same hash."""
    from mrag.config.profile import ProfileConfig
    prof = ProfileConfig(name="test")
    h1 = prof.compute_hash()
    h2 = prof.compute_hash()
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex


def test_profile_hash_differs_on_chunk_size_change():
    from mrag.config.profile import ProfileConfig
    p1 = ProfileConfig(name="a")
    p2 = ProfileConfig(name="b")
    p2.chunking.chunk_size = p1.chunking.chunk_size + 100
    assert p1.compute_hash() != p2.compute_hash()


def test_profile_hash_differs_on_embedding_model_change():
    from mrag.config.profile import ProfileConfig
    p1 = ProfileConfig(name="a")
    p2 = ProfileConfig(name="b")
    p2.embedding.model = "other-model"
    assert p1.compute_hash() != p2.compute_hash()


def test_profile_hash_unaffected_by_name():
    """Profile name is not part of the hash (name is cosmetic)."""
    from mrag.config.profile import ProfileConfig
    p1 = ProfileConfig(name="alpha")
    p2 = ProfileConfig(name="beta")
    assert p1.compute_hash() == p2.compute_hash()


def test_profile_hash_differs_on_augmentation_strategy_change():
    from mrag.config.profile import ProfileConfig
    p1 = ProfileConfig(name="a")
    p2 = ProfileConfig(name="b")
    p2.augmentation.strategy = "contextual"
    assert p1.compute_hash() != p2.compute_hash()


def test_profile_hash_unaffected_by_rerank_change():
    """rerank is excluded from the hash because it doesn't affect index data."""
    from mrag.config.profile import ProfileConfig
    p1 = ProfileConfig(name="a")
    p2 = ProfileConfig(name="b")
    p2.rerank.enabled = True
    p2.rerank.top_n = 999
    assert p1.compute_hash() == p2.compute_hash()


# ---------------------------------------------------------------------------
# Augmentation: contextual strategy (unit tests with mocked LLM)
# ---------------------------------------------------------------------------

def test_augmentation_none_produces_raw_variants(tmp_path: Path, sample_txt: Path):
    """strategy: none → variant_type='raw', context_text=NULL."""
    from unittest.mock import patch
    from mrag.config.project import ProjectConfig, QdrantConfig
    from mrag.config.profile import ProfileConfig

    db_path = _make_db(tmp_path)
    project_dir = tmp_path

    (project_dir / "profiles").mkdir()
    profile = ProfileConfig(name="default")
    import yaml
    (project_dir / "profiles" / "default.yaml").write_text(
        yaml.dump(profile.model_dump()), encoding="utf-8"
    )
    (project_dir / "mrag.yaml").write_text(
        "project:\n  name: t\nknowledge_base:\n  id: kb_t\n  name: T\n"
        "default_profile: default\nqdrant:\n  mode: local\n",
        encoding="utf-8",
    )

    from mrag.config.project import load_project_config
    from mrag.db.connection import db_connection, open_connection
    import uuid as _uuid

    doc_id = str(_uuid.uuid4())
    doc_dir = project_dir / "data" / "documents" / doc_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "extracted.txt").write_text(sample_txt.read_text(), encoding="utf-8")
    now = "2026-01-01T00:00:00+00:00"
    with db_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
            "extracted_text_path, extracted_markdown_path, extracted_hash, source_type, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, "kb_t", "sample.txt",
             f"data/documents/{doc_id}/original.txt",
             "fh1",
             f"data/documents/{doc_id}/extracted.txt",
             f"data/documents/{doc_id}/extracted.txt",
             "eh1", "txt", "extracted", now, now),
        )

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

    conn = open_connection(db_path)
    rows = conn.execute("SELECT variant_type, context_text FROM chunk_variants").fetchall()
    conn.close()
    assert rows, "expected at least one chunk_variant"
    for vtype, ctx in rows:
        assert vtype == "raw"
        assert ctx is None


def test_augmentation_contextual_strategy_calls_llm(tmp_path: Path, sample_txt: Path):
    """strategy: contextual → LLM called per chunk, context_text stored, variant_type='contextual'."""
    from unittest.mock import patch, MagicMock
    from mrag.config.project import load_project_config
    from mrag.config.profile import ProfileConfig, AugmentationConfig
    from mrag.db.connection import db_connection, open_connection
    import yaml
    import uuid as _uuid

    db_path = _make_db(tmp_path)
    project_dir = tmp_path

    (project_dir / "profiles").mkdir()
    profile_data = ProfileConfig(name="default").model_dump()
    profile_data["augmentation"] = {"strategy": "contextual", "provider": "ollama",
                                     "model": "gemma4:e2b", "endpoint": "http://localhost:11434"}
    (project_dir / "profiles" / "default.yaml").write_text(
        yaml.dump(profile_data), encoding="utf-8"
    )
    (project_dir / "mrag.yaml").write_text(
        "project:\n  name: t\nknowledge_base:\n  id: kb_t\n  name: T\n"
        "default_profile: default\nqdrant:\n  mode: local\n",
        encoding="utf-8",
    )

    doc_id = str(_uuid.uuid4())
    doc_dir = project_dir / "data" / "documents" / doc_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "extracted.txt").write_text(sample_txt.read_text(), encoding="utf-8")
    now = "2026-01-01T00:00:00+00:00"
    with db_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
            "extracted_text_path, extracted_markdown_path, extracted_hash, source_type, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, "kb_t", "sample.txt",
             f"data/documents/{doc_id}/original.txt",
             "fh1",
             f"data/documents/{doc_id}/extracted.txt",
             f"data/documents/{doc_id}/extracted.txt",
             "eh1", "txt", "extracted", now, now),
        )

    config = load_project_config(project_dir)
    provider = FakeEmbeddingProvider()
    qdrant = _fake_qdrant_client()

    fake_context = "This chunk is about testing augmentation."

    with patch("mrag.core.indexing.augmentation.generate_context", return_value=fake_context) as mock_gen:
        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=provider,
            qdrant_client=qdrant,
        )

    conn = open_connection(db_path)
    rows = conn.execute("SELECT variant_type, context_text, content_for_embedding FROM chunk_variants").fetchall()
    conn.close()

    assert rows, "expected at least one chunk_variant"
    assert mock_gen.call_count == len(rows)
    for vtype, ctx, cfe in rows:
        assert vtype == "contextual"
        assert ctx == fake_context
        assert cfe.startswith(fake_context + "\n\n")

    # Contextual BM25: FTS5 must contain the contextualized text, not the raw chunk.
    # The LLM-generated context phrase should be matchable via FTS5 MATCH.
    from mrag.db.connection import open_fts_connection
    fts_conn = open_fts_connection(db_path, "trigram")
    fts_rows = fts_conn.execute(
        "SELECT content FROM fts_chunks WHERE knowledge_id=?", ("kb_t",)
    ).fetchall()
    fts_conn.close()
    assert fts_rows, "expected FTS rows for indexed chunks"
    for (fts_content,) in fts_rows:
        # Each FTS row's stored content should begin with the LLM context (contextualized BM25).
        assert fts_content.startswith(fake_context), (
            "FTS5 must store contextualized content (Anthropic Contextual BM25 pattern); "
            f"got: {fts_content[:80]!r}"
        )


def test_augmentation_contextual_uses_project_prompt_file(tmp_path: Path, sample_txt: Path):
    """When profiles/context_prompt.txt exists, its content is used as the prompt template."""
    from unittest.mock import patch
    from mrag.config.project import load_project_config
    from mrag.config.profile import ProfileConfig, load_profile
    from mrag.db.connection import db_connection
    import yaml
    import uuid as _uuid

    db_path = _make_db(tmp_path)
    project_dir = tmp_path

    (project_dir / "profiles").mkdir()
    profile_data = ProfileConfig(name="default").model_dump()
    profile_data["augmentation"] = {"strategy": "contextual", "provider": "ollama",
                                     "model": "gemma4:e2b", "endpoint": "http://localhost:11434"}
    (project_dir / "profiles" / "default.yaml").write_text(yaml.dump(profile_data), encoding="utf-8")

    custom_template = "Custom prompt: doc={document} chunk={chunk}"
    (project_dir / "profiles" / "context_prompt.txt").write_text(custom_template, encoding="utf-8")

    (project_dir / "mrag.yaml").write_text(
        "project:\n  name: t\nknowledge_base:\n  id: kb_t\n  name: T\n"
        "default_profile: default\nqdrant:\n  mode: local\n",
        encoding="utf-8",
    )

    doc_id = str(_uuid.uuid4())
    doc_dir = project_dir / "data" / "documents" / doc_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "extracted.txt").write_text(sample_txt.read_text(), encoding="utf-8")
    now = "2026-01-01T00:00:00+00:00"
    with db_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, knowledge_id, filename, original_path, file_hash, "
            "extracted_text_path, extracted_markdown_path, extracted_hash, source_type, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, "kb_t", "sample.txt",
             f"data/documents/{doc_id}/original.txt",
             "fh1",
             f"data/documents/{doc_id}/extracted.txt",
             f"data/documents/{doc_id}/extracted.txt",
             "eh1", "txt", "extracted", now, now),
        )

    config = load_project_config(project_dir)

    captured_templates: list[str | None] = []

    def fake_generate(chunk_content, full_text, cfg, prompt_template=None, on_retry=None):
        captured_templates.append(prompt_template)
        return "context"

    with patch("mrag.core.indexing.augmentation.generate_context", side_effect=fake_generate), \
         patch("mrag.core.ollama_client.probe_connection"):
        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=FakeEmbeddingProvider(),
            qdrant_client=_fake_qdrant_client(),
        )

    assert captured_templates, "generate_context was not called"
    assert all(t == custom_template for t in captured_templates)

    profile = load_profile("default", project_dir)
    conn = open_connection(db_path)
    first_hash = conn.execute(
        "SELECT profile_hash FROM profiles WHERE name='default'"
    ).fetchone()[0]
    conn.close()
    assert first_hash == profile.compute_hash(
        context_prompt=custom_template,
        effective_tokenizer=config.fts_tokenizer,
    )

    changed_template = "Changed prompt: doc={document} chunk={chunk}"
    (project_dir / "profiles" / "context_prompt.txt").write_text(
        changed_template,
        encoding="utf-8",
    )
    captured_templates.clear()
    with patch("mrag.core.indexing.augmentation.generate_context", side_effect=fake_generate), \
         patch("mrag.core.ollama_client.probe_connection"):
        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=FakeEmbeddingProvider(),
            qdrant_client=_fake_qdrant_client(),
        )

    assert result.indexed == 1
    assert captured_templates
    assert all(t == changed_template for t in captured_templates)
    conn = open_connection(db_path)
    changed_hash = conn.execute(
        "SELECT profile_hash FROM profiles WHERE name='default'"
    ).fetchone()[0]
    conn.close()
    assert changed_hash != first_hash


def test_init_creates_context_prompt_file(tmp_path: Path):
    """mrag init must create profiles/context_prompt.txt with the default template."""
    from mrag.core.indexing.context_prompt_template import DEFAULT_CONTEXT_PROMPT_TEMPLATE

    with pytest.MonkeyPatch().context() as mp:
        mp.chdir(tmp_path)
        result = runner.invoke(app, ["init", "--name", "myproj", "--non-interactive"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    prompt_file = tmp_path / "myproj" / "profiles" / "context_prompt.txt"
    assert prompt_file.exists(), "profiles/context_prompt.txt not created by mrag init"
    content = prompt_file.read_text(encoding="utf-8")
    assert content == DEFAULT_CONTEXT_PROMPT_TEMPLATE
    assert "{document}" in content
    assert "{chunk}" in content


# ---------------------------------------------------------------------------
# Parent-child indexing (V03)
# ---------------------------------------------------------------------------

def _run_pc_pipeline(tmp_path: Path, text: str) -> tuple[Path, Path]:
    """Init project with parent_child profile, add a doc, run index. Returns (project_dir, db_path)."""
    import yaml
    from mrag.config.profile import ProfileConfig

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        result = runner.invoke(app, ["init", "--name", "pc-kb", "--non-interactive"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        project_dir = tmp_path / "pc-kb"
        mp.chdir(project_dir)

        from mrag.config.project import load_project_config

        config = load_project_config(project_dir)

        # Write a parent_child profile
        profile_data = ProfileConfig(name="pc").model_dump()
        profile_data["chunking"]["strategy"] = "parent_child"
        profile_data["chunking"]["parent"] = {"strategy": "fixed_size", "max_chars": 200}
        profile_data["chunking"]["child"] = {"strategy": "recursive", "chunk_size": 60, "overlap": 10}
        profile_data["retrieval"]["strategy"] = "parent_child"
        profile_data["keyword"]["tokenizer"] = config.fts_tokenizer
        (project_dir / "profiles" / "pc.yaml").write_text(yaml.dump(profile_data), encoding="utf-8")

        doc_file = tmp_path / "pc_doc.txt"
        doc_file.write_text(text, encoding="utf-8")
        _add_document(project_dir, doc_file)

        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="pc",
            embedding_provider=provider,
            qdrant_client=qdrant,
        )

        db_path = find_db(project_dir)
        return project_dir, db_path


def test_pc_pipeline_creates_parent_and_child_chunks(tmp_path: Path):
    text = "word " * 200  # ~1000 chars → multiple parents
    _, db = _run_pc_pipeline(tmp_path, text)
    conn = open_connection(db)
    types = {r[0] for r in conn.execute("SELECT DISTINCT chunk_type FROM chunks").fetchall()}
    conn.close()
    assert "parent" in types
    assert "child" in types


def test_pc_pipeline_parent_chunk_id_resolved(tmp_path: Path):
    """Every child must have parent_chunk_id pointing to a real parent row."""
    text = "word " * 200
    _, db = _run_pc_pipeline(tmp_path, text)
    conn = open_connection(db)
    children = conn.execute(
        "SELECT parent_chunk_id FROM chunks WHERE chunk_type='child'"
    ).fetchall()
    parent_ids = {r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE chunk_type='parent'"
    ).fetchall()}
    conn.close()
    assert children, "expected at least one child chunk"
    for row in children:
        assert row[0] in parent_ids, f"dangling parent_chunk_id: {row[0]}"


def test_pc_pipeline_parents_not_in_fts(tmp_path: Path):
    """Parent chunks must NOT be inserted into fts_chunks."""
    import sqlite3 as _sqlite3
    text = "word " * 200
    _, db = _run_pc_pipeline(tmp_path, text)

    conn_raw = _sqlite3.connect(str(db))
    fts_ids = {r[0] for r in conn_raw.execute("SELECT chunk_id FROM fts_chunks").fetchall()}
    conn_raw.close()

    conn = open_connection(db)
    parent_ids = {r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE chunk_type='parent'"
    ).fetchall()}
    conn.close()

    overlap = fts_ids & parent_ids
    assert not overlap, f"parent chunk(s) found in fts_chunks: {overlap}"


def test_pc_pipeline_parents_not_in_chunk_variants(tmp_path: Path):
    """Parent chunks must NOT have chunk_variants rows."""
    text = "word " * 200
    _, db = _run_pc_pipeline(tmp_path, text)
    conn = open_connection(db)
    parent_ids = {r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE chunk_type='parent'"
    ).fetchall()}
    variant_chunk_ids = {r[0] for r in conn.execute(
        "SELECT chunk_id FROM chunk_variants"
    ).fetchall()}
    conn.close()
    overlap = parent_ids & variant_chunk_ids
    assert not overlap, f"parent chunk(s) found in chunk_variants: {overlap}"


def test_pc_pipeline_hint_not_in_parent_metadata(tmp_path: Path):
    """_parent_id_hint must be stripped from parent chunk metadata after indexing."""
    import json
    text = "word " * 200
    _, db = _run_pc_pipeline(tmp_path, text)
    conn = open_connection(db)
    parents = conn.execute(
        "SELECT metadata_json FROM chunks WHERE chunk_type='parent'"
    ).fetchall()
    conn.close()
    for row in parents:
        if row[0]:
            meta = json.loads(row[0])
            assert "_parent_id_hint" not in meta, "hint leaked into stored metadata"


def test_pc_pipeline_indexed_count_excludes_parents(tmp_path: Path):
    """IndexResult.indexed counts child chunks only, not parents."""
    import yaml
    from mrag.config.profile import ProfileConfig

    text = "word " * 200
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        runner.invoke(app, ["init", "--name", "pc-kb2", "--non-interactive"], catch_exceptions=False)
        project_dir = tmp_path / "pc-kb2"
        mp.chdir(project_dir)

        from mrag.config.project import load_project_config

        config = load_project_config(project_dir)

        profile_data = ProfileConfig(name="pc").model_dump()
        profile_data["chunking"]["strategy"] = "parent_child"
        profile_data["chunking"]["parent"] = {"strategy": "fixed_size", "max_chars": 200}
        profile_data["chunking"]["child"] = {"strategy": "recursive", "chunk_size": 60, "overlap": 10}
        profile_data["retrieval"]["strategy"] = "parent_child"
        profile_data["keyword"]["tokenizer"] = config.fts_tokenizer
        (project_dir / "profiles" / "pc.yaml").write_text(yaml.dump(profile_data), encoding="utf-8")

        doc_file = tmp_path / "pc_doc2.txt"
        doc_file.write_text(text, encoding="utf-8")
        _add_document(project_dir, doc_file)

        provider = FakeEmbeddingProvider()
        qdrant = _fake_qdrant_client()

        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="pc",
            embedding_provider=provider,
            qdrant_client=qdrant,
        )
        db_path = find_db(project_dir)

    conn = open_connection(db_path)
    child_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE chunk_type='child'"
    ).fetchone()[0]
    conn.close()

    assert result.indexed == 1  # 1 document indexed
    assert child_count >= 1
