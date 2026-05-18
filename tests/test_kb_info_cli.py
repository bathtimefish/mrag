"""Tests for `mrag kb-info` subcommands (Phase 1 — v0.17)."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.kb_info import KB_INFORMATION_FILENAME

runner = CliRunner()


def _init_project(tmp_path: Path, monkeypatch, name: str = "kb-cli-test") -> Path:
    """Run `mrag init --non-interactive` and return the project directory."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--name", name, "--non-interactive"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    project_dir = tmp_path / name
    monkeypatch.chdir(project_dir)
    return project_dir


# ---------------------------------------------------------------------------
# mrag kb-info show
# ---------------------------------------------------------------------------

class TestKbInfoShow:
    def test_prints_kb_information_yaml(self, tmp_path, monkeypatch):
        project_dir = _init_project(tmp_path, monkeypatch)
        result = runner.invoke(app, ["kb-info", "show"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "version:" in result.output
        assert "knowledge_base:" in result.output
        assert "agent_usage:" in result.output
        # Should contain the actual file content (raw YAML)
        actual = (project_dir / KB_INFORMATION_FILENAME).read_text(encoding="utf-8")
        for line in actual.splitlines()[:3]:
            assert line.strip() in result.output

    def test_missing_file_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["kb-info", "show"])
        assert result.exit_code != 0
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# mrag kb-info validate
# ---------------------------------------------------------------------------

class TestKbInfoValidate:
    def test_valid_kb_info_passes(self, tmp_path, monkeypatch):
        _init_project(tmp_path, monkeypatch)
        result = runner.invoke(app, ["kb-info", "validate"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "valid" in result.output
        assert "knowledge_base.id" in result.output

    def test_invalid_kb_id_fails(self, tmp_path, monkeypatch):
        project_dir = _init_project(tmp_path, monkeypatch)
        # Corrupt the file with an invalid id
        (project_dir / KB_INFORMATION_FILENAME).write_text(
            "version: 1\n"
            "knowledge_base:\n"
            "  id: 'BAD-ID!'\n"
            "  name: x\n"
            "  description: ''\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["kb-info", "validate"])
        assert result.exit_code != 0
        assert "validation failed" in result.output
        assert "knowledge_base.id" in result.output

    def test_missing_required_field_fails(self, tmp_path, monkeypatch):
        project_dir = _init_project(tmp_path, monkeypatch)
        (project_dir / KB_INFORMATION_FILENAME).write_text(
            "version: 1\n"
            "# missing knowledge_base entirely\n"
            "agent_usage: {}\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["kb-info", "validate"])
        assert result.exit_code != 0
        assert "validation failed" in result.output

    def test_missing_file_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["kb-info", "validate"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_validate_shows_preferred_profiles(self, tmp_path, monkeypatch):
        _init_project(tmp_path, monkeypatch)
        result = runner.invoke(app, ["kb-info", "validate"], catch_exceptions=False)
        assert "preferred_profiles" in result.output
        assert "default" in result.output


# ---------------------------------------------------------------------------
# mrag kb-info schema
# ---------------------------------------------------------------------------

class TestKbInfoSchema:
    def test_outputs_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["kb-info", "schema"], catch_exceptions=False)
        assert result.exit_code == 0
        schema = json.loads(result.output)
        assert isinstance(schema, dict)

    def test_schema_matches_init_print_kb_info_schema(self, tmp_path, monkeypatch):
        """`mrag kb-info schema` and `mrag init --print-kb-info-schema` must be identical."""
        monkeypatch.chdir(tmp_path)
        r1 = runner.invoke(app, ["kb-info", "schema"], catch_exceptions=False)
        r2 = runner.invoke(app, ["init", "--print-kb-info-schema"], catch_exceptions=False)
        assert r1.exit_code == 0
        assert r2.exit_code == 0
        # Same JSON content
        assert json.loads(r1.output) == json.loads(r2.output)

    def test_schema_required_top_level_fields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["kb-info", "schema"], catch_exceptions=False)
        schema = json.loads(result.output)
        assert "project" in schema["required"]
        assert "knowledge_base" in schema["required"]

    def test_schema_does_not_require_project(self, tmp_path, monkeypatch):
        """schema command should work outside an mrag project (no mrag.yaml needed)."""
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "mrag.yaml").exists()
        result = runner.invoke(app, ["kb-info", "schema"], catch_exceptions=False)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# mrag kb-info (no args) — group help
# ---------------------------------------------------------------------------

class TestKbInfoGroupHelp:
    def test_help_lists_subcommands(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["kb-info"])
        # no_args_is_help=True returns exit code 2 from Typer; output should list subcommands
        assert "show" in result.output
        assert "validate" in result.output
        assert "schema" in result.output
