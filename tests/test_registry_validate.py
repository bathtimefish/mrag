"""Integration tests for `mrag registry validate` (Phase 2 — v0.18)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mrag.cli import app
from mrag.cli.registry import (
    ISSUE_DUPLICATE_ID,
    ISSUE_KB_INFORMATION_YAML_NOT_FOUND,
    ISSUE_MRAG_YAML_NOT_FOUND,
    ISSUE_PATH_NOT_FOUND,
    ISSUE_PREFERRED_PROFILE_NOT_FOUND,
)
from mrag.config.registry import REGISTRY_FILENAME


runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers — build registries and KB-shaped directories in tmp_path
# ---------------------------------------------------------------------------


def _write_registry(
    path: Path, knowledge_bases: list[dict], *, version: int = 1
) -> None:
    """Write a knowledge_registry.yaml directly (bypass mrag registry generate)."""
    data = {
        "version": version,
        "generated_at": "2026-05-17T12:00:00+00:00",
        "agent_instructions": {
            "selection_policy": "policy",
            "search_command_template": "tmpl",
        },
        "knowledge_bases": knowledge_bases,
    }
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _scaffold_kb(
    root: Path,
    name: str,
    *,
    include_mrag_yaml: bool = True,
    include_kb_info: bool = True,
    profiles: list[str] | None = None,
) -> Path:
    """Create a minimal KB-shaped directory under `root`."""
    kb_dir = root / name
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / "profiles").mkdir(exist_ok=True)
    if include_mrag_yaml:
        (kb_dir / "mrag.yaml").write_text("project:\n  name: x\n", encoding="utf-8")
    if include_kb_info:
        (kb_dir / "kb_information.yaml").write_text(
            "version: 1\n", encoding="utf-8"
        )
    for p in profiles or ["default"]:
        (kb_dir / "profiles" / f"{p}.yaml").write_text(
            f"name: {p}\n", encoding="utf-8"
        )
    return kb_dir


def _make_kb_entry(
    kb_id: str = "kb_a",
    path: str = "./kb-a",
    name: str = "KB A",
    preferred_profiles: list[str] | None = None,
) -> dict:
    return {
        "id": kb_id,
        "path": path,
        "name": name,
        "description": "",
        "tags": [],
        "best_for": [],
        "avoid_for": [],
        "preferred_profiles": preferred_profiles or ["default"],
        "example_queries": [],
    }


# ---------------------------------------------------------------------------
# Clean registry passes
# ---------------------------------------------------------------------------


class TestCleanRegistry:
    def test_validates_clean_registry(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a")
        _scaffold_kb(tmp_path, "kb-b")
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [
            _make_kb_entry("kb_a", "./kb-a", "KB A"),
            _make_kb_entry("kb_b", "./kb-b", "KB B"),
        ])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "registry schema valid" in result.stdout
        assert "all ids unique" in result.stdout

    def test_json_output_clean(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a")
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [_make_kb_entry("kb_a", "./kb-a", "KB A")])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["schema_valid"] is True
        assert payload["ids_unique"] is True
        assert payload["issues"] == []
        assert payload["issue_count"] == 0


# ---------------------------------------------------------------------------
# Per-issue type detection
# ---------------------------------------------------------------------------


class TestIssueDetection:
    def test_path_not_found(self, tmp_path):
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [
            _make_kb_entry("kb_x", "./kb-missing", "X"),
        ])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        issue_keys = [i["issue"] for i in payload["issues"]]
        assert ISSUE_PATH_NOT_FOUND in issue_keys

    def test_mrag_yaml_not_found(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", include_mrag_yaml=False)
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [_make_kb_entry("kb_a", "./kb-a", "A")])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        issue_keys = [i["issue"] for i in payload["issues"]]
        assert ISSUE_MRAG_YAML_NOT_FOUND in issue_keys

    def test_kb_information_yaml_not_found(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", include_kb_info=False)
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [_make_kb_entry("kb_a", "./kb-a", "A")])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        issue_keys = [i["issue"] for i in payload["issues"]]
        assert ISSUE_KB_INFORMATION_YAML_NOT_FOUND in issue_keys

    def test_preferred_profile_not_found(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", profiles=["default"])
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [
            _make_kb_entry(
                "kb_a", "./kb-a", "A",
                preferred_profiles=["default", "hybrid-rerank"],
            ),
        ])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        issue_keys = [i["issue"] for i in payload["issues"]]
        # only hybrid-rerank is missing
        assert ISSUE_PREFERRED_PROFILE_NOT_FOUND in issue_keys
        # detail should mention hybrid-rerank
        details = " ".join(i["detail"] for i in payload["issues"])
        assert "hybrid-rerank" in details

    def test_duplicate_id(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a1")
        _scaffold_kb(tmp_path, "kb-a2")
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [
            _make_kb_entry("kb_dup", "./kb-a1", "First"),
            _make_kb_entry("kb_dup", "./kb-a2", "Second"),
        ])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        issue_keys = [i["issue"] for i in payload["issues"]]
        assert ISSUE_DUPLICATE_ID in issue_keys


# ---------------------------------------------------------------------------
# Aggregation behavior (collect all issues, don't stop early)
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_collects_multiple_issues_at_once(self, tmp_path):
        # kb-a OK, kb-b path missing, kb-c missing profile
        _scaffold_kb(tmp_path, "kb-a")
        _scaffold_kb(tmp_path, "kb-c", profiles=["default"])
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [
            _make_kb_entry("kb_a", "./kb-a", "A"),
            _make_kb_entry("kb_b", "./kb-b-missing", "B"),
            _make_kb_entry(
                "kb_c", "./kb-c", "C",
                preferred_profiles=["nonexistent"],
            ),
        ])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["issue_count"] == 2
        issue_keys = [i["issue"] for i in payload["issues"]]
        assert ISSUE_PATH_NOT_FOUND in issue_keys
        assert ISSUE_PREFERRED_PROFILE_NOT_FOUND in issue_keys

    def test_json_issue_keys_are_stable(self, tmp_path):
        # Verify the stable issue key contract used by agent code
        reg_path = tmp_path / REGISTRY_FILENAME
        _write_registry(reg_path, [
            _make_kb_entry("kb_missing", "./does-not-exist", "X"),
        ])
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path), "--json"],
            catch_exceptions=False,
        )
        payload = json.loads(result.stdout)
        # required keys per DESIGN_V18 §3.2.2
        first = payload["issues"][0]
        assert set(first.keys()) == {
            "knowledge_base_index", "knowledge_base_id", "issue", "detail",
        }
        assert isinstance(first["knowledge_base_index"], int)
        assert isinstance(first["knowledge_base_id"], str)
        assert isinstance(first["issue"], str)
        assert isinstance(first["detail"], str)


# ---------------------------------------------------------------------------
# Fatal errors (exit 1 immediately, do not aggregate)
# ---------------------------------------------------------------------------


class TestFatalErrors:
    def test_yaml_parse_failure_exits_immediately(self, tmp_path):
        reg_path = tmp_path / REGISTRY_FILENAME
        # Unterminated flow sequence — PyYAML rejects this
        reg_path.write_text("knowledge_bases: [\n  - missing_close", encoding="utf-8")
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        # rich may wrap long lines; collapse whitespace for matching
        stderr_flat = " ".join(result.stderr.split())
        assert "failed to parse" in stderr_flat

    def test_schema_invalid_exits_immediately(self, tmp_path):
        reg_path = tmp_path / REGISTRY_FILENAME
        # version: 2 violates Literal[1]
        _write_registry(reg_path, [
            _make_kb_entry("kb_a", "./kb-a", "A"),
        ], version=2)
        result = runner.invoke(
            app, ["registry", "validate", str(reg_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        stderr_flat = " ".join(result.stderr.split())
        assert "schema validation failed" in stderr_flat

    def test_missing_registry_file(self, tmp_path):
        result = runner.invoke(
            app, ["registry", "validate", str(tmp_path / "absent.yaml")],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        assert "does not exist" in result.stderr


# ---------------------------------------------------------------------------
# End-to-end: generate then validate
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_generated_registry_validates_clean(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a")
        _scaffold_kb(tmp_path, "kb-b")
        # also need kb_information.yaml content acceptable to load_kb_info
        for name, kb_id in [("kb-a", "kb_a"), ("kb-b", "kb_b")]:
            (tmp_path / name / "kb_information.yaml").write_text(
                yaml.dump({
                    "version": 1,
                    "knowledge_base": {
                        "id": kb_id,
                        "name": name,
                        "description": "",
                    },
                    "agent_usage": {"preferred_profiles": ["default"]},
                }, sort_keys=False),
                encoding="utf-8",
            )
        # generate
        gen = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert gen.exit_code == 0
        # validate
        val = runner.invoke(
            app, ["registry", "validate", str(tmp_path / REGISTRY_FILENAME)],
            catch_exceptions=False,
        )
        assert val.exit_code == 0
