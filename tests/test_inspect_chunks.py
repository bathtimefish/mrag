"""Integration tests for `mrag inspect chunks` (Phase 2 — v0.18)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from tests.inspect_fixtures import (
    init_inspect_project,
    open_seed_conn,
    seed_basic_indexed_document,
    seed_chunk,
    seed_document,
    seed_profile,
    seed_variant,
)

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    project_dir, db_path = init_inspect_project(tmp_path, monkeypatch)
    return project_dir, db_path


# ---------------------------------------------------------------------------
# Default behavior: returns ALL chunks (agent-first)
# ---------------------------------------------------------------------------


class TestDefaultBehavior:
    def test_default_returns_all_chunks(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=5)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 5
        assert payload["returned"] == 5
        assert payload["limit"] is None
        assert payload["offset"] == 0
        assert len(payload["chunks"]) == 5

    def test_no_content_no_context_by_default(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=2, contextual=True)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        for entry in payload["chunks"]:
            assert "content" not in entry
            assert "context_text" not in entry


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


class TestPaging:
    def test_limit_only(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=10)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--limit", "3", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert payload["total"] == 10
        assert payload["returned"] == 3
        assert [c["chunk_index"] for c in payload["chunks"]] == [0, 1, 2]

    def test_offset_only(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=5)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--offset", "2", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert [c["chunk_index"] for c in payload["chunks"]] == [2, 3, 4]

    def test_limit_and_offset(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=10)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1",
                  "--limit", "2", "--offset", "5", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert [c["chunk_index"] for c in payload["chunks"]] == [5, 6]


# ---------------------------------------------------------------------------
# --show-content / --show-context
# ---------------------------------------------------------------------------


class TestShowFlags:
    def test_show_content_includes_full_body(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=2)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--show-content", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        for i, entry in enumerate(payload["chunks"]):
            assert "content" in entry
            assert entry["content"] == f"chunk {i} body text"

    def test_show_context_includes_context_text_for_contextual_variant(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=2, contextual=True)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--show-context", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        for i, entry in enumerate(payload["chunks"]):
            assert "context_text" in entry
            assert entry["context_text"] == f"context for chunk {i}"

    def test_show_context_null_for_raw_variant(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1, contextual=False)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--show-context", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert "context_text" in payload["chunks"][0]
        assert payload["chunks"][0]["context_text"] is None

    def test_show_content_and_context_combined(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1, contextual=True)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1",
                  "--show-content", "--show-context", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        entry = payload["chunks"][0]
        assert "content" in entry
        assert "context_text" in entry


# ---------------------------------------------------------------------------
# --profile resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    def test_single_profile_auto_select(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=2)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["profile"] == "default"

    def test_multiple_profiles_requires_flag(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            for prof in ["default", "alt"]:
                seed_profile(conn, prof)
                seed_chunk(conn, f"c-{prof}", profile_name=prof, chunk_index=0)
                seed_variant(conn, f"v-{prof}", chunk_id=f"c-{prof}", profile_name=prof)
        conn.close()
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "multiple profiles" in result.stderr
        # candidates are listed
        assert "default" in result.stderr
        assert "alt" in result.stderr

    def test_explicit_profile_used(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            for prof in ["default", "alt"]:
                seed_profile(conn, prof)
                seed_chunk(conn, f"c-{prof}", profile_name=prof, chunk_index=0)
        conn.close()
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--profile", "alt", "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["profile"] == "alt"
        assert payload["total"] == 1


# ---------------------------------------------------------------------------
# Metadata handling
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_metadata_parsed_as_dict(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=3)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        # heading_path is a list[str], contains_table is a bool
        first = payload["chunks"][0]
        assert isinstance(first["metadata"], dict)
        assert first["metadata"]["heading_path"] == ["Ch1", "Sec1"]

    def test_variant_info_in_payload(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        v = payload["chunks"][0]["variant"]
        assert v["type"] == "raw"
        assert v["qdrant_collection"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_document_not_found(self, project):
        result = runner.invoke(
            app, ["inspect", "chunks", "no-such-id"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_document_without_chunks(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
        conn.close()
        result = runner.invoke(
            app, ["inspect", "chunks", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "no indexed chunks" in result.stderr


# ---------------------------------------------------------------------------
# Human output sanity
# ---------------------------------------------------------------------------


class TestHumanOutput:
    def test_human_output_shows_total(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=3)
        result = runner.invoke(
            app, ["inspect", "chunks", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "total chunks: 3" in result.stdout
        assert "chunk_id=c0" in result.stdout
        assert "heading_path:" in result.stdout

    def test_human_output_null_token_count_as_dash(self, project):
        # Real-world: parent_child profile leaves token_count NULL on chunks.
        # The render should show "-" rather than the Python repr "None".
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "default")
            seed_chunk(
                conn, "c0",
                chunk_index=0,
                token_count=None,
                content="some body",
            )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "chunks", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "tokens=-" in result.stdout
        assert "tokens=None" not in result.stdout
