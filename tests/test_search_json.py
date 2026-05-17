"""Tests for `mrag search --json` output format (Phase 1 — v0.17)."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.project import load_project_config
from mrag.core.indexing.pipeline import run_index
from tests.test_indexing import FakeEmbeddingProvider, _fake_qdrant_client

runner = CliRunner()


@pytest.fixture
def searchable_project(tmp_path: Path, monkeypatch):
    """Create a project, add a document, and index it for search testing."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "kb-search", "--non-interactive"], catch_exceptions=False)
    project_dir = tmp_path / "kb-search"
    monkeypatch.chdir(project_dir)

    doc = project_dir / "doc.txt"
    doc.write_text(
        "Hello world. This document discusses MQTT publish and LTE modules.\n"
        "Section 2 covers serial communication and AT commands.\n",
        encoding="utf-8",
    )
    runner.invoke(app, ["add", str(doc)], catch_exceptions=False)

    config = load_project_config(project_dir)
    run_index(
        project_dir=project_dir,
        config=config,
        profile_name="default",
        embedding_provider=FakeEmbeddingProvider(),
        qdrant_client=_fake_qdrant_client(),
    )
    return project_dir


class TestJsonOutputStructure:
    def test_emits_valid_json_to_stdout(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # stdout must be parseable JSON
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict)

    def test_top_level_keys(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert set(payload.keys()) == {
            "query", "profile", "strategy", "reranked",
            "result_count", "results", "score_stats", "document_distribution",
        }

    def test_query_echoed(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert payload["query"] == "Hello"

    def test_strategy_echoed(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert payload["strategy"] == "keyword"

    def test_reranked_false_when_rerank_disabled(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert payload["reranked"] is False


class TestResultEntryStructure:
    def test_result_count_matches_results_length(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        assert payload["result_count"] == len(payload["results"])

    def test_each_result_has_required_fields(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--top-k", "3", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        if not payload["results"]:
            pytest.skip("FTS5 returned no results for this query — environment-dependent")
        for entry in payload["results"]:
            assert "rank" in entry
            assert "chunk_id" in entry
            assert "document_id" in entry
            assert "filename" in entry
            assert "score" in entry
            assert "content" in entry
            assert "metadata" in entry
            assert isinstance(entry["rank"], int)
            assert isinstance(entry["score"], (int, float))
            assert isinstance(entry["metadata"], dict)

    def test_ranks_are_sequential_from_1(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--top-k", "5", "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        if not payload["results"]:
            pytest.skip("FTS5 returned no results for this query")
        ranks = [entry["rank"] for entry in payload["results"]]
        assert ranks == list(range(1, len(ranks) + 1))


class TestEmptyResults:
    def test_no_match_returns_empty_results(self, searchable_project):
        result = runner.invoke(
            app, ["search", "xyznonexistentterm123", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["result_count"] == 0
        assert payload["results"] == []
        assert payload["score_stats"] is None
        assert payload["document_distribution"] == {}


class TestStdoutCleanlinessForPiping:
    def test_no_status_lines_on_stdout(self, searchable_project):
        """stdout must contain only the JSON payload — no rich-formatted status lines."""
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        # stdout should parse as a single JSON value with no extra content
        payload = json.loads(result.stdout)  # would raise otherwise
        assert isinstance(payload, dict)

    def test_stdout_starts_with_json_brace(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--strategy", "keyword", "--json"],
            catch_exceptions=False,
        )
        assert result.stdout.lstrip().startswith("{")


class TestErrorHandling:
    def test_missing_profile_exits_nonzero(self, searchable_project):
        result = runner.invoke(
            app, ["search", "Hello", "--profile", "nonexistent", "--json"],
        )
        assert result.exit_code != 0

    def test_outside_project_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["search", "Hello", "--json"])
        assert result.exit_code != 0
