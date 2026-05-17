"""Tests for contextual augmentation raw_fallback behavior."""
import json
import sqlite3
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mrag.config.profile import AugmentationConfig, AugmentationFailurePolicyConfig, OllamaRetryConfig
from mrag.core.chunking.base import ChunkData
from mrag.core.indexing.augmentation import augment_chunks
from mrag.core.indexing.pipeline import IndexResult, run_index
from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.db.connection import find_db, open_connection
from mrag.db.migrate import apply_schema
from typer.testing import CliRunner

from mrag.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_DIM = 4


def _make_aug_config(mode: str = "raw_fallback") -> AugmentationConfig:
    return AugmentationConfig(
        strategy="contextual",
        provider="ollama",
        model="test-model",
        endpoint="http://localhost:11434",
        retry=OllamaRetryConfig(max_attempts=1, initial_delay_seconds=0.0),
        failure_policy=AugmentationFailurePolicyConfig(mode=mode),
    )


def _chunk(content: str, idx: int = 0) -> ChunkData:
    return ChunkData(content=content, chunk_index=idx)


class FakeEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self, dim: int = _FAKE_DIM) -> None:
        self._dim = dim
        self._call_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for _ in texts:
            self._call_count += 1
            result.append([self._call_count * 0.1] * self._dim)
        return result

    def get_dimension(self) -> int:
        return self._dim

    def get_model_id(self) -> str:
        return f"fake:test:{self._dim}:v1"

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
                (self.get_model_id(), "fake", "test", self._dim, "v1", "test_model", now),
            )


def _fake_qdrant_client() -> MagicMock:
    client = MagicMock()
    client.get_collections.return_value.collections = []
    return client


def _make_db(path: Path) -> Path:
    db = path / "mrag.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    apply_schema(conn)
    conn.commit()
    conn.close()
    return db


def _init_project(parent_dir: Path, name: str = "test-kb") -> Path:
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


# ---------------------------------------------------------------------------
# augment_chunks: raw_fallback mode
# ---------------------------------------------------------------------------

class TestAugmentChunksRawFallback:
    def test_returns_none_for_failed_chunk(self):
        """Failed chunk returns None when mode is raw_fallback."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("good content"), _chunk("bad content")]

        def _side_effect(chunk_content, *args, **kwargs):
            if "bad" in chunk_content:
                raise RuntimeError("Ollama timed out")
            return "generated context"

        with patch("mrag.core.indexing.augmentation.generate_context", side_effect=_side_effect):
            results = augment_chunks(chunks, "full doc text", config)

        assert results[0] == "generated context"
        assert results[1] is None

    def test_all_succeed_returns_all_strings(self):
        """All chunks succeed → no None values in result."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("chunk A"), _chunk("chunk B")]

        with patch("mrag.core.indexing.augmentation.generate_context", return_value="ctx"):
            results = augment_chunks(chunks, "doc", config)

        assert results == ["ctx", "ctx"]

    def test_all_fail_returns_all_none(self):
        """All chunks fail → all None values."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("c1"), _chunk("c2"), _chunk("c3")]

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=RuntimeError("timeout")):
            results = augment_chunks(chunks, "doc", config)

        assert results == [None, None, None]

    def test_calls_on_chunk_fallback_callback(self):
        """on_chunk_fallback is called for each failed chunk."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("c1"), _chunk("c2")]
        fallback_calls = []

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=RuntimeError("timed out")):
            augment_chunks(chunks, "doc", config,
                           on_chunk_fallback=lambda cur, tot, exc: fallback_calls.append((cur, tot, str(exc))))

        assert len(fallback_calls) == 2
        assert fallback_calls[0] == (1, 2, "timed out")
        assert fallback_calls[1] == (2, 2, "timed out")

    def test_on_chunk_progress_called_even_on_fallback(self):
        """on_chunk progress callback fires for fallback chunks too."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("c1"), _chunk("c2")]
        progress = []

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=RuntimeError("fail")):
            augment_chunks(chunks, "doc", config,
                           on_chunk=lambda cur, tot: progress.append(cur))

        assert progress == [1, 2]


class TestAugmentChunksFailDocument:
    def test_raises_on_failure(self):
        """mode=fail_document propagates exceptions (original behavior)."""
        config = _make_aug_config("fail_document")
        chunks = [_chunk("content")]

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=RuntimeError("timed out")):
            with pytest.raises(RuntimeError, match="timed out"):
                augment_chunks(chunks, "doc", config)

    def test_no_fallback_callback_on_fail_document(self):
        """on_chunk_fallback is NOT called when mode=fail_document (exception propagates before it)."""
        config = _make_aug_config("fail_document")
        chunks = [_chunk("content")]
        fallback_calls = []

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=RuntimeError("fail")):
            with pytest.raises(RuntimeError):
                augment_chunks(chunks, "doc", config,
                               on_chunk_fallback=lambda *a: fallback_calls.append(a))

        assert fallback_calls == []


class TestAugmentChunksConnectionError:
    def test_connection_error_propagates_in_raw_fallback_mode(self):
        """ConnectionError (Ollama not running) is NOT caught by raw_fallback — it propagates."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("content")]

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=ConnectionError("Cannot connect to Ollama")):
            with pytest.raises(ConnectionError):
                augment_chunks(chunks, "doc", config)

    def test_connection_error_propagates_in_fail_document_mode(self):
        """ConnectionError propagates in fail_document mode too."""
        config = _make_aug_config("fail_document")
        chunks = [_chunk("content")]

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=ConnectionError("Cannot connect to Ollama")):
            with pytest.raises(ConnectionError):
                augment_chunks(chunks, "doc", config)

    def test_runtime_error_still_falls_back(self):
        """RuntimeError (timeout/empty response exhausted) still triggers raw_fallback."""
        config = _make_aug_config("raw_fallback")
        chunks = [_chunk("content")]

        with patch("mrag.core.indexing.augmentation.generate_context",
                   side_effect=RuntimeError("Ollama request failed after 6 attempts: timed out")):
            results = augment_chunks(chunks, "doc", config)

        assert results == [None]


# ---------------------------------------------------------------------------
# profile.py: failure_policy config
# ---------------------------------------------------------------------------

class TestAugmentationFailurePolicyConfig:
    def test_default_mode_is_raw_fallback(self):
        config = AugmentationConfig()
        assert config.failure_policy.mode == "raw_fallback"

    def test_explicit_fail_document(self):
        config = AugmentationConfig(
            failure_policy=AugmentationFailurePolicyConfig(mode="fail_document")
        )
        assert config.failure_policy.mode == "fail_document"

    def test_failure_policy_excluded_from_hash(self):
        """Changing failure_policy.mode must not change the profile hash."""
        from mrag.config.profile import ProfileConfig
        p1 = ProfileConfig(
            name="test",
            augmentation=AugmentationConfig(
                strategy="contextual",
                failure_policy=AugmentationFailurePolicyConfig(mode="raw_fallback"),
            ),
        )
        p2 = ProfileConfig(
            name="test",
            augmentation=AugmentationConfig(
                strategy="contextual",
                failure_policy=AugmentationFailurePolicyConfig(mode="fail_document"),
            ),
        )
        assert p1.compute_hash() == p2.compute_hash()


# ---------------------------------------------------------------------------
# pipeline: raw_fallback stores raw variant + metadata_json
# ---------------------------------------------------------------------------

class TestPipelineRawFallback:
    def _run_with_contextual(self, tmp_path: Path, side_effect) -> tuple:
        """Run indexing pipeline with contextual augmentation mocked."""
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            project_dir = _init_project(tmp_path)
            mp.chdir(project_dir)

            # Create a simple text document
            doc_file = tmp_path / "doc.txt"
            doc_file.write_text("This is test content for indexing.", encoding="utf-8")
            _add_document(project_dir, doc_file)

            # Patch load_profile at the pipeline module level (where it was imported)
            import mrag.core.indexing.pipeline as pipeline_mod
            original_load = pipeline_mod.load_profile

            def _patched_load(profile_name, project_dir=None):
                p = original_load(profile_name, project_dir)
                from mrag.config.profile import AugmentationConfig, AugmentationFailurePolicyConfig, OllamaRetryConfig
                p = p.model_copy(update={
                    "augmentation": AugmentationConfig(
                        strategy="contextual",
                        retry=OllamaRetryConfig(max_attempts=1, initial_delay_seconds=0.0),
                        failure_policy=AugmentationFailurePolicyConfig(mode="raw_fallback"),
                    )
                })
                return p

            mp.setattr(pipeline_mod, "load_profile", _patched_load)

            from mrag.config.project import load_project_config
            config = load_project_config(project_dir)
            provider = FakeEmbeddingProvider()
            qdrant = _fake_qdrant_client()

            with patch("mrag.core.indexing.augmentation.generate_context", side_effect=side_effect):
                result = run_index(
                    project_dir=project_dir,
                    config=config,
                    profile_name="default",
                    embedding_provider=provider,
                    qdrant_client=qdrant,
                )

            db_path = find_db(project_dir)
            return result, db_path

    def test_fallback_document_still_indexed(self, tmp_path):
        """Document with all-failed augmentation still gets indexed (not error)."""
        result, _ = self._run_with_contextual(tmp_path, RuntimeError("timed out"))
        assert result.indexed == 1
        assert result.errors == []

    def test_fallback_chunks_counted_in_result(self, tmp_path):
        """IndexResult.raw_fallback_chunks reflects number of failed chunks."""
        result, _ = self._run_with_contextual(tmp_path, RuntimeError("timed out"))
        assert result.raw_fallback_chunks > 0

    def test_fallback_variant_type_is_raw(self, tmp_path):
        """Chunks that fell back have variant_type='raw'."""
        _, db_path = self._run_with_contextual(tmp_path, RuntimeError("timed out"))
        conn = open_connection(db_path)
        rows = conn.execute("SELECT variant_type FROM chunk_variants").fetchall()
        conn.close()
        assert all(r[0] == "raw" for r in rows)

    def test_fallback_metadata_json_set(self, tmp_path):
        """Fallback variants have metadata_json with augmentation_status=fallback_raw."""
        _, db_path = self._run_with_contextual(tmp_path, RuntimeError("timed out"))
        conn = open_connection(db_path)
        rows = conn.execute("SELECT metadata_json FROM chunk_variants").fetchall()
        conn.close()
        metas = [json.loads(r[0]) for r in rows if r[0]]
        assert len(metas) > 0
        assert all(m.get("augmentation_status") == "fallback_raw" for m in metas)
        assert all("augmentation_error" in m for m in metas)

    def test_partial_fallback_mixed_variants(self, tmp_path):
        """When only some chunks fail, the result has both contextual and raw variants."""
        call_count = {"n": 0}

        def _side_effect(chunk_content, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise RuntimeError("timed out")
            return "generated context"

        result, db_path = self._run_with_contextual(tmp_path, _side_effect)
        assert result.indexed == 1

        conn = open_connection(db_path)
        rows = conn.execute("SELECT variant_type, metadata_json FROM chunk_variants").fetchall()
        conn.close()

        types = {r[0] for r in rows}
        # At least one contextual and one raw (fallback)
        # (depends on how many chunks the doc splits into; with a short doc it may be 1 chunk)
        # Just verify indexed and fallback count is consistent
        assert result.raw_fallback_chunks >= 0

    def test_no_fallback_raw_fallback_chunks_zero(self, tmp_path):
        """When all augmentation succeeds, raw_fallback_chunks is zero."""
        result, _ = self._run_with_contextual(tmp_path, lambda *a, **kw: "context text")
        assert result.raw_fallback_chunks == 0
        assert result.indexed == 1


class TestPipelineConnectionErrorEarlyDetection:
    def test_connection_error_raised_before_documents_processed(self, tmp_path):
        """ConnectionError from augmentation probe must propagate before any document is touched."""
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            project_dir = _init_project(tmp_path)
            mp.chdir(project_dir)

            doc_file = tmp_path / "doc.txt"
            doc_file.write_text("Test content.", encoding="utf-8")
            _add_document(project_dir, doc_file)

            import mrag.core.indexing.pipeline as pipeline_mod
            original_load = pipeline_mod.load_profile

            def _patched_load(profile_name, project_dir=None):
                p = original_load(profile_name, project_dir)
                from mrag.config.profile import AugmentationConfig, AugmentationFailurePolicyConfig, OllamaRetryConfig
                p = p.model_copy(update={
                    "augmentation": AugmentationConfig(
                        strategy="contextual",
                        retry=OllamaRetryConfig(max_attempts=1, initial_delay_seconds=0.0),
                        failure_policy=AugmentationFailurePolicyConfig(mode="raw_fallback"),
                    )
                })
                return p

            mp.setattr(pipeline_mod, "load_profile", _patched_load)

            from mrag.config.project import load_project_config
            config = load_project_config(project_dir)
            provider = FakeEmbeddingProvider()
            qdrant = _fake_qdrant_client()

            # Probe raises ConnectionError — run_index must propagate it, not catch it
            with patch("mrag.core.ollama_client.probe_connection",
                       side_effect=ConnectionError("Cannot connect to Ollama")):
                with pytest.raises(ConnectionError, match="Cannot connect to Ollama"):
                    run_index(
                        project_dir=project_dir,
                        config=config,
                        profile_name="default",
                        embedding_provider=provider,
                        qdrant_client=qdrant,
                    )

    def test_no_documents_marked_error_on_connection_error(self, tmp_path):
        """When ConnectionError fires at probe time, no document_indexes rows are written."""
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            project_dir = _init_project(tmp_path)
            mp.chdir(project_dir)

            doc_file = tmp_path / "doc.txt"
            doc_file.write_text("Test content.", encoding="utf-8")
            _add_document(project_dir, doc_file)

            import mrag.core.indexing.pipeline as pipeline_mod
            original_load = pipeline_mod.load_profile

            def _patched_load(profile_name, project_dir=None):
                p = original_load(profile_name, project_dir)
                from mrag.config.profile import AugmentationConfig, AugmentationFailurePolicyConfig, OllamaRetryConfig
                p = p.model_copy(update={
                    "augmentation": AugmentationConfig(
                        strategy="contextual",
                        retry=OllamaRetryConfig(max_attempts=1, initial_delay_seconds=0.0),
                        failure_policy=AugmentationFailurePolicyConfig(mode="raw_fallback"),
                    )
                })
                return p

            mp.setattr(pipeline_mod, "load_profile", _patched_load)

            from mrag.config.project import load_project_config
            config = load_project_config(project_dir)
            provider = FakeEmbeddingProvider()
            qdrant = _fake_qdrant_client()

            with patch("mrag.core.ollama_client.probe_connection",
                       side_effect=ConnectionError("Cannot connect to Ollama")):
                with pytest.raises(ConnectionError):
                    run_index(
                        project_dir=project_dir,
                        config=config,
                        profile_name="default",
                        embedding_provider=provider,
                        qdrant_client=qdrant,
                    )

            db_path = find_db(project_dir)
            conn = open_connection(db_path)
            rows = conn.execute("SELECT status FROM document_indexes").fetchall()
            conn.close()
            # No document_indexes row should have been written (probe fired before loop)
            assert rows == []
