"""Integration tests for `mrag inspect sections` (Phase 2 — v0.18)."""
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
)

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    project_dir, db_path = init_inspect_project(tmp_path, monkeypatch)
    return project_dir, db_path


# ---------------------------------------------------------------------------
# Heading hierarchy mode (block_aware / markdown_recursive profiles)
# ---------------------------------------------------------------------------


class TestHeadingHierarchy:
    def test_tree_structure_from_heading_paths(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "default")
            # Ch1 > Sec1.1 (2 chunks), Ch1 > Sec1.2 (1 chunk), Ch2 (1 chunk)
            seed_chunk(conn, "c0", chunk_index=0, char_count=910,
                       metadata={"heading_path": ["Ch1", "Sec1.1"]})
            seed_chunk(conn, "c1", chunk_index=1, char_count=910,
                       metadata={"heading_path": ["Ch1", "Sec1.1"]})
            seed_chunk(conn, "c2", chunk_index=2, char_count=820,
                       metadata={"heading_path": ["Ch1", "Sec1.2"]})
            seed_chunk(conn, "c3", chunk_index=3, char_count=500,
                       metadata={"heading_path": ["Ch2"]})
        conn.close()

        result = runner.invoke(
            app, ["inspect", "sections", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["mode"] == "heading"
        # Top-level sections: Ch1, Ch2
        titles = [s["title"] for s in payload["sections"]]
        assert titles == ["Ch1", "Ch2"]

        ch1 = payload["sections"][0]
        assert ch1["chunk_count"] == 3
        assert ch1["char_count"] == 910 + 910 + 820
        child_titles = [c["title"] for c in ch1["children"]]
        assert child_titles == ["Sec1.1", "Sec1.2"]

        ch2 = payload["sections"][1]
        assert ch2["chunk_count"] == 1
        assert ch2["char_count"] == 500

    def test_human_output_renders_tree(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=3)
        result = runner.invoke(
            app, ["inspect", "sections", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "§ Ch1" in result.stdout
        # 3 chunks total under Ch1
        assert "chunks" in result.stdout


# ---------------------------------------------------------------------------
# Parent-child mode
# ---------------------------------------------------------------------------


class TestParentChild:
    def test_parent_child_layered_view(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "parent-child")
            seed_chunk(
                conn, "p0", profile_name="parent-child",
                chunk_type="parent", chunk_index=0, char_count=1800,
                metadata={"heading_path": ["Ch1"]},
            )
            for i, cid in enumerate(["c0", "c1", "c2"]):
                seed_chunk(
                    conn, cid, profile_name="parent-child",
                    chunk_type="child", chunk_index=i,
                    parent_chunk_id="p0",
                    char_count=600,
                )
        conn.close()

        result = runner.invoke(
            app, ["inspect", "sections", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["mode"] == "parent_child"
        assert len(payload["parents"]) == 1
        p = payload["parents"][0]
        assert p["parent_id"] == "p0"
        assert p["char_count"] == 1800
        assert len(p["children"]) == 3
        assert [c["chunk_id"] for c in p["children"]] == ["c0", "c1", "c2"]

    def test_parent_child_without_heading_path_still_works(self, project):
        # heading_path absence is fine for parent_child mode
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "parent-child")
            seed_chunk(
                conn, "p0", profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
            )
            seed_chunk(
                conn, "c0", profile_name="parent-child",
                chunk_type="child", chunk_index=0,
                parent_chunk_id="p0",
            )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "sections", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["mode"] == "parent_child"

    def test_parent_child_human_output(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "parent-child")
            seed_chunk(
                conn, "p0", profile_name="parent-child",
                chunk_type="parent", chunk_index=0, char_count=1800,
                metadata={"heading_path": ["Ch1"]},
            )
            seed_chunk(
                conn, "c0", profile_name="parent-child",
                chunk_type="child", chunk_index=0,
                parent_chunk_id="p0", char_count=600,
            )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "sections", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "parent: p0" in result.stdout
        assert "↳ child: c0" in result.stdout


# ---------------------------------------------------------------------------
# heading_path absent (recursive profile) — exits 1
# ---------------------------------------------------------------------------


class TestNoHeadingPath:
    def test_exits_1_when_no_heading_path(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "recursive")
            for i in range(3):
                seed_chunk(
                    conn, f"c{i}", profile_name="recursive", chunk_index=i,
                    metadata=None,
                )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "sections", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "no section structure" in result.stderr

    def test_error_message_suggests_inspect_chunks(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "recursive")
            seed_chunk(conn, "c0", profile_name="recursive", chunk_index=0)
        conn.close()
        result = runner.invoke(
            app, ["inspect", "sections", "d1"], catch_exceptions=False
        )
        # error message points the user to inspect chunks
        assert "mrag inspect chunks d1 --profile recursive" in result.stderr


# ---------------------------------------------------------------------------
# Profile resolution / errors
# ---------------------------------------------------------------------------


class TestProfileResolution:
    def test_multiple_profiles_requires_flag(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            for prof in ["default", "alt"]:
                seed_profile(conn, prof)
                seed_chunk(
                    conn, f"c-{prof}", profile_name=prof, chunk_index=0,
                    metadata={"heading_path": ["Ch1"]},
                )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "sections", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "multiple profiles" in result.stderr


class TestErrors:
    def test_document_not_found(self, project):
        result = runner.invoke(
            app, ["inspect", "sections", "no-such-id"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr
