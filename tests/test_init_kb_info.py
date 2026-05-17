"""Integration tests for `mrag init` kb_information.yaml generation (Phase 1 — v0.17)."""
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.kb_info import KB_INFORMATION_FILENAME, load_kb_info

runner = CliRunner()


def _load_kb_info_yaml(project_dir: Path) -> dict:
    """Read kb_information.yaml as raw dict (without Pydantic re-validation)."""
    path = project_dir / KB_INFORMATION_FILENAME
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Non-interactive mode — minimal template generation
# ---------------------------------------------------------------------------

class TestNonInteractiveMinimal:
    def test_creates_kb_information_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["init", "--name", "my-kb", "--non-interactive"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "my-kb" / KB_INFORMATION_FILENAME).exists()

    def test_kb_information_yaml_is_minimal_template(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(
            app, ["init", "--name", "my-kb", "--non-interactive"], catch_exceptions=False
        )
        data = _load_kb_info_yaml(tmp_path / "my-kb")
        assert data["version"] == 1
        assert data["knowledge_base"]["description"] == ""
        assert data["agent_usage"]["tags"] == []
        assert data["agent_usage"]["best_for"] == []
        assert data["agent_usage"]["avoid_for"] == []
        assert data["agent_usage"]["preferred_profiles"] == ["default"]
        assert data["agent_usage"]["example_queries"] == []

    def test_kb_information_loads_back_validly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(
            app, ["init", "--name", "my-kb", "--non-interactive"], catch_exceptions=False
        )
        cfg = load_kb_info(tmp_path / "my-kb")
        assert cfg.version == 1


# ---------------------------------------------------------------------------
# --kb-info-json mode — full template from JSON
# ---------------------------------------------------------------------------

class TestKbInfoJson:
    def test_full_input_populates_kb_information(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        json_path = tmp_path / "input.json"
        json_path.write_text(json.dumps({
            "project": {"name": "device-kb"},
            "knowledge_base": {
                "id": "kb_device",
                "name": "Device KB",
                "description": "Embedded device knowledge.",
            },
            "agent_usage": {
                "tags": ["m5stack", "lte"],
                "best_for": ["LTE issues"],
                "avoid_for": ["Contracts"],
                "preferred_profiles": ["default", "hybrid-rerank"],
                "example_queries": ["MQTT stops"],
            },
        }), encoding="utf-8")

        result = runner.invoke(app, [
            "init", "--kb-info-json", str(json_path), "--non-interactive",
        ], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        # The JSON specifies project.name = "device-kb" so the dir is named after it
        project_dir = tmp_path / "device-kb"
        data = _load_kb_info_yaml(project_dir)
        assert data["knowledge_base"]["id"] == "kb_device"
        assert data["knowledge_base"]["name"] == "Device KB"
        assert data["knowledge_base"]["description"] == "Embedded device knowledge."
        assert data["agent_usage"]["tags"] == ["m5stack", "lte"]
        assert data["agent_usage"]["preferred_profiles"] == ["default", "hybrid-rerank"]
        # version is always 1
        assert data["version"] == 1

    def test_missing_required_field_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        json_path = tmp_path / "bad.json"
        json_path.write_text(json.dumps({
            # missing 'project'
            "knowledge_base": {"id": "kb_x", "name": "x", "description": "x"},
        }), encoding="utf-8")

        result = runner.invoke(app, [
            "init", "--kb-info-json", str(json_path), "--non-interactive",
        ])
        assert result.exit_code != 0
        assert "validation failed" in result.output or "project" in result.output

    def test_invalid_kb_id_in_json_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        json_path = tmp_path / "bad.json"
        json_path.write_text(json.dumps({
            "project": {"name": "x"},
            "knowledge_base": {"id": "KB-Bad!", "name": "x", "description": "x"},
        }), encoding="utf-8")

        result = runner.invoke(app, [
            "init", "--kb-info-json", str(json_path), "--non-interactive",
        ])
        assert result.exit_code != 0

    def test_invalid_json_file_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        json_path = tmp_path / "bad.json"
        json_path.write_text("not valid json {", encoding="utf-8")

        result = runner.invoke(app, [
            "init", "--kb-info-json", str(json_path), "--non-interactive",
        ])
        assert result.exit_code != 0
        assert "invalid JSON" in result.output

    def test_nonexistent_json_file_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "init", "--kb-info-json", str(tmp_path / "missing.json"), "--non-interactive",
        ])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_validation_failure_leaves_no_project_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        json_path = tmp_path / "bad.json"
        json_path.write_text(json.dumps({
            "knowledge_base": {"id": "kb_x", "name": "x"},  # missing project
        }), encoding="utf-8")

        runner.invoke(app, [
            "init", "--kb-info-json", str(json_path), "--non-interactive",
        ])
        # No project directory should have been created from validation failure
        assert not (tmp_path / "x").exists()
        assert not (tmp_path / "kb-x").exists()

    def test_json_input_takes_precedence_over_name_flag(self, tmp_path, monkeypatch):
        """If both --name and --kb-info-json are given, --name overrides JSON."""
        monkeypatch.chdir(tmp_path)
        json_path = tmp_path / "input.json"
        json_path.write_text(json.dumps({
            "project": {"name": "from-json"},
            "knowledge_base": {"id": "kb_json", "name": "JSON", "description": ""},
        }), encoding="utf-8")
        result = runner.invoke(app, [
            "init", "--name", "from-flag",
            "--kb-info-json", str(json_path),
            "--non-interactive",
        ], catch_exceptions=False)
        assert result.exit_code == 0
        # --name wins for the project dir
        assert (tmp_path / "from-flag").exists()


# ---------------------------------------------------------------------------
# Positional PROJECT_DIR argument
# ---------------------------------------------------------------------------

class TestPositionalProjectDir:
    def test_creates_at_positional_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "subdir" / "my-kb"
        result = runner.invoke(app, [
            "init", str(target), "--non-interactive",
        ], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert target.exists()
        assert (target / "mrag.yaml").exists()
        assert (target / KB_INFORMATION_FILENAME).exists()

    def test_positional_derives_name_from_basename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "kb-device"
        runner.invoke(app, [str("init"), str(target), "--non-interactive"], catch_exceptions=False)
        # mrag.yaml project.name should derive from basename
        mrag_yaml = yaml.safe_load((target / "mrag.yaml").read_text(encoding="utf-8"))
        assert mrag_yaml["project"]["name"] == "kb-device"

    def test_positional_with_name_uses_name_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "physical-path"
        runner.invoke(app, [
            "init", str(target), "--name", "logical-name", "--non-interactive",
        ], catch_exceptions=False)
        # Files are at the positional path
        assert target.exists()
        # But project.name uses --name
        mrag_yaml = yaml.safe_load((target / "mrag.yaml").read_text(encoding="utf-8"))
        assert mrag_yaml["project"]["name"] == "logical-name"


# ---------------------------------------------------------------------------
# --print-kb-info-schema
# ---------------------------------------------------------------------------

class TestPrintKbInfoSchema:
    def test_prints_json_schema_to_stdout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", "--print-kb-info-schema"], catch_exceptions=False)
        assert result.exit_code == 0
        # Output should be valid JSON
        schema = json.loads(result.output)
        assert "required" in schema
        assert "project" in schema["required"]
        assert "knowledge_base" in schema["required"]

    def test_does_not_create_any_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "--print-kb-info-schema"], catch_exceptions=False)
        # tmp_path should still be empty (no project dir created)
        assert list(tmp_path.iterdir()) == []

    def test_ignores_other_arguments(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "init", "--print-kb-info-schema", "--name", "should-be-ignored", "--non-interactive",
        ], catch_exceptions=False)
        assert result.exit_code == 0
        # No project dir created despite --name being given
        assert not (tmp_path / "should-be-ignored").exists()
        # Output is JSON Schema, not init success messages
        json.loads(result.output)  # would raise if non-JSON


# ---------------------------------------------------------------------------
# Existing mrag init behavior preserved
# ---------------------------------------------------------------------------

class TestExistingBehaviorPreserved:
    def test_still_generates_mrag_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "--name", "kb-a", "--non-interactive"], catch_exceptions=False)
        assert (tmp_path / "kb-a" / "mrag.yaml").exists()

    def test_still_generates_default_profile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "--name", "kb-a", "--non-interactive"], catch_exceptions=False)
        assert (tmp_path / "kb-a" / "profiles" / "default.yaml").exists()

    def test_still_generates_context_prompt(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "--name", "kb-a", "--non-interactive"], catch_exceptions=False)
        assert (tmp_path / "kb-a" / "profiles" / "context_prompt.txt").exists()

    def test_still_initializes_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "--name", "kb-a", "--non-interactive"], catch_exceptions=False)
        assert (tmp_path / "kb-a" / "mrag.db").exists()

    def test_force_flag_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init", "--name", "kb-a", "--non-interactive"], catch_exceptions=False)
        # Second run without --force should fail
        result = runner.invoke(app, ["init", "--name", "kb-a", "--non-interactive"])
        assert result.exit_code != 0
        # With --force it should succeed
        result = runner.invoke(app, [
            "init", "--name", "kb-a", "--non-interactive", "--force",
        ], catch_exceptions=False)
        assert result.exit_code == 0
