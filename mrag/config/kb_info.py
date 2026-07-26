"""KbInformationConfig — agent-facing metadata for an mrag project.

Each mrag project carries a `kb_information.yaml` describing what the knowledge
base is for, what queries it handles well, and which retrieval profiles are
preferred. This file is read by external agents (Agentic RAG workflows) — mrag
itself does not consume it at runtime.

See:
  - dev_docs/01_EXTENSION_STAGE_1/MRAG_KB_INFORMATION_INIT_SPEC.md  (specification)
  - dev_docs/01_EXTENSION_STAGE_1/DESIGN_V17_KB_INFORMATION.md      (design)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


KB_INFORMATION_FILENAME = "kb_information.yaml"

# slug rule (spec §12.1): lowercase alphanumeric + underscore (digits allowed,
# leading digit allowed for slug compatibility, but cannot be all-digits).
_KB_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


def _slugify_kb_id(raw: str) -> str:
    """Best-effort normalization of a free-form name to a slug-safe kb id."""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    return s.strip("_")


def suggest_kb_id(raw: str) -> str:
    """Return a slug-safe kb id derived from `raw`, never empty."""
    return _slugify_kb_id(raw) or "kb_unnamed"


def validate_kb_id(value: str) -> str:
    """Validate a kb id against the spec §12.1 slug rule; raise ValueError if invalid.

    This is the single definition of the rule. `mrag init` calls it before the id
    reaches either mrag.yaml or kb_information.yaml, so one id can never be
    accepted by one file and rejected by the other.
    """
    if not value:
        raise ValueError("knowledge_base.id must not be empty")
    if not _KB_ID_PATTERN.match(value):
        raise ValueError(
            f"knowledge_base.id '{value}' contains invalid characters. "
            f"Use lowercase alphanumeric + underscore. "
            f"Suggestion: '{suggest_kb_id(value)}'"
        )
    return value


class KbInfoKnowledgeBase(BaseModel):
    id: str
    name: str
    description: str = ""

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return validate_kb_id(v)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v:
            raise ValueError("knowledge_base.name must not be empty")
        return v


class KbInfoAgentUsage(BaseModel):
    tags: list[str] = Field(default_factory=list)
    best_for: list[str] = Field(default_factory=list)
    avoid_for: list[str] = Field(default_factory=list)
    preferred_profiles: list[str] = Field(default_factory=lambda: ["default"])
    example_queries: list[str] = Field(default_factory=list)


class KbInformationConfig(BaseModel):
    """Top-level kb_information.yaml schema (version 1)."""

    version: Literal[1] = 1
    knowledge_base: KbInfoKnowledgeBase
    agent_usage: KbInfoAgentUsage = Field(default_factory=KbInfoAgentUsage)

    @model_validator(mode="after")
    def _normalize_preferred_profiles(self) -> "KbInformationConfig":
        # spec §9.2: empty preferred_profiles defaults to ["default"].
        if not self.agent_usage.preferred_profiles:
            self.agent_usage.preferred_profiles = ["default"]
        return self


# ---------------------------------------------------------------------------
# JSON input (agent-facing) schema
#
# The JSON file accepted by `mrag init --kb-info-json` adds a top-level
# `project.name` field that does not exist in kb_information.yaml itself
# (it is consumed by mrag.yaml). Modeling it separately keeps the YAML schema
# clean while still letting Pydantic validate the JSON input shape.
# ---------------------------------------------------------------------------


class KbInfoJsonProject(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v:
            raise ValueError("project.name must not be empty")
        return v


class KbInformationInput(BaseModel):
    """Pydantic shape for `--kb-info-json` input files."""

    project: KbInfoJsonProject
    knowledge_base: KbInfoKnowledgeBase
    agent_usage: KbInfoAgentUsage = Field(default_factory=KbInfoAgentUsage)

    def to_kb_information(self) -> KbInformationConfig:
        """Extract the kb_information.yaml-shaped payload from the JSON input."""
        return KbInformationConfig(
            knowledge_base=self.knowledge_base,
            agent_usage=self.agent_usage,
        )


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def build_minimal_kb_info(name: str, kb_id: str) -> KbInformationConfig:
    """Construct the spec §12 minimal template from project name + kb_id."""
    return KbInformationConfig(
        knowledge_base=KbInfoKnowledgeBase(
            id=kb_id,
            name=name,
            description="",
        ),
    )


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def kb_info_path(project_dir: Path) -> Path:
    return Path(project_dir) / KB_INFORMATION_FILENAME


def load_kb_info(project_dir: Path) -> KbInformationConfig:
    """Read and validate kb_information.yaml from a project directory."""
    path = kb_info_path(project_dir)
    if not path.exists():
        raise FileNotFoundError(f"kb_information.yaml not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return KbInformationConfig(**data)


def dump_kb_info(config: KbInformationConfig, project_dir: Path) -> Path:
    """Write kb_information.yaml to a project directory; returns the written path."""
    path = kb_info_path(project_dir)
    data = config.model_dump()
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def kb_info_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for `--kb-info-json` input files."""
    return KbInformationInput.model_json_schema()


__all__ = [
    "KB_INFORMATION_FILENAME",
    "KbInfoAgentUsage",
    "KbInfoJsonProject",
    "KbInfoKnowledgeBase",
    "KbInformationConfig",
    "KbInformationInput",
    "build_minimal_kb_info",
    "dump_kb_info",
    "kb_info_json_schema",
    "kb_info_path",
    "load_kb_info",
    "suggest_kb_id",
    "validate_kb_id",
]
