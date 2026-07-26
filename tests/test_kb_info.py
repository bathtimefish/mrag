"""Tests for KbInformationConfig (Phase 1 — v0.17 kb_information.yaml)."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mrag.config.kb_info import (
    KB_INFORMATION_FILENAME,
    KbInfoAgentUsage,
    KbInfoKnowledgeBase,
    KbInformationConfig,
    KbInformationInput,
    build_minimal_kb_info,
    dump_kb_info,
    kb_info_json_schema,
    kb_info_path,
    load_kb_info,
    suggest_kb_id,
    validate_kb_id,
)


# ---------------------------------------------------------------------------
# KbInfoKnowledgeBase validation
# ---------------------------------------------------------------------------

class TestKnowledgeBaseValidation:
    def test_minimal_valid(self):
        kb = KbInfoKnowledgeBase(id="kb_x", name="x")
        assert kb.id == "kb_x"
        assert kb.name == "x"
        assert kb.description == ""

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            KbInfoKnowledgeBase(id="", name="x")

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            KbInfoKnowledgeBase(id="kb_x", name="")

    def test_invalid_id_chars_raise(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            KbInfoKnowledgeBase(id="KB-Device!", name="x")

    def test_invalid_id_suggests_normalized(self):
        with pytest.raises(ValidationError, match="kb_device"):
            KbInfoKnowledgeBase(id="KB-Device", name="x")

    def test_id_uppercase_rejected(self):
        with pytest.raises(ValidationError):
            KbInfoKnowledgeBase(id="KbDevice", name="x")

    def test_id_with_hyphen_rejected(self):
        with pytest.raises(ValidationError):
            KbInfoKnowledgeBase(id="kb-device", name="x")

    def test_id_with_underscore_and_digits_ok(self):
        kb = KbInfoKnowledgeBase(id="kb_device_v2", name="x")
        assert kb.id == "kb_device_v2"

    def test_description_can_be_empty(self):
        kb = KbInfoKnowledgeBase(id="kb_x", name="x", description="")
        assert kb.description == ""

    def test_description_with_unicode(self):
        kb = KbInfoKnowledgeBase(id="kb_x", name="x", description="日本語の説明")
        assert kb.description == "日本語の説明"


# ---------------------------------------------------------------------------
# validate_kb_id / suggest_kb_id — the shared rule
#
# `mrag init` calls these before writing mrag.yaml, and KbInfoKnowledgeBase
# calls validate_kb_id from its field validator. Both entry points must agree,
# or an id could be accepted by one file and rejected by the other.
# ---------------------------------------------------------------------------

class TestValidateKbId:
    def test_valid_id_returns_unchanged(self):
        assert validate_kb_id("kb_device_v2") == "kb_device_v2"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_kb_id("")

    def test_hyphen_raises_with_suggestion(self):
        with pytest.raises(ValueError, match="kb_device"):
            validate_kb_id("kb-device")

    def test_uppercase_raises(self):
        with pytest.raises(ValueError, match="invalid characters"):
            validate_kb_id("KbDevice")

    def test_agrees_with_the_model_validator(self):
        """Whatever validate_kb_id accepts, KbInfoKnowledgeBase must accept too."""
        for candidate in ["kb_x", "kb_device_v2", "a", "0", "kb_", "_"]:
            assert validate_kb_id(candidate) == candidate
            assert KbInfoKnowledgeBase(id=candidate, name="x").id == candidate

    def test_rejections_agree_with_the_model_validator(self):
        for candidate in ["", "kb-device", "KbDevice", "kb device", "kb.device", "kb/../x"]:
            with pytest.raises(ValueError):
                validate_kb_id(candidate)
            with pytest.raises(ValidationError):
                KbInfoKnowledgeBase(id=candidate, name="x")

    def test_suggestion_is_always_usable(self):
        for raw in ["KB-Device!", "my kb", "___", "", "日本語"]:
            assert validate_kb_id(suggest_kb_id(raw)) == suggest_kb_id(raw)


# ---------------------------------------------------------------------------
# KbInfoAgentUsage defaults
# ---------------------------------------------------------------------------

class TestAgentUsageDefaults:
    def test_all_defaults(self):
        au = KbInfoAgentUsage()
        assert au.tags == []
        assert au.best_for == []
        assert au.avoid_for == []
        assert au.preferred_profiles == ["default"]
        assert au.example_queries == []

    def test_tags_must_be_list_of_str(self):
        with pytest.raises(ValidationError):
            KbInfoAgentUsage(tags=[{"not": "a string"}])

    def test_preferred_profiles_explicit_overrides_default(self):
        au = KbInfoAgentUsage(preferred_profiles=["foo", "bar"])
        assert au.preferred_profiles == ["foo", "bar"]


# ---------------------------------------------------------------------------
# KbInformationConfig
# ---------------------------------------------------------------------------

class TestKbInformationConfig:
    def test_minimal_config_is_valid(self):
        cfg = KbInformationConfig(knowledge_base={"id": "kb_x", "name": "x"})
        assert cfg.version == 1
        assert cfg.knowledge_base.id == "kb_x"
        assert cfg.agent_usage.preferred_profiles == ["default"]

    def test_version_must_be_1(self):
        with pytest.raises(ValidationError):
            KbInformationConfig(version=2, knowledge_base={"id": "kb_x", "name": "x"})

    def test_preferred_profiles_default(self):
        cfg = KbInformationConfig(knowledge_base={"id": "kb_x", "name": "x"})
        assert cfg.agent_usage.preferred_profiles == ["default"]

    def test_preferred_profiles_empty_normalizes(self):
        cfg = KbInformationConfig(
            knowledge_base={"id": "kb_x", "name": "x"},
            agent_usage={"preferred_profiles": []},
        )
        assert cfg.agent_usage.preferred_profiles == ["default"]

    def test_preferred_profiles_explicit_kept(self):
        cfg = KbInformationConfig(
            knowledge_base={"id": "kb_x", "name": "x"},
            agent_usage={"preferred_profiles": ["custom"]},
        )
        assert cfg.agent_usage.preferred_profiles == ["custom"]

    def test_knowledge_base_required(self):
        with pytest.raises(ValidationError):
            KbInformationConfig()


# ---------------------------------------------------------------------------
# KbInformationInput (JSON input shape)
# ---------------------------------------------------------------------------

class TestJsonInput:
    def test_full_input_parses(self):
        data = {
            "project": {"name": "device-kb"},
            "knowledge_base": {
                "id": "kb_device",
                "name": "Device KB",
                "description": "A device KB.",
            },
            "agent_usage": {
                "tags": ["m5stack"],
                "best_for": ["LTE troubleshooting"],
                "avoid_for": ["Contracts"],
                "preferred_profiles": ["default", "hybrid-rerank"],
                "example_queries": ["MQTT stops"],
            },
        }
        inp = KbInformationInput(**data)
        assert inp.project.name == "device-kb"
        assert inp.knowledge_base.id == "kb_device"
        assert inp.agent_usage.tags == ["m5stack"]

    def test_minimal_input_with_required_only(self):
        data = {
            "project": {"name": "x"},
            "knowledge_base": {"id": "kb_x", "name": "x", "description": ""},
        }
        inp = KbInformationInput(**data)
        assert inp.agent_usage.preferred_profiles == ["default"]

    def test_missing_project_raises(self):
        with pytest.raises(ValidationError):
            KbInformationInput(knowledge_base={"id": "kb_x", "name": "x"})

    def test_missing_knowledge_base_raises(self):
        with pytest.raises(ValidationError):
            KbInformationInput(project={"name": "x"})

    def test_missing_description_defaults_to_empty(self):
        # spec §9.1 lists description as required but §12 minimal template
        # uses empty string — we accept missing as default "".
        inp = KbInformationInput(
            project={"name": "x"},
            knowledge_base={"id": "kb_x", "name": "x"},
        )
        assert inp.knowledge_base.description == ""

    def test_to_kb_information_strips_project(self):
        inp = KbInformationInput(
            project={"name": "device-kb"},
            knowledge_base={"id": "kb_device", "name": "Device", "description": "d"},
            agent_usage={"tags": ["a"]},
        )
        cfg = inp.to_kb_information()
        assert isinstance(cfg, KbInformationConfig)
        assert cfg.knowledge_base.id == "kb_device"
        assert cfg.agent_usage.tags == ["a"]
        # KbInformationConfig has no `project` attribute
        assert not hasattr(cfg, "project")


# ---------------------------------------------------------------------------
# build_minimal_kb_info
# ---------------------------------------------------------------------------

class TestBuildMinimal:
    def test_produces_spec_minimal_template(self):
        cfg = build_minimal_kb_info("my-kb", "kb_my")
        d = cfg.model_dump()
        assert d == {
            "version": 1,
            "knowledge_base": {
                "id": "kb_my",
                "name": "my-kb",
                "description": "",
            },
            "agent_usage": {
                "tags": [],
                "best_for": [],
                "avoid_for": [],
                "preferred_profiles": ["default"],
                "example_queries": [],
            },
        }


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------

class TestJsonSchema:
    def test_json_schema_returns_dict(self):
        schema = kb_info_json_schema()
        assert isinstance(schema, dict)

    def test_json_schema_has_required_top_level_fields(self):
        schema = kb_info_json_schema()
        assert "project" in schema["required"]
        assert "knowledge_base" in schema["required"]


# ---------------------------------------------------------------------------
# I/O: load and dump round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_dump_creates_file(self, tmp_path: Path):
        cfg = build_minimal_kb_info("my-kb", "kb_my")
        path = dump_kb_info(cfg, tmp_path)
        assert path == tmp_path / KB_INFORMATION_FILENAME
        assert path.exists()

    def test_dump_then_load_preserves_data(self, tmp_path: Path):
        original = KbInformationConfig(
            knowledge_base={"id": "kb_round", "name": "Round Trip", "description": "ok"},
            agent_usage={
                "tags": ["a", "b"],
                "best_for": ["x"],
                "avoid_for": ["y"],
                "preferred_profiles": ["default", "second"],
                "example_queries": ["q1", "q2"],
            },
        )
        dump_kb_info(original, tmp_path)
        loaded = load_kb_info(tmp_path)
        assert loaded.model_dump() == original.model_dump()

    def test_dump_writes_human_readable_yaml(self, tmp_path: Path):
        cfg = build_minimal_kb_info("my-kb", "kb_my")
        path = dump_kb_info(cfg, tmp_path)
        text = path.read_text(encoding="utf-8")
        # Block style (not flow style)
        assert "{" not in text or text.count("{") < 3
        # Keys appear in expected order
        version_pos = text.find("version:")
        kb_pos = text.find("knowledge_base:")
        au_pos = text.find("agent_usage:")
        assert 0 <= version_pos < kb_pos < au_pos

    def test_dump_preserves_unicode(self, tmp_path: Path):
        cfg = KbInformationConfig(
            knowledge_base={"id": "kb_jp", "name": "日本語KB", "description": "説明"},
        )
        path = dump_kb_info(cfg, tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "日本語KB" in text
        assert "説明" in text

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_kb_info(tmp_path)

    def test_load_invalid_yaml_raises(self, tmp_path: Path):
        path = kb_info_path(tmp_path)
        path.write_text("knowledge_base:\n  id: 'BAD-ID!'\n  name: x\n", encoding="utf-8")
        with pytest.raises(ValidationError):
            load_kb_info(tmp_path)

    def test_load_empty_yaml_raises(self, tmp_path: Path):
        path = kb_info_path(tmp_path)
        path.write_text("", encoding="utf-8")
        # Empty YAML → empty dict → missing required knowledge_base → ValidationError
        with pytest.raises(ValidationError):
            load_kb_info(tmp_path)
