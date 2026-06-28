"""Resource helpers for the mrag MCP server."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from mrag.mcp.tools import (
    McpToolContext,
    inspect_chunk_tool,
    list_documents_tool,
    list_profiles_tool,
)


def _read_limited(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8")
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def kb_info_resource(ctx: McpToolContext) -> str:
    path = ctx.project_dir / "kb_information.yaml"
    if not path.exists():
        raise FileNotFoundError("kb_information.yaml not found")
    return _read_limited(path, ctx.effective.raw.limits.content_max_chars)


def profiles_resource(ctx: McpToolContext) -> str:
    return json.dumps(list_profiles_tool(ctx), ensure_ascii=False, indent=2)


def profile_resource(ctx: McpToolContext, profile: str) -> str:
    path = ctx.project_dir / "profiles" / f"{profile}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"profile '{profile}' not found")
    return _read_limited(path, ctx.effective.raw.limits.content_max_chars)


def documents_resource(ctx: McpToolContext) -> str:
    return json.dumps(list_documents_tool(ctx), ensure_ascii=False, indent=2)


def document_resource(ctx: McpToolContext, document_id: str) -> str:
    docs = list_documents_tool(ctx, limit=100000)["documents"]
    for doc in docs:
        if doc["id"] == document_id:
            return json.dumps(doc, ensure_ascii=False, indent=2)
    raise FileNotFoundError(f"document '{document_id}' not found")


def extracted_resource(ctx: McpToolContext, document_id: str, suffix: str) -> str:
    from mrag.db.connection import open_connection

    conn = open_connection(ctx.db_path)
    try:
        row = conn.execute(
            """
            SELECT extracted_text_path, extracted_markdown_path
            FROM documents WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise FileNotFoundError(f"document '{document_id}' not found")
    rel = row["extracted_text_path"] if suffix == "txt" else row["extracted_markdown_path"]
    if not rel:
        raise FileNotFoundError(f"document '{document_id}' has no extracted.{suffix}")
    path = ctx.project_dir / rel
    if not path.exists():
        raise FileNotFoundError(f"extracted file not found: {rel}")
    return _read_limited(path, ctx.effective.raw.limits.content_max_chars)


def chunk_resource(ctx: McpToolContext, chunk_id: str) -> str:
    return json.dumps(inspect_chunk_tool(ctx, chunk_id=chunk_id), ensure_ascii=False, indent=2)


def config_resource(ctx: McpToolContext) -> str:
    from mrag.config.mcp import effective_config_dict

    return yaml.dump(
        effective_config_dict(ctx.effective),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


__all__ = [
    "chunk_resource",
    "config_resource",
    "document_resource",
    "documents_resource",
    "extracted_resource",
    "kb_info_resource",
    "profile_resource",
    "profiles_resource",
]
