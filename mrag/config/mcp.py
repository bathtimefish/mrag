"""Configuration loading for `mrag mcp`.

The MCP command intentionally keeps CLI options small. Runtime behaviour is
resolved from:

    environment variables > mrag-mcp.yaml > mrag.yaml/profile > built-ins
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mrag.config.profile import ProfileConfig, load_profile
from mrag.config.project import ProjectConfig, load_project_config


class McpRetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["hybrid", "vector", "keyword", "parent_child"] | None = None
    top_k_default: int | None = Field(default=None, ge=1)
    top_k_max: int = Field(default=50, ge=1)
    no_rerank: bool = False


class McpHttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8001, ge=1, le=65535)
    path: str = "/mcp"
    allowed_origins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_path(self) -> "McpHttpConfig":
        if not self.path:
            self.path = "/mcp"
        if not self.path.startswith("/"):
            self.path = "/" + self.path
        return self


class McpAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bearer_token_env: str | None = "MRAG_MCP_API_KEY"
    bearer_token_file_env: str | None = "MRAG_MCP_API_KEY_FILE"


class McpFeaturesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: bool = True
    resources: bool = True
    management_tools: bool = False


class McpLimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_max_chars: int = Field(default=20000, ge=0)
    request_timeout_seconds: int = Field(default=60, ge=1)


class McpLoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    project_dir: Path = Path(".")
    profile: str | None = None
    transport: Literal["stdio", "streamable-http"] = "stdio"
    retrieval: McpRetrievalConfig = Field(default_factory=McpRetrievalConfig)
    http: McpHttpConfig = Field(default_factory=McpHttpConfig)
    auth: McpAuthConfig = Field(default_factory=McpAuthConfig)
    features: McpFeaturesConfig = Field(default_factory=McpFeaturesConfig)
    limits: McpLimitsConfig = Field(default_factory=McpLimitsConfig)
    logging: McpLoggingConfig = Field(default_factory=McpLoggingConfig)

    @model_validator(mode="after")
    def _validate_limits(self) -> "McpConfig":
        if (
            self.retrieval.top_k_default is not None
            and self.retrieval.top_k_default > self.retrieval.top_k_max
        ):
            raise ValueError("retrieval.top_k_default must be <= retrieval.top_k_max")
        if self.features.management_tools:
            raise ValueError("features.management_tools is not supported in v0.22.0")
        return self


@dataclass(frozen=True)
class EffectiveMcpConfig:
    raw: McpConfig
    project_dir: Path
    project_config: ProjectConfig
    profile_name: str
    profile_config: ProfileConfig
    retrieval_strategy: str
    top_k_default: int
    auth_token: str | None


def _parse_bool(value: str, env_name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{env_name} must be a boolean value "
        "(1/0, true/false, yes/no, on/off)"
    )


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur = data
    for key in path[:-1]:
        cur = cur.setdefault(key, {})
    cur[path[-1]] = value


_ENV_OVERRIDES: dict[str, tuple[tuple[str, ...], Any]] = {
    "MRAG_PROJECT_DIR": (("project_dir",), Path),
    "MRAG_MCP_TRANSPORT": (("transport",), str),
    "MRAG_MCP_PROFILE": (("profile",), str),
    "MRAG_MCP_STRATEGY": (("retrieval", "strategy"), str),
    "MRAG_MCP_TOP_K_DEFAULT": (("retrieval", "top_k_default"), int),
    "MRAG_MCP_TOP_K_MAX": (("retrieval", "top_k_max"), int),
    "MRAG_MCP_NO_RERANK": (("retrieval", "no_rerank"), _parse_bool),
    "MRAG_MCP_HOST": (("http", "host"), str),
    "MRAG_MCP_PORT": (("http", "port"), int),
    "MRAG_MCP_PATH": (("http", "path"), str),
    "MRAG_MCP_LOG_LEVEL": (("logging", "level"), str),
}


def _apply_env_overrides(
    data: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    for env_name, (path, caster) in _ENV_OVERRIDES.items():
        raw = env.get(env_name)
        if raw is None or raw == "":
            continue
        if caster is _parse_bool:
            value = _parse_bool(raw, env_name)
        else:
            value = caster(raw)
        _set_nested(data, path, value)

    origins = env.get("MRAG_MCP_ALLOWED_ORIGINS")
    if origins:
        _set_nested(
            data,
            ("http", "allowed_origins"),
            [item.strip() for item in origins.split(",") if item.strip()],
        )
    return data


def _read_yaml_config(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}
    if not config_path.exists():
        raise FileNotFoundError(f"MCP config file not found: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"MCP config file must contain a YAML mapping: {config_path}")
    return data


def _resolve_config_path(
    config_path: Path | None,
    env: Mapping[str, str],
) -> Path | None:
    if config_path is not None:
        return config_path.expanduser()
    env_path = env.get("MRAG_MCP_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return None


def load_mcp_config(
    config_path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> McpConfig:
    """Load MCP config YAML and apply environment variable overrides."""
    env = env or os.environ
    resolved_path = _resolve_config_path(config_path, env)
    data = _read_yaml_config(resolved_path)
    data = _apply_env_overrides(data, env)
    cfg = McpConfig(**data)

    # Relative project_dir in a config file is resolved from the config file's
    # directory. Without a config file, it remains relative to cwd.
    if not cfg.project_dir.is_absolute():
        base_dir = resolved_path.parent if resolved_path is not None else Path.cwd()
        cfg.project_dir = (base_dir / cfg.project_dir).resolve()
    else:
        cfg.project_dir = cfg.project_dir.resolve()
    return cfg


def resolve_auth_token(
    cfg: McpConfig,
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    env = env or os.environ

    if cfg.auth.bearer_token_file_env:
        file_path = env.get(cfg.auth.bearer_token_file_env)
        if file_path:
            return Path(file_path).read_text(encoding="utf-8").strip()

    direct = env.get("MRAG_MCP_API_KEY")
    if direct:
        return direct

    if cfg.auth.bearer_token_env:
        value = env.get(cfg.auth.bearer_token_env)
        if value:
            return value
    return None


def resolve_mcp_config(
    cfg: McpConfig,
    *,
    env: Mapping[str, str] | None = None,
) -> EffectiveMcpConfig:
    """Resolve project/profile-derived defaults and validate project state."""
    project_config = load_project_config(cfg.project_dir)
    profile_name = cfg.profile or project_config.default_profile
    profile_config = load_profile(profile_name, cfg.project_dir)
    strategy = cfg.retrieval.strategy or profile_config.retrieval.strategy
    top_k_default = cfg.retrieval.top_k_default or profile_config.retrieval.top_k
    if top_k_default > cfg.retrieval.top_k_max:
        raise ValueError("resolved retrieval.top_k_default must be <= top_k_max")
    return EffectiveMcpConfig(
        raw=cfg,
        project_dir=cfg.project_dir,
        project_config=project_config,
        profile_name=profile_name,
        profile_config=profile_config,
        retrieval_strategy=strategy,
        top_k_default=top_k_default,
        auth_token=resolve_auth_token(cfg, env=env),
    )


def effective_config_dict(
    effective: EffectiveMcpConfig,
    *,
    include_secrets: bool = False,
) -> dict[str, Any]:
    cfg = effective.raw
    data = cfg.model_dump(mode="json")
    data["project_dir"] = str(effective.project_dir)
    data["profile"] = effective.profile_name
    data["retrieval"]["strategy"] = effective.retrieval_strategy
    data["retrieval"]["top_k_default"] = effective.top_k_default
    data["auth"]["bearer_token_resolved"] = (
        effective.auth_token if include_secrets else ("***" if effective.auth_token else None)
    )
    return data


def dump_effective_config(effective: EffectiveMcpConfig) -> str:
    return yaml.dump(
        effective_config_dict(effective),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def mcp_json_schema() -> dict[str, Any]:
    return McpConfig.model_json_schema()


def default_mcp_config_yaml() -> str:
    sample = {
        "version": 1,
        "project_dir": ".",
        "profile": None,
        "transport": "stdio",
        "retrieval": {
            "strategy": None,
            "top_k_default": None,
            "top_k_max": 50,
            "no_rerank": False,
        },
        "http": {
            "host": "127.0.0.1",
            "port": 8001,
            "path": "/mcp",
            "allowed_origins": [],
        },
        "auth": {
            "bearer_token_env": "MRAG_MCP_API_KEY",
            "bearer_token_file_env": "MRAG_MCP_API_KEY_FILE",
        },
        "features": {
            "tools": True,
            "resources": True,
            "management_tools": False,
        },
        "limits": {
            "content_max_chars": 20000,
            "request_timeout_seconds": 60,
        },
        "logging": {
            "level": "INFO",
        },
    }
    return yaml.dump(
        sample,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


__all__ = [
    "EffectiveMcpConfig",
    "McpConfig",
    "default_mcp_config_yaml",
    "dump_effective_config",
    "effective_config_dict",
    "load_mcp_config",
    "mcp_json_schema",
    "resolve_auth_token",
    "resolve_mcp_config",
]
