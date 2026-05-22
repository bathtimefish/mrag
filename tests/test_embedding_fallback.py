"""Unit tests for mrag.core.indexing.embedding_fallback.

Covers DESIGN_V21_EMBEDDING_FALLBACK.md §7.1:
  - all-succeed path
  - single failure (first / last / middle position)
  - multiple scattered failures
  - all-fail edge case
  - singleton recovery on full-retry escalation
  - bisection request-count efficiency
  - error message truncation
  - fail_document mode (no bisection)
"""
from __future__ import annotations

import pytest

from mrag.core.embedding.base import BaseEmbeddingProvider
from mrag.core.indexing.embedding_fallback import embed_with_fallback


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class _FakeProvider(BaseEmbeddingProvider):
    """Embedding provider stub that fails when any input contains a marker.

    Records every call as (batch_size, max_attempts) so tests can assert the
    bisection / retry sequence.
    """

    def __init__(
        self,
        fail_markers: tuple[str, ...] = ("trigger_nan",),
        max_attempts: int = 3,
        dim: int = 8,
    ) -> None:
        self.fail_markers = fail_markers
        self.max_attempts = max_attempts
        self.dim = dim
        self.call_log: list[tuple[int, int]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_log.append((len(texts), self.max_attempts))
        if any(any(m in t for m in self.fail_markers) for t in texts):
            raise RuntimeError(
                f"simulated provider failure after {self.max_attempts} "
                f"attempts on batch of {len(texts)}"
            )
        return [[0.1] * self.dim for _ in texts]

    def get_dimension(self) -> int:
        return self.dim

    def get_model_id(self) -> str:
        return f"fake:test:{self.dim}:v1"

    def get_normalized_name(self) -> str:
        return "fake_test"


class _FlakyProvider(_FakeProvider):
    """Embedding provider stub modeling transient failures.

    Rules:
      - Multi-input batch containing a flaky marker → always fail (forces bisection).
      - Singleton with flaky marker + max_attempts == 1 → fail (fast retry not enough).
      - Singleton with flaky marker + max_attempts >= 2 → succeed (full retry recovers).
      - No flaky marker → succeed.

    Models a real-world transient failure where the underlying input is fine
    but the provider needs retry budget to overcome network/server hiccups.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_log.append((len(texts), self.max_attempts))
        has_flaky = any(any(m in t for m in self.fail_markers) for t in texts)

        if not has_flaky:
            return [[0.1] * self.dim for _ in texts]

        # Multi-input batch with flaky marker → always fail
        if len(texts) > 1:
            raise RuntimeError(
                f"multi-input batch with flaky marker (size={len(texts)})"
            )

        # Singleton: succeed only if we have retry budget
        if self.max_attempts >= 2:
            return [[0.1] * self.dim for _ in texts]
        raise RuntimeError(
            f"singleton flaky chunk failed at max_attempts={self.max_attempts}"
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_all_succeed_returns_vectors_no_failures():
    provider = _FakeProvider()
    texts = ["alpha", "beta", "gamma"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert len(vectors) == 3
    assert all(v is not None and len(v) == 8 for v in vectors)
    assert failures == {}
    # Single call covers everything
    assert provider.call_log == [(3, 3)]


def test_empty_input_short_circuits():
    provider = _FakeProvider()

    vectors, failures = embed_with_fallback([], provider)

    assert vectors == []
    assert failures == {}
    assert provider.call_log == []


# ---------------------------------------------------------------------------
# Single failure isolation
# ---------------------------------------------------------------------------


def test_single_fail_first_position_isolated():
    provider = _FakeProvider()
    texts = ["trigger_nan_alpha", "beta", "gamma", "delta"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert vectors[0] is None
    assert vectors[1] is not None
    assert vectors[2] is not None
    assert vectors[3] is not None
    assert 0 in failures
    assert "simulated provider failure" in failures[0]


def test_single_fail_last_position_isolated():
    provider = _FakeProvider()
    texts = ["alpha", "beta", "gamma", "trigger_nan_delta"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert vectors[0] is not None
    assert vectors[1] is not None
    assert vectors[2] is not None
    assert vectors[3] is None
    assert 3 in failures


def test_single_fail_middle_position_isolated():
    provider = _FakeProvider()
    texts = ["alpha", "trigger_nan_beta", "gamma", "delta"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert vectors[0] is not None
    assert vectors[1] is None
    assert vectors[2] is not None
    assert vectors[3] is not None
    assert 1 in failures


def test_single_text_input_failure_no_bisection():
    """Single-text input that fails initial full-retry attempt is marked immediately."""
    provider = _FakeProvider()
    texts = ["trigger_nan_only"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert vectors == [None]
    assert 0 in failures
    assert "simulated provider failure" in failures[0]
    # Single full-retry attempt, no bisection
    assert provider.call_log == [(1, 3)]


# ---------------------------------------------------------------------------
# Multiple scattered failures
# ---------------------------------------------------------------------------


def test_multiple_scattered_failures_all_isolated():
    provider = _FakeProvider()
    texts = [
        "alpha",
        "trigger_nan_beta",
        "gamma",
        "delta",
        "trigger_nan_epsilon",
        "zeta",
        "eta",
        "trigger_nan_theta",
    ]

    vectors, failures = embed_with_fallback(texts, provider)

    expected_failed = {1, 4, 7}
    actual_failed = {i for i, v in enumerate(vectors) if v is None}
    assert actual_failed == expected_failed
    assert set(failures.keys()) == expected_failed
    # Successful chunks have valid vectors
    for i, v in enumerate(vectors):
        if i not in expected_failed:
            assert v is not None and len(v) == 8


def test_all_fail_extreme_case():
    provider = _FakeProvider()
    texts = ["trigger_nan_1", "trigger_nan_2", "trigger_nan_3", "trigger_nan_4"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert all(v is None for v in vectors)
    assert set(failures.keys()) == {0, 1, 2, 3}


# ---------------------------------------------------------------------------
# 2-stage retry escalation
# ---------------------------------------------------------------------------


def test_singleton_recovers_on_full_retry():
    """A chunk that fails when isolated with max_attempts=1 should succeed
    when escalated to full retry (simulates a transient failure)."""
    provider = _FlakyProvider(fail_markers=("flaky",))
    texts = ["alpha", "flaky_chunk", "gamma"]

    vectors, failures = embed_with_fallback(texts, provider)

    # All chunks succeed because the flaky one recovers on full retry
    assert all(v is not None for v in vectors)
    assert failures == {}

    # Verify 2-stage behavior: the flaky chunk was attempted at least once
    # with max_attempts=1 (during bisection) before escalating
    attempts_for_singletons = [
        max_attempts for size, max_attempts in provider.call_log if size == 1
    ]
    assert 1 in attempts_for_singletons  # fast retry happened
    assert 3 in attempts_for_singletons  # full retry happened


def test_bisection_uses_fast_retry():
    """During bisection, multi-input sub-batches must use max_attempts=1."""
    provider = _FakeProvider()
    texts = ["alpha", "trigger_nan_beta", "gamma", "delta"]

    embed_with_fallback(texts, provider)

    # The very first call uses full retry (original max_attempts=3)
    assert provider.call_log[0] == (4, 3)
    # All subsequent multi-input calls (size > 1) use max_attempts=1
    for size, max_attempts in provider.call_log[1:]:
        if size > 1:
            assert max_attempts == 1, (
                f"Bisection sub-batch of size {size} used max_attempts={max_attempts}, "
                f"expected 1"
            )


def test_max_attempts_restored_after_fallback():
    """The provider's max_attempts must be restored to its original value
    after embed_with_fallback returns (even on partial failure)."""
    provider = _FakeProvider(max_attempts=5)
    original = provider.max_attempts

    embed_with_fallback(
        ["alpha", "trigger_nan_beta", "gamma"], provider
    )

    assert provider.max_attempts == original


def test_max_attempts_restored_after_all_success():
    provider = _FakeProvider(max_attempts=5)
    original = provider.max_attempts

    embed_with_fallback(["alpha", "beta"], provider)

    assert provider.max_attempts == original


# ---------------------------------------------------------------------------
# fail_document mode (v0.20.0 compatibility)
# ---------------------------------------------------------------------------


def test_fail_document_mode_reraises():
    provider = _FakeProvider()
    texts = ["alpha", "trigger_nan_beta", "gamma"]

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        embed_with_fallback(texts, provider, mode="fail_document")

    # Only the initial full-retry call happened; no bisection
    assert provider.call_log == [(3, 3)]


def test_fail_document_mode_succeeds_when_no_failures():
    provider = _FakeProvider()
    texts = ["alpha", "beta"]

    vectors, failures = embed_with_fallback(texts, provider, mode="fail_document")

    assert all(v is not None for v in vectors)
    assert failures == {}


# ---------------------------------------------------------------------------
# Request count efficiency (DESIGN §3.2 cost estimate)
# ---------------------------------------------------------------------------


def test_request_count_bounded_for_single_failure_in_large_batch():
    """For a 32-chunk batch with 1 bad chunk, total calls should be modest
    (DESIGN §3.2 estimates ~11 requests: 1 initial + ~8 bisection + 2 retries)."""
    provider = _FakeProvider()
    texts = [f"text_{i}" for i in range(32)]
    texts[15] = "trigger_nan_middle"

    embed_with_fallback(texts, provider)

    # log_2(32) ≈ 5 levels of bisection × ~1 call per level + initial + singleton retries
    # Conservative upper bound: 15 calls
    assert len(provider.call_log) <= 15, (
        f"Too many calls during bisection: {len(provider.call_log)}\n"
        f"call_log: {provider.call_log}"
    )


# ---------------------------------------------------------------------------
# Error message handling
# ---------------------------------------------------------------------------


def test_error_message_truncation():
    """Errors longer than 500 chars are truncated with '...' suffix in failures dict."""

    class _LongErrorProvider(_FakeProvider):
        def embed(self, texts):
            self.call_log.append((len(texts), self.max_attempts))
            if any("fail" in t for t in texts):
                raise RuntimeError("X" * 1000)
            return [[0.1] * self.dim for _ in texts]

    provider = _LongErrorProvider()
    texts = ["good", "fail_me", "also_good"]

    _, failures = embed_with_fallback(texts, provider)

    assert 1 in failures
    msg = failures[1]
    assert len(msg) <= 503  # 500 chars + "..."
    assert msg.endswith("...")


def test_short_error_message_not_truncated():
    provider = _FakeProvider()
    _, failures = embed_with_fallback(["trigger_nan_only"], provider)

    assert 0 in failures
    assert not failures[0].endswith("...")


# ---------------------------------------------------------------------------
# Provider without max_attempts attribute (graceful degradation)
# ---------------------------------------------------------------------------


class _AttempsLessProvider(BaseEmbeddingProvider):
    """Provider that does not expose max_attempts. Bisection should still work
    but without the 2-stage retry optimization."""

    def __init__(self, fail_markers=("trigger_nan",), dim=8):
        self.fail_markers = fail_markers
        self.dim = dim
        self.call_log = []

    def embed(self, texts):
        self.call_log.append(len(texts))
        if any(any(m in t for m in self.fail_markers) for t in texts):
            raise RuntimeError(f"failure on batch of {len(texts)}")
        return [[0.1] * self.dim for _ in texts]

    def get_dimension(self):
        return self.dim

    def get_model_id(self):
        return "attemptless:test:8:v1"

    def get_normalized_name(self):
        return "attemptless_test"


def test_provider_without_max_attempts_still_isolates():
    provider = _AttempsLessProvider()
    texts = ["alpha", "trigger_nan_beta", "gamma", "delta"]

    vectors, failures = embed_with_fallback(texts, provider)

    assert vectors[0] is not None
    assert vectors[1] is None
    assert vectors[2] is not None
    assert vectors[3] is not None
    assert 1 in failures


# ---------------------------------------------------------------------------
# Profile config: EmbeddingFailurePolicyConfig
# ---------------------------------------------------------------------------


class TestEmbeddingFailurePolicyConfig:
    def test_default_mode_is_fallback_no_vector(self):
        from mrag.config.profile import EmbeddingConfig

        config = EmbeddingConfig()
        assert config.failure_policy.mode == "fallback_no_vector"

    def test_explicit_fail_document(self):
        from mrag.config.profile import EmbeddingConfig, EmbeddingFailurePolicyConfig

        config = EmbeddingConfig(
            failure_policy=EmbeddingFailurePolicyConfig(mode="fail_document")
        )
        assert config.failure_policy.mode == "fail_document"

    def test_failure_policy_excluded_from_hash(self):
        """Changing embedding.failure_policy.mode must not change profile_hash."""
        from mrag.config.profile import (
            EmbeddingConfig,
            EmbeddingFailurePolicyConfig,
            ProfileConfig,
        )

        p1 = ProfileConfig(
            name="test",
            embedding=EmbeddingConfig(
                failure_policy=EmbeddingFailurePolicyConfig(mode="fallback_no_vector"),
            ),
        )
        p2 = ProfileConfig(
            name="test",
            embedding=EmbeddingConfig(
                failure_policy=EmbeddingFailurePolicyConfig(mode="fail_document"),
            ),
        )
        assert p1.compute_hash() == p2.compute_hash()

    def test_yaml_round_trip(self, tmp_path):
        """failure_policy can be set from YAML and loaded back."""
        import yaml as yaml_lib
        from mrag.config.profile import load_profile

        profile_dir = tmp_path / "profiles"
        profile_dir.mkdir()
        (profile_dir / "strict.yaml").write_text(
            yaml_lib.dump(
                {
                    "name": "strict",
                    "embedding": {
                        "failure_policy": {"mode": "fail_document"},
                    },
                }
            )
        )

        profile = load_profile("strict", project_dir=tmp_path)
        assert profile.embedding.failure_policy.mode == "fail_document"

    def test_yaml_omitted_uses_default(self, tmp_path):
        """When failure_policy is omitted in YAML, the default is used."""
        import yaml as yaml_lib
        from mrag.config.profile import load_profile

        profile_dir = tmp_path / "profiles"
        profile_dir.mkdir()
        (profile_dir / "min.yaml").write_text(
            yaml_lib.dump({"name": "min", "embedding": {"model": "bge-m3"}})
        )

        profile = load_profile("min", project_dir=tmp_path)
        assert profile.embedding.failure_policy.mode == "fallback_no_vector"


# ---------------------------------------------------------------------------
# Pipeline integration: end-to-end run_index with a failing provider
# ---------------------------------------------------------------------------


class _FailingPipelineProvider(BaseEmbeddingProvider):
    """Provider that fails when ANY input contains TRIGGER_FAIL.

    Mirrors test_indexing.FakeEmbeddingProvider but raises for the marker.
    Has `max_attempts` attribute so 2-stage retry works.
    """

    def __init__(self, dim: int = 4, max_attempts: int = 3) -> None:
        self._dim = dim
        self.max_attempts = max_attempts
        self._dimension = dim  # signal that dimension is known (skip warm-up)
        self._call_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._call_count += 1
        if any("TRIGGER_FAIL" in t for t in texts):
            raise RuntimeError(
                f"simulated embedding NaN failure (batch size {len(texts)})"
            )
        return [[0.1] * self._dim for _ in texts]

    def get_dimension(self) -> int:
        return self._dim

    def get_model_id(self) -> str:
        return f"fake:fail-pipeline:{self._dim}:v1"

    def get_normalized_name(self) -> str:
        return "fake_fail_pipeline"

    def ensure_model_registered(self, db_path) -> None:
        from datetime import datetime, timezone
        from mrag.db.connection import db_connection
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db_connection(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO embedding_models "
                "(id, provider, model_name, dimension, model_revision, normalized_name, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.get_model_id(), "fake", "fail-pipeline", self._dim, "v1", "fake_fail_pipeline", now),
            )


def _build_multi_chunk_text_with_trigger() -> str:
    """Build a text guaranteed to produce multiple chunks, with TRIGGER_FAIL
    in exactly one of them. Default chunk_size is 800 chars."""
    block_a = ("Alpha content. " * 60)            # ~960 chars → 2+ chunks
    block_trigger = "TRIGGER_FAIL marker chunk. " * 40   # ~1080 chars
    block_c = ("Gamma content. " * 60)            # ~900 chars
    return block_a + "\n\n" + block_trigger + "\n\n" + block_c


class TestPipelineEmbeddingFallback:
    """Integration: run_index with a provider that fails for one chunk."""

    def _setup(self, tmp_path, monkeypatch):
        """Init a project, add a multi-chunk doc with a TRIGGER_FAIL marker."""
        from typer.testing import CliRunner
        from mrag.cli import app
        from mrag.config.project import load_project_config
        from tests.test_indexing import _fake_qdrant_client

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["init", "--name", "fb-kb", "--non-interactive"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        project_dir = tmp_path / "fb-kb"

        doc_path = tmp_path / "trigger.txt"
        doc_path.write_text(_build_multi_chunk_text_with_trigger(), encoding="utf-8")
        monkeypatch.chdir(project_dir)
        add_result = runner.invoke(app, ["add", str(doc_path)], catch_exceptions=False)
        assert add_result.exit_code == 0, add_result.output

        config = load_project_config(project_dir)
        qdrant = _fake_qdrant_client()
        return project_dir, config, qdrant

    def test_fallback_document_still_indexed(self, tmp_path, monkeypatch):
        from mrag.core.indexing.pipeline import run_index
        project_dir, config, qdrant = self._setup(tmp_path, monkeypatch)

        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=_FailingPipelineProvider(),
            qdrant_client=qdrant,
        )

        assert result.indexed == 1
        assert result.errors == []
        assert result.embedding_fallback_chunks >= 1

    def test_fallback_chunk_has_null_qdrant_point_id(self, tmp_path, monkeypatch):
        from mrag.core.indexing.pipeline import run_index
        from mrag.db.connection import find_db, open_connection
        project_dir, config, qdrant = self._setup(tmp_path, monkeypatch)

        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=_FailingPipelineProvider(),
            qdrant_client=qdrant,
        )

        db = find_db(project_dir)
        conn = open_connection(db)
        # The chunk containing TRIGGER_FAIL must have NULL qdrant_point_id
        null_rows = conn.execute(
            """SELECT cv.qdrant_point_id, c.content
               FROM chunk_variants cv
               JOIN chunks c ON cv.chunk_id = c.id
               WHERE cv.qdrant_point_id IS NULL"""
        ).fetchall()
        all_rows = conn.execute(
            "SELECT qdrant_point_id FROM chunk_variants"
        ).fetchall()
        conn.close()

        assert len(null_rows) >= 1
        for row in null_rows:
            assert "TRIGGER_FAIL" in row["content"]
        # Other chunks should have non-NULL point_id
        non_null_count = sum(1 for r in all_rows if r["qdrant_point_id"] is not None)
        assert non_null_count >= 1

    def test_fallback_metadata_json_records_embedding_status(self, tmp_path, monkeypatch):
        from mrag.core.indexing.pipeline import run_index
        from mrag.db.connection import find_db, open_connection
        import json as _json
        project_dir, config, qdrant = self._setup(tmp_path, monkeypatch)

        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=_FailingPipelineProvider(),
            qdrant_client=qdrant,
        )

        db = find_db(project_dir)
        conn = open_connection(db)
        rows = conn.execute(
            "SELECT metadata_json FROM chunk_variants WHERE qdrant_point_id IS NULL"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1
        for row in rows:
            meta = _json.loads(row["metadata_json"])
            assert meta.get("embedding_status") == "fallback_no_vector"
            assert "embedding_error" in meta
            assert "simulated embedding NaN failure" in meta["embedding_error"]

    def test_fallback_chunks_searchable_via_fts(self, tmp_path, monkeypatch):
        """A fallback chunk has no Qdrant vector but is still in FTS5 → keyword
        search finds it."""
        from mrag.core.indexing.pipeline import run_index
        from mrag.db.connection import find_db
        import sqlite3
        project_dir, config, qdrant = self._setup(tmp_path, monkeypatch)

        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=_FailingPipelineProvider(),
            qdrant_client=qdrant,
        )

        db = find_db(project_dir)
        conn = sqlite3.connect(str(db))
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM fts_chunks WHERE content LIKE '%TRIGGER_FAIL%'"
        ).fetchone()[0]
        conn.close()
        # Fallback chunk must still be present in FTS5
        assert fts_count >= 1

    def test_fail_document_mode_raises(self, tmp_path, monkeypatch):
        """With failure_policy.mode=fail_document, indexing fails the document
        (v0.20.0 compatibility)."""
        import yaml as yaml_lib
        from mrag.core.indexing.pipeline import run_index
        project_dir, config, qdrant = self._setup(tmp_path, monkeypatch)

        # Override default profile to use fail_document mode
        profile_path = project_dir / "profiles" / "default.yaml"
        profile_data = yaml_lib.safe_load(profile_path.read_text())
        profile_data.setdefault("embedding", {})["failure_policy"] = {"mode": "fail_document"}
        profile_path.write_text(yaml_lib.dump(profile_data))

        result = run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=_FailingPipelineProvider(),
            qdrant_client=qdrant,
        )

        # Document is marked as failed, not indexed
        assert result.indexed == 0
        assert result.embedding_fallback_chunks == 0
        assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# inspect_queries: EmbeddingStatus aggregation + ChunkVariantInfo fields
# ---------------------------------------------------------------------------


class TestInspectEmbeddingFallback:
    """v0.21.0 inspect surface: embedding status counts + per-chunk fields."""

    def _index_with_fallback(self, tmp_path, monkeypatch):
        """Helper: run the pipeline with TRIGGER_FAIL marker → 1+ fallback chunk."""
        from typer.testing import CliRunner
        from mrag.cli import app
        from mrag.config.project import load_project_config
        from mrag.core.indexing.pipeline import run_index
        from mrag.db.connection import find_db
        from tests.test_indexing import _fake_qdrant_client

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        init_result = runner.invoke(
            app, ["init", "--name", "insp-kb", "--non-interactive"], catch_exceptions=False
        )
        assert init_result.exit_code == 0, init_result.output
        project_dir = tmp_path / "insp-kb"

        doc_path = tmp_path / "doc.txt"
        doc_path.write_text(_build_multi_chunk_text_with_trigger(), encoding="utf-8")
        monkeypatch.chdir(project_dir)
        add_result = runner.invoke(app, ["add", str(doc_path)], catch_exceptions=False)
        assert add_result.exit_code == 0, add_result.output

        config = load_project_config(project_dir)
        qdrant = _fake_qdrant_client()
        run_index(
            project_dir=project_dir,
            config=config,
            profile_name="default",
            embedding_provider=_FailingPipelineProvider(),
            qdrant_client=qdrant,
        )
        # Extract document_id from `mrag add` output
        doc_id = None
        for line in add_result.output.splitlines():
            if "document_id:" in line:
                doc_id = line.split("document_id:")[-1].strip()
                break
        assert doc_id is not None, f"document_id not in: {add_result.output}"
        return project_dir, find_db(project_dir), doc_id

    def test_document_summary_counts_embedding_fallback(self, tmp_path, monkeypatch):
        from mrag.db.connection import open_connection
        from mrag.db.inspect_queries import fetch_document_summary
        project_dir, db_path, doc_id = self._index_with_fallback(tmp_path, monkeypatch)

        conn = open_connection(db_path)
        try:
            summary = fetch_document_summary(conn, doc_id, profile_name="default")
        finally:
            conn.close()
        assert summary is not None
        default_profile = summary.profiles[0]
        # At least one fallback chunk, plus at least one successful chunk
        assert default_profile.embedding.fallback_no_vector >= 1
        assert default_profile.embedding.embedded >= 1

    def test_chunks_query_exposes_embedding_status(self, tmp_path, monkeypatch):
        from mrag.db.connection import open_connection
        from mrag.db.inspect_queries import fetch_chunks
        project_dir, db_path, doc_id = self._index_with_fallback(tmp_path, monkeypatch)

        conn = open_connection(db_path)
        try:
            rows = fetch_chunks(
                conn, doc_id, "default", limit=None, offset=0,
                include_content=True, include_context=False,
            )
        finally:
            conn.close()

        # Find at least one chunk where TRIGGER_FAIL is present → embedding fallback
        fallback_rows = [r for r in rows if "TRIGGER_FAIL" in (r.content or "")]
        assert len(fallback_rows) >= 1
        for r in fallback_rows:
            assert r.variant.embedding_status == "fallback_no_vector"
            assert r.variant.has_qdrant_point is False
            assert r.variant.embedding_error is not None
        # Successful chunks should have no embedding_status
        success_rows = [r for r in rows if "TRIGGER_FAIL" not in (r.content or "")]
        for r in success_rows:
            assert r.variant.embedding_status is None
            assert r.variant.has_qdrant_point is True

    def test_inspect_document_json_includes_embedding_block(self, tmp_path, monkeypatch):
        """`mrag inspect document --json` payload exposes the embedding counts."""
        from typer.testing import CliRunner
        from mrag.cli import app
        import json as _json
        runner = CliRunner()
        project_dir, db_path, doc_id = self._index_with_fallback(tmp_path, monkeypatch)
        monkeypatch.chdir(project_dir)

        result = runner.invoke(
            app, ["inspect", "document", doc_id, "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.stdout)
        assert payload["profiles"][0]["embedding"]["fallback_no_vector"] >= 1
        assert payload["profiles"][0]["embedding"]["embedded"] >= 1

    def test_inspect_document_human_output_shows_status_section(
        self, tmp_path, monkeypatch
    ):
        """Human-readable output renders an Embedding Status section when fallback > 0."""
        from typer.testing import CliRunner
        from mrag.cli import app
        runner = CliRunner()
        project_dir, db_path, doc_id = self._index_with_fallback(tmp_path, monkeypatch)
        monkeypatch.chdir(project_dir)

        result = runner.invoke(
            app, ["inspect", "document", doc_id], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert "Embedding Status" in result.stdout
        assert "fallback_no_vector" in result.stdout
