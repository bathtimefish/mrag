"""Resource helpers for the mrag MCP server."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from mrag.mcp.tools import (
    McpToolContext,
    find_document_row,
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
    row = find_document_row(ctx, document_id)
    if row is None:
        raise FileNotFoundError(f"document '{document_id}' not found")
    return json.dumps(row, ensure_ascii=False, indent=2)


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


# The MIME type a stored original is served as, by the source type recorded
# when it was added. Whether it is sent as text or as a blob follows from this,
# not from trying to decode the bytes: a file that happened to decode would
# otherwise arrive as text or not depending on its contents.
_ORIGINAL_MIME_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "pdf": "application/pdf",
}
_ORIGINAL_URI = re.compile(r"^mrag://documents/([^/]+)/original$")


def _original_row(ctx: McpToolContext, document_id: str):
    from mrag.db.connection import open_connection

    conn = open_connection(ctx.db_path)
    try:
        row = conn.execute(
            "SELECT original_path, source_type FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise FileNotFoundError(f"document '{document_id}' not found")
    return row


def original_document_id(uri: str) -> str | None:
    """The document a ``mrag://documents/<id>/original`` URI names, if it is one."""
    match = _ORIGINAL_URI.match(uri)
    return match.group(1) if match else None


def original_mime_type(ctx: McpToolContext, document_id: str) -> str:
    """The MIME type a document's stored original is served as."""
    row = _original_row(ctx, document_id)
    return _ORIGINAL_MIME_TYPES.get(row["source_type"], "application/octet-stream")


def original_resource(ctx: McpToolContext, document_id: str) -> str | bytes:
    """A document's stored original: text for a text type, bytes otherwise.

    Refused above ``limits.artifact_max_bytes`` rather than cut, and text that
    fits is held to ``limits.content_max_chars`` like every other text served.
    """
    row = _original_row(ctx, document_id)
    rel = row["original_path"]
    path = ctx.project_dir / rel
    if not path.is_file():
        raise FileNotFoundError(f"original file not found: {rel}")
    limits = ctx.effective.raw.limits
    size = path.stat().st_size
    if size > limits.artifact_max_bytes:
        raise ValueError(
            f"the original of '{document_id}' is {size} bytes, over limits.artifact_max_bytes "
            f"({limits.artifact_max_bytes}); read {rel} from the project instead"
        )
    if _ORIGINAL_MIME_TYPES.get(row["source_type"], "application/octet-stream").startswith("text/"):
        return _read_limited(path, limits.content_max_chars)
    return path.read_bytes()


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
    "original_document_id",
    "original_mime_type",
    "original_resource",
    "profile_resource",
    "profiles_resource",
]
