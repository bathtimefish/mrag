import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mrag.cli import app
from mrag.config.mcp import (
    default_mcp_config_yaml,
    effective_config_dict,
    load_mcp_config,
    mcp_json_schema,
    resolve_mcp_config,
)


runner = CliRunner()


def _init_project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["init", "--name", "kb-mcp", "--non-interactive"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    return tmp_path / "kb-mcp"


def test_default_config_resolves_project_and_profile(tmp_path: Path, monkeypatch):
    project_dir = _init_project(tmp_path, monkeypatch)
    monkeypatch.chdir(project_dir)

    cfg = load_mcp_config(env={})
    effective = resolve_mcp_config(cfg, env={})

    assert cfg.transport == "stdio"
    assert effective.project_dir == project_dir.resolve()
    assert effective.profile_name == "default"
    assert effective.retrieval_strategy == "hybrid"
    assert effective.top_k_default == 8


def test_yaml_config_relative_project_dir_resolves_from_config_file(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_project(tmp_path, monkeypatch)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg_path = config_dir / "mrag-mcp.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "project_dir": "../kb-mcp",
                "transport": "streamable-http",
                "http": {"port": 9001},
            }
        ),
        encoding="utf-8",
    )

    cfg = load_mcp_config(cfg_path, env={})
    effective = resolve_mcp_config(cfg, env={})

    assert effective.project_dir == project_dir.resolve()
    assert cfg.transport == "streamable-http"
    assert cfg.http.port == 9001


def test_env_overrides_yaml(tmp_path: Path, monkeypatch):
    project_dir = _init_project(tmp_path, monkeypatch)
    cfg_path = tmp_path / "mrag-mcp.yaml"
    cfg_path.write_text(
        "project_dir: missing\ntransport: stdio\nhttp:\n  port: 8001\n",
        encoding="utf-8",
    )

    cfg = load_mcp_config(
        cfg_path,
        env={
            "MRAG_PROJECT_DIR": str(project_dir),
            "MRAG_MCP_TRANSPORT": "streamable-http",
            "MRAG_MCP_PORT": "9010",
            "MRAG_MCP_NO_RERANK": "true",
            "MRAG_MCP_ALLOWED_ORIGINS": "http://localhost:3000, https://example.com",
        },
    )

    assert cfg.project_dir == project_dir.resolve()
    assert cfg.transport == "streamable-http"
    assert cfg.http.port == 9010
    assert cfg.retrieval.no_rerank is True
    assert cfg.http.allowed_origins == [
        "http://localhost:3000",
        "https://example.com",
    ]


def test_effective_config_masks_secret(tmp_path: Path, monkeypatch):
    project_dir = _init_project(tmp_path, monkeypatch)
    cfg = load_mcp_config(env={"MRAG_PROJECT_DIR": str(project_dir)})
    effective = resolve_mcp_config(cfg, env={"MRAG_MCP_API_KEY": "secret"})

    data = effective_config_dict(effective)

    assert data["auth"]["bearer_token_resolved"] == "***"
    assert "secret" not in json.dumps(data)


def test_top_k_default_must_not_exceed_max(tmp_path: Path, monkeypatch):
    project_dir = _init_project(tmp_path, monkeypatch)
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "project_dir": str(project_dir),
                "retrieval": {"top_k_default": 20, "top_k_max": 10},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top_k_default"):
        load_mcp_config(cfg_path, env={})


def test_schema_and_init_config_are_machine_readable():
    schema = mcp_json_schema()
    assert "properties" in schema
    sample = yaml.safe_load(default_mcp_config_yaml())
    assert sample["transport"] == "stdio"
    assert sample["features"]["tools"] is True

