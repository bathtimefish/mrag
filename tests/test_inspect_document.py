"""Integration tests for `mrag inspect document` (Phase 2 — v0.18)."""
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
    seed_index,
    seed_profile,
    seed_variant,
)

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    project_dir, db_path = init_inspect_project(tmp_path, monkeypatch)
    return project_dir, db_path


# ---------------------------------------------------------------------------
# Basic / Human output
# ---------------------------------------------------------------------------


class TestHumanOutput:
    def test_basic_document_renders(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=3)
        result = runner.invoke(
            app, ["inspect", "document", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "Document:" in result.stdout
        assert "d1" in result.stdout
        assert "ble_guide.pdf" in result.stdout
        # Indexed Profiles table includes the profile name and chunk count
        assert "default" in result.stdout
        # rich Table renders chunk count cell — verify a "3" is somewhere
        # after the "Indexed Profiles" header
        idx = result.stdout.find("Indexed Profiles")
        assert idx != -1
        assert "3" in result.stdout[idx:]

    def test_no_indexed_profiles_message(self, project):
        # Document exists but no chunks at all
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
        conn.close()
        result = runner.invoke(
            app, ["inspect", "document", "d1"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "No profiles have indexed this document" in result.stdout


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_top_level_keys(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=2)
        result = runner.invoke(
            app, ["inspect", "document", "d1", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert set(payload.keys()) == {"document", "profiles"}
        assert payload["document"]["id"] == "d1"
        assert payload["document"]["filename"] == "ble_guide.pdf"
        assert len(payload["profiles"]) == 1
        prof = payload["profiles"][0]
        assert prof["name"] == "default"
        assert prof["status"] == "indexed"
        assert prof["chunk_counts"]["chunk"] == 2
        assert prof["chunk_counts"]["total"] == 2

    def test_json_stdout_only(self, project):
        _, db_path = project
        seed_basic_indexed_document(db_path, n_chunks=1)
        result = runner.invoke(
            app, ["inspect", "document", "d1", "--json"], catch_exceptions=False
        )
        # stdout must be parseable JSON with no extra noise
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict)


# ---------------------------------------------------------------------------
# Multi-profile / parent-child / augmentation
# ---------------------------------------------------------------------------


class TestMultiProfile:
    def test_multiple_profiles_listed(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            for prof in ["default", "parent-child"]:
                seed_profile(conn, prof)
                seed_index(conn, f"i-{prof}", profile_name=prof)
            for i in range(2):
                seed_chunk(conn, f"c-d-{i}", profile_name="default", chunk_index=i)
            seed_chunk(
                conn, "p1", profile_name="parent-child",
                chunk_type="parent", chunk_index=0,
            )
            seed_chunk(
                conn, "ch1", profile_name="parent-child",
                chunk_type="child", chunk_index=0, parent_chunk_id="p1",
            )
        conn.close()

        result = runner.invoke(
            app, ["inspect", "document", "d1", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        names = sorted(p["name"] for p in payload["profiles"])
        assert names == ["default", "parent-child"]
        pc = next(p for p in payload["profiles"] if p["name"] == "parent-child")
        assert pc["chunk_counts"]["parent"] == 1
        assert pc["chunk_counts"]["child"] == 1

    def test_profile_filter_returns_only_one(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            for prof in ["default", "alt"]:
                seed_profile(conn, prof)
                seed_chunk(conn, f"c-{prof}", profile_name=prof, chunk_index=0)
        conn.close()
        result = runner.invoke(
            app,
            ["inspect", "document", "d1", "--profile", "default", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert [p["name"] for p in payload["profiles"]] == ["default"]


class TestAugmentation:
    def test_raw_fallback_counted(self, project):
        _, db_path = project
        conn = open_seed_conn(db_path)
        with conn:
            seed_document(conn, "d1")
            seed_profile(conn, "default")
            seed_index(conn, "i1")
            seed_chunk(conn, "c0", chunk_index=0)
            seed_chunk(conn, "c1", chunk_index=1)
            # c0: contextual variant — counts as a successful augmentation
            seed_variant(
                conn, "v0", chunk_id="c0",
                variant_type="contextual", context_text="ctx",
            )
            # c1: raw with fallback_raw status — counts as a failed augmentation
            seed_variant(
                conn, "v1", chunk_id="c1",
                augmentation_status="fallback_raw",
            )
        conn.close()
        result = runner.invoke(
            app, ["inspect", "document", "d1", "--json"], catch_exceptions=False
        )
        payload = json.loads(result.stdout)
        aug = payload["profiles"][0]["augmentation"]
        assert aug["succeeded"] == 1
        assert aug["raw_fallback"] == 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_not_found_exits_1(self, project):
        result = runner.invoke(
            app, ["inspect", "document", "no-such-id"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_not_in_project_exits_1(self, tmp_path, monkeypatch):
        # outside any mrag project
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["inspect", "document", "anything"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "mrag.db not found" in result.stderr
