from pathlib import Path

import pytest

from mrag.config.mcp import load_mcp_config, resolve_mcp_config
from mrag.config.project import load_project_config
from mrag.mcp.tools import (
    McpToolContext,
    inspect_chunk_tool,
    inspect_document_tool,
    list_documents_tool,
    list_profiles_tool,
    search_tool,
)
from tests.inspect_fixtures import (
    init_inspect_project,
    open_seed_conn,
    seed_chunk,
    seed_document,
    seed_index,
    seed_profile,
    seed_variant,
)


def _seed_mcp_project(tmp_path: Path, monkeypatch) -> McpToolContext:
    import importlib

    init_mod = importlib.import_module("mrag.cli.init")
    monkeypatch.setattr(init_mod, "detect_best_tokenizer", lambda: ("trigram", None))
    project_dir, db_path = init_inspect_project(tmp_path, monkeypatch)
    knowledge_id = load_project_config(project_dir).knowledge_id
    conn = open_seed_conn(db_path)
    try:
        with conn:
            seed_document(conn, "d1", filename="manual.txt", knowledge_id=knowledge_id)
            seed_profile(conn, "default", knowledge_id=knowledge_id)
            seed_index(
                conn,
                "idx_d1_default",
                document_id="d1",
                profile_name="default",
                knowledge_id=knowledge_id,
            )
            seed_chunk(
                conn,
                "c1",
                document_id="d1",
                profile_name="default",
                content="alpha beta gamma",
                metadata={"heading_path": ["Manual", "Alpha"]},
                knowledge_id=knowledge_id,
            )
            seed_variant(
                conn,
                "v1",
                chunk_id="c1",
                document_id="d1",
                knowledge_id=knowledge_id,
            )
            conn.execute(
                """
                INSERT INTO fts_chunks
                  (content, knowledge_id, profile_name, chunk_id, document_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("alpha beta gamma", knowledge_id, "default", "c1", "d1"),
            )
    finally:
        conn.close()

    cfg = load_mcp_config(env={"MRAG_PROJECT_DIR": str(project_dir)})
    return McpToolContext(resolve_mcp_config(cfg, env={}))


def test_list_documents_tool(tmp_path: Path, monkeypatch):
    ctx = _seed_mcp_project(tmp_path, monkeypatch)

    payload = list_documents_tool(ctx)

    assert payload["total"] == 1
    assert payload["documents"][0]["filename"] == "manual.txt"


def test_list_profiles_tool(tmp_path: Path, monkeypatch):
    ctx = _seed_mcp_project(tmp_path, monkeypatch)

    payload = list_profiles_tool(ctx)

    assert payload["default_profile"] == "default"
    assert payload["profiles"][0]["name"] == "default"
    assert payload["profiles"][0]["retrieval_strategy"] == "hybrid"


def test_search_tool_keyword(tmp_path: Path, monkeypatch):
    ctx = _seed_mcp_project(tmp_path, monkeypatch)

    payload = search_tool(ctx, query="alpha", strategy="keyword", top_k=3)

    assert payload["strategy"] == "keyword"
    assert payload["result_count"] == 1
    assert payload["results"][0]["chunk_id"] == "c1"
    assert payload["results"][0]["filename"] == "manual.txt"


def test_search_tool_rejects_top_k_above_limit(tmp_path: Path, monkeypatch):
    ctx = _seed_mcp_project(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="top_k"):
        search_tool(ctx, query="alpha", strategy="keyword", top_k=999)


def test_search_tool_resolves_defaults_from_requested_profile(tmp_path: Path, monkeypatch):
    import yaml
    import mrag.mcp.tools as tools_module

    ctx = _seed_mcp_project(tmp_path, monkeypatch)
    default_path = ctx.project_dir / "profiles" / "default.yaml"
    alternate_path = ctx.project_dir / "profiles" / "alternate.yaml"
    profile_data = yaml.safe_load(default_path.read_text(encoding="utf-8"))
    profile_data["name"] = "alternate"
    profile_data["retrieval"].update({"strategy": "keyword", "top_k": 9})
    alternate_path.write_text(
        yaml.safe_dump(profile_data, sort_keys=False),
        encoding="utf-8",
    )

    captured = {}

    def fake_run_retrieval(**kwargs):
        captured.update(kwargs)
        return type(
            "Run",
            (),
            {
                "results": [],
                "profile_name": "alternate",
                "strategy": "keyword",
                "reranked": False,
            },
        )()

    monkeypatch.setattr(tools_module, "run_retrieval", fake_run_retrieval)

    payload = search_tool(ctx, query="alpha", profile="alternate")

    assert payload["profile"] == "alternate"
    assert captured["profile_name"] == "alternate"
    assert captured["strategy"] is None
    assert captured["top_k"] == 9


def test_inspect_document_tool(tmp_path: Path, monkeypatch):
    ctx = _seed_mcp_project(tmp_path, monkeypatch)

    payload = inspect_document_tool(ctx, document_id="d1")

    assert payload["document"]["filename"] == "manual.txt"
    assert payload["profiles"][0]["name"] == "default"


def test_inspect_chunk_tool_includes_content(tmp_path: Path, monkeypatch):
    ctx = _seed_mcp_project(tmp_path, monkeypatch)

    payload = inspect_chunk_tool(ctx, chunk_id="c1")

    assert payload["content"] == "alpha beta gamma"
    assert payload["metadata"]["heading_path"] == ["Manual", "Alpha"]
