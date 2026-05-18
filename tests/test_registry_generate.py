"""Integration tests for `mrag registry generate` (Phase 2 — v0.18)."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.kb_info import KB_INFORMATION_FILENAME
from mrag.config.registry import REGISTRY_FILENAME, load_registry


runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers — manually scaffold KB-shaped directories without running `mrag init`
# (much faster than invoking the full init pipeline 2-3 times per test).
# ---------------------------------------------------------------------------


def _scaffold_kb(
    root: Path,
    name: str,
    *,
    kb_id: str,
    display_name: str | None = None,
    description: str = "",
    preferred_profiles: list[str] | None = None,
    include_default_profile: bool = True,
    include_mrag_yaml: bool = True,
    include_kb_info: bool = True,
    extra_profiles: list[str] | None = None,
    invalid_kb_info: bool = False,
) -> Path:
    """Create a minimal mrag-like KB directory under `root`."""
    kb_dir = root / name
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / "profiles").mkdir(exist_ok=True)

    if include_mrag_yaml:
        (kb_dir / "mrag.yaml").write_text(
            dedent(f"""\
                project:
                  name: {name}
                knowledge_base:
                  id: {kb_id}
                  name: {display_name or name}
                default_profile: default
            """),
            encoding="utf-8",
        )

    if include_default_profile:
        (kb_dir / "profiles" / "default.yaml").write_text(
            "name: default\n", encoding="utf-8"
        )

    for p in extra_profiles or []:
        (kb_dir / "profiles" / f"{p}.yaml").write_text(
            f"name: {p}\n", encoding="utf-8"
        )

    if include_kb_info:
        if invalid_kb_info:
            (kb_dir / KB_INFORMATION_FILENAME).write_text(
                "version: 1\nknowledge_base:\n  id: ''\n  name: ''\n",
                encoding="utf-8",
            )
        else:
            kb_info = {
                "version": 1,
                "knowledge_base": {
                    "id": kb_id,
                    "name": display_name or name,
                    "description": description,
                },
                "agent_usage": {
                    "preferred_profiles": preferred_profiles or ["default"],
                },
            }
            (kb_dir / KB_INFORMATION_FILENAME).write_text(
                yaml.dump(kb_info, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )

    return kb_dir


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


class TestGenerateSuccess:
    def test_two_kbs_aggregated(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a", display_name="KB A")
        _scaffold_kb(tmp_path, "kb-b", kb_id="kb_b", display_name="KB B")
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        out_path = tmp_path / REGISTRY_FILENAME
        assert out_path.exists()
        reg = load_registry(out_path)
        ids = sorted(kb.id for kb in reg.knowledge_bases)
        assert ids == ["kb_a", "kb_b"]

    def test_path_is_posix_relative(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-device", kb_id="kb_device")
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        reg = load_registry(tmp_path / REGISTRY_FILENAME)
        assert reg.knowledge_bases[0].path == "./kb-device"

    def test_yaml_unicode_preserved(self, tmp_path):
        _scaffold_kb(
            tmp_path, "kb-jp", kb_id="kb_jp",
            display_name="日本語KB", description="日本語の説明",
        )
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        content = (tmp_path / REGISTRY_FILENAME).read_text(encoding="utf-8")
        assert "日本語KB" in content
        assert "日本語の説明" in content

    def test_output_message_includes_count(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        _scaffold_kb(tmp_path, "kb-b", kb_id="kb_b")
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert "Wrote 2 knowledge_base" in result.stdout


# ---------------------------------------------------------------------------
# Skip rules (warn + continue)
# ---------------------------------------------------------------------------


class TestSkipBehavior:
    def test_skip_dir_without_kb_info(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        # "extras" subdir has no kb_information.yaml
        (tmp_path / "extras").mkdir()
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "skipping extras" in result.stderr
        reg = load_registry(tmp_path / REGISTRY_FILENAME)
        assert len(reg.knowledge_bases) == 1

    def test_skip_dir_without_mrag_yaml(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        # "broken" has kb_information.yaml but no mrag.yaml
        _scaffold_kb(
            tmp_path, "broken", kb_id="kb_broken",
            include_mrag_yaml=False,
        )
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "skipping broken" in result.stderr
        assert "no mrag.yaml" in result.stderr
        reg = load_registry(tmp_path / REGISTRY_FILENAME)
        assert [kb.id for kb in reg.knowledge_bases] == ["kb_a"]

    def test_skip_invalid_kb_info(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        _scaffold_kb(tmp_path, "kb-bad", kb_id="kb_bad", invalid_kb_info=True)
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "skipping kb-bad" in result.stderr
        reg = load_registry(tmp_path / REGISTRY_FILENAME)
        assert [kb.id for kb in reg.knowledge_bases] == ["kb_a"]

    def test_does_not_recurse_into_nested_dirs(self, tmp_path):
        # Place a KB at depth 2 — should be ignored (1-level only per DESIGN_V18 §3.2.1)
        _scaffold_kb(tmp_path, "kb-top", kb_id="kb_top")
        nested = tmp_path / "team-a"
        nested.mkdir()
        _scaffold_kb(nested, "kb-nested", kb_id="kb_nested")
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        reg = load_registry(tmp_path / REGISTRY_FILENAME)
        ids = [kb.id for kb in reg.knowledge_bases]
        assert "kb_top" in ids
        assert "kb_nested" not in ids


# ---------------------------------------------------------------------------
# Errors — no KBs found, id collisions, bad root_dir
# ---------------------------------------------------------------------------


class TestErrors:
    def test_no_kbs_found_exits_1(self, tmp_path):
        # empty root
        (tmp_path / "data").mkdir()  # subdir without kb_info
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "no kb_information.yaml found" in result.stderr
        # tip + skipped list included
        assert "data" in result.stderr
        assert "Tip:" in result.stderr

    def test_root_dir_does_not_exist(self, tmp_path):
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path / "nope")],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        assert "does not exist or is not a directory" in result.stderr

    def test_id_collision_exits_1(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a1", kb_id="kb_dup")
        _scaffold_kb(tmp_path, "kb-a2", kb_id="kb_dup")
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "duplicate knowledge_base.id 'kb_dup'" in result.stderr
        assert not (tmp_path / REGISTRY_FILENAME).exists()


# ---------------------------------------------------------------------------
# --dry-run / --output
# ---------------------------------------------------------------------------


class TestDryRunAndOutput:
    def test_dry_run_writes_no_file(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path), "--dry-run"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert not (tmp_path / REGISTRY_FILENAME).exists()
        # stdout contains the YAML
        data = yaml.safe_load(result.stdout)
        assert data["version"] == 1
        assert data["knowledge_bases"][0]["id"] == "kb_a"

    def test_output_override(self, tmp_path):
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        out = tmp_path / "alt" / "custom_registry.yaml"
        out.parent.mkdir()
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path), "--output", str(out)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert out.exists()
        # default location should NOT have been written
        assert not (tmp_path / REGISTRY_FILENAME).exists()

    def test_output_path_relative_to_registry_dir(self, tmp_path):
        # When --output points to a different dir, the per-KB `path` should
        # be relative to that output dir, not to root_dir.
        _scaffold_kb(tmp_path, "kb-a", kb_id="kb_a")
        out_dir = tmp_path / "different-place"
        out_dir.mkdir()
        out = out_dir / REGISTRY_FILENAME
        result = runner.invoke(
            app, ["registry", "generate", str(tmp_path), "--output", str(out)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        reg = load_registry(out)
        # kb-a is at <tmp_path>/kb-a, registry is at <tmp_path>/different-place/
        # so the relative path is "../kb-a"
        assert reg.knowledge_bases[0].path == "../kb-a"
