"""Integration tests for `mrag inspect chunk` (singular, Phase 2 — v0.18)."""
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
# Content + context always returned
# ---------------------------------------------------------------------------


class TestAlwaysShowsContentAndContext:
    def test_returns_full_content(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=2)
        result = runner.invoke(
            app, ["inspect", "chunk", "c0", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["content"] == "chunk 0 body text"

    def test_returns_context_text_when_contextual(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1, contextual=True)
        result = runner.invoke(
            app, ["inspect", "chunk", "c0", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        assert payload["context_text"] == "context for chunk 0"
        assert payload["variant"]["type"] == "contextual"

    def test_context_null_for_raw_variant(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1, contextual=False)
        result = runner.invoke(
            app, ["inspect", "chunk", "c0", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        assert payload["context_text"] is None
        assert payload["variant"]["type"] == "raw"

    def test_no_variant_at_all(self, project):
        """parent chunks (parent_child profile) have no variant row."""
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "parent-child")
            seed_chunk(
                conn, "p1",
                profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
                content="parent body",
            )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "chunk", "p1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["content"] == "parent body"
        assert payload["context_text"] is None
        assert payload["variant"]["type"] is None


# ---------------------------------------------------------------------------
# Profile flag not needed
# ---------------------------------------------------------------------------


class TestNoProfileFlag:
    def test_works_without_profile_flag(self, project):
        # chunk_id is unique across profiles (PRIMARY KEY).
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            for prof in ["default", "alt"]:
                seed_profile(conn, prof)
                seed_chunk(
                    conn, f"c-{prof}",
                    profile_name=prof,
                    chunk_index=0,
                    content=f"body for {prof}",
                )
        conn.close()
        for prof in ["default", "alt"]:
            result = runner.invoke(
                app, ["inspect", "chunk", f"c-{prof}", "--json"],
                catch_exceptions=False,
            )
            payload = json.loads(result.stdout)
            assert payload["profile"] == prof
            assert payload["content"] == f"body for {prof}"


# ---------------------------------------------------------------------------
# Payload structure / filename
# ---------------------------------------------------------------------------


class TestPayloadStructure:
    def test_includes_document_filename(self, project):
        _, db_path = project
        seed_basic_indexed_document(
            db_path, n_chunks=1, filename="my-file.pdf",
        )
        result = runner.invoke(
            app, ["inspect", "chunk", "c0", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        assert payload["document_filename"] == "my-file.pdf"

    def test_includes_metadata_as_dict(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1)
        result = runner.invoke(
            app, ["inspect", "chunk", "c0", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        assert isinstance(payload["metadata"], dict)
        assert payload["metadata"]["heading_path"] == ["Ch1", "Sec1"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_not_found_exits_1(self, project):
        result = runner.invoke(
            app, ["inspect", "chunk", "no-such-id"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr


# ---------------------------------------------------------------------------
# Human output sanity
# ---------------------------------------------------------------------------


class TestHumanOutput:
    def test_human_shows_chunk_id_and_content(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1)
        result = runner.invoke(
            app, ["inspect", "chunk", "c0"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "Chunk:" in result.stdout
        assert "c0" in result.stdout
        assert "Content:" in result.stdout
        assert "chunk 0 body text" in result.stdout
        # Context section is always present even when (none)
        assert "Context (LLM-generated):" in result.stdout

    def test_human_shows_context_for_contextual_variant(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1, contextual=True)
        result = runner.invoke(
            app, ["inspect", "chunk", "c0"], catch_exceptions=False
        )
        assert "context for chunk 0" in result.stdout

    def test_human_null_token_count_as_dash(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "default")
            seed_chunk(conn, "c0", chunk_index=0, token_count=None)
        conn.close()
        result = runner.invoke(
            app, ["inspect", "chunk", "c0"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "token_count  : -" in result.stdout
        assert "token_count  : None" not in result.stdout
