"""Tests for KnowledgeRegistry models (Phase 2 — v0.18 knowledge_registry.yaml)."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mrag.config.kb_info import (
    KbInfoAgentUsage,
    KbInfoKnowledgeBase,
    KbInformationConfig,
)
from mrag.config.registry import (
    REGISTRY_FILENAME,
    KnowledgeRegistry,
    RegistryAgentInstructions,
    RegistryKnowledgeBase,
    dump_registry,
    from_kb_information,
    load_registry,
    now_utc_iso,
    registry_json_schema,
    registry_path,
    to_relative_posix_path,
)


# ---------------------------------------------------------------------------
# RegistryKnowledgeBase validation
# ---------------------------------------------------------------------------


class TestRegistryKnowledgeBaseValidation:
    def test_minimal_valid(self):
        kb = RegistryKnowledgeBase(id="kb_x", path="./kb-x", name="X")
        assert kb.id == "kb_x"
        assert kb.path == "./kb-x"
        assert kb.name == "X"
        assert kb.description == ""
        assert kb.tags == []
        assert kb.best_for == []
        assert kb.avoid_for == []
        assert kb.preferred_profiles == ["default"]
        assert kb.example_queries == []

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="id must not be empty"):
            RegistryKnowledgeBase(id="", path="./kb-x", name="X")

    def test_empty_path_raises(self):
        with pytest.raises(ValidationError, match="path must not be empty"):
            RegistryKnowledgeBase(id="kb_x", path="", name="X")

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError, match="name must not be empty"):
            RegistryKnowledgeBase(id="kb_x", path="./kb-x", name="")

    def test_lists_round_trip(self):
        kb = RegistryKnowledgeBase(
            id="kb_x",
            path="./kb-x",
            name="X",
            tags=["a", "b"],
            best_for=["bf"],
            avoid_for=["af"],
            preferred_profiles=["default", "p2"],
            example_queries=["q1"],
        )
        assert kb.tags == ["a", "b"]
        assert kb.preferred_profiles == ["default", "p2"]


# ---------------------------------------------------------------------------
# RegistryAgentInstructions defaults
# ---------------------------------------------------------------------------


class TestRegistryAgentInstructions:
    def test_defaults_non_empty(self):
        ai = RegistryAgentInstructions()
        assert ai.selection_policy
        assert "knowledge base" in ai.selection_policy.lower()
        assert "{path}" in ai.search_command_template
        assert "{query}" in ai.search_command_template
        assert "{profile}" in ai.search_command_template
        assert "--json" in ai.search_command_template

    def test_user_can_override(self):
        ai = RegistryAgentInstructions(
            selection_policy="custom",
            search_command_template="custom\n",
        )
        assert ai.selection_policy == "custom"
        assert ai.search_command_template == "custom\n"


# ---------------------------------------------------------------------------
# KnowledgeRegistry top-level
# ---------------------------------------------------------------------------


class TestKnowledgeRegistry:
    def test_version_default_is_1(self):
        reg = KnowledgeRegistry(generated_at=now_utc_iso())
        assert reg.version == 1

    def test_version_2_rejected(self):
        with pytest.raises(ValidationError):
            KnowledgeRegistry(version=2, generated_at=now_utc_iso())

    def test_generated_at_required(self):
        with pytest.raises(ValidationError):
            KnowledgeRegistry()

    def test_empty_knowledge_bases_allowed(self):
        # Model allows empty list — the CLI enforces non-empty at generate time (§3.2.1).
        reg = KnowledgeRegistry(generated_at=now_utc_iso(), knowledge_bases=[])
        assert reg.knowledge_bases == []

    def test_agent_instructions_default_factory(self):
        reg = KnowledgeRegistry(generated_at=now_utc_iso())
        assert isinstance(reg.agent_instructions, RegistryAgentInstructions)

    def test_full_construction(self):
        reg = KnowledgeRegistry(
            generated_at="2026-05-17T12:00:00+00:00",
            knowledge_bases=[
                RegistryKnowledgeBase(id="kb_a", path="./kb-a", name="A"),
                RegistryKnowledgeBase(id="kb_b", path="./kb-b", name="B"),
            ],
        )
        assert len(reg.knowledge_bases) == 2


# ---------------------------------------------------------------------------
# now_utc_iso
# ---------------------------------------------------------------------------


class TestNowUtcIso:
    def test_format(self):
        s = now_utc_iso()
        # ISO 8601 with +00:00 suffix, seconds precision (no microseconds)
        assert "T" in s
        assert s.endswith("+00:00")
        assert "." not in s.split("T")[1]


# ---------------------------------------------------------------------------
# to_relative_posix_path
# ---------------------------------------------------------------------------


class TestRelativePath:
    def test_simple_subdir(self, tmp_path: Path):
        registry_dir = tmp_path
        kb_dir = tmp_path / "kb-device"
        kb_dir.mkdir()
        assert to_relative_posix_path(kb_dir, registry_dir) == "./kb-device"

    def test_uses_posix_separators(self, tmp_path: Path):
        registry_dir = tmp_path
        kb_dir = tmp_path / "a" / "b"
        kb_dir.mkdir(parents=True)
        rel = to_relative_posix_path(kb_dir, registry_dir)
        assert "/" in rel
        assert "\\" not in rel
        assert rel == "./a/b"


# ---------------------------------------------------------------------------
# from_kb_information
# ---------------------------------------------------------------------------


class TestFromKbInformation:
    def test_minimal_conversion(self):
        kb_info = KbInformationConfig(
            knowledge_base=KbInfoKnowledgeBase(id="kb_x", name="X"),
        )
        entry = from_kb_information(kb_info, "./kb-x")
        assert entry.id == "kb_x"
        assert entry.path == "./kb-x"
        assert entry.name == "X"
        assert entry.description == ""
        assert entry.preferred_profiles == ["default"]

    def test_full_conversion(self):
        kb_info = KbInformationConfig(
            knowledge_base=KbInfoKnowledgeBase(
                id="kb_device", name="Device", description="desc"
            ),
            agent_usage=KbInfoAgentUsage(
                tags=["t1", "t2"],
                best_for=["bf"],
                avoid_for=["af"],
                preferred_profiles=["default", "p2"],
                example_queries=["q1"],
            ),
        )
        entry = from_kb_information(kb_info, "./kb-device")
        assert entry.tags == ["t1", "t2"]
        assert entry.best_for == ["bf"]
        assert entry.avoid_for == ["af"]
        assert entry.preferred_profiles == ["default", "p2"]
        assert entry.example_queries == ["q1"]
        assert entry.description == "desc"


# ---------------------------------------------------------------------------
# I/O: dump + load round-trip
# ---------------------------------------------------------------------------


class TestRegistryIO:
    def test_registry_path_helper(self, tmp_path: Path):
        assert registry_path(tmp_path) == tmp_path / REGISTRY_FILENAME

    def test_dump_creates_file(self, tmp_path: Path):
        reg = KnowledgeRegistry(
            generated_at=now_utc_iso(),
            knowledge_bases=[
                RegistryKnowledgeBase(id="kb_a", path="./kb-a", name="A")
            ],
        )
        path = registry_path(tmp_path)
        written = dump_registry(reg, path)
        assert written == path
        assert path.exists()

    def test_dump_is_valid_yaml(self, tmp_path: Path):
        reg = KnowledgeRegistry(
            generated_at=now_utc_iso(),
            knowledge_bases=[
                RegistryKnowledgeBase(id="kb_a", path="./kb-a", name="A"),
            ],
        )
        path = registry_path(tmp_path)
        dump_registry(reg, path)
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["version"] == 1
        assert loaded["generated_at"]
        assert loaded["knowledge_bases"][0]["id"] == "kb_a"

    def test_round_trip(self, tmp_path: Path):
        original = KnowledgeRegistry(
            generated_at="2026-05-17T12:00:00+00:00",
            knowledge_bases=[
                RegistryKnowledgeBase(
                    id="kb_device",
                    path="./kb-device",
                    name="Device",
                    description="d",
                    tags=["t"],
                    preferred_profiles=["default", "p2"],
                ),
            ],
        )
        path = registry_path(tmp_path)
        dump_registry(original, path)
        reloaded = load_registry(path)
        assert reloaded.version == original.version
        assert reloaded.generated_at == original.generated_at
        assert len(reloaded.knowledge_bases) == 1
        kb = reloaded.knowledge_bases[0]
        assert kb.id == "kb_device"
        assert kb.path == "./kb-device"
        assert kb.tags == ["t"]
        assert kb.preferred_profiles == ["default", "p2"]

    def test_load_missing_file_raises(self, tmp_path: Path):
        path = registry_path(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_registry(path)

    def test_yaml_uses_unicode(self, tmp_path: Path):
        # allow_unicode=True is critical for Japanese KB descriptions
        reg = KnowledgeRegistry(
            generated_at=now_utc_iso(),
            knowledge_bases=[
                RegistryKnowledgeBase(
                    id="kb_jp", path="./kb-jp", name="日本語KB", description="テスト"
                ),
            ],
        )
        path = registry_path(tmp_path)
        dump_registry(reg, path)
        content = path.read_text(encoding="utf-8")
        assert "日本語KB" in content
        assert "テスト" in content


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------


class TestJsonSchema:
    def test_schema_returns_dict(self):
        schema = registry_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema

    def test_schema_includes_knowledge_bases(self):
        schema = registry_json_schema()
        assert "knowledge_bases" in schema["properties"]
